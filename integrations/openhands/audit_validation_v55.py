"""Independent post-run audit of the v55 prefix-selection fork pilot.

What this audit does **not** trust:

* the runner's own summary: every manifest source hash is re-checked;
* the frozen snapshot: its complete file map is recomputed and compared;
* the frozen prefix's own selection: the pre-registered rule is re-evaluated
  over the recorded attempts, and the recorded trigger prediction is recomputed
  **offline from the persisted prefix event log** with the real condenser;
* each branch: its final workspace is rebuilt from the frozen snapshot plus
  **that branch's own persisted bytes**, the rebuilt full-file hash map is
  compared with the branch's recorded map, and the host target test is re-run on
  those bytes and compared with the branch's recorded verdict;
* the branch ledgers: the carried request count/estimated input must equal the
  prefix's, and no branch may exceed the shared request ceiling;
* the credential scan covers generated artifacts only.

Usage:
    python audit_validation_v55.py --out runs/stage5-openhands/<batch>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

from audit_v53_fast_core import map_digest
from branch_artifacts_v55 import (load_batch_index, reconstruct_branch_workspace,
                                  verify_batch_index)
from prefix_selection_v55 import (attempt_plan, predict_trigger, rule_fingerprint,
                                  selection_decision)
from same_prefix_fork_v54 import tree_hashes
from validation_tasks_v49 import TASKS, evaluate, hashes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'integrations' / 'openhands'))
SKIPPED_TREES = ('workspaces', 'prefix-snapshot', 'baseline', 'prefix-select',
                 'audit-workspaces')
TEXT_SUFFIXES = {'.py', '.json', '.md', '.txt', '.log', '.csv'}

#: Tooling files that may legitimately be repaired after this batch's gate run.
#: The batch is never re-labelled: the drift is recorded in the audit output.
POST_GATE_TOOLING = (
    'integrations/openhands/run_validation_v55.py',
    'integrations/openhands/audit_validation_v55.py',
    'integrations/openhands/fork_tools_v54.py',
    'integrations/openhands/budget_policy_v54.py',
    'integrations/openhands/branch_artifacts_v55.py',
    'integrations/openhands/prefix_selection_v55.py',
    'integrations/openhands/run_validation_windows_v55.py',
    'tests/test_openhands_v55_formal_runner_gate.py',
)


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def redirect_temp(out: Path) -> None:
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


def usage(report: dict, kind: str) -> int:
    metrics = report[kind + '_metrics']['accumulated_token_usage']
    return metrics['prompt_tokens'] + metrics['completion_tokens']


def complete_total(report: dict) -> int:
    return usage(report, 'agent') + usage(report, 'summary')


def credential_scan(out: Path, key: str):
    leaks, scanned, skipped = [], set(), []
    for path in out.rglob('*'):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(out)
        if relative.parts and relative.parts[0] in SKIPPED_TREES:
            skipped.append(str(relative))
            continue
        scanned.add(path)
    for path in sorted(scanned):
        if key and key in path.read_text(encoding='utf-8', errors='replace'):
            leaks.append(str(path.relative_to(out)))
    return leaks, {'mode': 'targeted_generated_artifacts', 'files_scanned': len(scanned),
                   'files_skipped': len(skipped),
                   'skipped_trees': sorted({Path(name).parts[0] for name in skipped})}


def fresh_destination(sample: Path, name: str) -> Path:
    destination = sample / name
    if destination.exists():
        destination.rename(sample / f'{name}-stale-{int(time.time())}')
    return destination


def evaluate_and_clean(workspace: Path, task: str, destination: Path) -> dict:
    """Run the host target test and drop its scoring copy (disk budget)."""
    result = evaluate(workspace, task, destination)
    scratch = Path(destination) / 'host-workspace'
    if scratch.is_dir():
        result['scoring_copy_file_count'] = sum(1 for p in scratch.rglob('*') if p.is_file())
        result['scoring_copy_removed'] = True
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--allow-incomplete', action='store_true')
    parser.add_argument('--keep-workspace', action='store_true')
    parser.add_argument('--accept-post-gate-tooling', action='store_true')
    parser.add_argument('--skip-branch-host-tests', action='store_true',
                        help='verify the per-branch bytes and hashes but do not re-run the host '
                             'target test (recorded, never silent)')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    redirect_temp(out)

    started = time.perf_counter()
    timings: dict[str, float] = {}

    def mark(segment: str, start: float) -> None:
        timings[segment] = round(timings.get(segment, 0.0) + (time.perf_counter() - start), 2)

    manifest = load(out / 'manifest.json')
    assert manifest['version'] == 'openhands-django-v55-prefix-selection-fork', manifest['version']
    assert manifest['arms'] == ['none', 'native_summary', 'pruner_v1'], manifest['arms']
    changed_sources = {}
    for file, expected in manifest['source_hashes'].items():
        actual = hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
        if actual != expected:
            assert file in POST_GATE_TOOLING and args.accept_post_gate_tooling, (
                f'a frozen experiment source changed after the gate: {file} - this batch '
                f'cannot be audited as the frozen design')
            changed_sources[file] = {'frozen': expected, 'current': actual}
    shim = load(out / 'windows-compatibility.json')
    assert hashlib.sha256((ROOT / shim['source_path']).read_bytes()).hexdigest() == \
        shim['source_sha256']

    freeze = load(out / 'prefix-freeze.json')
    frozen_tree = load(out / 'frozen-hashes.json')
    frozen_source = load(out / 'frozen-source-hashes.json')
    select = load(out / 'select.json')
    prefix_report = load(out / 'prefix' / 'report.json')
    branch_dirs = [path for path in sorted(out.glob('branch-*/'))
                   if (path / 'report.json').is_file()]
    branch_reports = [load(path / 'report.json') for path in branch_dirs]
    expected_branches = len(manifest['arms'])
    complete = len(branch_reports) == expected_branches
    assert complete or (args.allow_incomplete and 0 < len(branch_reports) < expected_branches), \
        (len(branch_reports), expected_branches)

    task = freeze['task']
    allowed = list(TASKS[task]['allowed'])
    snapshot = Path(freeze['snapshot'])
    assert snapshot.is_dir(), snapshot
    assert task in manifest['tasks'] and task in manifest['task_order']

    # ---- the frozen prefix -------------------------------------------------
    start = time.perf_counter()
    snapshot_hashes = tree_hashes(snapshot)
    assert snapshot_hashes == frozen_tree, 'frozen snapshot changed since the select stage'
    assert len(frozen_tree) == freeze['workspace_file_count']
    assert map_digest(frozen_tree) == freeze['workspace_tree_digest']
    assert map_digest(frozen_source) == freeze['workspace_source_digest']
    assert len(frozen_source) == freeze['workspace_file_count_source']
    mark('frozen_snapshot_verification', start)

    # ---- the pre-registered selection rule, re-evaluated --------------------
    start = time.perf_counter()
    protocol = {key: manifest[key] for key in
                ('tasks', 'phase1_work_request_ladder', 'trigger_input_tokens',
                 'trigger_prediction_margin_tokens', 'trigger_prediction_ledger_margin_tokens',
                 'model')}
    recorded_attempts = select['attempts']
    assert len(recorded_attempts) == len(select['decision']['considered'])
    decision = selection_decision(protocol, recorded_attempts)
    assert decision['selected'] is True, 'the recorded ladder has no qualifying attempt'
    assert decision['task'] == freeze['task'], (decision['task'], freeze['task'])
    assert decision['rung'] == freeze['rung']
    assert select['rule_fingerprint'] == rule_fingerprint(protocol), \
        'the recorded selection-rule fingerprint does not match the frozen rule'
    plan = [(rung, task_) for rung, _cap, task_ in attempt_plan(protocol)]
    executed = [(row['rung'], row['task']) for row in recorded_attempts]
    assert executed == plan[:len(executed)], (
        'attempts were not executed in the pre-registered order', executed, plan)
    assert freeze['prefix_needs_correction'] is True
    assert freeze['host_test_passed'] is False
    mark('selection_rule_recheck', start)

    # ---- the trigger prediction, recomputed offline ------------------------
    start = time.perf_counter()
    prefix_events_path = out / 'prefix' / 'events.json'
    assert prefix_events_path.is_file(), prefix_events_path
    events_payload = load(prefix_events_path)
    assert events_payload['event_id_digest'] == freeze['event_id_digest']
    frozen_prediction = freeze['trigger_prediction']
    assert frozen_prediction.get('triggered') is True, \
        'the fork was taken without a triggering prediction'
    prompt_path = out / 'prefix' / 'correction-prompt.txt'
    if prompt_path.is_file():
        correction_text = prompt_path.read_text(encoding='utf-8')
    else:
        from run_validation_v55 import correction_prompt
        correction_text = correction_prompt(task, freeze['correction_feedback'])
    recomputed_prediction = None
    prediction_note = ('recomputed offline from the persisted prefix event log; the frozen '
                       'event-ID list is available, so the token counting input is rebuilt '
                       'from the live SDK events during the run and compared here by digest')
    released = False
    try:
        import run_validation_v55 as runner
        from openhands.sdk.event import Event
        from integrations.openhands.fork_tools_v54 import register_v54_tools
        register_v54_tools()
        persistence = out / 'sdk-persistence' / 'prefix'
        candidate_dirs = [path for path in sorted(persistence.iterdir())
                          if path.is_dir()] if persistence.is_dir() else []
        # The persistence directory is named by the conversation's hex id, while
        # `state.id` renders with dashes, so the frozen id is matched by the
        # event-ID digest instead of by string equality.
        sdk_events = None
        for candidate in candidate_dirs:
            files = sorted((candidate / 'events').glob('event-*.json'),
                           key=lambda p: int(p.name.split('-')[1]))
            loaded = [Event.model_validate_json(path.read_text(encoding='utf-8'))
                      for path in files]
            from run_validation_v55 import digest_ids
            if digest_ids([str(event.id) for event in loaded]) == freeze['event_id_digest']:
                sdk_events = loaded
                break
        if sdk_events is not None:
            engine = runner.Engine(runner.EngineConfig(
                out=out, task=task, stage='audit', api_key=None,
                workspace=out / 'workspaces' / 'prefix', prepare_workspace=False))
            engine.plan = dict(runner.PROTOCOL)
            engine.task_state = None
            recomputed_prediction = predict_trigger(
                events=sdk_events, protocol=engine.plan, task=task, allowed=allowed,
                correction_text=correction_text,
                condenser_factory=lambda: engine.make_condenser('pruner_v1', None, task=task),
                current_state='',
                ledger_last_estimated_input=frozen_prediction.get(
                    'ledger_last_estimated_input'))
            released = True
        else:
            prediction_note = ('no persisted prefix conversation matches the frozen event '
                               'digest, so the offline replay was not attempted')
    except Exception as exc:  # recorded, never silently swallowed
        prediction_note = f'offline replay unavailable: {type(exc).__name__}: {exc}'
    if recomputed_prediction is not None:
        assert recomputed_prediction['triggered'] is True, (
            'the offline replay does not reproduce the frozen trigger prediction',
            recomputed_prediction)
        assert recomputed_prediction['condenser_replay_result'] == \
            frozen_prediction['condenser_replay_result']
        assert recomputed_prediction['view_event_count'] == frozen_prediction['view_event_count']
    mark('trigger_prediction_replay', start)

    # ---- the frozen prefix's host verdict ----------------------------------
    start = time.perf_counter()
    prefix_evaluation = evaluate_and_clean(snapshot, task,
                                           fresh_destination(out / 'prefix', 'evaluation-audit'))
    mark('prefix_host_test', start)
    assert prefix_evaluation['passed'] == prefix_report['host_evaluation']['passed'], (
        'audit re-run disagrees with the recorded prefix host verdict')
    assert prefix_report['host_passed'] is prefix_evaluation['passed']
    assert prefix_report['needs_correction'] is (not prefix_evaluation['passed'])

    # ---- per-branch artifacts and independent host re-runs ------------------
    start = time.perf_counter()
    index = load_batch_index(out)
    verify_batch_index(out, branch_dirs, frozen_source)
    mark('branch_artifact_verification', start)

    identities = [b['branch_event_ids'] for b in branch_reports]
    for index_, ids in enumerate(identities):
        for other in identities[index_ + 1:]:
            assert not set(ids) & set(other), 'branch event suffixes overlap'

    ceiling = manifest['max_agent_calls_per_sample']
    audit_workspace = out / 'workspaces' / 'audit-prefix'
    rows = []
    for position, (sample_dir, report) in enumerate(zip(branch_dirs, branch_reports)):
        start = time.perf_counter()
        assert report['prefix_event_count'] == freeze['event_count']
        assert report['restore_hash_equal'] is True
        assert report['source_prefix_unchanged'] is True
        assert report['branch_events_persisted_match_observation'] is True
        assert report['trigger_prediction_matches_freeze'] is True, (
            f'{report["sample"]}: the branch fork did not reproduce the frozen prediction')
        ledger = report['ledger']
        assert ledger['carried_over_agent_requests'] == freeze['agent_requests']
        assert ledger['carried_over_estimated_input'] == freeze['estimated_input']
        assert ledger['shared_prefix_agent_requests'] == freeze['agent_requests']
        assert all(call['status'] == 'returned' for call in ledger['calls']), 'failed branch request'
        assert report['post_fork_agent_requests'] == sum(
            call['kind'] == 'agent' for call in ledger['calls'])
        assert report['budget_state']['correction_stop_at'] == min(
            ceiling, freeze['agent_requests'] + manifest['host_feedback_correction_reserve'])
        assert freeze['agent_requests'] + report['post_fork_agent_requests'] <= ceiling
        mark('branch_ledger_checks', start)

        # Rebuild this branch's exact final workspace from the frozen snapshot
        # plus this branch's own persisted bytes, then score that.
        start = time.perf_counter()
        entry = index['branches'][report['sample']]
        verified = reconstruct_branch_workspace(
            audit_workspace, snapshot, frozen_tree=frozen_tree, frozen_source=frozen_source,
            artifacts_dir=sample_dir, experiment_root=out, allowed=allowed,
            index_entry=entry, report=report)
        record = verified['record']
        changed = sorted(record['changed_files'])
        assert changed == sorted(report['changed_files'])
        boundary = set(changed) <= set(allowed)
        assert boundary == report['file_boundary_ok'] is True
        assert record['final_source_digest'] == report['final_source_digest']
        mark('branch_reconstruction', start)

        recorded = (report.get('host_rounds') or [{}])[-1].get('host', {})
        if args.skip_branch_host_tests:
            re_run = None
            verdict_source = ('NOT re-run: --skip-branch-host-tests was passed; the branch bytes '
                              'and hashes were verified but the host verdict is the recorded one')
        else:
            start = time.perf_counter()
            re_run = evaluate_and_clean(audit_workspace, task,
                                        fresh_destination(sample_dir, 'evaluation-audit'))
            mark('branch_host_test', start)
            verdict_source = ('audit rebuilt this branch\'s own persisted bytes and re-ran the '
                              'host target test on them')
        if re_run is not None:
            assert re_run['passed'] == recorded.get('passed'), (
                f'{report["sample"]}: audit re-run host verdict differs from the record')
            assert re_run['passed'] == report['final_host_passed']
            assert (re_run['passed'] and boundary) == report['artifact_success']
        else:
            assert recorded.get('passed') == report['final_host_passed']
            assert (recorded.get('passed') and boundary) == report['artifact_success']
        rows.append({
            'sample': report['sample'], 'arm': report['arm'], 'position': report['position'],
            'post_fork_tokens': complete_total(report),
            'post_fork_agent_requests': report['post_fork_agent_requests'],
            'post_fork_total_requests': report['post_fork_total_requests'],
            'carried_over_agent_requests': ledger['carried_over_agent_requests'],
            'carried_over_estimated_input': ledger['carried_over_estimated_input'],
            'final_host_passed': bool(recorded.get('passed')),
            'artifact_success': bool(recorded.get('passed') and boundary),
            'file_boundary_ok': boundary,
            'host_verdict_source': verdict_source,
            'host_rounds': len(report.get('host_rounds') or []),
            'condensation_events': report['condensation_events'],
            'mechanism_triggered': report['mechanism_triggered'],
            'changed_files': changed,
            'final_source_digest': record['final_source_digest'],
            'persisted_files': len(record['final_files']),
            'restore_hash_equal': True,
            'error_type': report.get('error_type'),
        })

    if not args.keep_workspace:
        shutil.rmtree(audit_workspace, ignore_errors=True)

    # ---- recomputed comparison --------------------------------------------
    all_attempts_total = sum(row['complete_total_tokens'] for row in recorded_attempts)
    prefix_total = freeze['complete_total_tokens']
    assert complete_total(prefix_report) == prefix_total
    by_arm = {row['arm']: row for row in rows}
    paired = {}
    baseline = by_arm.get('none')
    if baseline:
        for arm, row in by_arm.items():
            if arm == 'none':
                continue
            post = row['post_fork_tokens']
            base_post = baseline['post_fork_tokens']
            whole = all_attempts_total + post
            base_whole = all_attempts_total + base_post
            paired[arm] = {
                'post_fork_savings': ((base_post - post) / base_post) if base_post else None,
                'whole_run_savings': ((base_whole - whole) / base_whole) if base_whole else None,
                'arm_post_fork_tokens': post, 'none_post_fork_tokens': base_post,
                'arm_whole_run_tokens': whole, 'none_whole_run_tokens': base_whole,
                'both_artifact_success': bool(row['artifact_success']
                                              and baseline['artifact_success']),
            }

    # ---- credential scan ---------------------------------------------------
    start = time.perf_counter()
    leaks, scan_stats = credential_scan(out, os.environ.get('DEEPSEEK_API_KEY') or '')
    assert not leaks, 'Credential leakage detected; do not publish artifacts'
    mark('credential_scan', start)
    timings['total_seconds'] = round(time.perf_counter() - started, 2)

    plugin = by_arm.get('pruner_v1', {})
    plugin_compactions = plugin.get('condensation_events')
    result = {
        'complete': complete,
        'expected_branches': expected_branches,
        'completed_branches': len(branch_reports),
        'selected_task': task,
        'selected_rung': freeze['rung'],
        'selection': {
            'attempts': len(recorded_attempts),
            'all_attempts_tokens_counted_once': all_attempts_total,
            'per_attempt': [{'sample': row['sample'], 'task': row['task'], 'rung': row['rung'],
                             'host_passed': row['host_passed'],
                             'trigger_prediction_triggered':
                                 (row.get('trigger_prediction') or {}).get('triggered'),
                             'measured_view_tokens':
                                 (row.get('trigger_prediction') or {}).get('measured_view_tokens'),
                             'agent_requests': row['agent_requests'],
                             'complete_total_tokens': row['complete_total_tokens']}
                            for row in recorded_attempts],
            'order_reproduced': True,
            'rule_fingerprint': select['rule_fingerprint'],
        },
        'prefix': {
            'complete_total_tokens': prefix_total,
            'host_test_passed': freeze['host_test_passed'],
            'prefix_needs_correction': freeze['prefix_needs_correction'],
            'agent_requests': freeze['agent_requests'],
            'summary_requests': freeze['summary_requests'],
            'event_count': freeze['event_count'],
            'measured_view_tokens': frozen_prediction.get('measured_view_tokens'),
            'measured_margin_tokens': frozen_prediction.get('measured_margin_tokens'),
            'ledger_last_estimated_input': frozen_prediction.get('ledger_last_estimated_input'),
            'trigger_prediction_recomputed': (
                None if recomputed_prediction is None else {
                    'triggered': recomputed_prediction['triggered'],
                    'measured_view_tokens': recomputed_prediction['measured_view_tokens'],
                    'condenser_replay_result': recomputed_prediction['condenser_replay_result'],
                    'view_event_count': recomputed_prediction['view_event_count']}),
            'trigger_prediction_note': prediction_note,
            'offline_replay_released': released,
        },
        'branches': rows,
        'paired': paired,
        'mechanism_triggered_plugin': bool(plugin.get('mechanism_triggered')),
        'plugin_compaction_count': plugin_compactions,
        'mechanical_claim': (
            'the plugin arm condensed after the fork; the post-fork difference is a compression '
            'difference' if plugin_compactions and plugin_compactions >= 1 else
            'the plugin arm did NOT condense after the fork: this round is a flow validation '
            'only and no compression effect may be claimed'),
        'cross_checks': {
            'source_hashes_valid': True,
            'post_gate_tooling_edits_recorded': sorted(changed_sources),
            'frozen_snapshot_valid': True,
            'selection_rule_order_reproduced': True,
            'trigger_prediction_reproduced_offline': bool(recomputed_prediction is not None
                                                          and recomputed_prediction['triggered']),
            'branch_restores_hash_equal': all(row['restore_hash_equal'] for row in rows),
            'branch_bytes_rebuilt_from_own_artifacts': True,
            'branch_host_tests_independently_reproduced': not args.skip_branch_host_tests,
            'file_boundary_violations': sum(not row['file_boundary_ok'] for row in rows),
            'failed_branch_requests': 0,
            'shared_ceiling_respected': all(
                freeze['agent_requests'] + row['post_fork_agent_requests'] <= ceiling
                for row in rows) or len(rows) == 0,
        },
        'credential_matches': len(leaks),
        'credential_scan': scan_stats,
        'frozen_experiment_sources_valid': not changed_sources,
        'timings_seconds': timings,
        'note': ('Post-fork totals are branch-local provider totals including every summary '
                 'request; whole-run totals add every prefix-selection attempt exactly once. A '
                 'single prefix cannot estimate the plugin average effect on any task '
                 'population.'),
    }
    (out / 'audit.json').write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                    encoding='utf-8')

    lines = ['# v55 前缀筛选 + 同前缀分叉独立审计', '',
             f"计划 1 个共同前缀 × {expected_branches} 臂；实际保存 {len(branch_reports)}/"
             f"{expected_branches} 分叉。完整={complete}。", '',
             f"前缀筛选：共 {len(recorded_attempts)} 次预注册尝试，选中 `{task}`（第 {freeze['rung']} 档，"
             f"工作请求上限 {freeze['work_cap']}）；该类尝试总计 {all_attempts_total:,} token（各计一次）。",
             f"共同前缀：宿主目标测试 "
             f"{'通过' if freeze['host_test_passed'] else '未通过（因此需要纠错）'}；"
             f"Agent 请求 {freeze['agent_requests']}，完整总 token {prefix_total:,}，"
             f"condenser 视图 token {frozen_prediction.get('measured_view_tokens'):,}"
             f"（触发阈值 {frozen_prediction.get('trigger_input_tokens'):,}）。", '',
             '| 分叉 | 臂 | 分叉后完整总 token | 全程（各尝试计一次） | 分叉后请求 | 携带前缀请求 | 宿主测试 | 独立重跑 | 文件边界 | 压缩次数 |',
             '|---|---|---:|---:|---:|---:|---|---|---|---:|']
    for row in rows:
        lines.append(
            f"| {row['sample']} | {row['arm']} | {row['post_fork_tokens']:,} | "
            f"{all_attempts_total + row['post_fork_tokens']:,} | "
            f"{row['post_fork_agent_requests']} | {row['carried_over_agent_requests']} | "
            f"{'通过' if row['final_host_passed'] else '未通过'} | "
            f"{'是' if row['host_verdict_source'].startswith('audit rebuilt') else '否'} | "
            f"{'是' if row['file_boundary_ok'] else '否'} | {row['condensation_events']} |")
    lines += ['', '## 审计核查', '',
              f"- 冻结源哈希：{len(manifest['source_hashes']) - len(changed_sources)}/"
              f"{len(manifest['source_hashes'])} 一致"
              + (f"；门控后修复并已记录：{sorted(changed_sources)}" if changed_sources else ''),
              f"- 冻结 `prefix-snapshot` 完整哈希：{freeze['workspace_file_count']} 文件一致",
              f"- 预注册筛选规则按序复算：{'一致' if True else '不一致'}；"
              f"规则指纹 `{select['rule_fingerprint'][:16]}…`",
              f"- 触发预测在线下用真实 condenser 复算："
              f"{'复现（触发）' if (recomputed_prediction or {}).get('triggered') else '未复现'}",
              '- **每个分叉独立可验证**：审计从冻结快照 + 该分叉自己持久化的字节重建其最终工作区，'
              '核对重建后的完整文件哈希表，再在重建字节上重跑宿主目标测试'
              '（见各行 `host_verdict_source`）',
              f"- 文件边界违规：{result['cross_checks']['file_boundary_violations']}；"
              f"失败分支请求：0；共享 36 次请求上限："
              f"{'全部尊重' if result['cross_checks']['shared_ceiling_respected'] else '存在违规'}",
              f"- 插件分叉压缩次数：{plugin_compactions}；"
              f"机制是否触发：{plugin.get('mechanism_triggered')}",
              f"- 凭据匹配：{len(leaks)}（扫描 {scan_stats['files_scanned']} 个生成物文本文件）",
              '', '## 解释边界', '',
              '本批只有 1 个共同前缀、1 个真实任务，**不能**估计插件在任务总体上的平均效果，'
              '也不能外推到其他宿主。分叉后成本是分支自身的 provider 总 token（含所有摘要请求）；'
              '全程成本把前缀筛选的每一次尝试各计一次，因此没有任何一臂被重复计入前缀费用。',
              result['mechanical_claim']]
    (out / 'AUDIT_REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'complete': complete, 'branches': len(rows),
                      'plugin_compaction_count': plugin_compactions,
                      'mechanism_triggered_plugin': result['mechanism_triggered_plugin'],
                      'trigger_prediction_reproduced': result['cross_checks'][
                          'trigger_prediction_reproduced_offline'],
                      'paired': paired, 'cross_checks': result['cross_checks'],
                      'timings_seconds': timings}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
