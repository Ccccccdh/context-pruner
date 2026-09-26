"""Read-only outcome audit and report for the frozen OpenHands experiment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics

from task_suite import TASKS, hashes, evaluate

ARMS = ('none', 'native_summary', 'pruner_v1')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    root = Path(__file__).resolve().parents[2]
    assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == value
               for path, value in manifest['source_hashes'].items()), 'Frozen source changed'
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(out.glob('r*/report.json'))]
    assert len(reports) == 27
    assert len({(r['task'], r['repeat'], r['arm']) for r in reports}) == 27
    assert {r['arm'] for r in reports} == set(ARMS)
    audit_rows = []
    for report in reports:
        sample = out / f'r{report["repeat"]}-{report["task"]}-{report["arm"]}'
        original = json.loads((sample / 'original_hashes.json').read_text(encoding='utf-8'))
        current = hashes(sample / 'workspace')
        changes = sorted(k for k in set(original) | set(current) if original.get(k) != current.get(k))
        # Evaluate EVERY immutable final workspace, including interrupted/limited
        # runs. Separate artifact correctness from normal agent completion.
        final_check = evaluate(sample / 'workspace', report['task'], sample / 'audit-evaluation')
        assert hashes(sample / 'workspace') == current
        events = [json.loads(p.read_text(encoding='utf-8')) for p in sample.glob('conversation/**/events/*.json')]
        stored_ids = {e['id'] for e in events}
        derived_ids = {e['id'] + '-summary' for e in events if e['kind'] == 'Condensation'}
        condensations = [e for e in events if e['kind'] == 'Condensation']
        batch_counts = {}
        for event in events:
            if event['kind'] == 'ActionEvent':
                batch_counts[event['llm_response_id']] = batch_counts.get(event['llm_response_id'], 0) + 1
        source_refs_ok = all(set(e['forgotten_event_ids']) <= stored_ids | derived_ids for e in condensations)
        state = json.loads((sample / 'pruner-state.json').read_text(encoding='utf-8')) if report['arm'] == 'pruner_v1' else None
        row = {
            'sample': sample.name, 'arm': report['arm'], 'task': report['task'], 'repeat': report['repeat'],
            'normal_completion_success': report['success'],
            'artifact_success': bool(final_check['passed'] and set(changes) <= {TASKS[report['task']]['allowed']}),
            'agent_error_type': report.get('error_type'),
            'call_or_token_limit_exhausted': any(e['kind'] == 'ConversationErrorEvent'
                                                and 'Frozen experiment call/token limit' in str(e.get('detail', ''))
                                                for e in events),
            'file_boundary_ok': set(changes) <= {TASKS[report['task']]['allowed']},
            'recorded_file_changes_match': changes == report.get('changed_files', changes),
            'all_requests_structural_valid': all(c['structural_valid'] for c in report['ledger']['calls']),
            'requests_returned': all(c['status'] == 'returned' for c in report['ledger']['calls']),
            'original_events_retained': source_refs_ok,
            'condensation_events': len(condensations),
            'multi_action_batches': sum(n > 1 for n in batch_counts.values()),
            'pruner_compressions': sum('forgotten_ids' in a for a in state['audit']) if state else 0,
            'pruner_validation_fallbacks': sum(a.get('skipped') == 'structural_validation_failed' for a in state['audit']) if state else 0,
            'pruner_hard_budget_exceeded': sum(a.get('hard_budget_exceeded', False) for a in state['audit']) if state else 0,
            'final_pytest_summary': final_check['summary'],
        }
        assert row['file_boundary_ok'] and row['recorded_file_changes_match'] and row['all_requests_structural_valid'] and source_refs_ok, row
        audit_rows.append(row)
    def tokens(row, key='prompt_tokens'):
        return sum(row[k + '_metrics']['accumulated_token_usage'][key] for k in ('agent', 'summary'))
    keyed = {(r['task'], r['repeat'], r['arm']): r for r in reports}
    outcomes = {(r['task'], r['repeat'], r['arm']): r for r in audit_rows}
    paired = {}
    for method in ('native_summary', 'pruner_v1'):
        pairs = []
        for task in TASKS:
            for repeat in (1, 2, 3):
                baseline, treatment = keyed[task, repeat, 'none'], keyed[task, repeat, method]
                base_in, method_in = tokens(baseline), tokens(treatment)
                base_total = tokens(baseline) + tokens(baseline, 'completion_tokens')
                method_total = tokens(treatment) + tokens(treatment, 'completion_tokens')
                pairs.append({'task': task, 'repeat': repeat, 'baseline_success': outcomes[task, repeat, 'none']['artifact_success'],
                              'method_success': outcomes[task, repeat, method]['artifact_success'], 'baseline_input': base_in,
                              'method_input': method_in, 'input_savings': 1 - method_in / base_in,
                              'total_token_savings': 1 - method_total / base_total})
        paired[method] = {'pairs': pairs, 'mean_pair_input_savings': statistics.mean(p['input_savings'] for p in pairs),
                          'median_pair_input_savings': statistics.median(p['input_savings'] for p in pairs),
                          'input_saving_wins': sum(p['input_savings'] > 0 for p in pairs),
                          'both_success': sum(p['baseline_success'] and p['method_success'] for p in pairs),
                          'mean_pair_total_token_savings': statistics.mean(p['total_token_savings'] for p in pairs)}
    summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
    assert summary['complete']
    arm_results = {arm: dict(stats, artifact_successes=sum(r['artifact_success'] for r in audit_rows if r['arm'] == arm))
                   for arm, stats in summary['arms'].items()}
    for arm, stats in arm_results.items():
        selected = [r for r in reports if r['arm'] == arm]
        stats['peak_provider_agent_input'] = max(u['prompt_tokens'] for r in selected for u in r['agent_metrics']['token_usages'])
        stats['cache_read_tokens'] = sum(r[k + '_metrics']['accumulated_token_usage']['cache_read_tokens']
                                       for r in selected for k in ('agent', 'summary'))
        stats['call_or_token_limit_exhaustions'] = sum(r['call_or_token_limit_exhausted'] for r in audit_rows if r['arm'] == arm)
    key = os.environ.get('DEEPSEEK_API_KEY')
    exposed = 0
    if key:
        raw_key = key.encode()
        for p in out.rglob('*'):
            if p.is_file() and p.suffix in ('.json', '.md', '.txt', '.py'):
                exposed += raw_key in p.read_bytes()
    audit = {'complete': True, 'arms': arm_results, 'samples': audit_rows, 'paired': paired,
             'credential_scan_performed': bool(key), 'credential_matches': exposed,
             'limitations': ['3 bugs in one public Python library; guided three-phase workflow',
                             'same model/provider, temperature 0; repeats are not independent tasks',
                             'selected upstream parser/variables tests only; POSIX sh tests excluded',
                             'estimated input caps use GPT-4o tokenizer; provider token totals used for savings',
                             'SDK monetary cost unavailable; cache hits are included in provider prompt tokens',
                             'development failures and interrupted pilot excluded from confirmatory effects but retained']}
    assert exposed == 0
    (out / 'audit.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding='utf-8')
    lines = ['# OpenHands 三组对照实验报告', '',
             f'协议：`{manifest["version"]}`。3 个真实开源缺陷 × 3 次重复 × 3 组，共 27 个样本。', '',
             '## 结果', '', '| 组别 | 文件任务通过 | 正常完整完成 | Agent 输入 | 摘要输入 | 总输出 | 模型调用 | 摘要调用 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for arm in ARMS:
        s = arm_results[arm]
        lines.append(f'| {arm} | {s["artifact_successes"]}/{s["samples"]} | {s["successes"]}/{s["samples"]} | {s["agent_input_tokens"]:,} | {s["summary_input_tokens"]:,} | {s["total_output_tokens"]:,} | {s["model_calls"]} | {s["summary_calls"]} |')
    lines += ['', '文件任务通过：全部 27 个最终工作区统一进行只读行为测试与文件边界审计。正常完整完成：原报告记载的 Agent 三阶段均结束、测试通过且遵守调查阶段不编辑。达到调用上限后已有正确代码的样本，其文件任务可以通过，但不算正常完整完成；原始报告不改写。', '',
              '输入与输出均为提供商回报值；摘要开销已计入。SDK 缺少该模型价格映射，0 美元不能解释为免费。', '',
              '| 相对完整历史 | 配对平均输入节省 | 配对中位数 | 输入节省胜率 | 两组同时成功 |', '|---|---:|---:|---:|---:|']
    for arm, p in paired.items():
        lines.append(f'| {arm} | {p["mean_pair_input_savings"]:.2%} | {p["median_pair_input_savings"]:.2%} | {p["input_saving_wins"]}/9 | {p["both_success"]}/9 |')
    lines += ['', '## 任务与判分', '', f'固定源码：python-dotenv v1.2.1，提交 `{manifest["source_commit"]}`（MIT）。', '']
    for task, spec in TASKS.items():
        lines.append(f'- [{task}]({spec["source"]})：{spec["problem"]}')
    lines += ['', '原版负对照：转义 12 项失败、注释 4 项失败、CRLF 3 项失败。每项实验还运行原版 parser/variables 回归用例；仅允许修改指定实现文件，测试不允许改动。', '',
              '三个阶段均固定：先调查且不编辑、实施修复、接收宿主测试反馈并完成检查或修正。模型只能用受限文件编辑器和 FinishTool，未启用终端。各组独立工作区、独立会话，顺序按任务和重复轮换。', '',
              '## 集成与审计', '',
              'Context-Pruner 通过官方 CondenserBase 扩展点接入，以安全工具批次边界压缩旧事件前缀；保留系统、初始任务和最近工具批次，使用 Condensation 派生视图，原事件仍在 SDK 持久化日志中。没有额外摘要模型调用。', '',
              f'27 个样本文件边界、模型请求工具结构、压缩事件引用已全部通过审计。实际多调用批次共 {sum(r["multi_action_batches"] for r in audit_rows)} 组。凭据扫描命中 {exposed} 项。', '',
              f'插件压缩次数：{sum(r["pruner_compressions"] for r in audit_rows)}；结构校验回退：{sum(r["pruner_validation_fallbacks"] for r in audit_rows)}；压缩后超过 16000 token 的派生视图：{sum(r["pruner_hard_budget_exceeded"] for r in audit_rows)}。', '',
              '12000 token 为 SDK 计数口径下的压缩触发阈值，插件目标 10000、硬目标 16000。受保护部分自身超限时无法保证满足硬目标；不能把目标称为 API 的硬上下文限制。各组请求均有总调用和估算输入上限。', '',
              '## 范围与限制', '',
              '配对输入指标包含全部九对，不剔除失败。文件任务通过数为统一只读补充审计结果；预设运行器的成功数保留为正常完整完成列。补充审计不修改原报告，也不重试模型。', '',
              '本次是单库、三个小缺陷的受控代码修复实验，不能推出所有 Agent、模型或长任务都获得相同收益，也不能仅凭 9 次重复证明质量等价。这里的工作流包含固定调查阶段和宿主测试反馈。Windows 未运行依赖 POSIX sh 的上游 test_main.py；通过所选回归测试不代表通过整个上游测试集。', '',
              '开发记录单独保留：installation-smoke-v1 的 LiteLLM 兼容故障发生在一次模型响应后；smoke-v2 成功。对照 v1/v2/v4 为请求前工具工厂兼容故障；v3 因全文件阅读耗尽预算停止；v5 暴露 pytest 默认临时目录权限冲突及低阈值下的摘要循环，已统一设置每轮独立临时目录并调整三组压缩参数后冻结 v6。这些开发调用不计入 v6 的效果统计，但存在额外 API 用量；中断调用的计费情况无法从本地完整确认。', '',
              '## 复现与证据', '',
              '`manifest.json` 冻结任务、版本、参数和源文件哈希；每样本保留 `report.json`、`ledger.json`、工作区、两轮 pytest 日志和 SDK 事件；插件组另有 `pruner-state.json`。`summary.json` 是组汇总，`audit.json` 是只读审计及 9 对逐对比较。', '',
              '```powershell', '.\\.venv-openhands\\Scripts\\python.exe -B integrations/openhands/run_comparison.py --run --out runs/stage5-openhands/new-experiment-id',
              '.\\.venv-openhands\\Scripts\\python.exe -B integrations/openhands/audit_comparison.py --out runs/stage5-openhands/new-experiment-id', '```', '',
              '已有完整样本可用 `--resume` 继续，源文件哈希必须匹配；未写最终报告的中断样本需要先审计，运行器拒绝静默覆盖。']
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'samples': len(reports), 'credential_matches': exposed,
                      'paired': {k: {a: b for a, b in v.items() if a != 'pairs'} for k, v in paired.items()}}, indent=2))

if __name__ == '__main__':
    main()
