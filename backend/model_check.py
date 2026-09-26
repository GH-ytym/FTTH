"""Two-turn real provider probe. Never sends project data or prints secrets."""
from langchain.tools import tool
from langchain_core.messages import HumanMessage, ToolMessage

from .model_client import build_model


@tool
def probe_project() -> dict:
    """Return a synthetic demo project's building count. This tool is read-only."""
    return {"building_count": 51, "synthetic": True}


async def check(settings):
    """Return (ok, message): a tool call plus a follow-up that uses its result."""
    if not settings.model_ready:
        return False, "模型未配置：设置 model.provider/model.name/model.base_url，并通过环境变量或 `ftth serve` 隐藏输入提供密钥。"
    try:
        model = build_model(settings)
        messages = [HumanMessage(content="这是工具接入测试。必须调用 probe_project 获取建筑数量，不能猜测。收到工具结果后用中文回答数量并标明这是合成测试数据。")]
        first = await model.bind_tools([probe_project]).ainvoke(messages)
        if not first.tool_calls or any(c["name"] != "probe_project" for c in first.tool_calls):
            return False, "FAIL: model did not request the expected read-only tool."
        messages.append(first)
        for call in first.tool_calls:
            messages.append(ToolMessage(content='{"building_count":51,"synthetic":true}', tool_call_id=call["id"]))
        final = await model.ainvoke(messages)
        if not final.text.strip() or "51" not in final.text:
            return False, "FAIL: follow-up response did not confirm the tool result."
        return True, f"PASS: {settings.model}; API={settings.model_api}; tool call + result follow-up."
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        return False, f"FAIL: {type(exc).__name__}" + (f" HTTP {status}" if status else "") + ". Check key, model access, endpoint and network."
