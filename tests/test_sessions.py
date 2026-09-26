from fastapi.testclient import TestClient
from backend.app import create_app
from backend.settings import Settings
from test_agent import FakeToolModel


def test_first_message_history_title_and_late_project_binding(tmp_path):
    settings = Settings(tmp_path)
    with TestClient(create_app(settings, model=FakeToolModel())) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        sid = client.post("/sessions", json={}).json()["id"]
        assert client.get(f"/sessions/{sid}/project").json()["project_id"] is None
        payload = {"request_id": "first", "content": "检查通信规划所需数据"}
        first = client.post(f"/sessions/{sid}/messages", json=payload).json()
        assert client.post(f"/sessions/{sid}/messages", json=payload).json()["id"] == first["id"]
        # TestClient requests share the application's live event loop.
        import time
        for _ in range(1500):  # Early exit; the bound only absorbs slow cold starts.
            history = client.get(f"/sessions/{sid}/history").json()
            if history["messages"] and history["messages"][0]["status"] == "succeeded":
                break
            time.sleep(.01)
        assert len(history["messages"]) == 1
        assert history["messages"][0]["content"] == payload["content"]
        assert history["messages"][0]["response"]
        assert history["messages"][0]["elapsed_seconds"] >= 0
        assert any(e.get('name') == 'inspect_project' for e in history['activities'])
        assert client.get("/sessions").json()[0]["title"] == payload["content"]
        events = client.get(f"/sessions/{sid}/events").json()
        assert len([e for e in events if e["type"] == "user"]) == 1
        assert not any("reasoning_content" in str(e) for e in events)
        assert client.post(f"/sessions/{sid}/validate", json={"request_id":"v"}).status_code == 400
        service = client.app.state.service
        service.store.put("project", {"id":"p"})
        service.store.put("project", {"id":"other"})
        assert client.post(f"/sessions/{sid}/project", json={"project_id":"p"}).status_code == 200
        assert client.post(f"/sessions/{sid}/project", json={"project_id":"other"}).status_code == 400
        other = client.post("/sessions", json={}).json()["id"]
        assert client.get(f"/sessions/{other}/history").json()["messages"] == []


def test_store_find_keeps_creation_order_and_filters_in_sql(tmp_path):
    from backend.store import Store
    store = Store(tmp_path / "find.sqlite")
    store.put("run", {"id": "a", "session_id": "s", "kind": "planning"})
    store.put("run", {"id": "b", "session_id": "other", "kind": "planning"})
    store.put("run", {"id": "c", "session_id": "s", "kind": "export"})
    store.put("run", {"id": "a", "session_id": "s", "kind": "planning", "status": "updated"})
    assert [r["id"] for r in store.find("run", "s")] == ["a", "c"]
    assert store.find("run", "s", kind="planning") == [{"id": "a", "session_id": "s", "kind": "planning", "status": "updated"}]
    assert store.lookup("run", "missing") is None
    import pytest
    with pytest.raises(ValueError):
        store.find("run", "s", **{"status') OR 1=1 --": "x"})


def test_final_reply_prunes_only_its_stream_snapshots(tmp_path):
    from backend.store import Store
    store = Store(tmp_path / "stream.sqlite")
    for mid in ("m1", "m2"):
        store.put("message", {"id": mid, "session_id": "s", "status": "running"})
        store.stream_message(mid, "r", "部分")
        store.stream_message(mid, "r", "部分回复")
    store.publish_message({"id": "m1", "session_id": "s", "status": "succeeded", "response": "部分回复"},
        [("assistant", {"text": "部分回复"})])
    kinds = [(e["type"], e["message_id"]) for e in store.events("s", 0)]
    assert ("assistant_stream", "m1") not in kinds and ("assistant", "m1") in kinds
    assert kinds.count(("assistant_stream", "m2")) == 2
