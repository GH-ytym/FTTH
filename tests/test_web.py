import asyncio
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.settings import Settings
from backend.store import Store
from qgis_plugin.ftth_demo0.web_contract import route


def test_atomic_message_concurrency_and_conflict(tmp_path):
    store = Store(tmp_path / "atomic.sqlite")
    sid = "session_a"
    session = {"id": sid}

    def create(i):
        return store.commit_message(
            session,
            {"id": f"m{i}", "session_id": sid, "content": "run", "status": "queued"},
            "message:req",
            "digest",
        )

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(create, range(16)))
    assert sum(created for _, created in results) == 1
    assert len(store.all("message")) == 1 and len(store.events(sid, 0)) == 1
    with pytest.raises(ValueError):
        store.commit_message(
            session,
            {"id": "bad", "session_id": sid, "content": "other"},
            "message:req",
            "changed",
        )
    store.db.close()


def test_atomic_message_rollback(tmp_path):
    store = Store(tmp_path / "rollback.sqlite")
    store.db.execute(
        "CREATE TRIGGER stop_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'test'); END;"
    )
    with pytest.raises(Exception):
        store.commit_message({"id": "s"}, {"id": "m", "content": "x"}, "k", "digest")
    assert (
        not store.all("message")
        and not store.all("session")
        and store.dedup("s", "k") is None
    )
    store.db.close()


def test_message_does_not_overwrite_newer_data_draft(tmp_path):
    store = Store(tmp_path / "merge.sqlite")
    store.put("session", {"id": "s", "title": "current", "data_draft": {"revision": 5}})
    store.commit_message(
        {"id": "s", "title": "stale", "data_draft": {"revision": 1}},
        {"id": "m", "content": "hello"},
        "key",
        "digest",
    )
    assert store.get("session", "s")["data_draft"]["revision"] == 5
    assert store.get("session", "s")["title"] == "current"
    store.db.close()


def test_http_request_conflict_and_model_free_check(tmp_path):
    settings = Settings(tmp_path)
    settings.model = settings.api_key = settings.base_url = ""
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        sid = client.post("/sessions", json={}).json()["id"]
        first = client.post(
            f"/sessions/{sid}/messages", json={"request_id": "same", "content": "hello"}
        )
        repeated = client.post(
            f"/sessions/{sid}/messages", json={"request_id": "same", "content": "hello"}
        )
        assert first.json()["id"] == repeated.json()["id"]
        assert (
            client.post(
                f"/sessions/{sid}/messages",
                json={"request_id": "same", "content": "changed"},
            ).status_code
            == 409
        )
        checked = client.post(f"/sessions/{sid}/checks", json={})
        assert checked.status_code == 200 and checked.json()["valid"] is False
        assert not client.get(f"/sessions/{sid}/runs").json()


def test_history_page_boundary_and_isolation(tmp_path):
    settings = Settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        sid = client.post("/sessions", json={}).json()["id"]
        other = client.post("/sessions", json={}).json()["id"]
        store = client.app.state.service.store
        for i in range(65):
            store.put(
                "message",
                {
                    "id": f"m{i}",
                    "session_id": sid,
                    "content": str(i),
                    "status": "succeeded",
                    "created": i,
                },
            )
        store.put(
            "message",
            {
                "id": "other",
                "session_id": other,
                "content": "private",
                "status": "succeeded",
            },
        )
        p = client.get(f"/sessions/{sid}/history-page").json()
        assert len(p["messages"]) == 30 and p["messages"][0]["id"] == "m35"
        q = client.get(f"/sessions/{sid}/history-page?before={p['next_before']}").json()
        assert len(q["messages"]) == 30 and q["messages"][0]["id"] == "m5"
        assert set(m["id"] for m in p["messages"]).isdisjoint(
            m["id"] for m in q["messages"]
        )
        assert (
            client.post(f"/sessions/{other}/messages/m1/cancel", json={}).status_code
            == 400
        )


@pytest.mark.parametrize(
    "action,args",
    [
        ("shell", {}),
        ("run", {"sid": "s", "rid": "../../secret"}),
        ("history", {"sid": "s?secret"}),
        ("history", {"sid": "s", "before": -1}),
        ("compare", {"sid": "s", "left": "../x", "right": "r"}),
    ],
)
def test_bridge_rejects_arbitrary_capabilities(action, args):
    with pytest.raises(ValueError):
        route(action, args)


def test_cancel_queued_turn(tmp_path):
    from backend.agent import AgentController
    from backend.models import MessageInput
    from test_service import setup_service

    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        agent = AgentController(service, None)
        await agent.locks[sid].acquire()
        item = agent.enqueue(sid, MessageInput(request_id="cancel", content="不要运行"))
        cancelled = await agent.cancel_turn(sid, item["id"])
        agent.locks[sid].release()
        assert cancelled["status"] == "cancelled"
        assert not service.store.all("run")
        await service.close()

    asyncio.run(scenario())
