import json
import httpx
from langchain_core.messages import HumanMessage, ToolMessage
from langchain.tools import tool
from backend.settings import Settings
from backend.model_client import build_model


def qwen_settings(tmp_path, monkeypatch):
    for key in ("FTTH_MODEL", "FTTH_BASE_URL", "FTTH_API_KEY", "FTTH_PROVIDER", "FTTH_MODEL_API", "FTTH_ENABLE_THINKING"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key-not-real")
    return Settings(tmp_path)


def test_qwen_defaults_and_secret_isolation(tmp_path, monkeypatch):
    settings = qwen_settings(tmp_path, monkeypatch)
    assert settings.provider == "qwen"
    assert settings.model == "qwen3.8-flash"
    assert settings.base_url == "https://maas.qianwenaiapi.com/compatible-mode/v1"
    assert settings.model_ready
    from backend.qgis_runtime import QgisRuntime
    for layout in ("host", "osgeo4w", "macos-bundle", "conda", "system"):
        settings.use_runtime(QgisRuntime(tmp_path / "python", layout, tmp_path, "test"))
        assert "DASHSCOPE_API_KEY" not in settings.worker_env()
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    monkeypatch.setenv("FTTH_PROVIDER", "qwen")
    assert not Settings(tmp_path).model_ready


@tool
def count_buildings() -> int:
    """Return the synthetic building count."""
    return 51


def test_streamed_reasoning_tool_result_round_trip(tmp_path, monkeypatch):
    settings = qwen_settings(tmp_path, monkeypatch)
    requests = []
    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path.endswith("/chat/completions")
        assert payload["enable_thinking"] is True
        assert payload["stream"] is True
        if len(requests) == 1:
            deltas = [{"role": "assistant", "reasoning_content": "test reason "},
                      {"reasoning_content": "continued"},
                      {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "count_buildings", "arguments": "{}"}}]}]
            finish = "tool_calls"
        else:
            assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
            assert assistant["reasoning_content"] == "test reason continued"
            assert payload["messages"][-1]["role"] == "tool"
            deltas, finish = [{"role": "assistant", "content": "51个建筑（合成数据）。"}], "stop"
        events = [{"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": settings.model,
                   "choices": [{"index": 0, "delta": d, "finish_reason": None}]} for d in deltas]
        events.append({"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": settings.model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        body = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = build_model(settings, http_client=client)
        messages = [HumanMessage(content="检查建筑数量")]
        first = model.bind_tools([count_buildings]).invoke(messages)
        assert first.tool_calls[0]["name"] == "count_buildings"
        messages += [first, ToolMessage(content="51", tool_call_id="call_1")]
        final = model.invoke(messages)
        assert "51" in final.text
        assert "test reason" not in final.text
    assert len(requests) == 2


def test_responses_option_and_thinking_switch(tmp_path, monkeypatch):
    qwen_settings(tmp_path, monkeypatch)  # Provider environment only.
    monkeypatch.setenv("FTTH_MODEL_API", "responses")
    monkeypatch.setenv("FTTH_ENABLE_THINKING", "false")
    model = build_model(Settings(tmp_path))
    payload = model._get_request_payload([HumanMessage(content="test")])
    assert "input" in payload and "messages" not in payload
    assert payload["extra_body"]["enable_thinking"] is False
    assert model.use_responses_api is True

