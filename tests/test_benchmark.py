import asyncio
import copy
import json
from pathlib import Path

import pytest
from benchmarks.run_dialogue import run_case, summarize
from benchmarks.compare import compare
from test_workflow import IntentModel

DATA = json.loads((Path(__file__).resolve().parents[1]/'benchmarks/dialogue_v1.json').read_text(encoding='utf-8'))


def test_harness_detects_wrong_router_and_records_visible_tool_trace():
    rows = asyncio.run(run_case(DATA['cases'][0], 'routed', True))
    assert rows[0]['routing']['action'] is False  # smoke model deliberately always says query
    assert all(row['checks']['no_side_effects'] for row in rows)
    assert any('output' in entry for entry in rows[-1]['tool_trace'])
    assert all(row['semantic_grade'] is None for row in rows)
    summary = summarize(rows)
    assert summary['semantic_pass_rate'] is None and summary['semantic_review_required'] == 3


def test_harness_intercepts_real_service_submission():
    case = {'id':'erroneous-write','split':'test','project':True,'turns':[
        {'input':'执行完整规划','actions':['query'],'rubric':['不能执行']}]}
    rows = asyncio.run(run_case(case,'routed',False,model=IntentModel()))
    assert rows[0]['side_effect_attempts'] == ['submit']
    assert rows[0]['checks']['no_side_effects'] is False


def test_react_ablation_skips_write_routing_and_keeps_actual_tools():
    skipped = asyncio.run(run_case(DATA['cases'][-1],'react-readonly',True))
    assert skipped[0]['skipped']
    rows = asyncio.run(run_case(DATA['cases'][2],'react-readonly',True))
    assert rows[0]['routing'] == {} and rows[0]['intent'] == {}
    assert 'read_algorithm_reference' in rows[0]['tools']


def test_unregistered_fixture_is_supported():
    case = next(c for c in DATA['cases'] if c['id']=='missing_project')
    rows = asyncio.run(run_case(case,'routed',True))
    assert rows[0]['status'] == 'succeeded' and rows[0]['checks']['no_side_effects']


def test_positive_export_has_successful_history_without_creating_artifacts():
    class ExportModel(IntentModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            payload = json.loads(messages[1].content)
            runs = payload['recent_runs']
            assert len(runs) == 1
            assert runs[0]['status'] == 'succeeded' and runs[0]['scope'] == 'full'
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    case = next(c for c in DATA['cases'] if c['id'] == 'positive_export')
    rows = asyncio.run(run_case(case, 'routed', False,
        model=ExportModel(action='export', use_latest=True)))
    assert rows[0]['routing']['action']
    assert rows[0]['checks']['no_side_effects']
    invalid = {**case, 'intent_only':False}
    with pytest.raises(ValueError, match='intent_only'):
        asyncio.run(run_case(invalid, 'routed', True))


def test_effect_comparison_requires_real_matched_cases_and_complete_grades():
    row = {'case_id':'a','repeat':0,'turn':0,'checks':{'completed':True},'routing':{},
        'seconds':1,'tools':[],'answer_model_calls':1,'rubric':['正确'],'semantic_grade':None}
    report = {'mode':'smoke','dataset_sha256':'same','architecture':'routed','model':{},'rows':[row]}
    with pytest.raises(ValueError,match='smoke'):
        compare(report,report)
    report['mode']='real'
    other=copy.deepcopy(report)
    other['architecture']='react-readonly'
    assert compare(report,other)['left']['summary']['semantic_pass_rate'] is None
    other['rows'][0]['semantic_grade']={'pass':False,'reviewer':'human','reason':'混淆分区'}
    assert compare(report,other)['right']['summary']['semantic_pass_rate'] == 0
    other['rows'][0]['turn']=2
    with pytest.raises(ValueError,match='轮次'):
        compare(report,other)
