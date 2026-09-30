"""Post-run report of prerequest metrics; refuses partial runs, zero model calls."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--out', required=True)
args = p.parse_args()
out = Path(args.out).resolve()
audit = json.loads((out / 'audit.json').read_text(encoding='utf-8'))
assert audit['complete'] and audit['completed_samples'] == 18
cross = json.loads((out / 'completion-state-crosscheck.json').read_text(encoding='utf-8'))
assert cross['samples'] == 18 and cross['mismatches'] == 0
working = json.loads((out / 'working-code-diagnostics.json').read_text(encoding='utf-8'))
method = working['method']
bykey = {(r['task'], r['repeat'], r['arm']): r for r in audit['samples']}
result = {'model_requests': 0, 'method': method, 'arms': {}, 'paired': {}}
for arm, values in audit['arms'].items():
    reports = [json.loads(f.read_text(encoding='utf-8')) for f in out.glob(f'r*-*-{arm}/report.json')]
    result['arms'][arm] = {**values,
        'first_acceptance_observed': sum('first_evaluation' in r for r in reports),
        'first_acceptance_passed': sum(r.get('first_evaluation', {}).get('passed', False) for r in reports),
        'correction_requested': sum(not r['first_evaluation']['passed'] for r in reports if 'first_evaluation' in r)}
for arm, data in audit['paired'].items():
    pairs = data['pairs']
    assert len(pairs) == 6
    normal = [r['input_savings'] for r in pairs if r['api_ok'] and bykey[r['task'],r['repeat'],'none']['normal_completion_success'] and bykey[r['task'],r['repeat'],arm]['normal_completion_success']]
    base_total = sum(r['baseline_input'] for r in pairs)
    method_total = sum(r['method_input'] for r in pairs)
    result['paired'][arm] = {'all': data['metrics']['all'], 'both_normal': {'n': len(normal), 'mean': statistics.mean(normal) if normal else None},
                            'aggregate_input_reduction': 1-method_total/base_total if base_total else None, 'pairs': pairs}
result['compression_subsets'] = working['subsets']
result['limitations'] = ['New contracts, previously seen projects; same author designed tasks and acceptance/reference.',
                        'All six pairs include no-compression and budget failures; subsets are descriptive.',
                        'No algorithm changes after new tasks or model responses; not a significance or stable-30-percent claim.',
                        'First acceptance may be absent after budget failure; final code is independently audited.']
result['report_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
(out/'validation-summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
fmt = lambda value: '不可用' if value is None else f'{value:.2%}'
lines = ['# v29新合同冻结候选验证', '', '18样本完整结束，独立审计和结束状态核对完成。候选v11保持旧冻结哈希；不是完全独立盲测。', '',
         '| 组 | 正常完成 | 最终代码通过 | 首次验收通过/全样本 | 首次验收实际观察 | 提供方总输入 |', '|---|---:|---:|---:|---:|---:|']
for arm,v in result['arms'].items():
    lines.append(f"| {arm} | {v['successes']}/6 | {v['artifact_successes']}/6 | {v['first_acceptance_passed']}/6 | {v['first_acceptance_observed']}/6 | {v['total_input_tokens']:,} |")
lines += ['', '| 方法 | 主指标：全部六对均值 | 中位数 | 正收益对数 | 双方正常子集 | 总输入汇总减少（非主指标） |','|---|---:|---:|---:|---:|---:|']
for arm,v in result['paired'].items():
    lines.append(f"| {arm} | {fmt(v['all']['mean'])} | {fmt(v['all']['median'])} | {v['all']['wins']}/6 | {fmt(v['both_normal']['mean'])} (n={v['both_normal']['n']}) | {fmt(v['aggregate_input_reduction'])} |")
lines += ['', '## 插件全部配对', '', '| 任务 | 次数 | 输入减少 | 压缩次数 | 双方正常 |', '|---|---:|---:|---:|---|']
for r in working['pairs']:
    lines.append(f"| {r['task']} | {r['repeat']} | {fmt(r['input_savings'])} | {r['accepted_compressions']} | {r['baseline_normal_completion'] and r['method_normal_completion']} |")
lines += ['', '## 压缩触发子集（事后描述）', '', '| 子集 | 对数 | 平均减少 | 正收益对数 |','|---|---:|---:|---:|']
for label,v in working['subsets'].items(): lines.append(f"| {label} | {v['n']} | {fmt(v['mean'])} | {v['wins']} |")
lines += ['', '所有失败与负收益保留；首次验收缺失不能算首次通过。公开所选回归并非完整上游套件。供应商美元费用未知，SDK零价格不等于免费。',
          '新任务未用于旧模型调参，但项目已接触，任务/验收/参考为同人编写。实际触发子集不能替换全部六对主指标，不能据此宣称因果提升、广泛质量等价或稳定30%。',
          '原始18样本、manifest、audit、ledger、事件、工作区和全部配对见本目录；脚本哈希记录在validation-summary.json。']
(out/'VALIDATION_SUMMARY.md').write_text(chr(10).join(lines)+chr(10),encoding='utf-8')
print(json.dumps({k:v['all'] for k,v in result['paired'].items()}))
