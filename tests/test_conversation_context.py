"""Conversation-boundary regressions with scripted model input assertions."""
import asyncio
import json
import pytest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain.agents import create_agent
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.agent import AgentController, BoundedContext
from backend.workflow import WorkflowController
from test_agent import FakeToolModel
from test_service import setup_service

QUESTION = '如果把容量改成600，会有什么影响？先不要修改或运行。'
CLARIFY = '这里的容量是指 NRO 最大容量、ZPM 最大容量，还是正式 duct 容量？'
EXPLICIT = '接着刚才的问题，讨论把 NRO 容量改成 600 的影响，不执行修改或规划。'
SUMMARY = '讨论将 NRO 容量改成600的影响，不修改、不运行规划。'


class ConversationModel(FakeToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if '需求理解阶段' in messages[0].content:
            payload = json.loads(messages[1].content)
            current = payload['current_request']
            if current == QUESTION:
                args = {'action': 'clarify', 'question': CLARIFY,
                        'purpose': 'read_only', 'constraints': ['不修改或运行'],
                        'request_summary': '讨论容量600的影响，不执行；容量类型待明确。'}
            elif current in {'NRO', EXPLICIT}:
                turns = payload['recent_dialogue']
                assert any(t['user'] == QUESTION and t['assistant'] == CLARIFY for t in turns)
                if current == 'NRO':
                    assert payload['pending_clarification']
                    assert turns[-1]['user'] == QUESTION and turns[-1]['assistant'] == CLARIFY
                args = {'action': 'discuss', 'parameter_overrides': {'nro_max_cap': 600},
                        'continue_task': payload.get('active_task') is not None,
                        'request_summary': SUMMARY}
            else:
                args = {'action': 'query', 'request_summary': current}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',
                tool_calls=[{'name': 'Intent', 'args': args, 'id': 'intent'}]))])
        current = messages[-1].content
        if current in {'NRO', EXPLICIT}:
            assert SUMMARY in messages[0].content
            assert current in messages[0].content
            pairs = [(m.type, m.content) for m in messages[1:]]
            index = pairs.index(('human', QUESTION))
            assert pairs[index + 1] == ('ai', CLARIFY)
            assert pairs[-1] == ('human', current)
            assert not any(isinstance(m, ToolMessage) for m in messages), 'old tool traces leaked into new request'
            assert not any('STALE_CHECKPOINT' in str(m.content) for m in messages)
            answer = '你说的是NRO容量600。这里只分析容量约束的影响，未修改参数或运行规划。'
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_clarification_and_explicit_question_share_complete_history(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.spawn = lambda coro: coro.close()
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'memory.sqlite')) as saver:
            agent = AgentController(service, saver, ConversationModel())
            for index, content in enumerate(['检查当前数据，先不要运行规划。', QUESTION, 'NRO', EXPLICIT]):
                item = {'id': 'm' + str(index), 'session_id': sid, 'project_id': 'p', 'content': content}
                await agent.respond(item)
                saved = service.store.get('message', item['id'])
                assert saved['status'] == 'succeeded', saved
                if index == 0:
                    stale = create_agent(ConversationModel(), tools=[], checkpointer=saver)
                    await stale.aupdate_state({'configurable': {'thread_id': sid}},
                        {'messages': [HumanMessage(content='STALE_CHECKPOINT: 只回答旧的数据检查'),
                            AIMessage(content='STALE_CHECKPOINT: 旧回复')]}, as_node='model')
                if content in {'NRO', EXPLICIT}:
                    assert 'NRO容量600' in saved['response']
            assert not service.store.all('run')
            assert not service.store.all('display')
            state = await saver.aget({'configurable': {'thread_id': sid}})
            humans = [m.content for m in state['channel_values']['messages'] if m.type == 'human']
            assert humans == ['检查当前数据，先不要运行规划。', QUESTION, 'NRO', EXPLICIT]
            assert len(humans) == len(set(humans)), 'history was appended twice'
        await service.close()
    asyncio.run(scenario())


def test_old_clarification_rehydrates_without_qa_checkpoint(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.store.put('message', {'id': 'old', 'session_id': sid, 'project_id': 'p',
            'status': 'succeeded', 'content': QUESTION, 'response': CLARIFY, 'workflow_id': 'old-flow'})
        service.store.put('workflow', {'id': 'old-flow', 'session_id': sid, 'status': 'blocked',
            'intent': {'action': 'clarify', 'question': CLARIFY}})
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'memory.sqlite')) as saver:
            # A new process has no model memory of the clarification; SQLite
            # transcript is sufficient to resume it without replaying any action.
            agent = AgentController(service, saver, ConversationModel())
            await agent.respond({'id': 'reply', 'session_id': sid, 'project_id': 'p', 'content': 'NRO'})
            assert service.store.get('message', 'reply')['response'].startswith('你说的是NRO容量600')
            assert not service.store.all('run')
        await service.close()
    asyncio.run(scenario())


def test_context_excludes_other_sessions_projects_notifications_and_future(tmp_path):
    service, sid, other = setup_service(tmp_path)
    for mid, changes in [('valid', {}), ('other', {'session_id': other}),
                         ('project', {'project_id': 'other'}), ('notice', {'notification': True}),
                         ('future', {'created': 30}), ('queued', {'status': 'queued'})]:
        service.store.put('message', {'id': mid, 'session_id': sid, 'project_id': 'p',
            'created': 10, 'status': 'succeeded', 'content': mid, 'response': mid + '-reply', **changes})
    context = WorkflowController(service).conversation({'id': 'now', 'session_id': sid, 'project_id': 'p', 'created': 20})
    assert [t['message_id'] for t in context['recent_dialogue']] == ['valid']


def test_pending_chain_survives_history_budget_and_cancel_clears_pending(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    service.store.put('message', {'id': 'long', 'session_id': sid, 'project_id': 'p',
        'content': 'old' * 20000, 'response': 'unrelated'})
    for i in range(7):
        wid = 'w' + str(i)
        service.store.put('message', {'id': 'm' + str(i), 'session_id': sid, 'project_id': 'p',
            'content': QUESTION if i == 0 else '补充' + str(i), 'response': CLARIFY, 'workflow_id': wid})
        service.store.put('workflow', {'id': wid, 'session_id': sid, 'status': 'blocked', 'intent': {'action': 'clarify'}})
    controller = WorkflowController(service)
    context = controller.conversation({'id': 'new', 'session_id': sid, 'project_id': 'p'})
    assert context['recent_dialogue'][0]['user'] == QUESTION
    assert len(context['pending_clarification']) == 7
    w = service.store.get('workflow', 'w6')
    service.store.put('workflow', {**w, 'status': 'cancelled'})
    assert not controller.conversation({'id': 'new', 'session_id': sid, 'project_id': 'p'})['pending_clarification']


def test_old_message_update_does_not_reorder_conversation(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    for index in range(3):
        service.store.put('message', {'id': str(index), 'session_id': sid, 'project_id': 'p',
            'created': index, 'status': 'succeeded', 'content': str(index), 'response': 'answer'})
    service.store.put('message', service.store.get('message', '0'))
    context = service.coordinator.conversation({'id': 'new', 'session_id': sid, 'project_id': 'p', 'created': 4})
    assert [t['message_id'] for t in context['recent_dialogue']] == ['0', '1', '2']


def test_model_budget_cannot_drop_pending_clarification():
    class Request:
        messages = [HumanMessage(content='容量600，不执行', id='user_question'),
                    AIMessage(content=CLARIFY), HumanMessage(content='NRO'),
                    ToolMessage(content='large evidence ' * 20000, tool_call_id='current')]
        system_message = None
    async def handler(request):
        pytest.fail('oversized protected context reached the model')
    with pytest.raises(ValueError, match='待澄清'):
        asyncio.run(BoundedContext(['question']).awrap_model_call(Request(), handler))
