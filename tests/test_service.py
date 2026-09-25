import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.models import RunInput, Parameters, AckInput
from backend.service import Service
from backend.service import run_summary
from backend.settings import Settings
from backend.store import Store


def setup_service(tmp_path):
    settings = Settings(tmp_path)
    store = Store(tmp_path / "test.sqlite")
    service = Service(settings, store)
    store.put("project", {"id": "p", "name": "test", "recognition_path": None,
        "summary": {"imb_count": 3, "nro_count": 1, "oversized_imbs": [], "infrastructure_count": 3}})
    a, b = service.session("p")["id"], service.session("p")["id"]
    return service, a, b


def test_strict_parameters():
    for change in ({"zpm_max_cap": 1.5}, {"zpm_max_cap": True}, {"zpm_max_cap": "10"}, {"pipe_weight": float("nan")}, {"anything": 2}):
        with pytest.raises(ValueError):
            Parameters.model_validate(change)


def test_export_summary_keeps_delivery_evidence(tmp_path):
    root = tmp_path / "delivery"
    result = {"output_dir": str(root), "files": [{"path": str(root / "map.pdf"), "size_bytes": 123, "sha256": "hash"}],
        "file_count": 1, "validation": {"counts": {"IMB": 51}}, "task6_route_filter": {"raw_count": 64}}
    summary = run_summary({"kind": "export", "result": result})["result"]
    assert summary["files"] == [{"path": "map.pdf", "size_bytes": 123}]
    assert summary["validation"]["counts"]["IMB"] == 51
    assert summary["task6_route_filter"]["raw_count"] == 64


def test_full_requires_explicit_split_policy_even_without_oversized_imbs(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    spec = RunInput(request_id="no-policy", scope="full",
        parameter_overrides={"use_formal_infrastructure": True})
    checked = service.validate(sid, spec)
    assert not checked["valid"]
    assert any("split_oversized_imbs=true" in e for e in checked["errors"])
    with pytest.raises(ValueError, match="split_oversized_imbs=true"):
        service.submit(sid, spec)
    assert service.store.all("run") == []
    spec.parameter_overrides["split_oversized_imbs"] = True
    assert service.validate(sid, spec)["valid"]
    assert service.validate(sid, RunInput(request_id="partition", scope="partition"))["valid"]


def test_validation_and_ownership(tmp_path):
    service, a, b = setup_service(tmp_path)
    assert service.validate(a, RunInput(request_id="x", scope="partition"))["valid"]
    assert not service.validate(a, RunInput(request_id="x"))["valid"]
    project = service.store.get("project", "p")
    project["summary"]["nro_count"] = 0
    service.store.put("project", project)
    errors = service.validate(a, RunInput(request_id="x", parameter_overrides={"use_formal_infrastructure": True}))["errors"]
    assert any("NRO" in e for e in errors)
    service.store.put("run", {"id": "r", "session_id": a})
    with pytest.raises(ValueError, match="不属于"):
        service.owned_run(b, "r")


def test_duplicate_and_inheritance(tmp_path):
    async def scenario():
        service, a, b = setup_service(tmp_path)
        # Keep jobs queued so assertions do not depend on QGIS or external data.
        service.spawn = lambda coro: coro.close()
        spec = RunInput(request_id="same", scope="partition", parameter_overrides={"zpm_max_cap": 300})
        first = service.submit(a, spec)
        assert service.submit(a, spec)["id"] == first["id"]
        second = service.submit(a, RunInput(request_id="next", base_run_id=first["id"], parameter_overrides={"nro_max_cap": 4096}))
        assert second["scope"] == "partition"
        assert second["parameters"]["zpm_max_cap"] == 300
        assert second["parameters"]["nro_max_cap"] == 4096
        assert len(service.store.all("run")) == 2
        cancelled = await service.cancel(a, second["id"])
        assert cancelled["status"] == "cancelled"
        await service.close()
        recovered = Service(service.settings, service.store)
        assert service.store.get("run", first["id"])["status"] == "interrupted"
        await recovered.close()
    asyncio.run(scenario())


def test_display_receipt_and_compare(tmp_path):
    service, a, b = setup_service(tmp_path)
    for id, cap in (("r1", 300), ("r2", 400)):
        service.store.put("run", {"id": id, "session_id": a, "kind": "planning", "scope": "full", "status": "succeeded", "parameters": {"zpm_max_cap": cap}, "metrics": {}, "result": {}})
    result = service.show(a, "r1")
    assert result["status"] == "pending"
    with pytest.raises(ValueError):
        service.ack(b, result["command_id"], AckInput(success=True))
    ack = service.ack(a, result["command_id"], AckInput(success=False, details={"error": "QGIS unavailable"}))
    assert ack["status"] == "failed"
    assert service.compare(a, "r1", "r2")["metrics"] == {}
    assert service.compare(a, "r1", "r2")["parameter_changes"] == {"zpm_max_cap": [300, 400]}


def test_http_auth_and_no_model(tmp_path):
    settings = Settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 401
        client.headers["Authorization"] = "Bearer " + settings.token
        assert client.get("/health").status_code == 200
        service = client.app.state.service
        service.store.put("project", {"id": "p"})
        sid = client.post("/sessions", json={"project_id": "p"}).json()["id"]
        payload = {"request_id": "m", "content": "检查数据"}
        first = client.post(f"/sessions/{sid}/messages", json=payload)
        assert first.status_code == 202
        second = client.post(f"/sessions/{sid}/messages", json=payload)
        assert first.json()["id"] == second.json()["id"]


def test_snapshot_tampering(tmp_path):
    import hashlib
    service, _, _ = setup_service(tmp_path)
    source = tmp_path / "input.csv"
    source.write_text("original")
    project = {"config_path": str(tmp_path / "config.json"), "hashes": {"input.csv": hashlib.sha256(source.read_bytes()).hexdigest()}}
    service.verify_snapshot(project)
    source.write_text("changed")
    with pytest.raises(ValueError, match="快照已改变"):
        service.verify_snapshot(project)


@pytest.mark.skipif(not Path("D:/osgeo/bin/python.exe").exists(), reason="Requires local QGIS runtime")
def test_real_process_cancel_and_timeout(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.store.put("run", {"id": "cancel-test", "session_id": sid, "status": "running"})
        task = asyncio.create_task(service.process(["-c", "import time; time.sleep(60)"], tmp_path / "cancel.log", "cancel-test"))
        for _ in range(100):
            if "cancel-test" in service.processes:
                break
            await asyncio.sleep(0.01)
        proc = service.processes["cancel-test"]
        await service.cancel(sid, "cancel-test")
        await task
        assert proc.returncode is not None
        assert service.store.get("run", "cancel-test")["status"] == "cancelled"
        service.settings.timeout = 0.1
        with pytest.raises(TimeoutError):
            await service.process(["-c", "import time; time.sleep(60)"], tmp_path / "timeout.log")
    asyncio.run(scenario())
