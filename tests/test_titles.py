import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend import titles


@pytest.mark.parametrize("prompt,expected", [
    ("使用当前会话已导入的 51IMB JSON 配置，按已保存的工程策略和默认参数执行完整规划", "51IMB完整规划"),
    ("沿用刚才规划，只把ZPM容量改为600，重新运行", "调整容量并重跑"),
    ("执行完整规划并导出交付包", "完整规划与导出"),
    ("检查通信规划所需数据", "检查通信规划所需数据"),
    ("我想使用现有数据完成一次 FTTH 光网规划，请先告诉我需要哪些输入，不要运行算法。", "FTTH规划输入检查"),
])
def test_task_titles(prompt, expected):
    assert titles.fallback_title(prompt) == expected


@pytest.mark.parametrize("reply", [
    "根据中国法律法规和技术安全规范，我无法执行或验证",
    "抱歉，我不能执行此操作", "我将为你检查数据", "道路数据检查\n这就是标题",
    "道路数据检查" * 8, "", "999IMB字段检查",
])
def test_refusals_and_malformed_titles_fall_back(monkeypatch, reply):
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(text=reply)))
    monkeypatch.setattr(titles, "build_model", lambda *a, **kw: model)
    result = asyncio.run(titles.generate_title(SimpleNamespace(provider="qwen"), "检查道路数据字段"))
    assert result == "检查道路数据字段"


def test_valid_semantic_title_and_no_thinking(monkeypatch):
    def build(settings, **kwargs):
        assert kwargs["extra_body"] == {"enable_thinking": False}
        return SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(text="道路字段检查")))
    monkeypatch.setattr(titles, "build_model", build)
    assert asyncio.run(titles.generate_title(SimpleNamespace(provider="qwen"), "检查道路数据字段")) == "道路字段检查"


def test_known_task_does_not_call_model(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("No title API needed for this explicit task")
    monkeypatch.setattr(titles, "build_model", forbidden)
    assert asyncio.run(titles.generate_title(SimpleNamespace(provider="qwen"), "使用51IMB配置执行完整规划")) == "51IMB完整规划"


def test_workflow_handled_first_message_still_gets_generated_title(tmp_path, monkeypatch):
    from backend.agent import AgentController
    from backend.models import MessageInput
    from test_service import setup_service
    service, sid, _ = setup_service(tmp_path)
    service.spawn = lambda coro: coro.close()
    settings = service.settings
    settings.model, settings.base_url = "synthetic", "https://synthetic.invalid/v1"
    controller = AgentController(service, None)
    async def start(item):
        return {"status": "blocked"}, "请明确本轮任务。"
    controller.workflows.start = start
    monkeypatch.setattr("backend.agent.generate_title", AsyncMock(return_value="规划需求澄清"))
    item = controller.enqueue(sid, MessageInput(request_id="first", content="帮我处理一下"))
    asyncio.run(controller.respond(item))
    assert service.store.get("message", item["id"])["response"] == "请明确本轮任务。"
    assert service.store.get("session", sid)["title"] == "规划需求澄清"
    assert any(e["type"] == "session_title" for e in service.store.events(sid, 0))
