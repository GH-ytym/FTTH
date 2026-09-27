"""Mandatory deterministic audit followed by bounded, read-only review tools."""
import asyncio
import hashlib
import os
import json
import time
from pathlib import Path

from .rule_checks import PACK_PATH


async def prepare_checks(service, evidence, directory):
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    root = Path(evidence.run.get("result", {}).get("output_dir", "")).resolve()
    report = {"status": "REVIEW_REQUIRED", "counts": {"unavailable": 1}, "results": [{"rule_id": "artifacts", "status": "unavailable", "severity": "warning", "description": "本次没有可读取的完整成果，未执行八图层交付检查", "object_ids": []}]}
    if evidence.run.get('scope') != 'full':
        report.update(status='PASS', counts={'not_applicable':1}, results=[{'rule_id':'artifacts', 'status':'not_applicable', 'severity':'info', 'description':'仅分区规划不适用八图层交付规则', 'object_ids':[]}])
    if evidence.run.get("scope") == "full" and root.is_relative_to(evidence.root) and not evidence.has_link(root):
        final = root / "final_layers"
        if final.is_dir() and not evidence.has_link(final):
            # Reject links anywhere in the small fixed artifact directory.
            if any(evidence.has_link(p) for p in final.iterdir()):
                raise ValueError("成果目录包含链接，不能建立只读审查快照")
            target = directory / "workbook_checks.json"
            try:
                code, output = await asyncio.wait_for(service.process([str(Path(__file__).resolve().parents[1] / "scripts/review_worker.py"), str(final), str(target)], directory / "rule-worker.log"), timeout=120)
                if code == 0 and target.is_file():
                    report = json.loads(target.read_text(encoding="utf-8"))
                    for item in report.get("artifact_manifest", []):
                        path = final / item["file"]
                        if path.name != item["file"] or evidence.has_link(path):
                            raise ValueError("成果清单路径无效")
                        evidence.files["artifact:" + item["file"]] = {"path": str(path), "sha256": item["sha256"], "size": item["bytes"]}
                else:
                    report["results"][0]["description"] = "QGIS 规则检查进程未完成，不能声称成果校验通过；参见审查工具日志"
            except (TimeoutError, OSError, ValueError) as exc:
                report["results"][0]["description"] = "QGIS 规则检查不可用：" + type(exc).__name__
    session = service.store.get("session", evidence.run["session_id"])
    desktop = session.get("qgis_states", {}).get(evidence.run["id"], session.get("qgis_state"))
    state = {"status": "REVIEW_REQUIRED", "desktop_observed": False, "reason": "没有本批次的新鲜桌面状态，未检查桌面项目", "runtime": report.get("qgis_runtime", {})}
    if desktop and desktop.get("run_id") == evidence.run["id"] and 0 <= time.time() - desktop.get("received", 0) < 60:
        state.update(desktop_observed=True, desktop=desktop, reason="只读桌面状态；与磁盘成果校验分别报告")
        relevant = desktop.get("layers", [])
        bad = [l["name"] for l in relevant if not l["valid"] or l["modified"] or l["crs"] != desktop["project_crs"]]
        expected_layers = report.get('qgis_runtime', {}).get('loaded_layers', {})
        for layer in relevant:
            name = layer['name']
            expected_hash = hashlib.sha256(os.path.normcase(str(root / 'final_layers' / (name + '.shp'))).encode('utf-8')).hexdigest()
            if name not in expected_layers or layer.get('source_hash') != expected_hash:
                bad.append(name + ':数据源不匹配或未验证')
            elif layer.get('count') != expected_layers[name]['count']:
                bad.append(name + ':要素数量不一致')
        if set(expected_layers) - {l['name'] for l in relevant}:
            bad.append('桌面未加载全部本批次成果层')
        state.update(status="REVIEW_REQUIRED" if bad or not relevant else "PASS", issues=bad)
    evidence.add("workbook_rules", {"version": pack["version"], "sha256": pack["sha256"], "scope": "最终成果交付检查；不用于阻止道路模式原始输入", "rules": [{"id": r["id"], "source": r["source"], "description": r["cells"][3] or r["cells"][6]} for r in pack["rules"]]})
    # A compact index always fits the evidence budget. Details are paginated tools.
    evidence.rule_report = report
    grouped = {}
    for item in report["results"]:
        group = grouped.setdefault(item["rule_id"], {"rule_id": item["rule_id"], "counts": {}, "examples": []})
        status = item["status"]
        group["counts"][status] = group["counts"].get(status, 0) + 1
        if status == "fail" or (status != "pass" and len(group["examples"]) < 2):
            group["examples"].append(item)
    evidence.add("workbook_checks", {"status": report["status"], "counts": report["counts"], "groups": list(grouped.values()), "detail_count": len(report["results"]), "details_tool": "read_rule_findings"})
    evidence.add("qgis_state", state)
    evidence.failed |= report["status"] == "FAIL"
    evidence.warning |= report["status"] == "REVIEW_REQUIRED" or state["status"] != "PASS"
    from .review import fingerprint
    evidence.digest = fingerprint({"items": evidence.items, "files": evidence.files, "missing": evidence.missing})
    (directory / "workbook_checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
