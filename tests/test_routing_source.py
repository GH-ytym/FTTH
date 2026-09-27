import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('use_roads', [True, False])
def test_task6_uses_roads_exclusively_or_formal_fallback(tmp_path, use_roads):
    path = Path(__file__).resolve().parents[1] / 'vendor/ftth/downstream_controller.py'
    spec = importlib.util.spec_from_file_location('routing_controller_test', path)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    inputs = tmp_path / 'task3/input'
    inputs.mkdir(parents=True)
    (inputs / 'IMB.csv').write_text('CODE,X,Y,NB_LOC_TOT\nI,0,0,10\n', encoding='utf-8')
    fixed = tmp_path / 'fixed.json'
    fixed.write_text('{"pm_sites": []}', encoding='utf-8')
    topology = {'edges': [{'code': 'FORMAL', 'path': [[0, 0], [1, 0]]}]}
    payload = {'imb_points': [{'x': 0, 'y': 0}],
               'recognition': {'roads': [{'id': 'ROAD', 'coordinates': [[0, 0], [1, 0]]}]}}
    target = controller._task6_base(tmp_path, topology, payload, {}, fixed,
        use_formal_infrastructure=True, use_recognition_roads=use_roads,
        formal_duct_capacity=2048, candidate_paths=5, water_cost_factor=2,
        ptech_max_spacing_m=200, terminal_snap_distance_m=80)
    roads = json.loads(target.read_text(encoding='utf-8'))['remote_sensing']['roads']
    assert len(roads) == 1
    assert (roads[0]['id'] == 'FORMAL') is (not use_roads)
    audit = json.loads((tmp_path / 'task6/base_input_audit.json').read_text(encoding='utf-8'))
    assert audit['formal_infrastructure_used'] is (not use_roads)
    assert audit['recognition_roads_used'] is use_roads
