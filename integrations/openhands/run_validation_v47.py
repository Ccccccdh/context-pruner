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

from validation_tasks_v47 import TASKS, COMMIT, SOURCES, INSTANCE, INSTANCE_DIR, prepare, hashes, evaluate, code_revision

ROOT = Path(__file__).resolve().parents[2]
ARMS = ('none', 'native_summary', 'pruner_v42')
# Set in main() before any SDK import; see the v46 temp-directory note there.
_RUNTIME_TEMP = None
PROTOCOL = {
    'version': 'openhands-django-17084-v47-pilot',
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
    'data': 'New django__django-17084 instance selected from the reselected candidate order after django-15563; one exploratory three-arm sample',
    'candidate_selection': 'V47_RESELECTION_RECORD.md; positive and negative host gate before model requests',
    'primary_statistic': 'Mean of all within-task/repeat fractional provider-input savings, including failures and no-condensation samples',
    'trigger_subsets': 'Descriptive only, never replace the primary statistic',
    'closing_request_reserve': 2, 'single_input_headroom': 12000,
    'budget_notice': 'Transient host system message after condensation on every Agent request; ledger records exact notice',
    'closing_tool_policy': 'Verify-only then Finish-only; unchanged observed passed verification can finish early',
    'verification_quality_gate': 'Normal completion also requires current code revision passed public tests',
    'navigation': 'AST source symbol lookup and versioned view progress shared by all arms; no arbitrary shell or source writes',
    'v47_changes': [
        'New instance django__django-17084 (Django 5.0.0a1 era) replacing django__django-15563',
        'Candidate order rebuilt with two added hard constraints: full-tree base checkout, and the instance must import under the frozen Python 3.12 interpreter',
        'v40 file-count rule relaxed from 2-4 to 1-4 python files, because combined with the version constraint it left the eligible set empty',
        'Carries v46 fixes: TEMP/TMP/TMPDIR inside the output directory, and prompts that require a real edit before finish',
    ],
    'v47_carryover': 'Model, endpoint, tools, navigation, budget policy, thresholds and limits are unchanged from v45-pilot-01/v46-pilot-02; only the task binding differs.',
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
    # v46: keep every temporary file inside the output directory. The v45 run
    # showed scratch writes failing for both the Agent and the host test runner.
    # Measured afterwards: the surviving WinError 5 comes from the test
    # interpreter's atexit rmtree of Django's own scratch directory, after the
    # test result has already been produced, and inside the DSH sandbox the
    # restricted ACL on process-created directories makes it unavoidable there.
    # It therefore relocates scratch files (machine %TEMP% -> <out>/.runtime-tmp)
    # without changing scoring; see PILOT_PROTOCOL_V46.md section 7.
    global _RUNTIME_TEMP
    _RUNTIME_TEMP = out / '.runtime-tmp'
    _RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
    for _name in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[_name] = str(_RUNTIME_TEMP)
    os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
    # v46: the host test runner inherits TEMP/TMP above, but make it explicit and
    # per-workspace so no scoring scratch directory can land in the machine %TEMP%.
    # Scoring itself is unchanged: same interpreter, module selection, host patch
    # and assertions as v45; only the scratch location differs.
    import validation_tasks_v47 as _vt
    _frozen_test_environment = _vt._test_environment

    def _temp_redirected_test_environment(test_workspace):
        environment = _frozen_test_environment(test_workspace)
        scratch = _RUNTIME_TEMP / Path(test_workspace).name
        scratch.mkdir(parents=True, exist_ok=True)
        for name in ('TEMP', 'TMP', 'TMPDIR'):
            environment[name] = str(scratch)
        return environment

    _vt._test_environment = _temp_redirected_test_environment
    from pydantic import PrivateAttr, SecretStr
    from litellm import token_counter
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.context.condenser import NoOpCondenser, LLMSummarizingCondenser
    from openhands.sdk.tool import Tool, register_tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.file_editor.definition import FileEditorObservation
    from openhands.tools.file_editor.impl import FileEditorExecutor
    from context_pruner.adapters.openhands_v42 import ContextPrunerCondenserV42

    upstream = ROOT / '.tooling/upstream'
    import subprocess
    for project, source in SOURCES.items():
        checkout = upstream / source['directory']
        # A git checkout is accepted only when its HEAD is the pinned commit. A
        # stray .git (parent repository, copied worktree) would otherwise be
        # accepted as proof of the wrong commit, which is exactly what happened
        # when a project-repo .git leaked into the export directory.
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                              capture_output=True, text=True)
        if head.returncode == 0 and head.stdout.strip() == source['commit']:
            continue
        # Full-tree export: no usable git HEAD. Git cannot reach the network in
        # this sandbox (schannel SEC_E_NO_CREDENTIALS), so the tree came from
        # GitHub's codeload endpoint for the pinned commit. Require the recorded
        # provenance to name that exact commit, that directory and that source.
        provenance_path = ROOT / f'.tooling/export_{project}_{source["directory"]}.json'
        assert provenance_path.is_file(), (
            f'no usable git HEAD (got {head.stdout.strip()[:12] or head.returncode}) and '
            f'no provenance record for {checkout}: {provenance_path}')
        provenance = json.loads(Path(provenance_path).read_text(encoding='utf-8'))
        assert provenance['base_commit'] == source['commit'], (provenance_path, provenance['base_commit'])
        assert Path(provenance['dest']).name == source['directory'], provenance['dest']
        assert 'codeload.github.com/django/django/tar.gz/' in provenance['url'], provenance['url']
    manifest = dict(PROTOCOL)
    manifest['versions'] = {n: importlib.metadata.version(n) for n in
                            ('openhands-sdk', 'openhands-tools', 'litellm', 'context-pruner')}
    sources = [Path(__file__), Path(__file__).with_name('validation_tasks_v47.py'), Path(__file__).with_name('validate_candidate_v45.py'), Path(__file__).with_name('edit_retry_guard_v33.py'), Path(__file__).with_name('task_suite.py'), ROOT / 'context_pruner/adapters/openhands_v2.py',
               ROOT / 'context_pruner/adapters/openhands_v5.py', ROOT / 'context_pruner/adapters/openhands_v4.py']
    sources += list((ROOT / 'context_pruner').rglob('*.py'))
    sources += [Path(__file__).with_name('bounded_llm_v30.py'),
                Path(__file__).with_name('budget_policy_v30.py'),
                Path(__file__).with_name('budget_policy_v31.py'),
                Path(__file__).with_name('budget_policy_v39.py'),
                Path(__file__).with_name('budget_policy_v42.py'),
                Path(__file__).with_name('source_navigation_v31.py'),
                Path(__file__).with_name('source_navigation_v42.py'),
                Path(__file__).with_name('symbol_tool_v31.py'),
                Path(__file__).with_name('symbol_tool_v35.py'),
                Path(__file__).with_name('symbol_tool_v42.py'),
                Path(__file__).with_name('symbol_tool_v44.py'),
                ROOT / 'tests/test_openhands_symbol_v44.py',
                Path(__file__).with_name('crosscheck_budget_completion.py'),
                ROOT / 'runs/stage5-openhands/django-17084-v47-gate-01/gate.json',
                INSTANCE_DIR / 'instance.json', INSTANCE_DIR / 'host-tests.patch',
                INSTANCE_DIR / 'reference.patch',
                Path(__file__).with_name('audit_validation_v47.py'),
                Path(__file__).with_name('run_validation_windows_v47.py'),
                Path(__file__).with_name('verification_tool_django_v45.py'),
                ROOT / 'tests/test_openhands_budget_v39.py',
                Path(__file__).with_name('feedback_v40.py'),
                Path(__file__).with_name('feedback_v42.py'),
                Path(__file__).with_name('task_state_v42.py'),
                ROOT / 'tests/test_openhands_feedback_v40.py',
                ROOT / 'tests/test_openhands_v42_offline.py',
                ROOT / 'tests/test_openhands_budget_v42.py',
                ROOT / 'tests/test_openhands_v41_scope.py',
                Path(__file__).with_name('edit_scope_guard_v41.py'),
                Path(__file__).with_name('replay_v41_contract.py'),
                Path(__file__).with_name('prototype_v11.py'),
                Path(__file__).with_name('PILOT_PROTOCOL_V41.md'),
                Path(__file__).with_name('V40_SELECTION_RULES.md'),
                Path(__file__).with_name('V40_FEEDBACK_REVIEW.md'),
                Path(__file__).with_name('V45_NEW_TASK_SELECTION.md'),
                Path(__file__).with_name('PILOT_PROTOCOL_V45.md'),
                Path(__file__).with_name('PILOT_PROTOCOL_V46.md'),
                Path(__file__).with_name('PILOT_PROTOCOL_V47.md'),
                Path(__file__).with_name('V47_RESELECTION_RECORD.md'),
                Path(__file__).with_name('V47_CHECKOUT_DEFECT_AND_RESELECTION.md')]
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
            # v47 instance values, measured by the zero-API gate and frozen in
            # runs/stage5-openhands/django-17084-v47-gate-01/gate.json:
            # public `aggregation` module is green (124 tests) on the base
            # workspace, and the host-patched copy fails on the added target test.
            assert regression['passed'] and regression['ran'] == 124 and regression['workspace_unchanged_by_test'], regression
            result = evaluate(sample / 'workspace', task, sample / 'evaluation')
            assert (not result['passed'] and result['ran'] == 125
                    and result['failures'] == 'errors=1'
                    and result['workspace_unchanged_by_test']), result
            baselines[task] = result
        save(out / 'baseline.json', baselines)
        print(json.dumps(baselines, indent=2))
        return 0
    if not args.run:
        print(json.dumps(manifest, indent=2))
        return 0
    baseline = json.loads((out / 'baseline.json').read_text(encoding='utf-8'))
    reference = json.loads((ROOT / 'runs/stage5-openhands/django-17084-v47-gate-01/gate.json').read_text(encoding='utf-8'))
    assert set(baseline) == set(TASKS)
    task = next(iter(TASKS))
    assert reference['instance'] == INSTANCE['instance_id'] and reference['base_commit'] == COMMIT['django']
    assert (not baseline[task]['passed']
            and not reference['base_host']['passed']
            and reference['reference_host']['passed']
            and any(x.startswith('Ran 125 tests') for x in reference['reference_host']['summary']))
    assert (ContextPrunerCondenserV42().trigger_tokens,
            ContextPrunerCondenserV42().target_tokens,
            ContextPrunerCondenserV42().hard_tokens) == (PROTOCOL['trigger_input_tokens'],
            PROTOCOL['pruner_target_tokens'], PROTOCOL['pruner_hard_tokens'])
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise SystemExit('DEEPSEEK_API_KEY unavailable')

    from edit_retry_guard_v33 import EditRetryGuard
    from bounded_llm_v30 import BudgetAwareLLM as BoundedLLM
    from budget_policy_v42 import StrictFeedbackReserveBudgetPolicy as BudgetPolicy
    from integrations.openhands.source_navigation_v42 import SourceNavigatorV42 as SourceNavigator
    from integrations.openhands.symbol_tool_v44 import ScopedSymbolsToolV44 as BaseSymbolsTool
    from integrations.openhands.task_state_v42 import TaskState
    from openhands.sdk.llm import TextContent
    from edit_scope_guard_v41 import scope_error

    class ScopedEditorExecutor(FileEditorExecutor):
        def __init__(self, **kw):
            self.scope = Path(kw['workspace_root']).resolve()
            super().__init__(**kw)

        def __call__(self, action, conversation=None):
            boundary_hint = scope_error(action.command, action.path, self.scope, current['allowed'])
            if boundary_hint:
                return FileEditorObservation.from_text(text=boundary_hint,
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
    from verification_tool_django_v45 import ScopedTestsTool
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
                task_state = TaskState(TASKS[task]['problem'][:900],
                                       ', '.join(TASKS[task]['allowed']))
                current['edit_guard'] = EditRetryGuard()
                ledger = {'calls': [], 'estimated_input': 0}
                policy = BudgetPolicy(lambda: code_revision(workspace, task),
                    initial_work_requests=PROTOCOL['initial_work_requests'],
                    correction_call_limit=PROTOCOL['host_feedback_correction_reserve'],
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
                             if arm == 'native_summary' else ContextPrunerCondenserV42(
                                 protected_contract='Edit only: ' + ', '.join(TASKS[task]['allowed']) +
                                                    '. All other source files are read-only.'))
                if arm == 'pruner_v42':
                    condenser.set_current_state(task_state.render(code_revision(workspace, task)))
                agent = Agent(llm=agent_llm, tools=[Tool(name=ScopedEditorTool.name), Tool(name=ScopedSymbolsTool.name), Tool(name=ScopedTestsTool.name, params={'workspace': str(workspace), 'task': task, 'destination': str(sample / 'tool-tests')})],
                              include_default_tools=['FinishTool'], condenser=condenser)
                events = []
                def callback(event):
                    policy.observe_event(event)
                    if arm == 'pruner_v42' and type(event).__name__ == 'ObservationEvent':
                        condenser.set_current_state(task_state.render(code_revision(workspace, task)))
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
                        'No arbitrary shell is available. '
                        'Your goal is to make the failing behavior pass, not merely to understand it. '
                        'Do not call finish before you have actually edited one of the allowed files. '
                        'After the edit, run scoped_tests and read its result; call finish once your change is in '
                        'place and the public regression selection passes. The host then runs separate feature '
                        'acceptance. Do not keep exploring after verification. Do not modify tests or documentation. '
                        'If an edit is rejected as unchanged, view the narrow current function range and make a changed replacement; '
                        'never resubmit identical old_str and new_str. '
                        f'You have at most {PROTOCOL["max_agent_calls_per_sample"]} Agent requests in total. '
                        'In this first phase, use up to 24 requests. Having requests left over is normal and is not a '
                        'reason to stop early; stop only when the request ceiling is reached or the work is truly done. '
                        'Running out of requests is not a valid reason to finish while the target behavior still fails. '
                        'The host then runs separate feature tests. If they fail, the host will return '
                        'read-only failure feedback and reserve up to ten remaining requests for correction, '
                        'including final public verification and Finish. '
                        'Live host budget notices and tool availability are authoritative; token limits can require earlier closure.')
                    conversation.run()
                    report['first_phase_agent_calls'] = sum(c['kind'] == 'agent' for c in ledger['calls'])
                    report['first_phase_estimated_input'] = ledger['estimated_input']
                    first = evaluate(workspace, task, sample / 'evaluation-first')
                    report['first_evaluation'] = first
                    from integrations.openhands.feedback_v42 import format_correction_feedback
                    task_state.record_verification(code_revision(workspace, task),
                                                   'Host acceptance passed' if first['passed'] else
                                                   'Host acceptance failed: ' + first['failures'])
                    feedback = 'All host acceptance and selected upstream regression tests passed.' if first['passed'] else (
                        'Host tests failed. Read-only test feedback follows:\n' +
                        format_correction_feedback((sample / 'evaluation-first/test.txt').read_text(encoding='utf-8'),
                                                   workspace_root=workspace))
                    if not first['passed']:
                        task_state.record_feedback(code_revision(workspace, task), feedback[:1000])
                    if arm == 'pruner_v42':
                        condenser.set_current_state(task_state.render(code_revision(workspace, task)))
                    report['correction_attempted'] = False
                    if not first['passed'] and policy.can_correct(sum(c['kind']=='agent' for c in ledger['calls']), ledger['estimated_input']):
                        report['correction_attempted'] = True
                        policy.begin_correction(sum(c['kind'] == 'agent' for c in ledger['calls']))
                        conversation.send_message(feedback + f' Correct only {TASKS[task]["allowed"]}. Do not edit tests. '
                                                  'Inspect the failed assertion, change the smallest relevant code section, '
                                                  'and never submit a replacement whose old and new strings are identical. '
                                                  'You must land an actual edit and re-run scoped_tests before finishing; '
                                                  'a correction that changes no allowed source file cannot succeed. '
                                                  'Finish only when the change is written to disk.')
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
                    if arm == 'pruner_v42':
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

