"""Readable rendering of deterministic facts, without changing rule outcomes."""


def describe_rule(rule_id, examples):
    descriptions = []
    for item in examples:
        totals = item.get('totals') or []
        if totals:
            objects = set(item.get('object_ids') or [])
            for row in totals:
                if objects and row['pm'] not in objects:
                    continue
                required, actual = row['pbo_capacity'], row['outgoing_cable_capacity']
                descriptions.append(f"`{row['pm']}` 的 PBO 额定容量合计 {required:g}，出线容量 {actual:g}，缺口 {max(0, required - actual):g}。本规则比较额定容量，不是当前已用纤数。")
        elif item.get('measured_overlaps'):
            for row in item['measured_overlaps']:
                names = ' 与 '.join('`' + str(name) + '`' for name in row['objects'])
                area = (f"{row['area_m2']:.6g} 平方米" if row.get('area_m2') is not None
                        else f"{row['area_crs_units_squared']:.6g} 平方坐标单位")
                descriptions.append(f'{names} 检出 {area} 的交叠，严格零重叠检查未通过。极小交叠仍需核查几何精度与工程容差，不能仅据此判断成因或实际影响。')
        else:
            descriptions.append(item['description'])
    return ('规则 ' + rule_id + '：' + '；'.join(descriptions))[:2000]
