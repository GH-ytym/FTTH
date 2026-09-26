"""Review regressions updated for 0.4.0; assertions now require corrected behavior.

Run: .venv/Scripts/python.exe -m pytest audit/test_review_repros.py -q
No production database, model endpoint, or engineering calculation is used.
"""
import ast
import asyncio
from pathlib import Path
import sys
import time
import pytest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_service import setup_service, use_host_python
from test_agent import FakeSubmitModel, FakeToolModel
from backend.agent import AgentController
from backend.app import create_app
from backend.service import Service
from backend.settings import Settings
from backend.store import Store
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def test_model_cannot_submit_despite_read_only_user_request(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.spawn = lambda coro: coro.close()
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "memory.sqlite")) as saver:
            controller = AgentController(service, saver, FakeSubmitModel())
            await controller.respond({"id": "audit-message", "session_id": sid,
                "content": "只检查数据，不要运行，不同意拆分楼栋"})
        runs = service.store.all("run")
        assert runs == []
        await service.close()
        service.store.db.close()
    asyncio.run(scenario())


def test_second_service_cannot_interrupt_first_service_records(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    service.store.put("run", {"id": "active", "session_id": sid, "status": "running"})
    second_store = Store(tmp_path / "test.sqlite")
    with pytest.raises(ValueError, match="已有后端"):
        Service(service.settings, second_store)
    assert service.store.get("run", "active")["status"] == "running"
    asyncio.run(service.close())
    second_store.db.close()
    service.store.db.close()


def test_cancel_during_process_creation_stops_child(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        use_host_python(service)
        service.store.put("run", {"id": "race", "session_id": sid, "status": "running"})
        entered, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec
        marker = tmp_path / "child-executed.txt"
        async def gated(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        with patch("asyncio.create_subprocess_exec", gated):
            task = asyncio.create_task(service.process(
                ["-c", "import time; time.sleep(1); from pathlib import Path; Path(" + repr(str(marker)) + ").write_text('executed')"],
                tmp_path / "child.log", "race"))
            await entered.wait()
            assert "race" not in service.processes
            cancelled = await service.cancel(sid, "race")
            assert cancelled["status"] == "cancelled"
            release.set()
            code, _ = await task
        assert code != 0 and not marker.exists()
        assert not service.processes
        await service.close()
        service.store.db.close()
    asyncio.run(scenario())


def test_history_cursor_cannot_skip_unpersisted_assistant_response(tmp_path):
    with TestClient(create_app(Settings(tmp_path), model=FakeToolModel())) as client:
        service = client.app.state.service
        sid = service.session()["id"]
        history = next(r.endpoint for r in client.app.routes if r.path == "/sessions/{sid}/history")
        observed = []
        original = service.store.publish_message
        def interleaved(item, events):
            original(item, events)
            if any(kind == "assistant" for kind, data in events):
                observed.append(history(item["session_id"]))
        service.store.publish_message = interleaved
        client.headers["Authorization"] = "Bearer " + service.settings.token
        client.post(f"/sessions/{sid}/messages", json={"request_id": "audit", "content": "检查数据"})
        for _ in range(1500):  # Early exit; the bound only absorbs slow cold starts.
            saved = service.store.all("message")
            if observed and saved and saved[0]["status"] == "succeeded":
                break
            time.sleep(.01)
        assert observed and "response" in saved[0]
        snap = observed[0]
        assert snap["messages"][0]["response"] == saved[0]["response"]
        assert not any(e["type"] == "assistant" for e in service.store.events(sid, snap["cursor"]))


def test_registration_callback_binds_original_session(tmp_path):
    # Execute the real two Panel methods with minimal widget/network stand-ins.
    # This tests callback state handling, not Qt rendering or real HTTP timing.
    tree = ast.parse((ROOT / "qgis_plugin/ftth_demo0/panel.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Panel")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {"register", "registered_project", "registration_callback"}]
    namespace = {"Path": Path, "BUSINESS_LAYERS": [], "__file__": str(ROOT / "qgis_plugin/ftth_demo0/panel.py"),
        "build_task1_config": lambda *a: None,
        "QgsProject": SimpleNamespace(instance=lambda: SimpleNamespace(title=lambda: "Project A"))}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "panel-review-methods", "exec"), namespace)
    class StandIn:
        register = namespace["register"]
        registered_project = namespace["registered_project"]
        registration_callback = namespace["registration_callback"]
        view_generation = 0
        session = "session-A"
        session_project = None
        sources, sublayers = {}, {}
        recognition = SimpleNamespace(text=lambda: "")
        project_label = SimpleNamespace(setText=lambda _: None)
        status = SimpleNamespace(setText=lambda _: None)
        def workspace(self): return tmp_path
        def route(self, suffix): return f"/sessions/{self.session}/{suffix}"
        def api(self, method, route, payload, callback):
            requests.append((route, payload, callback))
    requests = []
    panel = StandIn()
    panel.register()
    panel.session = "session-B"
    panel.view_generation += 1
    requests[0][2]({"id": "project-A", "name": "Project A"})
    assert requests[1][0] == "/sessions/session-A/project"
    assert requests[1][1] == {"project_id": "project-A"}
