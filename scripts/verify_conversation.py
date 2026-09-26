"""Isolated real-provider replay of the reported clarification failure.

Uses the calling terminal's model settings and a synthetic project; never runs
the planning algorithm. Saves actual answers for human semantic assessment.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.agent import AgentController
from backend.settings import Settings
from test_service import setup_service
from test_conversation_context import QUESTION, EXPLICIT


async def verify():
    with tempfile.TemporaryDirectory(prefix='ftth-context-') as directory:
        root = Path(directory)
        if not Settings(root).model_ready:
            raise SystemExit('当前终端没有模型配置，未执行真实模型验收。请在启动后端的模型配置终端运行本脚本。')
        service, sid, _ = setup_service(root)
        forbidden = []
        def no_background(coro):
            forbidden.append(coro.cr_code.co_name)
            coro.close()
        service.spawn = no_background
        answers = []
        try:
            async with AsyncSqliteSaver.from_conn_string(str(root / 'memory.sqlite')) as saver:
                agent = AgentController(service, saver)
                for i, content in enumerate(['检查当前数据，先不要运行规划。', QUESTION, 'NRO', EXPLICIT]):
                    item = {'id': 'case-' + str(i), 'session_id': sid, 'project_id': 'p',
                            'content': content, 'created': time.time()}
                    await agent.respond(item)
                    saved = service.store.get('message', item['id'])
                    answers.append({k: saved.get(k) for k in ('content', 'response', 'status', 'task_id', 'task_revision')})
                    assert saved['status'] == 'succeeded', answers[-1]
                    assert not forbidden and not service.store.all('run') and not service.store.all('display'), '讨论请求错误触发执行'
                    if i >= 2:
                        flow = service.store.get('workflow', saved['workflow_id'])
                        assert flow['intent']['action'] in {'discuss', 'query'}, flow['intent']
                        task = service.coordinator.context(item)
                        assert task['parameters'].get('nro_max_cap') == 600, task
                        assert task['mode'] == 'read_only', task
                        assert '600' in saved['response'] and 'NRO' in saved['response'].upper(), answers[-1]
        finally:
            output = ROOT / 'runtime/conversation-real-model.json'
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps({'fixture': 'synthetic', 'turns': answers,
                'note': '自动断言不替代人工核对影响分析是否正确、是否重复旧答案。'}, ensure_ascii=False, indent=2), encoding='utf-8')
            await service.close()
        print('PASS: 结构与禁止执行断言通过；请核对实际回答：', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-model', action='store_true', required=True)
    parser.parse_args()
    asyncio.run(verify())
