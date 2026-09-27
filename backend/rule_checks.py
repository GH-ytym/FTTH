"""Deterministic workbook rules. No LLM, no planning, no mutation of GIS data."""
import json
import math
import re
from collections import Counter
from pathlib import Path

PACK_PATH = Path(__file__).parent / "rules" / "ftth_workbook.json"
LAYERS = {"IMB": 0, "SITE": 0, "BOITE": 0, "CABLE": 1, "PTECH": 0, "INFRASTRUCTURE": 1, "ZNRO": 2, "ZPM": 2}
FIELD_RULES = {"IMB": "4.1", "BOITE": "4.3", "CABLE": "4.5", "PTECH": "4.7", "INFRASTRUCTURE": "4.9", "ZPM": "4.11", "ZNRO": "4.13", "SITE": "4.15"}


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def capacity(value):
    direct = number(value)
    if direct is not None:
        return direct
    # One numeric component only: do not guess whether 2x24 means 2 or 48.
    tokens = re.findall(r"[0-9]+(?:\.[0-9]+)?", str(value))
    return float(tokens[0]) if len(tokens) == 1 else None


class Checks:
    def __init__(self, pack=None):
        self.pack = pack or json.loads(PACK_PATH.read_text(encoding="utf-8"))
        self.results = []
        self.rules = {r["id"]: r for r in self.pack["rules"]}

    def add(self, rule, status, description, objects=(), source=None, **details):
        self.results.append({"rule_id": rule, "status": status,
            "severity": {"fail": "error", "pending": "warning", "unavailable": "warning", "pass": "info", "not_applicable": "info"}[status],
            "description": description, "object_ids": [str(x) for x in list(objects)[:30]],
            "object_count": len(objects), "source": source or self.rules.get(rule, {}).get("source", "runtime"), **details})

    def report(self):
        counts = Counter(x["status"] for x in self.results)
        return {"rulepack_version": self.pack["version"], "workbook_sha256": self.pack["sha256"],
            "scope": "final_delivery", "status": "FAIL" if counts["fail"] else "REVIEW_REQUIRED" if counts["pending"] or counts["unavailable"] else "PASS",
            "counts": dict(counts), "results": self.results,
            "coverage": {rid: sorted({x["status"] for x in self.results if x["rule_id"] == rid}) or ["unavailable"] for rid in self.rules}}

    def fields(self, name, records, field_names, aliases=None):
        rule = FIELD_RULES[name]
        aliases = aliases or {}
        definitions = {f["name"]: f for f in self.pack["fields"][name]}
        required = set(re.split(r"[,\n]", self.rules[rule]["cells"][5]))
        for key, spec in definitions.items():
            source = spec["source"]
            actual = key if key in field_names else key[:10] if key[:10] in field_names else aliases.get(key)
            if actual not in field_names:
                actual = None
            # Contradictions must not be silently resolved into a false error.
            disputed = (name == "ZPM" and key == "REF_SRO") or (name == "PTECH" and key == "TYPE_APPUI")
            conditional = spec["required"] == "C" or bool(spec["condition"] and "Si " in str(spec["condition"]))
            mandatory = key in required and spec["required"] == "O" and not conditional
            if disputed:
                self.add(rule, "pending", f"{name}.{key}：规则表与明细字段/类型矛盾，待确认", source=source)
                continue
            if not actual:
                self.add(rule, "fail" if mandatory else "pending", f"{name}.{key} 字段缺失" + ("（必填范围待确认）" if not mandatory else ""), source=source)
                continue
            if actual not in {key, key[:10]}:
                self.add(rule, "pending", f"{name}.{key} 使用适配别名 {actual}，与表中前10字符规则不同", source=source)
            values = [(r.get("CODE", r.get("_fid")), r.get(key)) for r in records]
            blanks = [oid for oid, value in values if value is None or str(value).strip() == ""]
            conflict = (key in required and not mandatory) or (spec["required"] == "O" and key not in required)
            if blanks:
                self.add(rule, "fail" if mandatory else "pending", f"{name}.{key} 有空值" + ("（条件适用性或必填规则待确认）" if conditional or conflict else ""), blanks, source=source)
            invalid = []
            for oid, value in values:
                if value is None or str(value).strip() == "":
                    continue
                kind, length = spec["type"], spec["length"]
                n = number(value)
                if kind in ("Entier", "Double") and (n is None or (kind == "Entier" and not n.is_integer())):
                    invalid.append(oid)
                elif kind == "Texte" and isinstance(length, int) and len(str(value)) > length:
                    invalid.append(oid)
                elif "OUI/NON/NC" in str(spec["format"]) and str(value) not in {"OUI", "NON", "NC"}:
                    invalid.append(oid)
            if invalid:
                self.add(rule, "fail", f"{name}.{key} 值违反明确的类型、长度或枚举约束", invalid, source=source)
        unknown = required - definitions.keys()
        for key in sorted(unknown):
            self.add(rule, "pending", f"{name}.{key} 仅出现于规则总表，明细表无定义，不能自动替换成其他字段")
        self.add(rule, "pending", f"{name} 标绿必填说明与 O/C/N 标识需确认；外部枚举字典未提供，未将示例当作枚举全集")
        codes = Counter(str(r.get("CODE")) for r in records if r.get("CODE") not in (None, ""))
        duplicates = [k for k, n in codes.items() if n > 1]
        unique_rule = "4." + str(int(rule.split(".")[1]) + 1)
        self.add(unique_rule, "fail" if duplicates else "pass", f"{name}.CODE 唯一性", duplicates)

    def field_types(self, name, types):
        for spec in self.pack['fields'][name]:
            key, expected = spec['name'], spec['type']
            if name == 'PTECH' and key == 'TYPE_APPUI':
                continue  # Contradictory numeric type and textual example.
            actual = types.get(key)
            if actual is None or expected not in {'Texte', 'Entier', 'Double'}:
                continue
            allowed = {'Texte': {'text'}, 'Entier': {'integer'}, 'Double': {'real', 'integer'}}[expected]
            if actual not in allowed:
                self.add(FIELD_RULES[name], 'fail', f'{name}.{key} 存储类型 {actual} 不符合 {expected}', source=spec['source'])

    def relations(self, rows):
        def selection(name, key, value):
            return [r for r in rows.get(name, []) if str(r.get(key, "")).upper() == value]
        pm = selection("SITE", "TYPE", "PM")
        pbo = selection("BOITE", "TYPE", "PBO")
        distribution = selection("CABLE", "TYPE_CABLE", "DISTRIBUTION")
        def codes(records, key="CODE"):
            return {str(r.get(key)) for r in records if r.get(key) not in (None, "")}
        pm_codes = codes(pm)
        for rid, others, key in (("5.1", rows.get("ZPM", []), "CODE"), ("5.2", pbo, "REF_PM"), ("5.3", distribution, "REF_PM")):
            diff = sorted(pm_codes ^ codes(others, key))
            self.add(rid, "fail" if diff else "pass", "双向归属引用一致性", diff)
        boxes = [r for r in rows.get("BOITE", []) if str(r.get("TYPE", "")).upper() in {"PBO", "BPE"}]
        endpoints = codes(distribution, "ORIGINE") | codes(distribution, "EXTREMITE")
        diff = sorted(endpoints ^ (pm_codes | codes(boxes)))
        self.add("5.4", "fail" if diff else "pass", "配线光缆端点与 PM/BPE/PBO 双向引用", diff)
        bad, unknown = [], []
        for r in pbo:
            used, cap = number(r.get("NB_FIBRE_UTIL")), capacity(r.get("CAPACITE"))
            if used is None or cap is None:
                unknown.append(r.get("CODE"))
            elif used > cap:
                bad.append(r.get("CODE"))
        self.add("7.1", "fail" if bad else "unavailable" if unknown else "pass", "PBO 已用纤数不得超过盒子容量", bad or unknown)
        bad, unknown, totals = [], [], []
        for r in pm:
            code = r.get("CODE")
            demand = [capacity(b.get("CAPACITE")) for b in pbo if b.get("REF_PM") == code]
            supply = [capacity(c.get("CAPACITE")) for c in distribution if c.get("ORIGINE") == code]
            if None in demand + supply:
                unknown.append(code)
            else:
                totals.append({"pm": code, "pbo_capacity": sum(demand), "outgoing_cable_capacity": sum(supply)})
                if sum(demand) > sum(supply):
                    bad.append(code)
        self.add("7.2", "fail" if bad else "unavailable" if unknown else "pass", "按原表有向起点规则核对 PM 下 PBO 总容量与出线容量（不以需求量替代）", bad or unknown, totals=totals)
