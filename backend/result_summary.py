"""Deterministic, run-bound summaries; review availability is not calculation success."""
from datetime import datetime


def summarize(run, review=None):
    action = '导出' if run.get('kind') == 'export' else '规划计算'
    state = {'succeeded': '已完成', 'failed': '失败', 'cancelled': '已取消',
             'interrupted': '已中断'}.get(run['status'], '处理中')
    stamp = datetime.fromtimestamp(run.get('created', 0)).strftime('%m-%d %H:%M')
    lines = [f"**{action}{state}**", f"{stamp} · 批次 `{run['id']}`"]
    if run.get('error'):
        lines.append(str(run['error']))
    metrics = run.get('metrics') or {}
    if metrics:
        labels = {'main_trunk_unique_length_m': '主干长度 / 米', 'installed_duct_length_m': '管道长度 / 米',
                  'construction_cost': '计算造价'}
        visible = [f'{label}：{metrics[key]}' for key, label in labels.items() if key in metrics]
        if visible:
            lines.append('；'.join(visible))
    if not review:
        if run.get('kind') == 'planning':
            lines.append('独立审查尚未完成；成果与程序校验已保留。')
        return '\n\n'.join(lines)
    result = review.get('result') or {}
    if review['status'] == 'completed':
        verdict = {'pass': '通过', 'needs_review': '有待复核事项', 'fail': '发现校验错误',
                   'insufficient_evidence': '证据不足'}.get(result.get('verdict'), '待复核')
        lines.append('**审查完成 · ' + verdict + '**')
        findings = result.get('findings', [])
        counts = {s: sum(f.get('severity') == s for f in findings) for s in ('error', 'warning', 'info')}
        lines.append(f"{counts['error']} 项错误、{counts['warning']} 项警告、{counts['info']} 项提醒")
        for severity, label, limit in [('error', '错误', None), ('warning', '警告', 3), ('info', '提醒', 2)]:
            items = [f for f in findings if f.get('severity') == severity]
            bullets = []
            for item in items[:limit]:
                text = f"- **{label}**：{item['description']}"
                if item.get('suggestion') and not item['suggestion'].startswith('查看规则工具明细与来源单元格'):
                    text += '\n  建议：' + item['suggestion']
                bullets.append(text)
            if bullets:
                lines.append('\n'.join(bullets))
            if limit and len(items) > limit:
                lines.append(f'另有 {len(items) - limit} 项{label}，见审查报告。')
        if not findings and result.get('summary'):
            lines.append(result['summary'])
    else:
        lines.append('审查暂未完成：' + (review.get('error_message') or
                     {'skipped': '审查模型未配置。', 'interrupted': '审查已中断。'}.get(review['status'], '正在整理证据或生成报告。')))
        lines.append('AI 审查尚无有效结论；无需因此重跑规划，可说“重新审查本次结果”。')
    evidence = review.get('evidence') or {}
    if evidence:
        if review['status'] != 'completed':
            for group in evidence.get('rule_findings', []):
                lines.append('程序规则 ' + group['rule_id'] + '：' + '；'.join(x['description'] for x in group['examples'] if x['status'] == 'fail'))
        lines.append('程序校验：' + ('存在失败项' if evidence.get('deterministic_failed') else
                    '存在警告或待复核项' if evidence.get('deterministic_warning') else '未检出失败项'))
    lines.append('成果已保留；规划及审查结论不代表施工验收。')
    return '\n\n'.join(lines)
