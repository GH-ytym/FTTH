import asyncio
import copy

import pytest

from backend.models import RunInput
from backend.workflow import Intent, WorkflowController
from test_workflow import fixture, IntentModel
from test_service import setup_service


def bind(service, sid, mid, intent):
    item = {'id': mid, 'session_id': sid, 'project_id': 'p', 'content': mid}
    w = {'id': 'w-' + mid, 'session_id': sid, 'message_id': mid, 'project_id': 'p',
         'status': 'interpreting', 'intent': intent.model_dump()}
    service.store.put('message', item)
    task = service.coordinator.bind(item, intent, w)
    return task, w, item


def test_clarification_preserves_goal_sources_and_readonly_boundary(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    task, first, _ = bind(service, sid, '讨论容量600，不运行', Intent(action='clarify', purpose='read_only',
        request_summary='分析容量600的影响，不执行', question='哪种容量？', constraints=['不运行']))
    continued, second, item = bind(service, sid, 'NRO', Intent(action='discuss', continue_task=True,
        request_summary='分析NRO容量600的影响，不执行', parameter_overrides={'nro_max_cap':600}))
    assert continued['id'] == task['id'] and continued['revision'] == 2
    assert continued['mode'] == 'read_only' and continued['constraints'] == ['不运行']
    assert continued['source_message_ids'] == ['讨论容量600，不运行', 'NRO']
    assert continued['original_request'] == '讨论容量600，不运行'
    assert continued['pending_question'] == ''
    with pytest.raises(ValueError, match='仅讨论'):
        bind(service, sid, 'yes', Intent(action='plan', continue_task=True, evidence_quote='yes'))
    assert not service.store.all('run')
    fresh, _, _ = bind(service, sid, '现在执行NRO600', Intent(action='plan', evidence_quote='现在执行NRO600',
        request_summary='运行NRO容量600的规划', parameter_overrides={'nro_max_cap':600}))
    assert fresh['id'] != task['id'] and fresh['mode'] == 'execute'
    assert first['task_context']['revision'] == 1 and second['task_context']['revision'] == 2


def test_topic_switch_and_late_workflow_do_not_replace_current_goal(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    w, _ = asyncio.run(controller.start(item))
    original = service.store.get('task', w['task_id'])
    new, _, _ = bind(service, item['session_id'], '解释坐标系', Intent(action='query', request_summary='解释坐标系'))
    run = service.store.get('run', w['run_id'])
    run.update(status='succeeded')
    service.store.put('run', run)
    controller.run_changed(run)
    controller.review_changed({'id':'late-review', 'session_id':item['session_id'], 'run_id':run['id'],
        'status':'completed', 'result':{'verdict':'needs_review'}})
    current = service.coordinator.context({'session_id':item['session_id'], 'project_id':'p'})
    assert current['id'] == new['id'] and current['objective'] == '解释坐标系'
    assert service.store.get('task', original['id'])['status'] == 'completed'
    done = service.store.get('workflow', w['id'])
    controller.run_changed({**run, 'status':'running'})
    assert service.store.get('workflow', w['id']) == done
    assert service.store.get('worker', 'algorithm:' + run['id'])['status'] == 'succeeded'


def test_old_revision_cannot_complete_new_clarification_goal(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    first, w, _ = bind(service, sid, 'first', Intent(action='clarify', purpose='read_only', question='容量？'))
    newer, _, _ = bind(service, sid, '600', Intent(action='discuss', continue_task=True, request_summary='分析600'))
    w.update(status='completed', intent={'action':'plan'})
    service.coordinator.sync_workflow(w)
    task = service.store.get('task', first['id'])
    assert task['revision'] == newer['revision'] and task['status'] == 'answering'


def test_recovery_preserves_waiting_goal_and_interrupts_workers(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    waiting, _, _ = bind(service, sid, 'wait', Intent(action='clarify', purpose='read_only', question='NRO还是ZPM？'))
    running, w, _ = bind(service, sid, 'run', Intent(action='plan', evidence_quote='run'))
    service.coordinator.worker('algorithm', {'id':'r', 'session_id':sid, 'status':'running'}, w)
    # Preserve a separate pending task as the selected task across restart.
    waiting['status'] = 'waiting_input'
    service.store.put('task', waiting)
    session = service.store.get('session', sid)
    session['focus_task_id'] = waiting['id']
    service.store.put('session', session)
    service.coordinator.recover()
    assert service.coordinator.active(sid, 'p')['pending_question'] == 'NRO还是ZPM？'
    assert service.store.get('task', running['id'])['status'] == 'interrupted'
    assert service.store.get('worker', 'algorithm:r')['status'] == 'interrupted'
    assert not service.store.all('run')


def test_repair_child_owns_progress_despite_late_root_events(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    w, _ = asyncio.run(controller.start(item))
    root = service.store.get('run', w['run_id'])
    child = {**root, 'id':'repair-child', 'repair_parent_id':root['id'], 'status':'running'}
    service.store.put('run', child)
    w.update(active_run_id=child['id'], repair_run_id=child['id'], status='running')
    service.store.put('workflow', w)
    controller.run_changed(child)
    controller.review_changed({'id':'parent-review', 'session_id':item['session_id'], 'run_id':root['id'],
        'status':'completed', 'repair':{'status':'submitted', 'run_id':child['id']}})
    controller.run_changed({**root, 'status':'succeeded'})
    assert service.store.get('workflow', w['id'])['status'] == 'running'
    child['status'] = 'succeeded'
    controller.run_changed(child)
    final = {'id':'child-review', 'session_id':item['session_id'], 'run_id':child['id'], 'status':'completed'}
    controller.review_changed(final)
    done = service.store.get('workflow', w['id'])
    controller.review_changed(final)
    controller.run_changed({**child, 'status':'running'})
    assert done == service.store.get('workflow', w['id'])
    assert done['review_id'] == 'child-review' and done['status'] == 'completed'


def test_task_snapshot_is_frozen_and_history_reads_are_scoped(tmp_path):
    service, controller, item, other = fixture(tmp_path)
    w, _ = asyncio.run(controller.start(item))
    run = service.store.get('run', w['run_id'])
    snapshot = copy.deepcopy(run['task_context'])
    task = service.store.get('task', w['task_id'])
    task['objective'] = 'later goal'
    service.store.put('task', task)
    assert service.store.get('run', run['id'])['task_context'] == snapshot
    assert service.coordinator.read_turns(item['session_id'], [item['id']])['turns']
    with pytest.raises(ValueError, match='本会话'):
        service.coordinator.read_turns(other, [item['id']])
    with pytest.raises(ValueError):
        service.coordinator.read_turns(item['session_id'], [item['id']], -1)
    with pytest.raises(ValueError, match='任务目标'):
        task['revision'] += 1
        service.store.put('task', task)
        service.authorize_agent(item['session_id'], item['id'])


def test_cancel_queued_work_updates_child_and_parent_states(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    w, _ = asyncio.run(controller.start(item))
    asyncio.run(controller.cancel(item['session_id'], w['id']))
    assert service.store.get('task', w['task_id'])['status'] == 'cancelled'
    assert service.store.get('worker', 'algorithm:' + w['run_id'])['status'] == 'cancelled'
    assert service.coordinator.active(item['session_id'], 'p') is None


def test_discussion_does_not_revoke_existing_execution_choice(tmp_path):
    from backend.models import WorkflowChoice
    service, controller, item, _ = fixture(tmp_path, conflict=True)
    waiting, _ = asyncio.run(controller.start(item))
    query = {**item, 'id':'question', 'content':'解释一下当前数据'}
    controller.model = IntentModel(action='query')
    asyncio.run(controller.start(query))
    resumed = controller.resolve(item['session_id'], waiting['id'], WorkflowChoice(
        request_id='choose', option_id='formal_only', version=waiting['version']))
    assert resumed['status'] == 'submitted'
    assert len(service.store.all('run')) == 1


def test_repair_ownership_transfer_rolls_back_with_failed_child_write(tmp_path):
    import sqlite3
    from test_review import fixture_run, approved_review
    service, sid, _, run, _ = fixture_run(tmp_path)
    w = {'id':'root-workflow', 'session_id':sid, 'status':'reviewing', 'run_id':run['id']}
    run['workflow_id'] = w['id']
    service.store.put('run', run)
    service.store.put('workflow', w)
    session = service.store.get('session', sid)
    session['latest_workflow_id'] = w['id']
    service.store.put('session', session)
    evidence, result, review = approved_review(service, run)
    service.store.db.execute("CREATE TRIGGER reject_child BEFORE INSERT ON events WHEN json_extract(NEW.data, '$.type')='run' BEGIN SELECT RAISE(ABORT, 'injected'); END")
    service.store.db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        service.submit_repair(run['id'], review['id'], evidence.repair_patch(result))
    assert service.store.get('workflow', w['id']) == w
    assert service.store.get('run', run['id'])['repair_count'] == 0
    assert len(service.store.all('run')) == 1
