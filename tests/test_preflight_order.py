import asyncio
from backend.models import RunInput
from test_workflow import fixture


def test_input_errors_stop_before_planning_and_persist_check(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    p = service.store.get('project', 'p')
    p['summary']['rule_preflight'].update(status='FAIL', results=[{'rule_id':'input.code_unique','severity':'error','description':'IMB CODE 重复','object_ids':['I1']}])
    service.store.put('project', p)
    w, text = asyncio.run(controller.start(item))
    assert w['status'] == 'blocked' and '预检未通过' in text
    assert not service.store.all('run')
    checks = service.store.all('check')
    assert len(checks) == 1 and checks[0]['status'] == 'failed'
    assert not service.validate(item['session_id'], RunInput(request_id='direct'))['valid']


def test_input_warnings_do_not_block_and_precede_algorithm_submission(tmp_path):
    service, controller, item, _ = fixture(tmp_path)
    p = service.store.get('project', 'p')
    p['summary']['rule_preflight']['results'] = [{'rule_id':'input.crs_difference','severity':'warning','description':'转换后复核 CRS','object_ids':[]}]
    service.store.put('project', p)
    w, _ = asyncio.run(controller.start(item))
    assert w['status'] == 'submitted'
    events = service.store.events(item['session_id'], 0)
    check_index = next(i for i,e in enumerate(events) if e['type']=='check')
    run_index = next(i for i,e in enumerate(events) if e['type']=='run')
    assert check_index < run_index
    assert len(service.store.all('run')) == 1


def test_legacy_preflight_cached_without_recopying_inputs(tmp_path):
    import hashlib,json
    service, controller, item, _ = fixture(tmp_path)
    config = tmp_path / 'config.json'; config.write_text('{}')
    p = service.store.get('project','p')
    p.update(config_path=str(config),hashes={'config.json':hashlib.sha256(config.read_bytes()).hexdigest()})
    p['summary'].pop('rule_preflight')
    service.store.put('project',p)
    called=[]
    async def worker(args, log):
        called.append(args)
        return 0, 'DEMO_RESULT='+json.dumps({'version':'ftth-input-preflight-v1','status':'PASS','results':[]})
    service.process=worker
    async def scenario():
        assert (await service.ensure_input_preflight(p))['status']=='PASS'
        await service.ensure_input_preflight(p)
    asyncio.run(scenario())
    assert len(called)==1 and called[0][1]=='preflight'
    assert len(service.store.all('project'))==1 and config.read_text()=='{}'
