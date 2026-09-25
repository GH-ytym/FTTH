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
