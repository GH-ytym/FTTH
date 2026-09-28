from backend.review_wording import describe_rule


def test_capacity_wording_reports_only_failing_objects_without_json():
    text = describe_rule('7.2', [{'object_ids': ['ZPM_002'], 'totals': [
        {'pm': 'ZPM_001', 'pbo_capacity': 672, 'outgoing_cable_capacity': 720},
        {'pm': 'ZPM_002', 'pbo_capacity': 648, 'outgoing_cable_capacity': 576}]}])
    assert 'ZPM_001' not in text
    assert '`ZPM_002`' in text and '缺口 72' in text and '额定容量' in text
    assert '{' not in text and 'pbo_capacity' not in text


def test_overlap_wording_retains_measurement_and_uncertainty():
    text = describe_rule('6.2', [{'measured_overlaps': [
        {'objects': ['ZPM_001', 'ZPM_002'], 'area_m2': .000380086997,
         'area_crs_units_squared': 3.65e-14}]}])
    assert '0.000380087 平方米' in text and '未通过' in text
    assert '不能仅据此判断' in text
