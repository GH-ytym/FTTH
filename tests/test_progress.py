import asyncio
import json
from backend.progress import algorithm_step
from test_service import setup_service


def test_stage_mapping():
    assert algorithm_step('[bridge] 任务四初算：建立关系')['stage'] == 'task4_initial'
    assert algorithm_step('[bridge] 任务三：依据道路反馈优化 PM')['stage'] == 'task3_ga'
    assert algorithm_step('[bridge] 任务四定稿：锁定 PM')['stage'] == 'task4_final'
    assert algorithm_step('[bridge] 任务六：使用定稿服务关系')['stage'] == 'task6'
    assert algorithm_step('[bridge] 任务五：生成完整光缆拓扑')['stage'] == 'task5'
    assert algorithm_step('unrelated log') is None


def test_actual_stdout_emits_structured_stages(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.store.put('run', {'id':'test', 'session_id':sid, 'status':'running'})
        lines = ['[bridge] 任务四初算：建立关系', '[bridge] 任务三：优化 PM', '[bridge] 任务六：管线路由']
        script = tmp_path / 'progress_fixture.py'
        script.write_text('import time\nfor line in ' + repr(lines) + ':\n print(line, flush=True)\n time.sleep(.03)\n', encoding='utf-8')
        code, _ = await service.process([str(script)], tmp_path / 'run.log', 'test')
        assert code == 0
        events = [e for e in service.store.events(sid, 0) if e['type'] == 'algorithm']
        assert [e['stage'] for e in events if e['status'] == 'running'] == ['task4_initial','task3_ga','task6']
        assert len([e for e in events if e['status'] == 'finished']) == 3
        assert all(e['elapsed_seconds'] >= 0 for e in events if 'elapsed_seconds' in e)
        assert service.store.get('run', 'test')['current_algorithm']['stage'] == 'task6'
    asyncio.run(scenario())
