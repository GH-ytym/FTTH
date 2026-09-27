import asyncio

from backend.rule_checks import Checks, capacity
from backend.review import Evidence
from backend.review_chain import prepare_checks
from test_review import fixture_run


def test_workbook_provenance_and_all_rules():
    c = Checks()
    assert len(c.rules) == 39
    assert len(c.pack["sha256"]) == 64
    assert c.rules["7.2"]["source"] == "校验规则!A40:J40"


def test_directed_capacity_is_not_demand_or_reverse_cable():
    rows = {"SITE": [{"CODE": "PM1", "TYPE": "PM"}],
        "BOITE": [{"CODE": "PBO1", "TYPE": "PBO", "REF_PM": "PM1", "NB_FIBRE_UTIL": 10, "CAPACITE": 48}],
        "CABLE": [{"CODE": "C1", "TYPE_CABLE": "DISTRIBUTION", "ORIGINE": "PBO1", "EXTREMITE": "PM1", "REF_PM": "PM1", "CAPACITE": 96}],
        "ZPM": [{"CODE": "PM1"}]}
    c = Checks(); c.relations(rows)
    assert next(r for r in c.results if r["rule_id"] == "7.1")["status"] == "pass"
    assert next(r for r in c.results if r["rule_id"] == "7.2")["status"] == "fail"
    rows["CABLE"][0].update(ORIGINE="PM1", EXTREMITE="PBO1")
    c = Checks(); c.relations(rows)
    assert next(r for r in c.results if r["rule_id"] == "7.2")["status"] == "pass"
    assert capacity("48FO") == 48
    assert capacity("2x24") is None


def test_unknown_dictionary_and_conflicts_are_not_errors():
    c = Checks()
    c.fields("ZPM", [{"CODE": "Z", "REF_SRO": "S"}], ["CODE", "REF_SRO"])
    assert all(x["status"] == "pending" for x in c.results if 'REF_SRO' in x["description"] or 'REF_PM' in x["description"])
    assert not any(x["status"] == "fail" and '枚举全集' in x["description"] for x in c.results)


def test_missing_desktop_never_passes_and_chain_does_not_plan(tmp_path):
    service, sid, _, run, root = fixture_run(tmp_path, scope="partition", search=False)
    e = Evidence(service, run)
    directory = tmp_path / "review"; directory.mkdir()
    asyncio.run(prepare_checks(service, e, directory))
    assert e.items["qgis_state"]["desktop_observed"] is False
    assert e.warning and len(service.store.all("run")) == 1
    assert (directory / "workbook_checks.json").exists()


def test_fresh_matching_desktop_state_only(tmp_path):
    import time
    service, sid, _, run, root = fixture_run(tmp_path, scope="partition", search=False)
    session = service.store.get("session", sid)
    session["qgis_state"] = {"run_id": "other", "received": time.time(), "project_crs": "EPSG:4326", "layers": []}
    service.store.put("session", session)
    directory = tmp_path / "review"; directory.mkdir()
    e = Evidence(service, run); asyncio.run(prepare_checks(service, e, directory))
    assert not e.items["qgis_state"]["desktop_observed"]
    session["qgis_state"].update(run_id=run["id"], layers=[{"name":"SITE", "valid":True, "modified":True,"crs":"EPSG:4326"}])
    service.store.put("session", session)
    e = Evidence(service, run); asyncio.run(prepare_checks(service, e, directory))
    assert e.items["qgis_state"]["desktop_observed"] and e.items["qgis_state"]["status"] == "REVIEW_REQUIRED"


def test_model_cannot_promote_pending_rule_to_hard_error(tmp_path):
    from backend.review import ReviewResult
    service, _, _, run, _ = fixture_run(tmp_path, search=False)
    e = Evidence(service,run)
    e.add('workbook_checks', {'groups':[{'rule_id':'4.11','counts':{'pending':1},'examples':[]}]})
    result = ReviewResult(verdict='fail',summary='规则待确认', findings=[{'category':'contract','severity':'error','description':'REF_PM','rule_ids':['4.11'],'evidence_ids':['workbook_checks']}])
    result = e.enforce(result, set(e.items))
    assert result.findings[0].severity == 'warning' and result.verdict == 'needs_review'
