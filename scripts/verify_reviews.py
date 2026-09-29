"""Real QGIS algorithm -> HTTP service -> isolated review, in a fresh data dir.

Default uses a scripted review model. --real-model explicitly opts into the
configured provider; it fails early when credentials/config are unavailable.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from fastapi.testclient import TestClient
from backend import __version__
from backend.app import create_app
from backend.settings import Settings
from backend.review import review_settings
from backend.model_client import build_model
from test_review import ReviewModel
from test_workflow import IntentModel


def main():
    parser = argparse.ArgumentParser()
    for name in ("imb", "site", "ptech", "infrastructure"):
        parser.add_argument("--" + name, type=Path, required=name == "imb")
        parser.add_argument("--" + name + "-layer", default="")
    parser.add_argument("--roads", type=Path)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument('--workflow', action='store_true')
    args = parser.parse_args()
    sources = {name.upper(): {"path": str(getattr(args, name).resolve()),
                             "source_layer": getattr(args, name + "_layer")}
               for name in ("imb", "site", "ptech", "infrastructure") if getattr(args, name)}
    directory = ROOT / "runtime" / f"release-{__version__}" / ("e2e-" + uuid.uuid4().hex[:8])
    settings = Settings(directory)
    configured = review_settings(settings)
    if args.real_model:
        if not configured.model_ready:
            raise SystemExit("当前终端没有审查模型配置，未执行真实模型验收。")
        model = build_model(configured, streaming=False, max_tokens=4096, max_retries=0)
    else:
        model = ReviewModel()
        for key in list(os.environ):
            if key.startswith("FTTH_REVIEW_"):
                os.environ.pop(key)
    intent_settings = copy.copy(settings)
    intent_settings.enable_thinking = False
    main_model = (build_model(intent_settings, streaming=False, max_tokens=2048) if args.real_model else IntentModel()) if args.workflow else None
    # Completion messages do not need a second paid model call.
    settings.model = settings.api_key = settings.base_url = ""
    receipt = {"model_test": "real" if args.real_model else "scripted", "data_directory": str(directory)}
    with TestClient(create_app(settings, model=main_model, review_model=model)) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        project = client.post("/projects", json={"name": "0.5.0 acceptance", "sources": sources, "recognition_path": str(args.roads.resolve()) if args.roads else None})
        assert project.status_code == 200, project.text
        sid = client.post("/sessions", json={"project_id": project.json()["id"]}).json()["id"]
        if args.workflow:
            client.post(f'/sessions/{sid}/policy', json={'use_formal_infrastructure':True,'use_recognition_roads':True,'split_oversized_imbs':True}).raise_for_status()
            if args.real_model:
                cases = [('不要运行，解释现有结果。', {'query','discuss'}), ('如果以后运行完整规划，需要什么数据？', {'query','discuss'}),
                    ('“执行一次完整规划”这句话是什么意思？不要执行。', {'query','discuss'}), ('取消之前的运行。', {'cancel','clarify'})]
                receipt['intent_cases'] = []
                for index, (content, expected) in enumerate(cases):
                    decision = client.portal.call(client.app.state.agent.workflows.interpret,
                        {'id':'case'+str(index),'session_id':sid,'content':content})
                    receipt['intent_cases'].append({'input':content,'action':decision.action})
                    assert decision.action in expected, receipt['intent_cases'][-1]
            service = client.app.state.service
            service.store.put('message', {'id':'old-report','session_id':sid,'notification':True,'status':'succeeded','response':'旧结果已通过，无需运行。'*5000})
            client.post(f'/sessions/{sid}/messages', json={'request_id':'workflow', 'content':'沿用当前已保存的工程策略，执行一次完整规划，完成后独立审查；如果符合受限修复条件，允许自动重算一次。只简短告诉我结果。'}).raise_for_status()
            deadline = time.monotonic()+120
            while time.monotonic()<deadline:
                flows = client.get(f'/sessions/{sid}/history').json().get('workflows', [])
                if flows and flows[-1]['status'] != 'interpreting':
                    workflow = flows[-1]
                    break
                time.sleep(.1)
            else:
                raise TimeoutError('intent')
            assert workflow['status']=='waiting_choice', workflow
            path = f"/sessions/{sid}/workflows/{workflow['id']}/resolve"
            choice = {'request_id':'choose-formal', 'option_id':'formal_only', 'version':workflow['version']}
            response = client.post(path,json=choice)
            response.raise_for_status()
            rid = response.json()['run_id']
            assert client.post(path,json=choice).json()['run_id']==rid
            receipt['workflow_id'] = workflow['id']
        else:
            response = client.post(f"/sessions/{sid}/runs", json={"request_id": "baseline",
                "scope": "full", "parameter_overrides": {"use_formal_infrastructure": True, "split_oversized_imbs": True}})
            assert response.status_code == 202, response.text
            rid = response.json()["id"]
        print("Real QGIS planning started:", rid, flush=True)

        def wait_run(run_id):
            deadline = time.monotonic() + settings.timeout + 30
            while time.monotonic() < deadline:
                run = client.get(f"/sessions/{sid}/runs/{run_id}").json()
                if run["status"] not in {"queued", "running"}:
                    assert run["status"] == "succeeded", run.get("error", run.get("result"))
                    return run
                time.sleep(.5)
            raise TimeoutError("algorithm")

        run = wait_run(rid)
        deadline = time.monotonic() + 200
        while time.monotonic() < deadline:
            reviews = client.get(f"/sessions/{sid}/runs/{rid}/reviews").json()
            if reviews and reviews[-1]["status"] not in {"queued", "running"}:
                review = reviews[-1]
                assert review["status"] == "completed", review
                assert not review["evidence"]["missing"], review["evidence"]
                break
            time.sleep(.25)
        else:
            raise TimeoutError("review")
        if args.workflow:
            if not args.real_model:
                client.app.state.agent.workflows.model = IntentModel(action='export', use_latest=True)
            export = client.post(f'/sessions/{sid}/messages', json={'request_id':'delivery', 'content':'导出上次结果'})
            export.raise_for_status()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                flows = client.get(f'/sessions/{sid}/history').json()['workflows']
                matches = [w for w in flows if w['message_id'] == export.json()['id']]
                if matches and matches[-1]['status'] != 'interpreting':
                    export_flow = matches[-1]
                    assert export_flow['status'] in {'submitted', 'running', 'completed'}, export_flow
                    export_id = export_flow['run_id']
                    receipt['export_workflow_id'] = export_flow['id']
                    break
                time.sleep(.1)
            else:
                raise TimeoutError('export intent')
        else:
            export = client.post(f"/sessions/{sid}/exports", json={"request_id": "delivery", "run_id": rid})
            assert export.status_code == 202, export.text
            export_id = export.json()['id']
        delivery = wait_run(export_id)
        if args.workflow:
            history = client.get(f'/sessions/{sid}/history').json()
            tasks = {t['id']: t for t in history['tasks']}
            workers = history['workers']
            assert len(tasks) == 2, tasks
            for completed in (run, delivery):
                snapshot = completed['task_context']
                assert snapshot['id'] in tasks and snapshot['mode'] == 'execute', snapshot
                assert any(w['task_id'] == snapshot['id'] and w['task_revision'] == snapshot['revision']
                    and w['run_id'] == completed['id'] and w['status'] == 'succeeded' for w in workers), workers
            assert any(w['actor'] == 'reviewer' and w['run_id'] == rid and w['status'] == 'completed' for w in workers), workers
            receipt['task_ids'] = list(tasks)
            receipt['workers'] = workers
        receipt.update(session_id=sid, run_id=rid, planning_status=run["status"], review_id=review["id"],
            review_status=review["status"], verdict=review["result"]["verdict"], evidence=review["evidence"],
            export_status=delivery["status"], export_id=delivery["id"], metrics=run.get("metrics"),
            output_dir=run["result"]["output_dir"])
    (directory / "acceptance.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS:", directory / "acceptance.json", flush=True)


if __name__ == "__main__":
    main()
