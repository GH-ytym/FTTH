import asyncio
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.agent import AgentController
from backend.models import PolicyInput
from test_service import setup_service


class FakeToolModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "scripted-test-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            response = AIMessage(content="已读取项目，有3个需求点。")
        else:
            response = AIMessage(content="", tool_calls=[{"name": "inspect_project", "args": {}, "id": "inspect-1", "type": "tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=response)])


def test_real_agent_loop_and_sqlite_memory(tmp_path):
    async def scenario():
        service, sid, other = setup_service(tmp_path)
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "memory.sqlite")) as saver:
            agent = AgentController(service, saver, FakeToolModel())
            await agent.respond({"id": "m1", "session_id": sid, "status": "queued", "content": "检查数据"})
            assert service.store.get("message", "m1")["status"] == "succeeded"
            state = await saver.aget({"configurable": {"thread_id": sid}})
            messages = state["channel_values"]["messages"]
            assert any(isinstance(m, ToolMessage) for m in messages)
            assert await saver.aget({"configurable": {"thread_id": other}}) is None
            await agent.respond({"id": "m2", "session_id": sid, "status": "queued", "content": "再检查一次"})
            later = await saver.aget({"configurable": {"thread_id": sid}})
            assert len(later["channel_values"]["messages"]) > len(messages)
    asyncio.run(scenario())


class FakeSubmitModel(FakeToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            response = AIMessage(content="任务已提交")
        else:
            response = AIMessage(content="", tool_calls=[{"name": "submit_planning",
                "args": {"scope": "full", "parameter_overrides": '{"use_formal_infrastructure":true,"split_oversized_imbs":true}'},
                "id": "submit-1", "type": "tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=response)])


def test_agent_submission_and_export_schedule_real_async_tasks(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        executed = []
        async def execute(run_id):
            executed.append(run_id)
            run = service.store.get("run", run_id)
            run["status"] = "succeeded"
            service.store.put("run", run)
        # Replace only the external algorithm, keeping Service.spawn and the
        # actual LangGraph tool dispatcher / event loop intact.
        service.execute = execute
        service.set_policy(sid, PolicyInput(use_formal_infrastructure=True, split_oversized_imbs=True))
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "memory.sqlite")) as saver:
            controller = AgentController(service, saver, FakeSubmitModel())
            await controller.respond({"id": "submit-message", "session_id": sid,
                "status": "queued", "content": "使用正式基础设施执行完整规划", "allow_actions": True, "project_id": "p"})
            await asyncio.sleep(0)
            assert service.store.get("message", "submit-message")["status"] == "succeeded"
            assert len(executed) == 1
            first = executed[0]
            assert service.store.get("run", first)["status"] == "succeeded"
            export = controller.tools(sid, "export-message")[-1]
            service.store.put("message", {"id": "export-message", "session_id": sid, "allow_actions": True, "project_id": "p"})
            result = await export.ainvoke({"run_id": first})
            await asyncio.sleep(0)
            assert executed == [first, result["id"]]
            assert service.store.get("run", result["id"])["status"] == "succeeded"
        await service.close()
    asyncio.run(scenario())


def test_export_notification_refreshes_source_display_receipts(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.settings.model = ""
        service.store.put("run", {"id": "export1", "session_id": sid, "kind": "export",
            "source_run_id": "source1", "status": "succeeded", "result": {"file_count": 54}})
        service.store.put("display", {"id": "display1", "session_id": sid, "run_id": "source1", "status": "succeeded"})
        await AgentController(service, None).respond({"id": "completion1", "session_id": sid,
            "notification": True, "run_id": "export1", "content": "old pending"})
        content = service.store.get("message", "completion1")["content"]
        assert '"display_run_id": "source1"' in content
        assert '"id": "display1", "status": "succeeded"' in content
        assert '"file_count": 54' in content
        assert "old pending" not in content
    asyncio.run(scenario())


class CompletionThenRerunModel(FakeToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            response = AIMessage(content="已完成当前请求")
        elif "当前轮次是后台完成事件" in messages[0].content:
            response = AIMessage(content="基线 seed 已完成，容量576。")
        else:
            assert "当前轮次是用户的新请求" in messages[0].content
            assert "600" in messages[-1].content
            assert not any(m.type == "human" and "后台任务完成通知" in str(m.content) for m in messages)
            response = AIMessage(content="", tool_calls=[{"name":"submit_planning", "id":"rerun600",
                "args":{"base_run_id":"seed", "parameter_overrides":{"zpm_max_cap":600}}, "type":"tool_call"}])
        return ChatResult(generations=[ChatGeneration(message=response)])


def test_completion_event_does_not_override_next_user_rerun(tmp_path):
    from backend.models import Parameters
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.spawn = lambda coro: coro.close()
        params = Parameters(use_formal_infrastructure=True, split_oversized_imbs=True).model_dump()
        service.set_policy(sid, PolicyInput(use_formal_infrastructure=True, split_oversized_imbs=True))
        service.store.put("run", {"id":"seed", "session_id":sid, "kind":"planning", "status":"succeeded", "scope":"full", "parameters":params})
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "memory.sqlite")) as saver:
            agent = AgentController(service, saver, CompletionThenRerunModel())
            await agent.respond({"id":"completion_seed", "session_id":sid, "notification":True,
                "run_id":"seed", "content":"后台任务完成通知"})
            state = await saver.aget({"configurable":{"thread_id":sid}})
            assert all(m.type != "human" for m in state["channel_values"]["messages"])
            await agent.respond({"id":"change600", "session_id":sid, "content":"沿用刚才方案，只把容量改为600并重跑", "allow_actions":True, "project_id":"p"})
            assert service.store.get("message", "change600")["status"] == "succeeded"
            new = [r for r in service.store.all("run") if r["id"] != "seed"]
            assert len(new) == 1 and new[0]["base_run_id"] == "seed"
            assert new[0]["parameters"] == {**params,"zpm_max_cap":600}
    asyncio.run(scenario())


def test_background_job_does_not_inherit_agent_callback_context(tmp_path):
    import contextvars
    context = contextvars.ContextVar("agent_callback", default=None)
    async def scenario():
        service, _, _ = setup_service(tmp_path)
        context.set("previous-message")
        async def background():
            return context.get()
        assert await service.spawn(background()) is None
        assert context.get() == "previous-message"
        await service.close()
    asyncio.run(scenario())
