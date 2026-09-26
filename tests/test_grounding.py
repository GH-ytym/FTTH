"""Ambiguous slots and unsupported engine assertions from live UI failures."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.agent import AgentController
from backend.knowledge import read_reference, capabilities
from backend.models import RunInput
from backend.workflow import Intent, WorkflowController
from test_agent import FakeToolModel
from test_service import setup_service


@pytest.mark.parametrize('action', ['discuss', 'plan', 'clarify'])
def test_unresolved_parameter_never_commits_guessed_value(tmp_path, action):
    service, sid, _ = setup_service(tmp_path)
    guess = Intent(action=action, question='哪种容量？', unresolved_fields=['nro_max_cap'],
        parameter_overrides={'nro_max_cap':600}, purpose='read_only')
    resolved = WorkflowController.normalize_intent(guess)
    assert resolved.action == 'clarify'
    assert resolved.parameter_overrides.model_dump(exclude_unset=True) == {}
    item = {'id':'ambiguous', 'session_id':sid, 'project_id':'p', 'content':'容量600，只讨论'}
    task = service.coordinator.bind(item, resolved, {'id':'w', 'session_id':sid})
    assert task['parameters'] == {} and task['status'] == 'waiting_input'
    assert not service.store.all('run')


def test_reference_sources_and_preflight_have_consistent_scope(tmp_path):
    for topic in ('distance', 'capacity'):
        reference = read_reference(topic)
        assert all(s['available'] and s['line'] > 0 and len(s['sha256']) == 64 for s in reference['sources'])
        assert len(json.dumps(reference, ensure_ascii=False)) < 11000
    with pytest.raises(ValueError):
        read_reference('../../runtime/connection.token')
    service, sid, _ = setup_service(tmp_path)
    checked = service.validate(sid, RunInput(request_id='r', scope='partition', parameter_overrides={'nro_max_cap':600}))
    assert checked['valid'] and not checked['feasibility_checked']
    assert checked['capabilities']['full_required_nro_count'] == capabilities()['full_required_nro_count'] == 1
    project = service.store.get('project', 'p')
    project['summary']['nro_count'] = 2
    service.store.put('project', project)
    checked = service.validate(sid, RunInput(request_id='r', scope='full'))
    assert any('只能有一个 NRO' in e for e in checked['errors'])


def test_echoed_question_does_not_turn_discussion_into_clarification():
    intent = WorkflowController.normalize_intent(Intent(action='discuss',
        question='EPSG4326是否意味着把度当米？', request_summary='解释距离计算'))
    assert intent.action == 'discuss' and intent.question == ''


def test_capacity_evidence_includes_branch_scope_and_both_partition_levels():
    sources = {s['id']:s for s in read_reference('capacity')['sources']}
    assert 'znro_target' in sources['capacity.partition']['excerpt']
    assert 'zpm_target' in sources['capacity.partition']['excerpt']
    assert '当前API的full与partition均不走此分支' in sources['capacity.scope']['applicability']
    assert '--run-task3' in sources['capacity.dispatch']['excerpt']
    assert '--run-tasks4-to6' in sources['capacity.dispatch']['excerpt']


class GroundedModel(FakeToolModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if '需求理解阶段' in messages[0].content:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[
                {'name':'Intent','args':{'action':'discuss','request_summary':'解释坐标与距离，只读'},'id':'intent'}]))])
        assert 'full_required_nro_count' in messages[0].content
        if not isinstance(messages[-1], ToolMessage):
            answer = AIMessage(content='', tool_calls=[{'name':'read_algorithm_reference', 'args':{'topic':'distance'},'id':'source'}])
        else:
            evidence = json.loads(messages[-1].content)
            assert evidence['kind'] == 'implementation_reference_not_run_result'
            assert 'geographic_distance' in evidence['sources'][0]['excerpt']
            answer = AIMessage(content='存储为经纬度不等于按度计算。任务二启用地理距离，任务三有米制投影距离；未运行规划。')
        return ChatResult(generations=[ChatGeneration(message=answer)])


def test_answer_agent_can_read_source_without_execution(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'memory.sqlite')) as saver:
            agent = AgentController(service, saver, GroundedModel())
            await agent.respond({'id':'question','session_id':sid,'project_id':'p','content':'坐标系如何影响距离？只讨论'})
            answer = service.store.get('message','question')
            assert answer['status'] == 'succeeded' and '地理距离' in answer['response']
            assert not service.store.all('run') and not service.store.all('display')
        await service.close()
    asyncio.run(scenario())
