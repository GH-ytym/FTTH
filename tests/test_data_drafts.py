import asyncio
import pytest
from backend.models import DataDraftInput
from backend.data_drafts import DraftConflict
from test_service import setup_service


def test_partial_draft_versions_and_snapshot_reuse(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.data_draft(sid)
        saved = service.save_data_draft(sid, DataDraftInput(revision=0, sources={'IMB': {'path': 'new.shp'}}))
        assert saved['revision'] == 1
        session = service.store.get('session', sid)
        assert session['policy'] == {'use_formal_infrastructure': True, 'use_recognition_roads': True,
                                     'split_oversized_imbs': True}
        assert session['policy_data_revision'] == saved['revision']
        version = session['policy_version']
        with pytest.raises(DraftConflict):
            service.save_data_draft(sid, DataDraftInput(revision=0))
        assert service.store.get('session', sid)['policy_version'] == version
        calls = []
        async def register(spec):
            calls.append(spec)
            return service.store.put('project', {'id': 'new', 'summary': {'infrastructure_count': 0}, 'recognition_path': None})
        service.register = register
        assert (await service.prepare_data(sid, 1))['id'] == 'new'
        assert (await service.prepare_data(sid, 1))['id'] == 'new'
        assert len(calls) == 1
        assert service.store.get('project', 'p')['id'] == 'p'
        await service.close()
    asyncio.run(scenario())


def test_changed_during_snapshot_never_binds_stale_project(tmp_path):
    async def scenario():
        service, sid, _ = setup_service(tmp_path)
        service.save_data_draft(sid, DataDraftInput(revision=0, sources={'IMB': {'path': 'a'}}))
        async def register(spec):
            service.save_data_draft(sid, DataDraftInput(revision=1, sources={'IMB': {'path': 'b'}}))
            return {'id': 'stale'}
        service.register = register
        with pytest.raises(DraftConflict):
            await service.prepare_data(sid, 1)
        assert service.store.get('session', sid)['project_id'] == 'p'
        assert service.data_draft(sid)['sources']['IMB']['path'] == 'b'
        await service.close()
    asyncio.run(scenario())


def test_failed_check_is_persisted_without_old_data_fallback(tmp_path):
    async def scenario():
        service, sid, other = setup_service(tmp_path)
        service.save_data_draft(sid, DataDraftInput(revision=0))
        value = await service.check_data(sid, expected_revision=1)
        assert value['valid'] is False
        record = service.owned_check(sid, value['check_id'])
        assert record['status'] == 'failed' and record['data_revision'] == 1
        with pytest.raises(ValueError):
            service.owned_check(other, record['id'])
        await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('layer', [None, '', 'GEO_ROAD'])
def test_optional_source_layer_saves_via_http(tmp_path, layer):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.settings import Settings
    settings = Settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        client.headers['Authorization'] = 'Bearer ' + settings.token
        sid = client.post('/sessions', json={}).json()['id']
        payload = {'revision':0, 'sources':{n:{'path':n + '.shp', 'source_layer':layer} for n in ('IMB','SITE')}}
        response = client.post(f'/sessions/{sid}/data', json=payload)
        assert response.status_code == 200, response.text
        saved = client.get(f'/sessions/{sid}/data').json()
        assert saved['revision'] == 1
        assert all(s['source_layer'] == (layer or '') for s in saved['sources'].values())
        assert client.get(f'/sessions/{sid}/runs').json() == []
        for bad in (123, False, [], {}):
            payload['revision'] = 1
            payload['sources']['IMB']['source_layer'] = bad
            assert client.post(f'/sessions/{sid}/data', json=payload).status_code == 422
        assert client.get(f'/sessions/{sid}/data').json()['revision'] == 1


def test_real_plugin_payload_with_blank_layer_names_is_valid():
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    tree = ast.parse((Path(__file__).parents[1] / 'qgis_plugin/ftth_demo0/panel.py').read_text(encoding='utf-8'))
    panel = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Panel')
    method = next(n for n in panel.body if isinstance(n, ast.FunctionDef) and n.name == 'form_inputs')
    namespace = {'BUSINESS_LAYERS':('IMB','SITE')}
    exec(compile(ast.Module(body=[method],type_ignores=[]),'actual-plugin-payload','exec'),namespace)
    widget = lambda text: SimpleNamespace(text=lambda:text)
    form = SimpleNamespace(sources={n:widget(n+'.shp') for n in namespace['BUSINESS_LAYERS']},
                           sublayers={n:widget('') for n in namespace['BUSINESS_LAYERS']})
    sources = namespace['form_inputs'](form)
    assert all(s['source_layer'] == '' for s in sources.values())
    assert DataDraftInput(revision=0,sources=sources).sources['IMB'].source_layer == ''
