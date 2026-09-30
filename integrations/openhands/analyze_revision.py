"""Analyze all pairs, then show sensitivity to API failures and task failures."""
import argparse
import json
from pathlib import Path
import random
import statistics
from analyze_traces import sample_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    audit = json.loads((out / 'audit.json').read_text(encoding='utf-8'))
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(out.glob('r*/report.json'))]
    keyed = {(r['task'], r['repeat'], r['arm']): r for r in reports}
    traces = []
    for path in sorted(out.glob('r*/report.json')):
        trace = sample_trace(path.parent)
        if (path.parent / 'pruner-state.json').exists():
            state = json.loads((path.parent / 'pruner-state.json').read_text(encoding='utf-8'))
            trace['pruner_audit'] = state['audit']
            trace['plugin_task_state'] = state['middleware']['plugin']['task_state'] if state['middleware'] else None
        traces.append(trace)
    result = {'experiment': out.name, 'paired': {}, 'samples': traces,
              'note': 'All pairs retained. API-success and both-task-success subsets are sensitivity analyses, not replacement outcomes. Failed requests may lack provider usage.'}
    lines = ['# 修复轮次分析', '', '全部样本均保留；子集分析仅用于辨别提前中断造成的账面低输入，不替换原结果。', '',
             '| 方法 | 全配对均值 | API 均正常配对数 | 该子集均值 | 双方任务通过配对数 | 该子集均值 |', '|---|---:|---:|---:|---:|---:|']
    for method, data in audit['paired'].items():
        rows = []
        for pair in data['pairs']:
            base = keyed[pair['task'], pair['repeat'], 'none']
            treatment = keyed[pair['task'], pair['repeat'], method]
            api_ok = all(c['status'] == 'returned' for r in (base, treatment) for c in r['ledger']['calls'])
            rows.append(dict(pair, api_ok=api_ok, both_normal_success=base['success'] and treatment['success']))
        subsets = {'all': rows, 'api_ok': [r for r in rows if r['api_ok']],
                   'both_artifact_success': [r for r in rows if r['baseline_success'] and r['method_success']],
                   'both_normal_success': [r for r in rows if r['both_normal_success']]}
        metrics = {}
        for label, subset in subsets.items():
            values = [r['input_savings'] for r in subset if r['input_savings'] is not None]
            rng = random.Random(20260926)
            boot = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(10000)) if values else []
            metrics[label] = {'n': len(values), 'mean': statistics.mean(values) if values else None,
                             'wins': sum(v > 0 for v in values),
                             'bootstrap_interval_descriptive': [boot[249], boot[9749]] if boot else None}
        result['paired'][method] = {'metrics': metrics, 'pairs': rows}
        fmt = lambda label: f"{metrics[label]['mean']:.2%}" if metrics[label]['mean'] is not None else '不可用'
        lines.append(f"| {method} | {fmt('all')} | {metrics['api_ok']['n']} | {fmt('api_ok')} | {metrics['both_artifact_success']['n']} | {fmt('both_artifact_success')} |")
    lines += ['', '## 插件记忆审计', '']
    for t in traces:
        if not t['arm'].startswith('pruner_'):
            continue
        counts = {}
        for row in t['pruner_audit']:
            label = row.get('skipped', 'accepted')
            counts[label] = counts.get(label, 0) + 1
        lines.append(f"- {t['sample']}：{counts}；任务状态非空={bool(t['plugin_task_state'])}；正常完成={t['normal_success']}。")
    lines += ['', '## 解释范围', '', '配对包含相同任务的重复，非独立项目；bootstrap 区间仅描述这批样本，不能证明质量等价或跨项目泛化。API 中断样本提供商用量可能不完整，其较低 token 数不能直接解释为裁剪收益。', '',
              '短任务没有触发压缩时，三组独立模型轨迹仍可能不同；这部分差异不能归因于压缩机制。所有本轮结果均为开发与验证数据，后续独立任务应与这些数据分开。']
    (out / 'revision-analysis.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8', newline='\n')
    (out / 'REVISION_ANALYSIS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({k: v['metrics'] for k, v in result['paired'].items()}, indent=2))


if __name__ == '__main__':
    main()
