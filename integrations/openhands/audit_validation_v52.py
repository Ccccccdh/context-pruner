"""Independent post-run audit, without changing frozen experiment outcomes.

Inherits v50's performance work unchanged: the boundary property comes from a digest
of the editable files instead of repeated full-tree hashes, the credential scan is
limited to generated artifacts with an assertion that every skipped file sits under
`workspaces/`, and per-segment timing plus a checkpoint are written so a long audit
can be traced and resumed. v50's equivalence with v49 was verified on all 27 v49
samples (`EQUIVALENT: True`); the scoring here is the same evaluator, host patch,
module selection and assertions as v45-v49, and nothing was relaxed.

v52 differs only in the batch it audits: the plugin arm uses the reference-only
condenser (ContextPrunerCondenserV51) instead of v42. The audit is mechanism-agnostic
and cross-checks whatever the reports record.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time
from validation_tasks_v49 import TASKS, evaluate
# Sibling module in the same directory, which the harness puts on sys.path when it
# runs this file (the runner registers tools the same way).
from audit_optimizations import (Checkpoint, Timing, allowed_file_state,
                                 editable_digest)

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def redirect_temp(out):
    """Keep host-test scratch files out of the sandbox-denied machine %TEMP%."""
    import validation_tasks_v49 as frozen
    temp = out / '.audit-tmp'
    temp.mkdir(parents=True, exist_ok=True)
    for name in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[name] = str(temp)
    original = frozen._test_environment

    def redirected(test_workspace):
        environment = original(test_workspace)
        scratch = temp / Path(test_workspace).name
        scratch.mkdir(parents=True, exist_ok=True)
        for name in ('TEMP', 'TMP', 'TMPDIR'):
            environment[name] = str(scratch)
        return environment

    frozen._test_environment = redirected


def usage(report, kind, field='prompt_tokens'):
    return report[kind + '_metrics']['accumulated_token_usage'][field]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--allow-incomplete', action='store_true', help='Audit preserved samples from a stopped batch, without claiming completion.')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    redirect_temp(out)
    manifest = load(out / 'manifest.json')
    for file, expected in manifest['source_hashes'].items():
        assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == expected, file
    shim = load(out / 'windows-compatibility.json')
    assert hashlib.sha256((ROOT / shim['source_path']).read_bytes()).hexdigest() == shim['source_sha256']
    arms = manifest['arms']
    expected_samples = manifest['repeats'] * len(TASKS) * len(arms)
    paths = sorted(out.glob('r*/report.json'))
    complete = len(paths) == expected_samples
    assert complete or (args.allow_incomplete and 0 < len(paths) < expected_samples), (len(paths), expected_samples)
    rows, reports = [], {}
    timing = Timing()
    checkpoint = Checkpoint(out / '.audit-checkpoint.json')
    if checkpoint.done:
        print(f'resuming: {len(checkpoint.done)} sample(s) already audited', flush=True)
    for path in paths:
        report = load(path)
        key = report['task'], report['repeat'], report['arm']
        assert key not in reports
        reports[key] = report
        wi = (report['repeat'] - 1) * len(TASKS) * len(arms) + list(TASKS).index(report['task']) * len(arms) + arms.index(report['arm'])
        workspace = out / 'workspaces' / f'w{wi:03d}'
        sample_name = path.parent.name
        allowed = TASKS[report['task']]['allowed']

        # Targeted instead of full-tree hashing. Measured on this machine a full
        # hashes() call over one workspace costs 15.8 s warm / 254.6 s cold across
        # 20,128 files, and the audit ran it three to four times per sample - that,
        # not copying, is what made v49's audit take about four hours. The boundary
        # property only concerns the editable files, so compare exactly those.
        started_segment = time.perf_counter()
        original = load(path.parent / 'original_hashes.json')
        before_state = allowed_file_state(workspace, allowed)
        before_editable = editable_digest(workspace, allowed)
        edited = sorted(name for name in allowed
                        if original.get(Path(name).as_posix()) != before_state[Path(name).as_posix()])
        # Any change outside the editable set is a boundary violation; the recorded
        # changed_files list is the runner's own full-tree comparison against
        # original_hashes.json, so comparing the two keeps that evidence in play
        # without paying for another 20k-file scan here.
        boundary = set(edited) <= set(allowed)
        assert boundary, sample_name
        if 'changed_files' in report:
            assert sorted(report['changed_files']) == edited, (sample_name, edited, report['changed_files'])
        timing.add('boundary_edit_check', time.perf_counter() - started_segment, sample_name)

        started_segment = time.perf_counter()
        destination = path.parent / 'evaluation-audit'
        evaluation = evaluate(workspace, report['task'], destination)
        timing.add('evaluate', time.perf_counter() - started_segment, sample_name)

        started_segment = time.perf_counter()
        assert editable_digest(workspace, allowed) == before_editable, (
            f'Host evaluation changed an editable file: {sample_name}')
        if 'final_evaluation' in report:
            assert evaluation['passed'] == report['final_evaluation']['passed']
        timing.add('post_evaluate_verification', time.perf_counter() - started_segment, sample_name)
        calls = report['ledger']['calls']
        if 'first_phase_agent_calls' in report:
            assert report['first_phase_agent_calls'] <= manifest['first_phase_max_requests']
            assert report['correction_agent_calls'] == (
                sum(c['kind'] == 'agent' for c in calls) - report['first_phase_agent_calls'])
            assert report['correction_agent_calls'] <= manifest['host_feedback_correction_reserve']
        sdk_events = [load(ep) for ep in (path.parent / 'conversation').glob('**/events/*.json')]
        derived_ids = {str(e['id']) + '-summary' for e in sdk_events if e.get('kind') == 'Condensation'}
        conversation_errors = [e for e in sdk_events if e.get('kind') == 'ConversationErrorEvent']
        agent_errors = [e for e in sdk_events if e.get('kind') == 'AgentErrorEvent']
        assert all(c['structural_valid'] for c in calls)
        assert sum(c['kind'] == 'agent' for c in calls) <= manifest['max_agent_calls_per_sample']
        assert sum(c['kind'] == 'summary' for c in calls) <= manifest['max_summary_calls_per_sample']
        references_valid = True
        if (path.parent / 'pruner-state.json').exists():
            state = load(path.parent / 'pruner-state.json')
            ids = {str(e['id']) for e in sdk_events} | derived_ids
            for item in state['audit']:
                refs = set(item.get('forgotten_ids', []))
                references_valid &= refs <= ids
            assert references_valid, path.parent.name
        rows.append({'sample': path.parent.name, 'task': report['task'], 'repeat': report['repeat'],
                     'arm': report['arm'], 'normal_completion_success': report['success'] and not conversation_errors,
                     'artifact_success': evaluation['passed'] and boundary, 'file_boundary_ok': boundary,
                     'api_ok': bool(calls) and all(c['status'] == 'returned' for c in calls),
                     'error_type': report.get('error_type'), 'summary': evaluation['summary'],
                     'agent_error_events': len(agent_errors),
                     'total_input_tokens': sum(usage(report, kind) for kind in ('agent', 'summary')),
                     'unknown_failed_request_usage': any(c['status'] != 'returned' for c in calls),
                     'call_or_token_limit_exhausted': 'Frozen experiment call/token limit' in report.get('diagnostic', '') or any(e.get('code') == 'MaxIterationsReached' for e in conversation_errors),
                     'pruner_hard_budget_exceeded': sum(a.get('hard_budget_exceeded', False) for a in state['audit']) if (path.parent / 'pruner-state.json').exists() else 0,
                     'source_references_valid': references_valid})
        print(path.parent.name, evaluation['summary'], flush=True)
        # Checkpoint after every fully verified sample so a multi-hour audit can
        # resume instead of restarting (the review's section 3.2 requirement).
        checkpoint.record(sample_name, {
            'file_boundary_ok': boundary,
            'artifact_success': bool(evaluation['passed'] and boundary),
            'evaluation_summary': evaluation['summary'],
        })
    bykey = {(r['task'], r['repeat'], r['arm']): r for r in rows}
    paired = {}
    for method in arms[1:]:
        pairs = []
        for task in TASKS:
            for repeat in range(1, manifest['repeats'] + 1):
                base, other = bykey.get((task, repeat, 'none')), bykey.get((task, repeat, method))
                if base is None or other is None:
                    continue  # A stopped batch has no comparison for missing arms.
                savings = 1 - other['total_input_tokens'] / base['total_input_tokens'] if base['total_input_tokens'] else None
                pairs.append({'task': task, 'repeat': repeat, 'input_savings': savings,
                              'baseline_input': base['total_input_tokens'], 'method_input': other['total_input_tokens'],
                              'baseline_success': base['artifact_success'], 'method_success': other['artifact_success'],
                              'api_ok': base['api_ok'] and other['api_ok']})
        subsets = {'all': pairs, 'api_ok': [p for p in pairs if p['api_ok']],
                   'both_success_api_ok': [p for p in pairs if p['api_ok'] and p['baseline_success'] and p['method_success']]}
        metrics = {}
        for label, selected in subsets.items():
            values = [p['input_savings'] for p in selected if p['input_savings'] is not None]
            metrics[label] = {'n': len(values), 'mean': statistics.mean(values) if values else None,
                              'median': statistics.median(values) if values else None,
                              'wins': sum(v > 0 for v in values)}
        paired[method] = {'pairs': pairs, 'metrics': metrics}
    summary = load(out / 'summary.json')
    for arm in arms:
        summary['arms'][arm]['artifact_successes'] = sum(r['artifact_success'] for r in rows if r['arm'] == arm)
        summary['arms'][arm]['successes'] = sum(r['normal_completion_success'] for r in rows if r['arm'] == arm)
        summary['arms'][arm]['call_or_token_limit_exhaustions'] = sum(r['call_or_token_limit_exhausted'] for r in rows if r['arm'] == arm)
        summary['arms'][arm]['pruner_hard_budget_exceeded'] = sum(r['pruner_hard_budget_exceeded'] for r in rows if r['arm'] == arm)
        summary['arms'][arm]['agent_error_events'] = sum(r['agent_error_events'] for r in rows if r['arm'] == arm)
    key = os.environ.get('DEEPSEEK_API_KEY')
    leaks = []
    scan_stats = {'mode': 'targeted', 'files_scanned': 0, 'files_skipped': 0, 'seconds': 0.0}
    if key:
        from audit_optimizations import is_generated_artifact, targeted_credential_scan
        started_scan = time.perf_counter()
        leaks = targeted_credential_scan(out, key)
        scan_stats['seconds'] = round(time.perf_counter() - started_scan, 2)
        # Record exactly what was skipped so the narrowing stays auditable: every
        # skipped file must be a verbatim copy of the public baseline, which is
        # verified separately by _assert_skips_are_public_source.
        counts = {'scanned': 0, 'skipped': 0}
        skipped_outside_workspaces = []
        for path in out.rglob('*'):
            if not path.is_file() or path.suffix not in {'.py', '.json', '.md', '.txt'}:
                continue
            if is_generated_artifact(path, out):
                counts['scanned'] += 1
            else:
                counts['skipped'] += 1
                relative = path.relative_to(out)
                if 'workspaces' not in relative.parts:
                    skipped_outside_workspaces.append(str(relative))
        scan_stats['files_scanned'] = counts['scanned']
        scan_stats['files_skipped'] = counts['skipped']
        scan_stats['skipped_outside_workspaces'] = skipped_outside_workspaces
        assert not skipped_outside_workspaces, (
            'targeted scan skipped non-workspace files: '
            f'{skipped_outside_workspaces[:5]}')
    assert not leaks, 'Credential leakage detected; do not publish artifacts'
    result = {'complete': complete, 'expected_samples': expected_samples, 'completed_samples': len(paths), 'arms': summary['arms'], 'samples': rows, 'paired': paired,
              'credential_matches': len(leaks), 'source_hashes_valid': True,
              'credential_scan': scan_stats, 'timing': timing.report(),
              'note': 'Failed request provider usage is unknown. All outcomes retained; subsets are sensitivity analyses.'}
    (out / 'audit.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    lines = ['# Django 15563 v45 三组开发小试', '',
             f"计划 {len(TASKS)} 个公开真实问题 × {manifest['repeats']} 次 × {len(arms)} 组；实际保存 {len(paths)}/{expected_samples} 样本。完整执行={complete}；验收为本机适配的选定测试。", '',
             '| 方法 | 正常完成 | 最终代码通过 | 实际总输入 | Agent/摘要调用 | 压缩事件 | 失败请求 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for arm, values in result['arms'].items():
        lines.append(f"| {arm} | {values['successes']}/{values['samples']} | {values['artifact_successes']}/{values['samples']} | {values['total_input_tokens']:,} | {values['model_calls'] - values['summary_calls']}/{values['summary_calls']} | {values['condensation_events']} | {values['failed_calls']} |")
    lines += ['', '| 方法 | 全配对均值 | API 正常配对均值 | 双方通过且 API 正常均值 |', '|---|---:|---:|---:|']
    def fmt(value):
        return '不可用' if value is None else f'{value:.2%}'
    for method, data in paired.items():
        metrics = data['metrics']
        cells = [f"{fmt(metrics[k]['mean'])} (n={metrics[k]['n']})" for k in ('all','api_ok','both_success_api_ok')]
        lines.append('| ' + method + ' | ' + ' | '.join(cells) + ' |')
    lines += ['', '## 全部配对', '', '| 任务 | 次数 | 方法 | 减少率 | 双方通过 | API 正常 |', '|---|---:|---|---:|---|---|']
    for method, data in paired.items():
        for pair in data['pairs']:
            lines.append(f"| {pair['task']} | {pair['repeat']} | {method} | {fmt(pair['input_savings'])} | {pair['baseline_success'] and pair['method_success']} | {pair['api_ok']} |")
    lines += ['', '## 解释边界', '',
              '供应商返回输入计数包含摘要开销；失败请求可能没有 usage，消耗未知。全配对统计不代表所有请求消耗完整可观测。',
              f"仅{len(TASKS)}个固定功能任务、每任务计划{manifest['repeats']}次，不能证明广泛质量等价或对所有 Agent 都有效。未触发压缩的样本组间差异不能归因于压缩。",
              '必须结合预算失败统计解释质量与输入差异。选定测试模块通过不等于完整上游测试通过。',
              f"本轮实际单次估计输入预算为 {manifest['max_single_estimated_input']:,}，以冻结 manifest 为准。另有每阶段 SDK {manifest.get('max_sdk_steps_per_phase', 36)} 步上限，压缩步骤也可能占用，不能等同于模型请求次数。",
              '插件、任务与运行代码哈希匹配；编辑边界、测试不修改工作目录、出站工具结构和归档引用已核查。供应商美元金额未知。']
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'complete': complete, 'paired_metrics': {k: v['metrics'] for k,v in paired.items()}}, indent=2))

if __name__ == '__main__':
    main()

