"""Bounded three-arm OpenHands experiment on fixed public bug tasks."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

from validation_tasks_v40 import TASKS, COMMIT, SOURCES, INSTANCE, INSTANCE_DIR, prepare, hashes, evaluate, code_revision

ROOT = Path(__file__).resolve().parents[2]
ARMS = ('none', 'native_summary', 'pruner_v40')
PROTOCOL = {
    'version': 'openhands-external-issue-v40-pilot',
    'model': 'openai/deepseek-v4-flash', 'temperature': 0,
    'thinking': 'disabled', 'api_retries': 0,
    'source_commit': COMMIT, 'tasks': list(TASKS),
    'arms': list(ARMS), 'repeats': 1,
    'trigger_input_tokens': 20000, 'pruner_target_tokens': 16000,
    'pruner_hard_tokens': 28000, 'native_max_events': 240,
    'max_agent_calls_per_sample': 36, 'max_summary_calls_per_sample': 16,
    'initial_work_requests': 24, 'first_phase_max_requests': 26,
    'host_feedback_correction_reserve': 10,
    'max_output_tokens': 3072, 'max_single_estimated_input': 80000,
    'max_total_estimated_input_per_sample': 2000000,
    'phase_plan': ['read_and_implement', 'evaluate_and_correct_only_if_failed'],
    'tools': ['scoped_editor', 'scoped_symbols', 'scoped_tests', 'finish'], 'max_sdk_steps_per_phase': 200,
    'initial_read_policy': 'TASK.md then self-directed relevant source reads; no repeated-read quota',
    'acceptance': 'external feature tests + selected unchanged upstream regression tests + file boundary',
    'upstream_test_limitation': 'Selected modules only; not the full upstream suites',
    'cost': 'provider tokens include native summary overhead; money unknown, SDK model price not mapped',
    'ordering': 'rotate arms; opaque equal-length workspace IDs; independent conversation per sample',
    'data': 'New independent public SWE-bench Verified instance, selected before inspecting reference fix',
    'candidate_selection': 'V40_SELECTION_RULES.md; earlier ranked Matplotlib instances were environment blocked',
    'primary_statistic': 'Mean of all within-task/repeat fractional provider-input savings, including failures and no-condensation samples',
    'trigger_subsets': 'Descriptive only, never replace the primary statistic',
    'closing_request_reserve': 2, 'single_input_headroom': 12000,
    'budget_notice': 'Transient host system message after condensation on every Agent request; ledger records exact notice',
    'closing_tool_policy': 'Verify-only then Finish-only; unchanged observed passed verification can finish early',
    'verification_quality_gate': 'Normal completion also requires current code revision passed public tests',
    'navigation': 'AST source symbol lookup and versioned view progress shared by all arms; no arbitrary shell or source writes',
}

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True)
    p.add_argument('--repeats', type=int, default=1, choices=[1])
    p.add_argument('--run', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    PROTOCOL['repeats'] = args.repeats
    out = Path(args.out).resolve()
    # The Windows wrapper creates only its SDK persistence directory before
    # calling us. That is not an existing experiment.
    if (out.exists() and not args.resume and
            any(p.name not in {'sdk-persistence', 'windows-compatibility.json'}
                for p in out.iterdir())):
        raise SystemExit('Output exists; --resume preserves completed samples.')
    out.mkdir(parents=True, exist_ok=True)
    os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
    from pydantic import PrivateAttr, SecretStr
    from litellm import token_counter
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.context.condenser import NoOpCondenser, LLMSummarizingCondenser
    from openhands.sdk.tool import Tool, register_tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from context_pruner.adapters.openhands_v36 import ContextPrunerCondenserV36

    upstream = ROOT / '.tooling/upstream'
    import subprocess
    for project, source in SOURCES.items():
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=upstream / source['directory'], text=True).strip()
        assert actual == source['commit']
    manifest = dict(PROTOCOL)
    manifest['versions'] = {n: importlib.metadata.version(n) for n in
                            ('openhands-sdk', 'openhands-tools', 'litellm', 'context-pruner')}
    sources = [Path(__file__), Path(__file__).with_name('validation_tasks_v40.py'), Path(__file__).with_name('validate_candidate_v40.py'), Path(__file__).with_name('edit_retry_guard_v33.py'), Path(__file__).with_name('task_suite.py'), ROOT / 'context_pruner/adapters/openhands_v2.py',
               ROOT / 'context_pruner/adapters/openhands_v5.py', ROOT / 'context_pruner/adapters/openhands_v4.py']
    sources += list((ROOT / 'context_pruner').rglob('*.py'))
    sources += [Path(__file__).with_name('bounded_llm_v30.py'),
                Path(__file__).with_name('budget_policy_v30.py'),
                Path(__file__).with_name('budget_policy_v31.py'),
                Path(__file__).with_name('budget_policy_v39.py'),
                Path(__file__).with_name('source_navigation_v31.py'),
                Path(__file__).with_name('symbol_tool_v31.py'),
                Path(__file__).with_name('symbol_tool_v35.py'),
                Path(__file__).with_name('crosscheck_budget_completion.py'),
                ROOT / 'runs/stage5-openhands/django-16315-v40-gate-01/gate.json',
                INSTANCE_DIR / 'instance.json', INSTANCE_DIR / 'host-tests.patch',
                INSTANCE_DIR / 'reference.patch',
                Path(__file__).with_name('audit_validation_v40.py'),
                Path(__file__).with_name('run_validation_windows_v40.py'),
                Path(__file__).with_name('verification_tool_django_v40.py'),
                ROOT / 'tests/test_openhands_budget_v39.py',
                Path(__file__).with_name('feedback_v40.py'),
                ROOT / 'tests/test_openhands_feedback_v40.py',
                Path(__file__).with_name('prototype_v11.py'),
                Path(__file__).with_name('PILOT_PROTOCOL_V40.md'),
                Path(__file__).with_name('V40_SELECTION_RULES.md'),
                Path(__file__).with_name('V40_FEEDBACK_REVIEW.md')]
    manifest['source_hashes'] = {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sources}
    if (out / 'manifest.json').exists():
        assert json.loads((out / 'manifest.json').read_text(encoding='utf-8')) == manifest, 'Frozen protocol changed'
    else:
        save(out / 'manifest.json', manifest)
    if args.check:
        baselines = {}
        for task in TASKS:
            sample = out / 'baseline' / task
            if not (sample / 'workspace').exists():
                prepare(upstream, sample / 'workspace', task)
            regression = evaluate(sample / 'workspace', task, sample / 'regression', regression_only=True)
            assert regression['passed'] and regression['ran'] == 50 and regression['workspace_unchanged_by_test'], regression
            result = evaluate(sample / 'workspace', task, sample / 'evaluation')
            assert (not result['passed'] and result['ran'] == 51
                    and result['failures'] == 'errors=1, skipped=7'
                    and result['workspace_unchanged_by_test']), result
            baselines[task] = result
        save(out / 'baseline.json', baselines)
        print(json.dumps(baselines, indent=2))
        return 0
    if not args.run:
        print(json.dumps(manifest, indent=2))
        return 0
    baseline = json.loads((out / 'baseline.json').read_text(encoding='utf-8'))
    reference = json.loads((ROOT / 'runs/stage5-openhands/django-16315-v40-gate-01/gate.json').read_text(encoding='utf-8'))
    assert set(baseline) == set(TASKS)
    assert reference['instance'] == INSTANCE['instance_id'] and reference['base_commit'] == COMMIT['django']
    assert (not baseline['django_bulk_create_db_column']['passed']
            and baseline['django_bulk_create_db_column']['ran'] == 51
            and reference['reference_host']['passed']
            and any(x.startswith('Ran 51 tests') for x in reference['reference_host']['summary']))
    assert (ContextPrunerCondenserV36().trigger_tokens,
            ContextPrunerCondenserV36().target_tokens,
            ContextPrunerCondenserV36().hard_tokens) == (PROTOCOL['trigger_input_tokens'],
            PROTOCOL['pruner_target_tokens'], PROTOCOL['pruner_hard_tokens'])
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise SystemExit('DEEPSEEK_API_KEY unavailable')

    from edit_retry_guard_v33 import EditRetryGuard
    from bounded_llm_v30 import BudgetAwareLLM as BoundedLLM
    from budget_policy_v39 import FeedbackReserveBudgetPolicy as BudgetPolicy
    from source_navigation_v31 import SourceNavigator
    from symbol_tool_v35 import ScopedSymbolsTool as BaseSymbolsTool
    from openhands.sdk.llm import TextContent

    class ScopedEditorExecutor(FileEditorExecutor):
        def __init__(self, **kw):
            self.scope = Path(kw['workspace_root']).resolve()
            super().__init__(**kw)

        def __call__(self, action, conversation=None):
            if not Path(action.path).resolve().is_relative_to(self.scope):
                return FileEditorObservation.from_text(text='Outside workspace blocked',
                                                       command=action.command, is_error=True)
            retry_hint = current['edit_guard'].check(action)
            if retry_hint:
                return FileEditorObservation.from_text(text=retry_hint,
                                                       command=action.command, is_error=True)
            observation = super().__call__(action, conversation)
            if action.command == 'view' and not observation.is_error:
                hint = current['navigator'].view_hint(action.path, action.view_range)
                if hint:
                    observation = observation.model_copy(update={
                        'content': [*observation.content, TextContent(text=hint)]})
            return observation

    # One sample at a time; registry factory receives the current immutable scope.
    current = {}
    class ScopedEditorTool(FileEditorTool):
        @classmethod
        def create(cls, conv_state):
            executor = ScopedEditorExecutor(workspace_root=str(current['workspace']),
                                            allowed_edits_files=[str(p) for p in current['allowed']])
            return [tool.set_executor(executor) for tool in super().create(conv_state)]
    register_tool(ScopedEditorTool.name, ScopedEditorTool)
    class ScopedSymbolsTool(BaseSymbolsTool):
        @classmethod
        def create(cls, conv_state):
            return super().create(conv_state, current['navigator'])
    register_tool(ScopedSymbolsTool.name, ScopedSymbolsTool)
    from verification_tool_django_v40 import ScopedTestsTool
    register_tool(ScopedTestsTool.name, ScopedTestsTool)

    for repeat in range(args.repeats):
        for index, task in enumerate(TASKS):
            rotation = (repeat + index) % 3
            for arm in ARMS[rotation:] + ARMS[:rotation]:
                sample = out / f'r{repeat + 1}-{task}-{arm}'
                if (sample / 'report.json').exists():
                    continue
                if sample.exists():
                    raise SystemExit(f'Interrupted sample needs audit before rerun: {sample.name}')
                workspace = out / 'workspaces' / f'w{repeat * len(TASKS) * len(ARMS) + index * len(ARMS) + ARMS.index(arm):03d}'
                sample.mkdir(parents=True)
                original = prepare(upstream, workspace, task)
                save(sample / 'original_hashes.json', original)
                current.update(workspace=workspace, allowed=[workspace / p for p in TASKS[task]['allowed']])
                current['navigator'] = SourceNavigator(
                    workspace, [workspace / p for p in TASKS[task]['research_files']], TASKS[task]['problem'])
                current['edit_guard'] = EditRetryGuard()
                ledger = {'calls': [], 'estimated_input': 0}
                policy = BudgetPolicy(lambda: code_revision(workspace, task),
                    initial_work_requests=PROTOCOL['initial_work_requests'],
                    max_agent_calls=PROTOCOL['max_agent_calls_per_sample'],
                    reserve_requests=PROTOCOL['closing_request_reserve'],
                    max_single_input=PROTOCOL['max_single_estimated_input'],
                    max_total_input=PROTOCOL['max_total_estimated_input_per_sample'],
                    single_input_headroom=PROTOCOL['single_input_headroom'])
                def make_llm(kind):
                    llm = BoundedLLM(model=PROTOCOL['model'], api_key=SecretStr(key), base_url='https://api.deepseek.com',
                                     temperature=0, num_retries=0, timeout=60, max_output_tokens=3072,
                                     litellm_extra_body={'thinking': {'type': 'disabled'}}, usage_id=f'{sample.name}-{kind}')
                    llm._ledger = ledger
                    llm._sample = sample
                    llm._kind = kind
                    llm._policy = policy
                    llm._max_summary_calls = PROTOCOL['max_summary_calls_per_sample']
                    return llm
                agent_llm, summary_llm = make_llm('agent'), make_llm('summary')
                condenser = (NoOpCondenser() if arm == 'none' else
                             LLMSummarizingCondenser(llm=summary_llm, max_size=240, max_tokens=24000,
                                                     keep_first=2, hard_context_reset_max_retries=1)
                             if arm == 'native_summary' else ContextPrunerCondenserV36())
                agent = Agent(llm=agent_llm, tools=[Tool(name=ScopedEditorTool.name), Tool(name=ScopedSymbolsTool.name), Tool(name=ScopedTestsTool.name, params={'workspace': str(workspace), 'task': task, 'destination': str(sample / 'tool-tests')})],
                              include_default_tools=['FinishTool'], condenser=condenser)
                events = []
                def callback(event):
                    policy.observe_event(event)
                    events.append({'type': type(event).__name__, 'id': str(event.id)})
                report = {'task': task, 'arm': arm, 'repeat': repeat + 1, 'success': False}
                conversation = None
                started = time.perf_counter()
                try:
                    conversation = Conversation(agent=agent, workspace=str(workspace), callbacks=[callback],
                                                persistence_dir=str(sample / 'conversation'), delete_on_close=False,
                                                visualizer=None, max_iteration_per_run=200)
                    conversation.send_message(
                        f'Work only inside {workspace}. Implement this complete feature contract:\n'
                        + TASKS[task]['problem'] + '\n'
                        f'Edit only {TASKS[task]["allowed"]}. TASK.md repeats the contract. '
                        'Use scoped_symbols to locate definitions in the named research source files; view those exact ranges. '
                        'Read relevant source as needed, implement, then run scoped_tests to verify public regressions. '
                        'No arbitrary shell is available. When implementation is ready and public tests pass, '
                        'use finish immediately; the host then runs separate feature acceptance. '
                        'Do not keep exploring after verification. Do not modify tests or documentation. '
                        'If an edit is rejected as unchanged, view the narrow current function range and make a changed replacement; '
                        'never resubmit identical old_str and new_str. '
                        f'You have at most {PROTOCOL["max_agent_calls_per_sample"]} Agent requests in total. '
                        'In this first phase, use at most 24 requests for investigation and implementation; '
                        'the next two close this phase with public verification and Finish. '
                        'The host then runs separate feature tests. If they fail, the host will return '
                        'read-only failure feedback and reserve up to ten remaining requests for correction, '
                        'including final public verification and Finish. '
                        'Live host budget notices and tool availability are authoritative; token limits can require earlier closure.')
                    conversation.run()
                    report['first_phase_agent_calls'] = sum(c['kind'] == 'agent' for c in ledger['calls'])
                    report['first_phase_estimated_input'] = ledger['estimated_input']
                    first = evaluate(workspace, task, sample / 'evaluation-first')
                    report['first_evaluation'] = first
                    from feedback_v40 import format_test_feedback
                    feedback = 'All host acceptance and selected upstream regression tests passed.' if first['passed'] else (
                        'Host tests failed. Read-only test feedback follows:\n' +
                        format_test_feedback((sample / 'evaluation-first/test.txt').read_text(encoding='utf-8'),
                                             workspace_root=workspace))
                    report['correction_attempted'] = False
                    if not first['passed'] and policy.can_correct(sum(c['kind']=='agent' for c in ledger['calls']), ledger['estimated_input']):
                        report['correction_attempted'] = True
                        policy.begin_correction()
                        conversation.send_message(feedback + f' Correct only {TASKS[task]["allowed"]}. Do not edit tests. '
                                                  'Inspect the failed assertion, change the smallest relevant code section, '
                                                  'and never submit a replacement whose old and new strings are identical. Finish when ready.')
                        conversation.run()
                    if not first['passed'] and not report['correction_attempted']:
                        report['correction_skipped_reason'] = 'No unreserved work capacity; reserved closure cannot edit'
                    report['correction_agent_calls'] = (
                        sum(c['kind'] == 'agent' for c in ledger['calls'])
                        - report['first_phase_agent_calls'])
                    final = evaluate(workspace, task, sample / 'evaluation-final')
                    final_hashes = hashes(workspace)
                    changed = sorted(k for k in set(original) | set(final_hashes)
                                     if original.get(k) != final_hashes.get(k))
                    report.update(final_evaluation=final, changed_files=changed,
                                  file_boundary_ok=set(changed) <= set(TASKS[task]['allowed']))
                    report['artifact_success'] = bool(final['passed'] and report['file_boundary_ok'])
                    report['sdk_status'] = str(conversation.state.execution_status.value)
                    report['workflow_verification_ok'] = policy.is_verified
                    report['success'] = bool(report['artifact_success'] and report['sdk_status'] == 'finished' and policy.is_verified)
                except Exception as exc:
                    report['error_type'] = type(exc).__name__
                    import traceback
                    report['diagnostic'] = traceback.format_exc().replace(key, '[REDACTED]')
                finally:
                    if 'first_phase_agent_calls' in report:
                        report['correction_agent_calls'] = (
                            sum(c['kind'] == 'agent' for c in ledger['calls'])
                            - report['first_phase_agent_calls'])
                    report['workflow_verification_ok'] = policy.is_verified
                    report.update(elapsed_seconds=time.perf_counter() - started, events=events, ledger=ledger,
                                  agent_metrics=agent_llm.metrics.model_dump(mode='json'),
                                  summary_metrics=summary_llm.metrics.model_dump(mode='json'))
                    if arm == 'pruner_v40':
                        save(sample / 'pruner-state.json', condenser.finalize({'success': report['success']}))
                    if conversation:
                        conversation.close()
                    save(sample / 'report.json', report)
                    summarize(out)
                    print(json.dumps({k: report.get(k) for k in ('task', 'arm', 'repeat', 'success', 'error_type')}) , flush=True)
                if 'insufficient balance' in report.get('diagnostic', '').lower():
                    raise SystemExit('Provider balance exhausted. Failed sample preserved; no further requests. Refill account, then start a fresh output directory for a complete comparison.')
                if ledger.get('provider_failure') or any(word in report.get('diagnostic', '').lower() for word in
                       ('connection error', 'llmserviceunavailableerror', 'request timed out', 'apitimeouterror')):
                    raise SystemExit('Provider connectivity failure. Failed sample preserved; no further model requests. Restore connectivity and use a fresh directory for a complete comparison.')
                if report.get('error_type') and not ledger['calls']:
                    raise SystemExit('Local pre-request failure; preserved report, stopped batch for diagnosis.')
    summarize(out)
    return 0

def summarize(out):
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(out.glob('r*/report.json'))]
    stats = {}
    for arm in ARMS:
        rows = [r for r in reports if r['arm'] == arm]
        token = lambda r, kind, key: r[kind + '_metrics']['accumulated_token_usage'][key]
        stats[arm] = {
            'samples': len(rows), 'successes': sum(r['success'] for r in rows),
            'agent_input_tokens': sum(token(r, 'agent', 'prompt_tokens') for r in rows),
            'summary_input_tokens': sum(token(r, 'summary', 'prompt_tokens') for r in rows),
            'total_input_tokens': sum(token(r, k, 'prompt_tokens') for r in rows for k in ('agent', 'summary')),
            'total_output_tokens': sum(token(r, k, 'completion_tokens') for r in rows for k in ('agent', 'summary')),
            'model_calls': sum(len(r['ledger']['calls']) for r in rows),
            'summary_calls': sum(c['kind'] == 'summary' for r in rows for c in r['ledger']['calls']),
            'failed_calls': sum(c['status'] != 'returned' for r in rows for c in r['ledger']['calls']),
            'peak_estimated_input': max((c['estimated_input'] for r in rows for c in r['ledger']['calls']), default=0),
            'elapsed_seconds': sum(r['elapsed_seconds'] for r in rows),
            'condensation_events': sum(e['type'] == 'Condensation' for r in rows for e in r['events']),
        }
    save(out / 'summary.json', {'expected_samples': PROTOCOL['repeats'] * len(TASKS) * len(ARMS), 'completed_samples': len(reports),
                              'complete': len(reports) == PROTOCOL['repeats'] * len(TASKS) * len(ARMS), 'arms': stats})

if __name__ == '__main__':
    raise SystemExit(main())
