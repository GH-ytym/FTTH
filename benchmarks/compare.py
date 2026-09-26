"""Compare the same readonly cases; report semantics only with complete reviews."""
import argparse
import json
from pathlib import Path
try:
    from .run_dialogue import summarize
except ImportError:
    from run_dialogue import summarize


def compare(left, right):
    if left['dataset_sha256'] != right['dataset_sha256']:
        raise ValueError('数据集版本不同，不能直接比较')
    if left['mode'] != 'real' or right['mode'] != 'real':
        raise ValueError('smoke不是模型效果，不能参与效果对比')
    def keyed(report):
        return {(r['case_id'],r['repeat'],r['turn']):r for r in report['rows']
            if not r.get('skipped') and not r.get('intent_only')}
    a, b = keyed(left), keyed(right)
    if not a or a.keys() != b.keys():
        raise ValueError('只读用例/轮次/重复次数不一致，不能直接比较')
    return {'left':{'architecture':left['architecture'],'model':left['model'],'summary':summarize(list(a.values()))},
        'right':{'architecture':right['architecture'],'model':right['model'],'summary':summarize(list(b.values()))},
        'note':'只比较相同只读轮次；前置路由诊断单列。语义未全部审核时不计算通过率。不得用只读消融证明写操作编排可靠。'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left',type=Path)
    parser.add_argument('right',type=Path)
    args=parser.parse_args()
    print(json.dumps(compare(json.loads(args.left.read_text(encoding='utf-8')),
        json.loads(args.right.read_text(encoding='utf-8'))),ensure_ascii=False,indent=2))
