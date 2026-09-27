from contextlib import asynccontextmanager
import hmac
import time
import json
import logging

from fastapi import FastAPI, Depends, Header, HTTPException
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from . import __version__
from .agent import AgentController
from .models import QgisStateInput, DataDraftInput, AckInput, DisplayInput, ExportInput, MessageInput, ProjectInput, RunInput, SessionInput, PolicyInput, ReviewInput, WorkflowChoice
from .review import ReviewController
from .service import Service, run_summary
from .settings import Settings
from .store import Store
from .titles import fallback_title

logger = logging.getLogger("ftth.app")


def create_app(settings=None, model=None, review_model=None, on_shutdown_request=None):
    settings = settings or Settings()
    store = Store(settings.data / "app.sqlite")

    @asynccontextmanager
    async def lifespan(app):
        service = None
        try:
            service = Service(settings, store)
            logger.info("FTTH backend %s ready: data=%s model=%s", __version__, settings.data,
                        settings.model if settings.model_ready else "not configured")
            async with AsyncSqliteSaver.from_conn_string(str(settings.data / "checkpoints.sqlite")) as saver:
                agent = AgentController(service, saver, model)
                service.on_complete = agent.completed
                service.reviewer = ReviewController(service, review_model)
                service.on_review = agent.review_completed
                app.state.service, app.state.agent = service, agent
                try:
                    yield
                finally:
                    await service.close()
        finally:
            if service:
                await service.close()
            store.db.close()
            logger.info("FTTH backend stopped")

    async def authenticate(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + settings.token):
            raise HTTPException(401, "需要本地连接令牌")

    app = FastAPI(title="FTTH demo0", lifespan=lifespan, dependencies=[Depends(authenticate)])

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        qgis = settings.runtime_status()
        return {"status": "ok", "version": __version__, "execution_authority": "user_request", "context_protocol": "task-v1", "knowledge_protocol": "source-v1", "model_configured": settings.model_ready, "model": settings.model,
                "model_api": settings.model_api, "provider": settings.provider,
                "qgis_python_exists": qgis["available"], "qgis": qgis}

    @app.post("/shutdown", status_code=202)
    async def shutdown():
        if on_shutdown_request is None:
            raise HTTPException(409, "此后端不是由 `ftth serve` 启动，请在其终端按 Ctrl+C 停止")
        logger.info("shutdown requested over HTTP")
        on_shutdown_request()
        return {"status": "stopping"}

    @app.post("/runtime/refresh")
    async def refresh_runtime():
        # Re-detect QGIS after installing it, without restarting the backend.
        try:
            await run_in_threadpool(settings.runtime, True)
        except ValueError:
            pass
        return settings.runtime_status()

    @app.post("/projects")
    async def project(spec: ProjectInput):
        return await app.state.service.register(spec)

    @app.post("/sessions")
    def session(spec: SessionInput):
        return app.state.service.session(spec.project_id)

    @app.get("/sessions")
    def sessions():
        items = store.all("session")
        for item in items:
            if "title" not in item:
                first = next((m for m in store.find("message", item["id"]) if not m.get("notification")), None)
                item["title"] = fallback_title(first["content"]) if first else "新对话"
        return sorted(items, key=lambda s: s.get("updated", s.get("created", 0)), reverse=True)

    @app.post("/sessions/{sid}/project")
    def bind_project(sid: str, spec: SessionInput):
        if not spec.project_id:
            raise ValueError("请选择项目数据")
        return app.state.service.bind_project(sid, spec.project_id)

    @app.post("/sessions/{sid}/qgis-state")
    def qgis_state(sid: str, spec: QgisStateInput):
        app.state.service.owned_run(sid, spec.run_id)
        with store.lock:
            session = store.get("session", sid)
            session["qgis_state"] = {**spec.model_dump(), "received": time.time()}
            session.setdefault("qgis_states", {})[spec.run_id] = session["qgis_state"]
            store.put("session", session)
        return {"saved": True}

    @app.get("/sessions/{sid}/data")
    def data(sid: str):
        return app.state.service.data_draft(sid)

    @app.post("/sessions/{sid}/data")
    def save_data(sid: str, spec: DataDraftInput):
        from .data_drafts import DraftConflict
        try:
            return app.state.service.save_data_draft(sid, spec)
        except DraftConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/sessions/{sid}/checks/{cid}")
    def check_report(sid: str, cid: str):
        return app.state.service.owned_check(sid, cid)

    @app.get("/sessions/{sid}/history")
    def history(sid: str):
        with store.lock:
            session = store.get("session", sid)
            messages = [{k: m[k] for k in ("id", "content", "response", "partial_response", "stream_id", "status", "notification", "elapsed_seconds", "workflow_id", "action_status", "run_id", "review_id", "created") if k in m}
                for m in sorted(store.find("message", sid), key=lambda m: m.get("created", 0))]
            for message in messages:
                if message.get("notification"):
                    message.pop("content", None)
            cursor = store.db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE session=?", (sid,)).fetchone()[0]
            # Stream snapshots are superseded by message objects; skip them in SQL.
            activity = [dict(id=row[0], **json.loads(row[1])) for row in store.db.execute(
                "SELECT id,data FROM events WHERE session=? AND id<=? AND json_extract(data, '$.type')!='assistant_stream' ORDER BY id",
                (sid, cursor))]
            return {"session": session, "messages": messages, "cursor": cursor,
                "active_task": app.state.service.coordinator.context({'session_id': sid, 'project_id': session.get('project_id')}),
                "tasks": store.find('task', sid),
                "workers": store.find('worker', sid),
                "timeline": [e for e in activity if e['type'] != 'progress'],
                "workflows": store.find('workflow', sid),
                "activities": [e for e in activity if e["type"] in ("activity", "progress", "run", "algorithm", "review")]}

    @app.get('/sessions/{sid}/workflows/{wid}')
    def workflow(sid: str, wid: str):
        return app.state.agent.workflows.owned(sid, wid)

    @app.post('/sessions/{sid}/workflows/{wid}/resolve')
    async def resolve_workflow(sid: str, wid: str, choice: WorkflowChoice):
        return app.state.agent.workflows.resolve(sid, wid, choice)

    @app.post('/sessions/{sid}/workflows/{wid}/cancel')
    async def cancel_workflow(sid: str, wid: str):
        return await app.state.agent.workflows.cancel(sid, wid)

    @app.post("/sessions/{sid}/policy")
    def policy(sid: str, spec: PolicyInput):
        return app.state.service.set_policy(sid, spec)

    @app.post("/sessions/{sid}/messages", status_code=202)
    async def message(sid: str, spec: MessageInput):
        return app.state.agent.enqueue(sid, spec)

    @app.get("/sessions/{sid}/events")
    def events(sid: str, after: int = 0):
        store.get("session", sid)
        return store.events(sid, after)

    @app.get("/sessions/{sid}/project")
    def inspect(sid: str):
        return app.state.service.inspect(sid)

    @app.post("/sessions/{sid}/validate")
    def validate(sid: str, spec: RunInput):
        return app.state.service.validate(sid, spec)

    @app.post("/sessions/{sid}/runs", status_code=202)
    async def run(sid: str, spec: RunInput):
        return app.state.service.submit(sid, spec)

    @app.get("/sessions/{sid}/runs/{rid}/reviews")
    def reviews(sid: str, rid: str):
        return app.state.service.reviewer.list(sid, rid)

    @app.post("/sessions/{sid}/runs/{rid}/reviews", status_code=202)
    async def review(sid: str, rid: str, spec: ReviewInput):
        return app.state.service.reviewer.enqueue(sid, rid, "manual:" + spec.request_id)

    @app.get("/sessions/{sid}/runs")
    def runs(sid: str):
        store.get("session", sid)
        return [run_summary(r) for r in store.find("run", sid)]

    @app.get("/sessions/{sid}/runs/{rid}")
    def get_run(sid: str, rid: str):
        return app.state.service.owned_run(sid, rid)

    @app.post("/sessions/{sid}/runs/{rid}/cancel")
    async def cancel(sid: str, rid: str):
        return await app.state.service.cancel(sid, rid)

    @app.get("/sessions/{sid}/compare")
    def compare(sid: str, left: str, right: str):
        return app.state.service.compare(sid, left, right)

    @app.post("/sessions/{sid}/exports", status_code=202)
    async def export(sid: str, spec: ExportInput):
        return app.state.service.export(sid, spec)

    @app.post("/sessions/{sid}/display", status_code=202)
    def display(sid: str, spec: DisplayInput):
        return app.state.service.show(sid, spec.run_id, spec.visible_layers, spec.zoom)

    @app.get("/sessions/{sid}/display")
    def pending(sid: str):
        store.get("session", sid)
        return store.find("display", sid, status="pending")

    @app.post("/sessions/{sid}/display/{cid}/ack")
    def ack(sid: str, cid: str, spec: AckInput):
        return app.state.service.ack(sid, cid, spec)

    return app
