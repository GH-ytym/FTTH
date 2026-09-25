"""Two-turn real provider probe. Never sends project data or prints secrets."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import HumanMessage, ToolMessage
from langchain.tools import tool
from backend.model_client import build_model
from backend.settings import Settings


@tool
def probe_project() -> dict:
    """Return a synthetic demo project's building count. This tool is read-only."""
    return {"building_count": 51, "synthetic": True}


async def main():
    settings = Settings()
    if not settings.model_ready:
        print("Model not configured. Run scripts/start_qwen.ps1 and enter the key locally.")
        return 2
    try:
        model = build_model(settings)
        messages = [HumanMessage(content="这是工具接入测试。必须调用 probe_project 获取建筑数量，不能猜测。收到工具结果后用中文回答数量并标明这是合成测试数据。")]
        bound = model.bind_tools([probe_project])
        first = await bound.ainvoke(messages)
        if not first.tool_calls or any(c["name"] != "probe_project" for c in first.tool_calls):
            print("FAIL: model did not request the expected read-only tool.")
            return 1
        messages.append(first)
        for call in first.tool_calls:
            messages.append(ToolMessage(content='{"building_count":51,"synthetic":true}', tool_call_id=call["id"]))
        final = await model.ainvoke(messages)
        if not final.text.strip() or "51" not in final.text:
            print("FAIL: follow-up response did not confirm the tool result.")
            return 1
        print(f"PASS: {settings.model}; API={settings.model_api}; tool call + result follow-up.")
        return 0
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        print(f"FAIL: {type(exc).__name__}" + (f" HTTP {status}" if status else "") + ". Check key, model access, endpoint and network.")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
