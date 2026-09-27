"""Plain text rendering of durable review records; no model-generated HTML."""
VERDICTS = {"pass": "通过（不代表工程验收）", "needs_review": "需要复核", "fail": "未通过", "insufficient_evidence": "证据不足"}
STATUSES = {"queued": "排队中", "running": "读取证据与审查中", "completed": "完成", "failed": "暂未完成", "interrupted": "中断", "skipped": "未执行"}


def render_reviews(reviews):
    if not reviews:
        return "此批次尚未审查。可在对话中说“重新审查本次结果”。"
    sections = []
    for review in reversed(reviews):
        result, evidence = review.get("result", {}), review.get("evidence", {})
        lines = ["独立审查 · " + STATUSES.get(review["status"], review["status"]),
            "运行：" + review["run_id"], "审查编号：" + review["id"],
            "触发方式：" + ("自动审查" if review.get("automatic") else "补充审查（不启动自动重算）")]
        if "elapsed_seconds" in review:
            lines.append(f"耗时：{review['elapsed_seconds']} 秒")
        if result:
            lines.extend(["审查结论：" + VERDICTS[result["verdict"]], "", result["summary"]])
        elif review.get("error"):
            lines.append(review.get("error_message") or "审查未完成，可在对话中说“重新审查本次结果”。")
            lines.append("诊断：" + review.get("stage", "unknown") + " / " + review.get("error_code", review["error"]))
        if evidence:
            lines.append("程序校验：" + ("发现失败项" if evidence.get("deterministic_failed") else "存在警告或待复核项" if evidence.get("deterministic_warning") else "未检出失败项"))
        for index, finding in enumerate(result.get("findings", []), 1):
            lines.extend(["", f"{index}. {finding['description']}", "对象：" + "、".join(finding.get("object_ids", [])),
                "证据：" + "、".join(finding["evidence_ids"]), "建议：" + finding.get("suggestion", "")])
        missing = result.get("unchecked") or evidence.get("missing", [])
        if missing:
            lines.extend(["", "未检查／缺失证据：" + "、".join(missing)])
        rule_report = review.get('rule_checks', {})
        if rule_report:
            lines.extend(['', '工作簿规则工具：' + rule_report.get('rulepack_version', ''), '来源校验值：' + rule_report.get('workbook_sha256', '')])
            for item in rule_report.get('results', []):
                if item['status'] == 'pass':
                    continue
                lines.extend([f"规则 {item['rule_id']} · {item['status']}：{item['description']}",
                    '来源：' + item.get('source', ''), '对象：' + '、'.join(item.get('object_ids', []))])
                for key in ('totals', 'measured_overlaps'):
                    if item.get(key):
                        import json
                        lines.append(json.dumps(item[key], ensure_ascii=False))
        desktop = review.get('qgis_state')
        if desktop:
            lines.extend(['', 'QGIS 状态：' + desktop.get('reason', ''), '桌面已检查：' + str(desktop.get('desktop_observed', False))])
        repair = review.get("repair", {})
        if repair:
            lines.extend(["", "一次重算：" + ("已提交 " + repair["run_id"] if repair["status"] == "submitted" else "未执行：" + repair.get("reason", ""))])
            for key, value in repair.get("parameter_patch", {}).items():
                lines.append(f"参数 {key} → {value}")
        comparison = review.get("comparison", {})
        if comparison:
            labels = {"improved": "程序问题减少", "worsened": "程序问题增加", "unchanged": "程序问题未改善", "incomparable": "无法判断改善"}
            lines.extend(["", "重算比较：" + labels.get(comparison["outcome"], comparison["outcome"]), comparison.get("description", "")])
            for name, values in comparison.get("metrics", {}).items():
                lines.append(f"{name}：{values['before']} → {values['after']}")
        lines.extend(["", "证据目录：" + "、".join(evidence.get("evidence_ids", []))])
        sections.append("\n".join(lines))
    return "\n\n────────────────────\n\n".join(sections)
