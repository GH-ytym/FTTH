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
        for _ in range(100):
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
