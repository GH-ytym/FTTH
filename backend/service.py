import asyncio
import contextlib
import contextvars
import copy
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .models import Parameters, PolicyInput, RunInput
from .settings import ROOT, VENDOR
from .store import uid
from .progress import algorithm_step
from .instance_lock import DataDirectoryLock
from .titles import valid_title, fallback_title
from .coordinator import TaskCoordinator

logger = logging.getLogger("ftth.service")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_result(output, prefix):
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            return json.loads(line[len(prefix):])
    return {"status": "failed", "errors": ["工作进程未返回结构化结果，请查看运行日志"]}


def run_summary(run):
    result = run.get("result") or {}
    summary = {k: result[k] for k in ("status", "phase", "errors", "warnings", "output_dir", "report", "input_summary", "region_counts", "release_status", "checks", "downstream_readiness") if k in result}
    if run.get("kind") == "planning":
        summary["validations"] = {name: (section or {}).get("validation") for name, section in {
            "task3": result.get("task3"), **{k: v for k, v in (result.get("downstream") or {}).items()
                if k in {"task4", "task5", "task6", "final_layers"}}}.items() if isinstance(section, dict)}
    if run.get("kind") == "export":
        for key in ("geopackage", "shape_dir", "tables_dir", "maps_dir", "manifest", "validation_report", "validation", "task6_route_filter", "file_count"):
            if key in result:
                summary[key] = result[key]
        root = Path(result["output_dir"]) if result.get("output_dir") else None
        summary["files"] = []
        for item in result.get("files", []):
            path = Path(item["path"])
            name = str(path.relative_to(root)) if root and path.is_relative_to(root) else str(path)
            summary["files"].append({"path": name, "size_bytes": item.get("size_bytes")})
    return {**{k: v for k, v in run.items() if k != "result"},
            "result": summary}


class Service:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.instance_lock = DataDirectoryLock(settings.data)
        self.queue = asyncio.Lock()
        self.tasks = set()
        self.processes = {}
        self.on_complete = None
        self.reviewer = None
        self.on_review = None
        self.closing = False
        self.workflows = None
        self.coordinator = TaskCoordinator(store)
        self.coordinator.recover()
        for workflow in store.all('workflow'):
            if workflow['status'] not in {'completed', 'cancelled', 'failed', 'interrupted', 'waiting_choice', 'blocked'}:
                workflow.update(status='interrupted', detail='服务已重启，任务不会自动重放', options=[])
                store.put('workflow', workflow)
        for run in store.all("run"):
            if run["status"] in ("queued", "running"):
                run.update(status="interrupted", error="后端已重启；原任务不自动重跑")
                store.put("run", run)
        for message in store.all("message"):
            if message["status"] in ("queued", "running"):
                message.update(status="interrupted")
                store.put("message", message)
        for review in store.all("review"):
            if review["status"] in ("queued", "running"):
                review.update(status="interrupted", error="后端已重启；不恢复自动重算")
                run = store.get("run", review["run_id"])
                run["review"] = {k: review[k] for k in ("id", "status", "error")}
                store.publish_objects([("review", review), ("run", run)], review["session_id"], "review",
                    run_id=run["id"], review_id=review["id"], status="interrupted", summary=run["review"])
        for session in store.all("session"):
            if session.get("title") != "新对话" and not valid_title(session.get("title")):
                first = next((m for m in store.find("message", session["id"]) if not m.get("notification")), None)
                if first:
                    session["title"] = fallback_title(first["content"])
                    store.put("session", session)

    def spawn(self, coro):
        # A detached job must not inherit the submitting LangChain run's callbacks.
        task = asyncio.create_task(coro, context=contextvars.Context())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def close(self):
        self.closing = True
        try:
            if self.reviewer:
                for run in self.store.all("run"):
                    if run.get("kind") == "planning":
                        self.reviewer.cancel(run["session_id"], run["id"])
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
        finally:
            self.instance_lock.close()

    def set_policy(self, session_id, policy):
        with self.store.lock:
            session = self.store.get("session", session_id)
            if not session.get("project_id"):
                raise ValueError("请先为此会话登记数据，再保存工程策略")
            session.update(policy=policy.model_dump(), policy_confirmed_at=time.time(), policy_version=session.get("policy_version", 0) + 1)
            return self.store.put("session", session)

    def authorize_agent(self, session_id, message_id, parameters=None, *, action='plan', waiting=False):
        message = self.store.get("message", message_id)
        session = self.store.get("session", session_id)
        if message["session_id"] != session_id or message.get("notification") or not message.get('workflow_id'):
            raise ValueError("本条消息没有经过需求识别的执行授权，请明确描述要执行的操作。")
        if not message.get("project_id") or message["project_id"] != session.get("project_id"):
            raise ValueError("消息提交时未绑定此项目数据，请登记后重新发送执行请求")
        workflow = self.store.get('workflow', message['workflow_id'])
        auth = workflow.get('authorization', {})
        intent = workflow.get('intent', {})
        states = {'waiting_choice'} if waiting else {'validating', 'submitted', 'running', 'reviewing'}
        quote = auth.get('evidence_quote', '')
        if (workflow.get('session_id') != session_id or workflow.get('message_id') != message_id or
            workflow.get('project_id') != message.get('project_id') or workflow['status'] not in states or
            auth.get('source') != 'user_request' or action not in {'plan', 'export', 'display', 'cancel'} or
            auth.get('action') != action or intent.get('action') != action or intent.get('multiple_plans') or
            auth.get('request_content') != message.get('content') or not quote.strip() or
            quote not in message.get('content', '') or quote != intent.get('evidence_quote')):
            raise ValueError('本次操作缺少有效的本轮用户请求授权')
        if workflow.get('task_id'):
            task = self.store.get('task', workflow['task_id'])
            if (task['revision'] != workflow.get('task_revision') or task['mode'] != 'execute' or
                task['current_message_id'] != message_id or task['status'] in {'cancelled', 'superseded', 'interrupted'}):
                raise ValueError('任务目标已变化或仅允许讨论，不能使用旧任务执行授权')
        for name in PolicyInput.model_fields:
            if parameters and parameters.get(name) and not session.get("policy", {}).get(name):
                raise ValueError("尚未确认工程策略：" + name + "。请在数据页勾选并保存本会话策略；模型不能代替用户确认。")

    def owned_run(self, session_id, run_id):
        run = self.store.get("run", run_id)
        if run["session_id"] != session_id:
            raise ValueError("运行不属于当前会话")
        return run

    async def register(self, spec):
        directory = self.settings.data / "projects" / uid("project")
        directory.mkdir(parents=True)
        source_config = Path(spec.config_path).resolve(strict=True)
        config = read_json(source_config)
        hashes = {}
        for i, layer in enumerate(config.get("vector_layers", [])):
            if layer.get("source_type") == "empty":
                continue
            source = Path(layer["source"])
            if not source.is_absolute():
                source = source_config.parent / source
            source = source.resolve(strict=True)
            dest = directory / "sources" / str(i)
            dest.mkdir(parents=True)
            files = [source.with_suffix(ext) for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix")] if source.suffix.lower() == ".shp" else [source]
            for file in files:
                if file.is_file():
                    target = dest / file.name
                    shutil.copy2(file, target)
                    hashes[str(target.relative_to(directory))] = hashlib.sha256(target.read_bytes()).hexdigest()
            layer["source"] = str(dest / source.name)
        config["rasters"] = []
        config["output"].update(quality_report=str(directory / "quality.json"), geopackage=str(directory / "base.gpkg"), add_to_project=False, capture_screenshot=False)
        config["task2_topology"]["output_json"] = str(directory / "topology.json")
        config_path = directory / "config.json"
        write_json(config_path, config)
        hashes["config.json"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
        recognition = None
        if spec.recognition_path:
            recognition = str(directory / "recognition.gpkg")
            shutil.copy2(Path(spec.recognition_path).resolve(strict=True), recognition)
            hashes["recognition.gpkg"] = hashlib.sha256(Path(recognition).read_bytes()).hexdigest()
        code, output = await self.process([str(ROOT / "scripts/qgis_worker.py"), "inspect", str(config_path)], directory / "inspect.log")
        summary = parse_result(output, "DEMO_RESULT=")
        if code or summary.get("status") != "success":
            raise ValueError("；".join(summary.get("errors", ["数据登记失败"])))
        project = {"id": directory.name, "name": spec.name, "config_path": str(config_path), "recognition_path": recognition,
                   "summary": summary, "hashes": hashes, "created": time.time()}
        write_json(directory / "snapshot.json", project)
        return self.store.put("project", project)

    def session(self, project_id=None):
        if project_id:
            self.store.get("project", project_id)
        return self.store.put("session", {"id": uid("session"), "project_id": project_id, "title": "新对话", "created": time.time(), "updated": time.time()})

    def bind_project(self, session_id, project_id):
        session = self.store.get("session", session_id)
        self.store.get("project", project_id)
        if session.get("project_id") and session["project_id"] != project_id:
            raise ValueError("该会话已绑定其他数据，请新建会话")
        session["project_id"] = project_id
        return self.store.put("session", session)

    def inspect(self, session_id):
        session = self.store.get("session", session_id)
        if not session.get("project_id"):
            return {"project_id": None, "error": "尚未登记项目数据，可先对话；执行规划前请在数据页登记输入。"}
        project = self.store.get("project", session["project_id"])
        return {"project_id": project["id"], "name": project["name"], **project["summary"]}

    def resolve(self, session_id, request):
        session = self.store.get("session", session_id)
        if not session.get("project_id"):
            raise ValueError("尚未登记项目数据，请先在数据页登记输入")
        values = Parameters().model_dump()
        scope = request.scope or "full"
        if request.base_run_id:
            base = self.owned_run(session_id, request.base_run_id)
            if base.get("kind") != "planning":
                raise ValueError("只能继承规划任务")
            values = copy.deepcopy(base["parameters"])
            scope = request.scope or base["scope"]
        values.update(session.get('policy', {}))
        values.update(request.parameter_overrides)
        parameters = Parameters.model_validate(values).model_dump()
        project = self.store.get("project", session["project_id"])
        return project, scope, parameters

    def verify_snapshot(self, project):
        directory = Path(project["config_path"]).parent
        for relative, expected in project["hashes"].items():
            file = directory / relative
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                raise ValueError("输入快照已改变，请重新登记数据：" + relative)

    def validate(self, session_id, request):
        from .knowledge import FULL_NRO_COUNT, capabilities
        project, scope, p = self.resolve(session_id, request)
        summary = project["summary"]
        errors = []
        if not summary["imb_count"]:
            errors.append("缺少 IMB 需求点")
        if p["spatial_weight"] == p["pipe_weight"] == 0:
            errors.append("空间权重和管线权重不能同时为零")
        if scope == "full":
            if summary["nro_count"] != FULL_NRO_COUNT:
                errors.append(f"完整规划需要且只能有一个 NRO，当前 {summary['nro_count']} 个")
            if not (p["use_formal_infrastructure"] and summary["infrastructure_count"] or p["use_recognition_roads"] and project["recognition_path"]):
                errors.append("需选择有实际数据的正式基础设施或识别道路作为路由来源")
            if not p["split_oversized_imbs"]:
                errors.append("当前算法的完整规划必须显式同意超过48端口的IMB按服务单元拆分（split_oversized_imbs=true）；即使当前未发现超限楼栋也要求确认此策略，实际仅拆分超限楼栋。仅分区不要求此确认。")
        if p["use_recognition_roads"] and not project["recognition_path"]:
            errors.append("未登记识别结果 GPKG")
        return {"valid": not errors, "errors": errors, "scope": scope, "parameters": p,
                "check_level": "input_preflight", "feasibility_checked": False, "capabilities": capabilities(),
                "warnings": ["当前算法的最终导出要求恰好两个 PM 分区；实际限制以执行报告为准。"]}

    def submit(self, session_id, request, *, agent_message_id=None):
        if agent_message_id:
            _, _, parameters = self.resolve(session_id, request)
            self.authorize_agent(session_id, agent_message_id, parameters)
        existing = self.store.dedup(session_id, "run:" + request.request_id)
        if existing:
            return self.owned_run(session_id, existing)
        checked = self.validate(session_id, request)
        if not checked["valid"]:
            raise ValueError("；".join(checked["errors"]))
        with self.store.lock:
            # Recheck within the same lock used by automatic repair creation.
            existing = self.store.dedup(session_id, "run:" + request.request_id)
            if existing:
                return self.owned_run(session_id, existing)
            project, scope, p = self.resolve(session_id, request)
            if agent_message_id:
                self.authorize_agent(session_id, agent_message_id, p)
            session = self.store.get("session", session_id)
            if not agent_message_id:
                # A manual run is itself the explicit policy selection.
                self.set_policy(session_id, PolicyInput(**{k: p[k] for k in PolicyInput.model_fields}))
                session = self.store.get("session", session_id)
            message = self.store.get("message", agent_message_id) if agent_message_id else {}
            allowed = bool(message.get("allow_auto_repair")) if agent_message_id else request.allow_auto_repair
            run = {"id": uid("run"), "session_id": session_id, "project_id": project["id"], "kind": "planning",
                   "status": "queued", "scope": scope, "parameters": p, "base_run_id": request.base_run_id, "created": time.time(),
                   "allow_auto_repair": allowed, "repair_count": 0,
                   "authorization": {"source": "message" if agent_message_id else "manual", "message_id": agent_message_id,
                       "policy_version": session.get("policy_version", 0), "project_id": project["id"], "input_hashes": project.get("hashes", {})}}
            run["repair_root_id"] = run["id"]
            session["latest_planning_id"] = run["id"]
            objects = [('session', session)]
            if agent_message_id:
                message['submitted_run_id'] = run['id']
                objects.append(('message', message))
            if message.get('workflow_id'):
                workflow = self.store.get('workflow', message['workflow_id'])
                run['workflow_id'] = workflow['id']
                run['task_context'] = copy.deepcopy(workflow.get('task_context'))
                workflow.update(status='submitted', run_id=run['id'], detail='规划已提交，等待后台计算。', options=[])
                objects.append(('workflow', workflow))
            digest = hashlib.sha256(json.dumps({'scope': scope, 'parameters': p, 'base': request.base_run_id}, sort_keys=True).encode()).hexdigest()
            run, created = self.store.commit_action(session_id, message.get('workflow_id') or agent_message_id or request.request_id,
                'planning', digest, run, objects, 'run:' + request.request_id)
        if created:
            if run.get('workflow_id'):
                workflow = self.store.get('workflow', run['workflow_id'])
                self.coordinator.worker('algorithm', run, workflow)
                self.coordinator.sync_workflow(workflow)
            self.spawn(self.execute(run["id"]))
        return run

    def repair_authorized(self, root):
        session = self.store.get("session", root["session_id"])
        auth = root.get("authorization", {})
        if root.get('workflow_id') and session.get('latest_workflow_id') != root['workflow_id']:
            raise ValueError('已有后续规划请求，禁止旧任务自动重算')
        if (not root.get("allow_auto_repair") or root.get("workflow_cancelled") or
            session.get("latest_planning_id") != root["id"] or
            session.get("policy_version", 0) != auth.get("policy_version") or
            session.get("project_id") != auth.get("project_id")):
            raise ValueError("自动重算未授权、已取消、策略改变或已被后续规划取代")
        if auth.get("source") == "message":
            self.authorize_agent(root["session_id"], auth["message_id"], root["parameters"])
            if not self.store.get("message", auth["message_id"]).get("allow_auto_repair"):
                raise ValueError("原消息的自动重算授权已撤销")
        project = self.store.get("project", root["project_id"])
        if not auth.get("input_hashes") or auth["input_hashes"] != project.get("hashes"):
            raise ValueError("输入快照身份不匹配")
        self.verify_snapshot(project)

    def submit_repair(self, run_id, review_id, patch):
        from .review import LIMITS, Evidence, ReviewResult
        with self.store.lock:
            root = self.store.get("run", run_id)
            if root.get("repair_parent_id") or root.get("repair_count", 0) or root.get("scope") != "full":
                raise ValueError("一次自动重算额度已使用或任务不适用")
            self.repair_authorized(root)
            review = self.store.get("review", review_id)
            if review["run_id"] != run_id or not review.get("automatic") or review["status"] != "completed":
                raise ValueError("审查不能授权此运行的修复")
            evidence = Evidence(self, root)
            evidence.verify()
            if evidence.digest != review.get("evidence", {}).get("digest"):
                raise ValueError("审查证据版本不匹配")
            accepted_patch = evidence.repair_patch(ReviewResult.model_validate(review["result"]))
            if patch != accepted_patch:
                raise ValueError("修复补丁与已验证审查不一致")
            if not patch:
                raise ValueError("空参数补丁")
            for name, value in patch.items():
                old = root["parameters"].get(name)
                valid = (name == "ga_refine" and old is False and value is True) or (
                    name in LIMITS and type(value) is int and old < value <= min(2 * old, LIMITS[name]))
                if not valid:
                    raise ValueError("自动重算参数越界：" + name)
            request = RunInput(request_id="repair:" + run_id, base_run_id=run_id, parameter_overrides=patch)
            checked = self.validate(root["session_id"], request)
            if not checked["valid"]:
                raise ValueError("修复参数未通过输入校验")
            child = {"id": uid("run"), "session_id": root["session_id"], "project_id": root["project_id"],
                "kind": "planning", "status": "queued", "scope": "full", "parameters": checked["parameters"],
                "base_run_id": run_id, "repair_parent_id": run_id, "repair_root_id": run_id,
                "repair_review_id": review_id, "repair_parameter_patch": patch,
                "repair_count": 1, "allow_auto_repair": False, "authorization": root["authorization"], "created": time.time()}
            root.update(repair_count=1, repair_child_id=child["id"])
            repair_objects = [("run", root), ("run", child)]
            if root.get('workflow_id'):
                child['workflow_id'] = root['workflow_id']
                child['task_context'] = copy.deepcopy(root.get('task_context'))
                workflow = self.store.get('workflow', root['workflow_id'])
                workflow.update(active_run_id=child['id'], repair_run_id=child['id'], status='running')
                repair_objects.append(('workflow', workflow))
            self.store.publish_objects(repair_objects, root["session_id"], "run", run_id=child["id"], status="queued")
            if root.get('workflow_id'):
                self.store.event(root['session_id'], 'workflow', workflow=workflow)
                self.coordinator.worker('algorithm', child, workflow)
                self.coordinator.sync_workflow(workflow)
        self.spawn(self.execute(child["id"]))
        return child

    def review_finished(self, review):
        if self.on_review and not self.closing:
            self.on_review(review)

    async def kill(self, proc):
        if proc.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                                                          creationflags=subprocess.CREATE_NO_WINDOW)
            await killer.wait()
        else:
            # The worker leads its own session, so this also stops the algorithm
            # subprocesses it started, like taskkill /T on Windows.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()

    async def process(self, args, log, run_id=None):
        if run_id and self.store.get("run", run_id)["status"] == "cancelled":
            return -1, ""
        # First use probes the interpreter; keep that off the event loop.
        runtime = await asyncio.to_thread(self.settings.runtime)
        creation = asyncio.create_task(asyncio.create_subprocess_exec(str(runtime.python), *args,
            env=self.settings.worker_env(), cwd=str(VENDOR), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=4 * 1024 * 1024,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True})))
        try:
            proc = await asyncio.shield(creation)
        except asyncio.CancelledError:
            proc = await creation
            await self.kill(proc)
            raise
        if run_id:
            self.processes[run_id] = proc
        chunks = []
        current_stage = None
        stage_started = time.monotonic()
        async def consume():
            nonlocal current_stage, stage_started
            with Path(log).open("w", encoding="utf-8") as handle:
                async for raw in proc.stdout:
                    text = raw.decode("utf-8", errors="replace")
                    handle.write(text)
                    handle.flush()
                    chunks.append(text)
                    if run_id and text.startswith("[bridge]"):
                        run = self.store.get("run", run_id)
                        step = algorithm_step(text)
                        if step and step != current_stage:
                            now = time.monotonic()
                            if current_stage:
                                self.store.event(run["session_id"], "algorithm", run_id=run_id, **current_stage,
                                    status="finished", elapsed_seconds=round(now - stage_started, 1))
                            current_stage, stage_started = step, now
                            run["current_algorithm"] = step
                            self.store.put("run", run)
                            self.store.event(run["session_id"], "algorithm", run_id=run_id, **step, status="running")
                        self.store.event(run["session_id"], "progress", run_id=run_id,
                            text=step["label"] if step else "算法流程衔接中")
            code = await proc.wait()
            if run_id and current_stage:
                run = self.store.get("run", run_id)
                self.store.event(run["session_id"], "algorithm", run_id=run_id, **current_stage,
                    status="finished" if code == 0 else "failed", elapsed_seconds=round(time.monotonic() - stage_started, 1))
            return code
        try:
            if run_id and self.store.get("run", run_id)["status"] == "cancelled":
                await self.kill(proc)
            code = await asyncio.wait_for(consume(), self.settings.timeout)
            return code, "".join(chunks)
        finally:
            await self.kill(proc)
            if run_id:
                self.processes.pop(run_id, None)

    async def execute(self, run_id):
        async with self.queue:
            run = self.store.get("run", run_id)
            if run["status"] != "queued":
                return
            if run.get('workflow_id') and not run.get('repair_parent_id'):
                session = self.store.get('session', run['session_id'])
                workflow = self.store.get('workflow', run['workflow_id'])
                if workflow['status'] in {'cancelled', 'interrupted'} or session.get('policy_version', 0) != run['authorization']['policy_version']:
                    run.update(status='cancelled', error='工作流已取消或策略版本已变化')
                    self.store.put('run', run)
                    self.coordinator.worker('algorithm' if run['kind'] == 'planning' else 'exporter', run, workflow)
                    if self.workflows:
                        self.workflows.publish(workflow, status='cancelled', detail=run['error'], options=[])
                    return
            if run.get("repair_parent_id"):
                try:
                    self.repair_authorized(self.store.get("run", run["repair_parent_id"]))
                except ValueError as exc:
                    run.update(status="cancelled", error=str(exc))
                    self.store.publish_objects([("run", run)], run["session_id"], "run", run_id=run_id, status="cancelled")
                    if self.reviewer:
                        self.reviewer.enqueue(run["session_id"], run_id, "automatic:" + run_id, automatic=True)
                    return
            directory = self.settings.data / "runs" / run_id
            directory.mkdir(parents=True)
            run.update(status="running", directory=str(directory), started=time.time())
            logger.info("run %s started (%s, scope=%s)", run_id, run["kind"], run.get("scope"))
            self.store.put("run", run)
            self.store.event(run["session_id"], "run", run_id=run_id, status="running")
            if self.workflows:
                self.workflows.run_changed(run)
            try:
                if run["kind"] == "export":
                    source = self.owned_run(run["session_id"], run["source_run_id"])
                    args = [str(VENDOR / "delivery_exporter.py"), "--run-dir", source["result"]["output_dir"], "--output-root", str(directory / "delivery")]
                    prefix = "FTTH_EXPORT_RESULT="
                else:
                    runtime = await asyncio.to_thread(self.settings.runtime)
                    missing = sorted(set(runtime.missing) - {"qgis", "osgeo"})
                    if run["scope"] == "full" and missing:
                        raise ValueError("QGIS Python 缺少完整规划所需模块 " + "、".join(missing) +
                            "；运行 `ftth doctor --fix` 安装到应用目录后重试")
                    project = self.store.get("project", run["project_id"])
                    self.verify_snapshot(project)
                    write_json(directory / "input_snapshot.json", project)
                    shutil.copy2(VENDOR / "algorithm_integrity_manifest.json", directory / "algorithm_integrity_manifest.json")
                    write_json(directory / "request.json", run)
                    args = [str(VENDOR / "task_bridge.py"), "--config", project["config_path"], "--output-root", str(directory / "results")]
                    if run["scope"] == "full":
                        args += ["--run-task3", "--run-tasks4-to6"]
                    for key, value in run["parameters"].items():
                        if isinstance(value, bool):
                            if key == "ga_refine":
                                if not value:
                                    args.append("--no-ga-refine")
                            elif value:
                                args.append("--" + key.replace("_", "-"))
                        else:
                            args += ["--" + key.replace("_", "-"), str(value)]
                    if project["recognition_path"]:
                        args += ["--recognition-gpkg", project["recognition_path"]]
                    prefix = "FTTH_BRIDGE_RESULT="
                code, output = await self.process(args, directory / "run.log", run_id)
                result = parse_result(output, prefix)
                current = self.store.get("run", run_id)
                run["current_algorithm"] = current.get("current_algorithm")
                run.update(result=result, exit_code=code, status="succeeded" if code == 0 and result.get("status") == "success" else "failed")
                if current["status"] == "cancelled":
                    run["status"] = "cancelled"
                run["metrics"] = self.metrics(result)
            except asyncio.CancelledError:
                run.update(status="interrupted", error="服务停止，任务中断")
                raise
            except TimeoutError:
                run.update(status="failed", error=f"算法运行超过 {self.settings.timeout} 秒，已终止（配置项 run.timeout / FTTH_RUN_TIMEOUT）")
            except Exception as exc:
                logger.warning("run %s failed before completion", run_id, exc_info=True)
                run.update(status="failed", error=str(exc) or type(exc).__name__)
            finally:
                if self.store.get("run", run_id)["status"] == "cancelled":
                    run["status"] = "cancelled"
                run["finished"] = time.time()
                # Cancellation may have updated workflow authority while awaiting the worker.
                saved = self.store.get("run", run_id)
                if saved.get("workflow_cancelled"):
                    run["workflow_cancelled"] = True
                self.store.put("run", run)
                write_json(directory / "run.json", run)
                logger.info("run %s %s (exit=%s, %.1fs)", run_id, run["status"], run.get("exit_code"), run["finished"] - run["started"])
                self.store.event(run["session_id"], "run", run_id=run_id, status=run["status"], elapsed_seconds=round(run["finished"] - run["started"], 1))
            if self.reviewer and run["kind"] == "planning":
                self.reviewer.enqueue(run["session_id"], run_id, "automatic:" + run_id, automatic=True)
            if self.on_complete:
                if run["kind"] == "planning" and run["status"] == "succeeded":
                    self.show(run["session_id"], run["id"])
                self.on_complete(run)

    def metrics(self, result):
        metrics = {}
        directory = result.get("output_dir")
        if directory:
            for path, label, key in [("task6/layered_plan.json", "duct_route_count", "duct_routes"), ("task5/fiber_link_plan.json", "fiber_link_count", "links")]:
                file = Path(directory) / path
                if file.exists():
                    value = read_json(file).get(key)
                    if isinstance(value, list):
                        metrics[label] = len(value)
            counts = ((result.get("downstream") or {}).get("final_layers") or {}).get("counts", {})
            for name, value in counts.items():
                if isinstance(value, int):
                    metrics["layer_count_" + name] = value
        return metrics

    async def cancel(self, session_id, run_id):
        run = self.owned_run(session_id, run_id)
        root_id = run.get("repair_root_id", run_id)
        with self.store.lock:
            root = self.owned_run(session_id, root_id)
            root["workflow_cancelled"] = True
            if root.get('workflow_id') and self.workflows:
                workflow = self.store.get('workflow', root['workflow_id'])
                self.workflows.publish(workflow, status='cancelled', detail='工作流已取消', options=[])
            self.store.put("run", root)
            targets = [r for r in self.store.find("run", session_id) if r["id"] == root_id or r.get("repair_root_id") == root_id]
            for target in targets:
                target["workflow_cancelled"] = True
                if target["status"] in ("queued", "running"):
                    target["status"] = "cancelled"
                self.store.publish_objects([("run", target)], session_id, "run", run_id=target["id"], status=target["status"])
                if target.get('workflow_id'):
                    workflow = self.store.get('workflow', target['workflow_id'])
                    self.coordinator.worker('algorithm' if target['kind'] == 'planning' else 'exporter', target, workflow)
        for target in targets:
            if target["id"] in self.processes:
                await self.kill(self.processes[target["id"]])
            if self.reviewer:
                if target.get("kind") == "planning":
                    self.reviewer.cancel(session_id, target["id"])
        return self.owned_run(session_id, run_id)

    def compare(self, session_id, a, b):
        left, right = self.owned_run(session_id, a), self.owned_run(session_id, b)
        if left["status"] != "succeeded" or right["status"] != "succeeded" or left["kind"] != "planning" or right["kind"] != "planning" or left.get("scope") != right.get("scope"):
            raise ValueError("仅可比较成功且范围相同的规划批次")
        left["metrics"] = self.metrics(left.get("result", {})) or left.get("metrics", {})
        right["metrics"] = self.metrics(right.get("result", {})) or right.get("metrics", {})
        return {"left": a, "right": b, "parameter_changes": {k: [v, right["parameters"].get(k)] for k, v in left["parameters"].items() if v != right["parameters"].get(k)},
                "metrics": {k: {"left": left["metrics"].get(k), "right": right["metrics"].get(k), "delta": right["metrics"][k] - left["metrics"][k]} for k in left.get("metrics", {}) if k in right.get("metrics", {})},
                "note": "未返回的指标不可比较；数量差异不代表方案质量高低。"}

    def show(self, session_id, run_id, visible_layers=None, zoom=True):
        run = self.owned_run(session_id, run_id)
        if run["status"] != "succeeded" or run["kind"] != "planning":
            raise ValueError("只能显示成功规划的成果")
        allowed = {"SITE", "BOITE", "PTECH", "IMB", "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO"}
        if set(visible_layers or []) - allowed:
            raise ValueError("未知图层类别")
        result = run["result"]
        display_result = {k: result[k] for k in ("geopackage", "candidate_layers") if k in result}
        display_result["downstream"] = {"final_layers": (result.get("downstream") or {}).get("final_layers", {})}
        command = {"id": uid("display"), "session_id": session_id, "status": "pending", "run_id": run_id,
                   "result": display_result, "visible_layers": visible_layers or [], "zoom": zoom}
        self.store.put("display", command)
        return {"command_id": command["id"], "status": "pending", "message": "等待 QGIS 加载回执，尚未展示"}

    def ack(self, session_id, command_id, ack):
        command = self.store.get("display", command_id)
        if command["session_id"] != session_id:
            raise ValueError("显示命令不属于此会话")
        if command["status"] != "pending":
            return command
        command.update(status="succeeded" if ack.success else "failed", details=ack.details)
        self.store.put("display", command)
        self.store.event(session_id, "display", command_id=command_id, status=command["status"], details=ack.details)
        return command

    def export(self, session_id, request, *, agent_message_id=None):
        if agent_message_id:
            self.authorize_agent(session_id, agent_message_id, action='export')
        existing = self.store.dedup(session_id, "export:" + request.request_id)
        if existing:
            return self.owned_run(session_id, existing)
        source = self.owned_run(session_id, request.run_id)
        if source["status"] != "succeeded" or source.get("scope") != "full" or source["kind"] != "planning":
            raise ValueError("仅成功的完整规划可导出完整交付包")
        run = {"id": uid("export"), "session_id": session_id, "project_id": source["project_id"], "kind": "export", "source_run_id": source["id"], "status": "queued", "created": time.time()}
        message = self.store.get('message', agent_message_id) if agent_message_id else {}
        objects = []
        if message.get('workflow_id'):
            w = self.store.get('workflow', message['workflow_id'])
            run['workflow_id'] = w['id']
            run['task_context'] = copy.deepcopy(w.get('task_context'))
            run['authorization'] = {'policy_version': self.store.get('session', session_id).get('policy_version', 0)}
            w.update(status='submitted', run_id=run['id'], detail='导出任务已提交', options=[])
            objects.append(('workflow', w))
        run, created = self.store.commit_action(session_id, message.get('workflow_id') or agent_message_id or request.request_id,
            'export', request.run_id, run, objects, 'export:' + request.request_id)
        if created:
            if run.get('workflow_id'):
                w = self.store.get('workflow', run['workflow_id'])
                self.coordinator.worker('exporter', run, w)
                self.coordinator.sync_workflow(w)
            self.spawn(self.execute(run["id"]))
        return run
