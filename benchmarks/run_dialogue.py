"""Isolated dialogue benchmark: structural checks and separate human semantics.

No requests go to the user's backend. Real mode uses terminal model settings.
The react-readonly ablation removes semantic routing, not business safeguards.
"""
import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.agent import AgentController
from backend.models import Parameters, PolicyInput
from backend.service import Service
from backend.settings import Settings
from backend.store import Store
from backend.workflow import WorkflowController


class ReadonlyAblation(WorkflowController):
    """Benchmark-only bypass; ordinary conversation is resolved in one tool loop."""
    async def start(self, item):
        # No synthetic semantic goal or Intent is given to the answer model.
        return {'status':'completed', 'intent':None}, None


class SmokeModel(BaseChatModel):
    @property
    def _llm_type(self):
        return 'benchmark-harness-smoke'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if '需求理解阶段' in messages[0].content:
            message = AIMessage(content='', tool_calls=[{'name':'Intent','id':'intent','args':{'action':'query'}}])
        elif not isinstance(messages[-1], ToolMessage):
            message = AIMessage(content='', tool_calls=[{'name':'read_algorithm_reference','id':'reference','args':{'topic':'distance'}}])
        else:
            message = AIMessage(content='工具循环自检文本，不代表实际模型正确回答。')
        return ChatResult(generations=[ChatGeneration(message=message)])


def check_turn(turn, result, architecture):
    checks = {'completed': result['status'] == 'succeeded',
        'no_side_effects': not result['side_effect_attempts'],
        'no_question_echo': result['response'].strip() != turn['input'].strip()}
    # Architecture-specific diagnostics are separate from comparable checks.
    routing = {}
    if architecture == 'routed' or result.get('intent_only'):
        routing['action'] = result['intent'].get('action') in turn['actions']
        if 'parameters' in turn:
            params = result['intent'].get('parameter_overrides', {})
            routing['parameters'] = (params == {} if not turn['parameters'] else
                all(params.get(k) == v for k, v in turn['parameters'].items()))
    return checks, routing


def summarize(rows):
    completed = [r for r in rows if not r.get('skipped')]
    latency = sorted(r['seconds'] for r in completed)
    semantic = [r for r in completed if r['rubric']]
    reviewed = [r for r in semantic if isinstance(r.get('semantic_grade'), dict)
        and type(r['semantic_grade'].get('pass')) is bool and r['semantic_grade'].get('reviewer')
        and r['semantic_grade'].get('reason')]
    return {'turns':len(completed), 'skipped':len(rows)-len(completed),
        'structural_passes':sum(all(r['checks'].values()) for r in completed),
        'routing_checks_passed':sum(all(r['routing'].values()) for r in completed if r['routing']),
        'routing_turns':sum(bool(r['routing']) for r in completed),
        'semantic_review_required':len(semantic)-len(reviewed),
        'semantic_reviewed':len(reviewed),
        'semantic_pass_rate':(sum(r['semantic_grade']['pass'] for r in reviewed)/len(reviewed)
            if reviewed and len(reviewed)==len(semantic) else None),
        'p50_seconds':statistics.median(latency) if latency else None,
        'p95_seconds':latency[max(0,math.ceil(len(latency)*.95)-1)] if latency else None,
        'answer_model_calls':sum(r['answer_model_calls'] for r in completed),
        'tool_calls':sum(len(r['tools']) for r in completed)}


async def run_case(case, architecture, smoke, model=None):
    rows = []
    if architecture == 'react-readonly' and case.get('intent_only'):
        return [{'case_id':case['id'], 'skipped':True, 'reason':'只读消融不评执行路由'}]
    with tempfile.TemporaryDirectory(prefix='ftth-benchmark-') as directory:
        root = Path(directory)
        settings = Settings(root)
        service = Service(settings, Store(root/'app.sqlite'))
        attempts = []
        def blocked(name):
            def deny(*args, **kwargs):
                attempts.append(name)
                raise ValueError('benchmark intercepted external action')
            return deny
        for name in ('submit', 'export', 'show', 'submit_repair'):
            setattr(service, name, blocked(name))
        async def cancel(*args, **kwargs):
            attempts.append('cancel')
            raise ValueError('benchmark intercepted cancel')
        service.cancel = cancel
        def no_spawn(coro):
            attempts.append('background_job')
            coro.close()
        service.spawn = no_spawn
        try:
            project_id = 'synthetic' if case['project'] else None
            if project_id:
                service.store.put('project', {'id':project_id, 'name':'benchmark synthetic fixture',
                    'recognition_path':None, 'summary':{'imb_count':51,'nro_count':1,
                    'infrastructure_count':193,'oversized_imbs':[], 'total_ports':1018,
                    'crs':'EPSG:4326','synthetic':True}})
            sid = service.session(project_id)['id']
            if project_id:
                service.set_policy(sid, PolicyInput(use_formal_infrastructure=True,
                    use_recognition_roads=False, split_oversized_imbs=True))
            if case.get('seed_successful_run'):
                if not project_id or not case.get('intent_only'):
                    raise ValueError('Synthetic successful runs require a project and intent_only')
                # Routing fixture only: no computed outputs or exportable files exist.
                service.store.put('run', {'id':'seed-successful-full','session_id':sid,
                    'project_id':project_id,'kind':'planning','scope':'full',
                    'status':'succeeded','parameters':Parameters().model_dump(),
                    'created':0,'synthetic':True})
            artifact_baseline = {kind: service.store.all(kind) for kind in ('run', 'display')}
            for index, pair in enumerate(case.get('history', [])):
                service.store.put('message', {'id':'seed-'+str(index),'session_id':sid,'project_id':project_id,
                    'status':'succeeded','content':pair['user'],'response':pair['assistant'],'created':index})
            async with AsyncSqliteSaver.from_conn_string(str(root/'memory.sqlite')) as saver:
                agent = AgentController(service, saver, model if model is not None else SmokeModel() if smoke else None)
                if architecture == 'react-readonly':
                    agent.workflows = ReadonlyAblation(service)
                for index, turn in enumerate(case['turns']):
                    item = {'id':'turn-'+str(index),'session_id':sid,'project_id':project_id,
                        'content':turn['input'],'created':time.time()}
                    before = len(attempts)
                    started = time.monotonic()
                    intent = {}
                    if case.get('intent_only'):
                        try:
                            intent = (await agent.workflows.interpret(item)).model_dump()
                            saved = {'status':'succeeded','response':''}
                        except Exception as exc:
                            saved = {'status':'failed','response':type(exc).__name__}
                    else:
                        await agent.respond(item)
                        saved = service.store.get('message',item['id'])
                        if saved.get('workflow_id'):
                            flow = service.store.get('workflow',saved['workflow_id'])
                            intent = flow.get('intent', {})
                    events = [json.loads(row[0]) for row in service.store.db.execute(
                        'SELECT data FROM events WHERE session=? ORDER BY id', (sid,))]
                    activity = [e for e in events if e.get('message_id')==item['id'] and e['type']=='activity' and e.get('status')=='running']
                    trace = await saver.aget({'configurable':{'thread_id':sid}})
                    tool_trace = []
                    trace_messages = (trace or {}).get('channel_values', {}).get('messages', [])
                    current = next((i for i, m in enumerate(trace_messages) if m.id == 'user_'+item['id']), None)
                    for message in trace_messages[current+1:] if current is not None else []:
                        if isinstance(message, ToolMessage):
                            tool_trace.append({'tool':message.name,'output':str(message.content)})
                        elif isinstance(message, AIMessage) and message.tool_calls:
                            tool_trace.append({'calls':message.tool_calls})
                    row = {'case_id':case['id'],'split':case['split'],'turn':index,'input':turn['input'],
                        'status':saved['status'],'response':saved.get('response',''),'intent':intent,
                        'intent_only':case.get('intent_only',False), 'seconds':round(time.monotonic()-started,3),
                        'side_effect_attempts':attempts[before:], 'rubric':turn['rubric'],
                        'tools':[e['name'] for e in activity if e['name']!='model_response'],
                        'answer_model_calls':sum(e['name']=='model_response' for e in activity),
                        'tool_trace':tool_trace, 'semantic_grade':None}
                    if saved.get('task_id'):
                        row['task'] = service.coordinator.snapshot(service.store.get('task',saved['task_id']))
                    if any(service.store.all(kind) != baseline for kind, baseline in artifact_baseline.items()):
                        row['side_effect_attempts'].append('unexpected_durable_artifact')
                    row['checks'], row['routing'] = check_turn(turn,row,architecture)
                    rows.append(row)
        finally:
            await service.close()
            service.store.db.close()
    return rows


async def main(args):
    data = json.loads(args.dataset.read_text(encoding='utf-8'))
    unknown = set(args.case or []) - {c['id'] for c in data['cases']}
    if unknown:
        raise SystemExit('未知用例：'+', '.join(sorted(unknown)))
    selected = [c for c in data['cases'] if not args.case or c['id'] in args.case]
    if not selected:
        raise SystemExit('没有匹配用例')
    with tempfile.TemporaryDirectory(prefix='ftth-benchmark-config-') as directory:
        settings = Settings(directory)
        if not args.smoke and not settings.model_ready:
            raise SystemExit('当前终端没有模型配置；请在配置模型的终端运行 --real-model。--smoke只验证评测管线。')
        metadata = {'provider':settings.provider,'model':settings.model,'model_api':settings.model_api,
            'thinking':settings.enable_thinking}
    report = {'dataset':data['version'], 'dataset_sha256':hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        'architecture':args.architecture,'model':metadata,'mode':'smoke' if args.smoke else 'real',
        'repeats':args.repeat, 'rows':[],
        'source_sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'backend').glob('*.py'))},
        'dependencies':{name:version(name) for name in ('langchain','langgraph','langchain-openai')},
        'notes':['结构通过不等于语义正确；semantic_grade保留待人工审核。',
                 '只保存可见回答与工具调用/结果，不保存私有推理。',
                 'answer_model_calls不含前置意图识别；每个routed普通轮额外一次解释调用（结构纠正可能增加）。']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for repeat in range(args.repeat):
        for case in selected:
            rows = await run_case(case,args.architecture,args.smoke)
            for row in rows:
                row['repeat'] = repeat
            report['rows'].extend(rows)
            report['summary'] = summarize(report['rows'])
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"{repeat+1}/{args.repeat} {case['id']} saved",flush=True)
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--real-model',action='store_true')
    mode.add_argument('--smoke',action='store_true')
    parser.add_argument('--architecture',choices=['routed','react-readonly'],default='routed')
    parser.add_argument('--dataset',type=Path,default=ROOT/'benchmarks/dialogue_v1.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',action='append')
    parser.add_argument('--repeat',type=int,default=1)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error('--repeat必须为1至20')
    report = asyncio.run(main(args))
    if not args.smoke and any(not all(r['checks'].values()) or not all(r['routing'].values()) for r in report['rows'] if not r.get('skipped')):
        raise SystemExit(1)
