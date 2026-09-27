"""Real OpenAI SSE adapter -> agent callbacks -> persisted HTTP events."""
import asyncio
import json
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.model_client import build_model
from backend.settings import Settings
from backend.store import Store
from test_service import setup_service, use_host_python
from unittest.mock import patch


class DelayedSSE(httpx.AsyncByteStream):
    def __init__(self, fail=False, content_parts=None):
        self.fail = fail
        self.content_parts = content_parts or ["正在检查", "项目数据。", "\n\n**检查完成。**"]

    async def __aiter__(self):
        deltas = [{"role": "assistant", "reasoning_content": "PRIVATE_REASONING_SENTINEL"}] + [
            {"content": text} for text in self.content_parts]
        for delta in deltas:
            await asyncio.sleep(.15)
            yield ("data: " + json.dumps({"id": "test-stream", "object": "chat.completion.chunk", "created": 1,
                "model": "test", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n").encode()
            if self.fail and delta.get("content"):
                raise RuntimeError("SECRET_PROVIDER_EXCEPTION")
        yield b'data: {"id":"test-stream","object":"chat.completion.chunk","created":1,"model":"test","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield b"data: [DONE]\n\n"


def streaming_model(settings, fail=False, content_parts=None):
    settings.provider = "qwen"
    settings.base_url = "https://synthetic.invalid/v1"
    settings.model = "test"
    settings.api_key = "synthetic-key"
    def transport(request):
        payload = json.loads(request.content)
        if any(t.get('function', {}).get('name') == 'Intent' for t in payload.get('tools', [])):
            class IntentSSE(httpx.AsyncByteStream):
                async def __aiter__(self):
                    chunk = {'id':'intent', 'object':'chat.completion.chunk', 'created':1, 'model':'test', 'choices':[{'index':0,'delta':{'role':'assistant','tool_calls':[{'index':0,'id':'intent','type':'function','function':{'name':'Intent','arguments':'{"action":"query"}'}}]},'finish_reason':'tool_calls'}]}
                    yield ('data: '+json.dumps(chunk)+'\n\n').encode()
                    yield b'data: [DONE]\n\n'
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, stream=IntentSSE())
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=DelayedSSE(fail, content_parts))
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return build_model(settings, http_async_client=client, max_retries=0)


@pytest.mark.parametrize("fail", [False, True])
def test_partial_reply_is_visible_before_completion_and_restorable(tmp_path, fail):
    settings = Settings(tmp_path)
    model = streaming_model(settings, fail)
    with TestClient(create_app(settings, model=model)) as client:
        client.headers["Authorization"] = "Bearer " + settings.token
        sid = client.post("/sessions", json={}).json()["id"]
        mid = client.post(f"/sessions/{sid}/messages", json={"request_id": "stream", "content": "检查"}).json()["id"]
        partial, history, streamed = None, None, False
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            history = client.get(f"/sessions/{sid}/history").json()
            message = history["messages"][0]
            if message.get("partial_response") and message["status"] == "running":
                partial = history
                streamed = streamed or any(e["type"] == "assistant_stream"
                    for e in client.get(f"/sessions/{sid}/events").json())
            if message["status"] in ("succeeded", "failed"):
                break
            time.sleep(.02)
        if not fail:
            assert partial, "No text was visible while the model was still running"
            assert partial["messages"][0]["partial_response"].startswith("正在检查")
            assert streamed, "No stream event was published while the model was still running"
        assert message["status"] == ("failed" if fail else "succeeded")
        assert "正在检查" in message["response"]
        events = client.get(f"/sessions/{sid}/events").json()
        # Cumulative snapshots are superseded by the final reply, not retained.
        assert not any(e["type"] == "assistant_stream" for e in events)
        assert len([e for e in events if e["type"] == "assistant"]) == 1
        assert "PRIVATE_REASONING_SENTINEL" not in json.dumps([history, events])
        assert "SECRET_PROVIDER_EXCEPTION" not in json.dumps([history, events])
        if partial:
            after = client.get(f"/sessions/{sid}/events", params={"after": partial["cursor"]}).json()
            assert any(e["type"] == "assistant" and e["message_id"] == mid for e in after)


def test_message_and_event_rollback_together(tmp_path):
    store = Store(tmp_path / "atomic.sqlite")
    store.put("message", {"id": "m", "session_id": "s", "status": "running"})
    store.db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected'); END")
    store.db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        store.publish_message({"id": "m", "session_id": "s", "status": "succeeded", "response": "answer"},
            [("assistant", {"text": "answer"})])
    assert store.get("message", "m")["status"] == "running"
    assert store.events("s", 0) == []
    store.db.close()




def test_shutdown_during_process_creation_reaps_child(tmp_path):
    async def scenario():
        service, _, _ = setup_service(tmp_path)
        use_host_python(service)
        entered, release = asyncio.Event(), asyncio.Event()
        original = asyncio.create_subprocess_exec
        children = []
        marker = tmp_path / "orphan.txt"
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            proc = await original(*args, **kwargs)
            children.append(proc)
            return proc
        with patch("asyncio.create_subprocess_exec", delayed):
            service.spawn(service.process(["-c", "import time; time.sleep(2); from pathlib import Path; Path(" + repr(str(marker)) + ").touch()"], tmp_path / "worker.log"))
            await entered.wait()
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            release.set()
            await closing
        assert children and all(p.returncode is not None for p in children)
        assert not marker.exists()
        service.store.db.close()
    asyncio.run(scenario())
