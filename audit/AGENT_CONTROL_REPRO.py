"""Read-only with respect to the application: all objects live in a temporary DB.

Reproduces observed control gaps; does not run QGIS, contact a model, or repair data.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_service import setup_service
from test_agent import FakeToolModel
from backend.agent import AgentController
from backend.models import PolicyInput, RunInput
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class IgnoresExecution(FakeToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="旧结果已完成，无需提交新规划。"))])


async def main():
    with tempfile.TemporaryDirectory(prefix="ftth-control-audit-") as directory:
        root = Path(directory)
        service, sid, _ = setup_service(root)
        service.spawn = lambda coro: coro.close()
        try:
            service.set_policy(sid, PolicyInput(use_formal_infrastructure=True, split_oversized_imbs=True))
            checked = service.validate(sid, RunInput(request_id="policy", scope="full"))
            print(json.dumps({"case": "saved_policy_omitted_overrides", "valid": checked["valid"], "errors": checked["errors"]}, ensure_ascii=False))
            async with AsyncSqliteSaver.from_conn_string(str(root / "memory.sqlite")) as saver:
                controller = AgentController(service, saver, IgnoresExecution())
                await controller.respond({"id": "audit-message", "session_id": sid,
                    "content": "沿用当前已保存的工程策略，执行一次完整规划。", "allow_actions": True,
                    "allow_auto_repair": True, "project_id": "p"})
                message = service.store.get("message", "audit-message")
                print(json.dumps({"case": "ignores_execution", "message_status": message["status"],
                    "new_runs": len(service.store.all("run")), "response": message["response"]}, ensure_ascii=False))
                for value in (5, 10):
                    service.submit(sid, RunInput(request_id="variation" + str(value), scope="full",
                        parameter_overrides={"candidate_paths": value, "use_formal_infrastructure": True,
                            "split_oversized_imbs": True}), agent_message_id="audit-message")
                print(json.dumps({"case": "same_message_two_roots", "new_runs": len(service.store.all("run")),
                    "root_count": len({run["repair_root_id"] for run in service.store.all("run")})}))
        finally:
            await service.close()
            service.store.db.close()


if __name__ == "__main__":
    asyncio.run(main())
