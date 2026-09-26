import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.agent import AgentController, BoundedContext
from backend.models import PolicyInput, RunInput, WorkflowChoice
from backend.workflow import Intent, WorkflowController, compact_run
from backend.store import Store
from test_agent import FakeToolModel
from test_service import setup_service


class IntentModel(FakeToolModel):
    action: str = 'plan'
    use_latest: bool = False
    quote: str | None = None
    malformed: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.malformed:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='旧结果已完成，无需新批次。'))])
        payload = json.loads(messages[1].content)
        assert 'result' not in payload
        args = {'action': self.action, 'scope':'full', 'use_latest': self.use_latest,
            'evidence_quote': self.quote if self.quote is not None else payload['current_request']}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[{'name':'Intent','args':args,'id':'intent'}]))])


def fixture(tmp_path, *, conflict=False, model=None):
    service, sid, other = setup_service(tmp_path)
    service.spawn = lambda coro: coro.close()
    service.set_policy(sid, PolicyInput(use_formal_infrastructure=True, split_oversized_imbs=True, use_recognition_roads=conflict))
    controller = WorkflowController(service, model or IntentModel())
    service.workflows = controller
    item = {'id':'message', 'session_id':sid, 'project_id':'p', 'content':'沿用已保存策略执行一次完整规划。',
        'status':'running', 'allow_auto_repair':True}
    service.store.put('message', item)
    return service, controller, item, other


def test_saved_policy_is_inherited_and_current_request_creates_one_run(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    service.store.put('message', {'id':'old', 'session_id':item['session_id'], 'content':'解释旧结果', 'response':'不要重跑。' * 10000})
    w, text = asyncio.run(controller.start(item))
    assert w['status'] == 'submitted' and '已提交' in text
    assert len(service.store.all('run')) == 1
    assert service.store.get('run', w['run_id'])['parameters']['use_formal_infrastructure']


@pytest.mark.parametrize('legacy', [None, True, False])
@pytest.mark.parametrize('auto_rerun', [True, False])
def test_request_authorizes_plan_then_export_without_mode(tmp_path, legacy, auto_rerun):
    from backend.models import MessageInput, ExportInput

    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.spawn = lambda coro: coro.close()
        service.set_policy(sid, PolicyInput(use_formal_infrastructure=True, split_oversized_imbs=True))
        controller = AgentController(service, None, IntentModel())
        fields = {} if legacy is None else {'allow_actions': legacy}
        item = controller.enqueue(sid, MessageInput(request_id='plan', content='开始完整规划',
            allow_auto_repair=auto_rerun, **fields))
        assert 'allow_actions' not in item
        await controller.respond(item)
        run = service.store.all('run')[0]
        assert run['allow_auto_repair'] is auto_rerun
        w = service.store.get('workflow', run['workflow_id'])
        assert w['authorization'] == {'source':'user_request', 'action':'plan',
            'evidence_quote':'开始完整规划', 'request_content':'开始完整规划'}
        run['status'] = 'succeeded'
        service.store.put('run', run)
        with pytest.raises(ValueError, match='授权'):
            service.export(sid, ExportInput(request_id='wrong-action', run_id=run['id']), agent_message_id=item['id'])
        controller.workflows.model = IntentModel(action='export', use_latest=True)
        export_item = controller.enqueue(sid, MessageInput(request_id='export', content='导出上次结果', **fields))
        await controller.respond(export_item)
        exports = [r for r in service.store.all('run') if r['kind'] == 'export']
        assert len(exports) == 1 and exports[0]['source_run_id'] == run['id']
        with pytest.raises(ValueError, match='授权'):
            service.submit(sid, RunInput(request_id='wrong-plan'), agent_message_id=export_item['id'])
        assert len(service.store.all('run')) == 2
        await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('content,action', [
    ('不要运行，解释现有结果', 'query'),
    ('如果把容量改成600，会怎样？', 'discuss'),
    ('“执行完整规划”这句话是什么意思？', 'query'),
    ('如果导出会生成什么？', 'query'),
    ('帮我处理一下', 'clarify'),
])
def test_non_execution_intent_cannot_authorize_actions(tmp_path, content, action):
    # Scripted classifications test dispatch and permission boundaries, not LLM accuracy.
    service, controller, item, _ = fixture(tmp_path, model=IntentModel(action=action))
    item.update(content=content, allow_actions=True)
    service.store.put('message', item)
    w, _ = asyncio.run(controller.start(item))
    assert 'authorization' not in w and not service.store.all('run')
    for requested in ('plan', 'export'):
        with pytest.raises(ValueError, match='授权'):
            service.authorize_agent(item['session_id'], item['id'], action=requested)


@pytest.mark.parametrize('tamper', ['notification', 'content', 'workflow_message', 'workflow_session', 'workflow_project', 'action', 'quote', 'legacy_only'])
def test_authorization_is_bound_to_current_request(tmp_path, tamper):
    from conftest import authorize_message
    service, sid, other = setup_service(tmp_path)
    authorize_message(service, sid, 'request')
    message = service.store.get('message', 'request')
    w = service.store.get('workflow', message['workflow_id'])
    if tamper == 'notification':
        message['notification'] = True
    elif tamper == 'content':
        message['content'] = '现在只讨论'
    elif tamper == 'legacy_only':
        message.pop('workflow_id')
        message['allow_actions'] = True
    elif tamper == 'action':
        w['intent']['action'] = 'export'
    elif tamper == 'quote':
        w['authorization']['evidence_quote'] = '旧消息中的授权'
    else:
        key = {'workflow_message':'message_id', 'workflow_session':'session_id', 'workflow_project':'project_id'}[tamper]
        w[key] = other
    service.store.put('message', message)
    service.store.put('workflow', w)
    with pytest.raises(ValueError, match='授权'):
        service.authorize_agent(sid, message['id'])
    assert not service.store.all('run')


def test_conflict_selection_replay_and_cancel(tmp_path):
    service, controller, item, other = fixture(tmp_path, conflict=True)
    w, _ = asyncio.run(controller.start(item))
    assert w['status'] == 'waiting_choice' and not service.store.all('run')
    with pytest.raises(ValueError, match='不属于'):
        controller.owned(other, w['id'])
    choice = WorkflowChoice(request_id='choose', option_id='formal_only', version=w['version'])
    def resolve():
        return controller.resolve(item['session_id'], w['id'], choice)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _:resolve(), range(2)))
    assert results[0]['run_id'] == results[1]['run_id']
    assert len(service.store.all('run')) == 1
    assert service.store.get('session', item['session_id'])['policy']['use_recognition_roads'] is False
    asyncio.run(controller.cancel(item['session_id'], w['id']))
    assert service.store.get('run', results[0]['run_id'])['status'] == 'cancelled'


@pytest.mark.parametrize('change', ['policy', 'revoke', 'cancel', 'new_plan'])
def test_stale_choice_never_executes(tmp_path, change):
    service, controller, item, _ = fixture(tmp_path, conflict=True)
    w, _ = asyncio.run(controller.start(item))
    choice = WorkflowChoice(request_id='choose', option_id='formal_only', version=w['version'])
    if change == 'policy':
        service.set_policy(item['session_id'], PolicyInput(use_formal_infrastructure=True, split_oversized_imbs=True))
    elif change == 'revoke':
        service.store.put('message', {**service.store.get('message', item['id']), 'content':'请求已撤销'})
    elif change == 'cancel':
        asyncio.run(controller.cancel(item['session_id'], w['id']))
    else:
        newer = {**item, 'id':'newer'}
        newer.pop('workflow_id', None)
        service.store.put('message', newer)
        asyncio.run(controller.start(newer))
    with pytest.raises(ValueError):
        controller.resolve(item['session_id'], w['id'], choice)
    assert not service.store.all('run')


@pytest.mark.parametrize('model', [IntentModel(malformed=True), IntentModel(quote='不存在的授权'), IntentModel(quote='')])
def test_no_valid_intent_or_authority_no_submission(tmp_path, model):
    service, controller, item, _ = fixture(tmp_path, model=model)
    w, response = asyncio.run(controller.start(item))
    assert w['status'] in {'blocked','failed'} and response
    assert not service.store.all('run')


def test_duplicate_parameter_variation_rejected_at_service(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    w, _ = asyncio.run(controller.start(item))
    with pytest.raises(ValueError, match='另一组参数'):
        service.submit(item['session_id'], RunInput(request_id='alternate', parameter_overrides={'candidate_paths':10}), agent_message_id=item['id'])
    assert len(service.store.all('run')) == 1


def test_claim_and_run_rollback_together(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    service.store.db.execute("CREATE TRIGGER reject_run BEFORE INSERT ON events WHEN json_extract(NEW.data, '$.type')='run' BEGIN SELECT RAISE(ABORT, 'injected'); END")
    service.store.db.commit()
    w, _ = asyncio.run(controller.start(item))
    assert w['status'] == 'failed'
    assert not service.store.all('run')
    assert service.store.db.execute('SELECT count(*) FROM workflow_actions').fetchone()[0] == 0


def test_migration_backup_and_restart_never_replays(tmp_path):
    path = tmp_path/'legacy.sqlite'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE objects (kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id))')
    c.commit(); c.close()
    store = Store(path)
    assert list(tmp_path.glob('legacy.sqlite.pre-0.6.0-*.bak'))
    store.db.close()
    service, controller, item, _ = fixture(tmp_path)
    w, _ = asyncio.run(controller.start(item))
    asyncio.run(service.close())
    from backend.service import Service
    restarted = Service(service.settings, service.store)
    assert service.store.get('workflow', w['id'])['status'] == 'interrupted'
    assert len(service.store.all('run')) == 1
    asyncio.run(restarted.close())


def test_bounded_context_keeps_current_tool_pair(tmp_path):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage, ToolMessage
    messages = [HumanMessage(content='old'*20000), AIMessage(content='old response'), HumanMessage(content='current'),
        AIMessage(content='', tool_calls=[{'name':'get_run','args':{},'id':'x'}]), ToolMessage(content='evidence',tool_call_id='x')]
    class Request:
        def __init__(self): self.messages = messages
        def override(self, **kwargs): return SimpleNamespace(**kwargs)
    async def handler(request):
        assert request.messages[0].content == 'current'
        assert request.messages[-1].tool_call_id == 'x'
    asyncio.run(BoundedContext().awrap_model_call(Request(), handler))


def test_readonly_agent_has_no_execution_tools(tmp_path):
    class GuardedQueryModel(FakeToolModel):
        def bind_tools(self, tools, **kwargs):
            names = [t.name if hasattr(t,'name') else t.get('function',{}).get('name') for t in tools]
            assert not {'submit_planning','export_result','show_result'} & set(names)
            return self
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'memory.sqlite')) as saver:
            agent = AgentController(service,saver,GuardedQueryModel())
            await agent.respond({'id':'query','session_id':sid,'content':'不要运行，解释现有结果','allow_actions':True,'project_id':'p'})
            assert not service.store.all('run')
            assert service.store.get('message','query')['status'] == 'succeeded'
        await service.close()
    asyncio.run(scenario())


def test_cancel_while_model_is_running_prevents_submission(tmp_path):
    async def scenario():
        service, controller, item, _ = fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(message):
            entered.set()
            await release.wait()
            return Intent(action='plan', evidence_quote=message['content'])
        controller.interpret = delayed
        task = asyncio.create_task(controller.start(item))
        await entered.wait()
        await controller.cancel(item['session_id'], item['workflow_id'])
        release.set()
        w, _ = await task
        assert w['status'] == 'cancelled' and not service.store.all('run')
    asyncio.run(scenario())


def test_snapshot_tampering_at_choice_blocks_run(tmp_path):
    import hashlib
    service, controller, item, _ = fixture(tmp_path, conflict=True)
    file = tmp_path/'config.json'
    file.write_text('{}')
    project = service.store.get('project','p')
    project.update(config_path=str(file), hashes={'config.json':hashlib.sha256(file.read_bytes()).hexdigest()})
    service.store.put('project',project)
    w, _ = asyncio.run(controller.start(item))
    file.write_text('{"changed":true}')
    w = controller.resolve(item['session_id'], w['id'], WorkflowChoice(request_id='x', option_id='formal_only', version=w['version']))
    assert w['status'] == 'blocked' and '快照已改变' in w['detail']
    assert not service.store.all('run')


def test_current_input_is_not_truncated_to_fit_budget():
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    class Request:
        messages = [HumanMessage(content='新任务' * 50000)]
    async def handler(request):
        raise AssertionError('oversized input reached the model')
    with pytest.raises(ValueError, match='预算'):
        asyncio.run(BoundedContext().awrap_model_call(Request(), handler))


def test_model_timeout_records_failure_without_secret(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    async def fail(message):
        raise TimeoutError('SECRET_PROVIDER_URL')
    controller.interpret = fail
    w, _ = asyncio.run(controller.start(item))
    assert w['status']=='failed' and w['error']=='TimeoutError'
    assert 'SECRET_PROVIDER_URL' not in json.dumps(w)
    assert not service.store.all('run')


def test_new_policy_overrides_old_baseline_only_for_policy_fields(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    first = service.submit(item['session_id'], RunInput(request_id='base', parameter_overrides={'zpm_max_cap':600}))
    p, _, values = service.resolve(item['session_id'], RunInput(request_id='next', base_run_id=first['id'], parameter_overrides={'candidate_paths':10}))
    assert values['zpm_max_cap']==600 and values['candidate_paths']==10
    assert values['use_formal_infrastructure'] is True


def test_discussion_does_not_supersede_pending_authorized_request(tmp_path):
    service, controller, item, _ = fixture(tmp_path, conflict=True)
    waiting, _ = asyncio.run(controller.start(item))
    read_only = {**item, 'id':'check', 'content':'解释一下现有问题'}
    controller.model = IntentModel(action='discuss')
    service.store.put('message', read_only)
    blocked, _ = asyncio.run(controller.start(read_only))
    assert blocked['status']=='completed' and 'authorization' not in blocked
    assert service.store.get('workflow',waiting['id'])['status']=='waiting_choice'
    assert service.store.get('session',item['session_id'])['latest_workflow_id']==waiting['id']


def test_workflow_has_one_combined_completion_notice(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    async def scenario():
        workflow, _ = await controller.start(item)
        run = service.store.get('run', workflow['run_id'])
        run['status'] = 'succeeded'
        service.store.put('run', run)
        agent = AgentController(service, None)
        service.settings.model = service.settings.api_key = service.settings.base_url = ''
        agent.completed(run)
        assert not any(m['id'].startswith('completion_') for m in service.store.all('message'))
        await agent.publish_review({'id':'combined', 'run_id':run['id'], 'session_id':item['session_id'],
            'status':'completed', 'result':{'verdict':'needs_review', 'findings':[
                {'severity':'warning', 'description':'需要确认管孔容量'},
                *[{'severity':'info', 'description':'说明'} for _ in range(3)]]}})
        text = service.store.get('message', 'review_report_combined')['response']
        assert '规划计算已完成' in text and '需要复核' in text
        assert '0 项错误、1 项警告、3 项说明' in text
        assert '需要确认管孔容量' in text and '本次未自动重算' in text
    asyncio.run(scenario())
