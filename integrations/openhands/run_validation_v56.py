"""v56 formal runner: pre-registered prefix selection, then three verifiable branches.

v56 is the v55 revision whose selection gate was corrected by what batch 01 measured
(see ``PILOT_PROTOCOL_V56.md`` §3): the measured condenser-view rule now uses the
mechanism's own condition (no extra margin), and the frozen phase-1 ladder gained a third
rung.  No compression-mechanism threshold changes.

It carries over the two v55 fixes, both frozen before the first paid request:

1. **The prefix must end in a state that needs correction, and the plugin must
   actually cross its trigger.**  v54 ran one committed task and its first phase
   ended with the host target test already passing, so the branches had nothing
   to correct and the plugin condensed zero times.  v55 walks the pre-registered
   ``(rung, task)`` ladder over the three frozen tasks in their frozen order,
   selects the first attempt whose phase-1 end state fails the host target test,
   and refuses to fork unless a **zero-API replay of the real condenser** on that
   exact prefix state says it would condense (see ``prefix_selection_v55``).

2. **Every branch is independently verifiable.**  v54 left only the last branch's
   bytes on the single shared path.  v55 persists, inside each branch's own
   directory, the bytes of every file that differs from the frozen prefix, the
   final cache-free file map, the branch event stream and an artifact index; the
   audit rebuilds each branch's final workspace from the frozen snapshot plus
   that branch's own bytes and re-runs the host target test there.

Stages:
    check     zero-API gate: prepare + hash + negative/positive host baseline for
              all three frozen tasks (no model request)
    select    the paid prefix-selection walk, ending in the frozen shared prefix
    branches  rebuild the frozen prefix from SDK persistence and fork the three
              arms sequentially at one absolute path
    run       select then branches
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from validation_tasks_v49 import (TASKS, COMMIT, SOURCES, INSTANCES, prepare,
                                  hashes, evaluate, code_revision)

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

ARMS = ('none', 'native_summary', 'pruner_v1')
CONDENSER_CLASS = {'none': 'NoOpCondenser', 'native_summary': 'LLMSummarizingCondenser',
                   'pruner_v1': 'ContextPrunerCondenserV51'}
DEFAULT_BRANCH_ORDER = ('none', 'native_summary', 'pruner_v1')
ARM_ALIAS = {'pruner_v1': 'pruner_v51'}

#: The three frozen tasks, in their frozen order (``validation_tasks_v49`` order).
FROZEN_TASKS = ('django_referenced_window_wrapping',
                'django_lookup_allowed_foreign_primary',
                'django_list_editable_atomicity')
#: The corpus arm: no condenser.  The prefix must be produced by an arm that
#: never compresses, so the shared history is exactly what the model did.
FROZEN_ARM = 'none'

#: Per-task negative/positive host gate, produced by the zero-API ``--check``.
GATE_FILES = {
    'django_referenced_window_wrapping': '.tooling/gates/v49-django-17084.json',
    'django_lookup_allowed_foreign_primary': '.tooling/gates/v49-django-16661.json',
    'django_list_editable_atomicity': '.tooling/gates/v49-django-16100.json',
}
#: Frozen baseline verdicts.  The host patch *adds* one target test in all three
#: tasks, so the public module run has exactly one test fewer than the host run
#: and is green on the untouched baseline.
EXPECTED_BASELINE = {
    'django_referenced_window_wrapping': {
        'public_passed': True, 'public_ran': 124, 'public_failures': None,
        'host_ran': 125, 'host_failures': 'errors=1'},
    'django_lookup_allowed_foreign_primary': {
        'public_passed': True, 'public_ran': 162, 'public_failures': None,
        'host_ran': 163, 'host_failures': 'failures=1'},
    'django_list_editable_atomicity': {
        'public_passed': True, 'public_ran': 74, 'public_failures': None,
        'host_ran': 75, 'host_failures': 'failures=1, skipped=7'},
}
#: The single file each frozen instance's reference fix touches, written out here
#: as literals so ``--check`` compares the live task table against the frozen
#: edit scope instead of against itself.
EXPECTED_ALLOWED = {
    'django_referenced_window_wrapping': ['django/db/models/sql/query.py'],
    'django_lookup_allowed_foreign_primary': ['django/contrib/admin/options.py'],
    'django_list_editable_atomicity': ['django/contrib/admin/options.py'],
}

PROTOCOL = {
    'version': 'openhands-django-v56-prefix-selection-fork',
    'model': 'openai/deepseek-v4-flash', 'temperature': 0,
    'thinking': 'disabled', 'api_retries': 0,
    'tasks': list(FROZEN_TASKS),
    'task_order': list(FROZEN_TASKS),
    'check_order': list(reversed(FROZEN_TASKS)),
    'check_order_reason': ('the zero-API check prepares the reverse of the paid order so the '
                           'first paid attempt can reuse the check\'s verified baseline '
                           'workspace instead of re-copying 20,128 files; the paid selection '
                           'order itself is unchanged and remains the frozen order'),
    'arms': list(ARMS), 'repeats': 1,
    'prefix_arm': FROZEN_ARM,
    'prefix_condenser': CONDENSER_CLASS[FROZEN_ARM],
    'branch_condensers': {arm: CONDENSER_CLASS[arm] for arm in ARMS},
    'branch_order': list(DEFAULT_BRANCH_ORDER),
    'branch_rotation': 'fixed prefix order for batch 01; --rotate is frozen per batch',
    'host_feedback_rounds_per_branch': 2,
    'phase1_work_request_ladder': [24, 26, 28],
    'phase1_request_ceiling_per_rung': [26, 28, 30],
    'initial_work_requests': 24, 'first_phase_max_requests': 26,
    'trigger_input_tokens': 28000, 'pruner_target_tokens': 22400,
    'pruner_hard_tokens': 39200, 'native_max_tokens': 33600,
    'native_max_events': 240,
    'trigger_prediction_margin_tokens': 0,
    'trigger_prediction_ledger_margin_tokens': 2000,
    'trigger_prediction_margin_note': (
        'the measured rule carries NO margin in v56: the measured quantity is exactly the '
        'input the condenser is given, so there is no measurement error for a margin to '
        'absorb, and the runner re-measures it at the fork and asserts the frozen verdict. '
        'Batch 01 is why this changed: its second attempt measured 28,775 view tokens against '
        'the frozen 28,000 trigger, the real condenser returned a Condensation (52 events '
        'forgotten, 28,775 -> 14,640), and only v55\'s pre-registered 2,000-token margin '
        'refused the fork. The ledger rule keeps its 2,000-token margin and stays a floor: in '
        'that same attempt the ledger estimate was 34,643 while the condenser view held '
        '28,775.'),
    'max_agent_calls_per_sample': 36, 'max_summary_calls_per_sample': 16,
    'host_feedback_correction_reserve': 10, 'closing_request_reserve': 2,
    'max_output_tokens': 3072, 'max_single_estimated_input': 80000,
    'max_total_estimated_input_per_sample': 2000000,
    'single_input_headroom': 12000,
    'min_free_disk_gb': 1.5,
    'phase_plan': ['shared_prefix_read_and_implement',
                   'host_feedback_correction_only_if_failed'],
    'tools': ['scoped_editor', 'scoped_symbols', 'scoped_tests', 'finish'],
    'max_sdk_steps_per_phase': 200,
    'tool_boundary': 'editor executor refuses every edit outside the frozen allowed list; '
                     'symbol search and file view are restricted to the frozen research set; '
                     'no shell is registered; the same boundary applies to the prefix and all '
                     'branches',
    'host_feedback_channel': 'host target test on a scoring copy (host-tests.patch applied '
                             'there only)',
    'prefix_selection': 'pre-registered three-rung rung/task ladder over the three frozen '
                        'tasks in their frozen order; the first attempt whose phase-1 end state '
                        'fails the host target test AND whose frozen zero-API trigger '
                        'prediction fires is selected, whatever the branches later do',
    'prefix_reuse': 'the branches stage rebuilds the prefix conversation from SDK persistence '
                    'and must reproduce the frozen event-ID sequence and full-file workspace '
                    'hashes; a mismatch aborts before any branch request',
    'branch_workspace_policy': 'one absolute workspace path; branches are strictly sequential; '
                               'full-hash restore from a read-only snapshot before each branch; '
                               'each branch persists its own final bytes, final file map, event '
                               'stream, ledger and report',
    'branch_persistence': 'final-files/ (bytes of every file differing from the frozen prefix), '
                          'final-hashes.json (cache-free full-file map), events.json (branch '
                          'event stream), artifacts.json (per-file SHA-256 index), ledger.json, '
                          'report.json, plus a batch-level branch-artifact-index.json',
    'branch_verification': 'the audit rebuilds each branch\'s final workspace from the frozen '
                           'snapshot plus that branch\'s own persisted bytes, checks the '
                           'rebuilt full-file hash map, and re-runs the host target test there',
    'primary_statistic': 'post-fork incremental complete-total provider tokens per branch '
                         '(including every summary/native request); failures and cap hits '
                         'retained',
    'secondary_statistic': 'whole-run complete-total tokens counting every prefix-selection '
                           'attempt, including the unselected ones, exactly once',
    'compaction_rule': 'if the plugin branch never condenses after the fork, the pilot reports '
                       '"mechanism not triggered" and no compression effect is claimed',
    'cost': 'provider tokens only; money unknown, SDK model price not mapped',
    'acceptance': 'host target test on the final workspace + selected unchanged upstream '
                  'regression module + file boundary',
    'upstream_test_limitation': 'selected module only; not the full upstream suite',
    'data': 'public Django SWE-bench-verified instances; no synthetic payload',
    'boundary_statement': 'one task x one prefix x three arms cannot estimate the plugin '
                          'average effect on any task population',
    'v56_changes': [
        'The measured condenser-view rule now uses the mechanism\'s own condition (view tokens '
        'above the frozen 28,000 trigger) with no extra margin, because batch 01 showed the '
        'pre-registered 2,000-token margin refusing a prefix whose real condenser already '
        'returned a Condensation',
        'The frozen phase-1 work ladder has three rungs (24 / 26 / 28), all inside the shared '
        '36-request ceiling, because batch 01 showed a failing task can close its first phase '
        'in as few as 6 requests',
        'The ledger arithmetic rule keeps its 2,000-token margin and remains a floor',
    ],
    'v55_carryover': [
        'Pre-registered prefix selection over three frozen tasks in their frozen order, with a '
        'frozen rung ladder, instead of one committed task',
        'A zero-API condenser replay gates the fork: the fork only happens if the real '
        'configured condenser condenses on the exact prefix state the branch will see first',
        'Per-branch persistence: final bytes, final file map, event stream, ledger, report and '
        'an artifact index inside each branch directory',
        'The audit re-runs the host target test for every branch from that branch\'s own '
        'persisted bytes instead of restoring the shared path',
        'Scoring copies from every host evaluation are digested and removed, so a batch no '
        'longer keeps one full Django copy per evaluation',
        'Task set, model, endpoint, tool boundary, navigation, prompts, budget policy values, '
        'per-sample limits, condenser implementation and the trigger/target/hard thresholds '
        'are unchanged from v54/v55',
    ],
}


#: v56's pre-registered selection rule, written from the frozen ladder itself so the
#: protocol document and the implementation cannot drift apart silently.  The rule logic
#: lives in ``prefix_selection_v55`` (``attempt_plan`` / ``next_step`` /
#: ``selection_decision`` / ``predict_trigger``); only the rule text and its fingerprint are
#: versioned here, because they name this version's ladder.
SELECTION_RULE = (
    'Attempts run in the pre-registered order: ' +
    ', then '.join(f'rung {index} (work cap {cap})'
                   for index, cap in enumerate(PROTOCOL['phase1_work_request_ladder'])) +
    ' over the three frozen tasks in their frozen order, and no further rung. A task that '
    'already passed the host target test is not re-attempted. An attempt qualifies only when '
    'its phase-1 end state FAILS the host target test and the frozen zero-API prediction '
    'reports that the plugin condenser would condense on that prefix state; the first '
    'qualifying attempt in that order is selected, whatever the branches later do. If no '
    'attempt qualifies, the round reports "mechanism still not triggered", no branch request '
    'is issued and the mechanism thresholds are not touched.')


def rule_fingerprint(protocol: dict) -> str:
    """A digest of the frozen rule inputs, so the report can pin what was applied."""
    payload = {
        'rule': SELECTION_RULE,
        'tasks': list(protocol['tasks']),
        'ladder': list(protocol['phase1_work_request_ladder']),
        'trigger_input_tokens': protocol['trigger_input_tokens'],
        'trigger_prediction_margin_tokens': protocol['trigger_prediction_margin_tokens'],
        'trigger_prediction_ledger_margin_tokens':
            protocol['trigger_prediction_ledger_margin_tokens'],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def timer() -> float:
    return time.perf_counter()


def digest_map(mapping: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()


def digest_ids(ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(list(ids)).encode()).hexdigest()


def free_disk_gb(path: Path) -> float:
    return round(shutil.disk_usage(str(path)).free / 1024 ** 3, 3)


# --------------------------------------------------------------------------
# Prompts (frozen literals; the branch prompt text is identical for all arms)
# --------------------------------------------------------------------------

def prefix_prompt(workspace: Path, task: str, work_cap: int) -> str:
    spec = TASKS[task]
    return (
        f'Work only inside {workspace}. Implement this complete feature contract:\n'
        + spec['problem'] + '\n'
        f'Edit only {spec["allowed"]}. TASK.md repeats the contract. '
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
        f'In this first phase, use up to {work_cap} requests. Having requests left over is normal '
        'and is not a reason to stop early; stop only when the request ceiling is reached or the '
        'work is truly done. '
        'Running out of requests is not a valid reason to finish while the target behavior still fails. '
        'The host then runs separate feature tests. If they fail, the host will return '
        'read-only failure feedback and reserve up to ten remaining requests for correction, '
        'including final public verification and Finish. '
        'Live host budget notices and tool availability are authoritative; token limits can require earlier closure.')


def correction_prompt(task: str, feedback: str) -> str:
    return (feedback + f' Correct only {TASKS[task]["allowed"]}. Do not edit tests. '
            'Inspect the failed assertion, change the smallest relevant code section, '
            'and never submit a replacement whose old and new strings are identical. '
            'You must land an actual edit and re-run scoped_tests before finishing; '
            'a correction that changes no allowed source file cannot succeed. '
            'Finish only when the change is written to disk.')


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

@dataclass
class EngineConfig:
    """Everything the flow needs, so the zero-API gate can drive it."""

    out: Path
    task: str = FROZEN_TASKS[0]
    stage: str = 'check'
    rotate: bool = False
    resume: bool = False
    api_key: str | None = None
    workspace: Path | None = None
    prepare_workspace: bool = True
    evaluator: Callable[..., dict] | None = None
    snapshot: Path | None = None
    plan: dict = field(default_factory=dict)
    #: Test seam: the gate injects a deterministic prediction so it can exercise
    #: both the abort path and the fork path without a real 28k-token prefix.
    predictor: Callable[..., dict] | None = None


class Engine:
    """Prefix selection, the snapshot and the three sequential branches."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.out = Path(config.out)
        self.workspace = (Path(config.workspace) if config.workspace
                          else self.out / 'workspaces' / 'prefix')
        self.snapshot = (Path(config.snapshot) if config.snapshot
                         else self.out / 'prefix-snapshot')
        self.experiment_root = self.out
        self.evaluator = config.evaluator or evaluate
        #: The frozen protocol actually in force (the gate patches this snapshot).
        self.plan: dict = dict(config.plan) if config.plan else dict(PROTOCOL)
        self.timing: dict[str, float] = {}
        self.prepared: dict = {}
        self.tool_ctx: dict[str, Any] = {}
        self.ledger: dict[str, Any] = {}
        self.policy = None
        self.prefix_llm = None
        self.prefix_summary_llm = None
        self.prefix_condenser = None
        self.source = None
        self.task_state = None
        self.state = None
        self.current_task = config.task
        self.branch_order = list(DEFAULT_BRANCH_ORDER)
        if config.rotate:
            self.branch_order = self.branch_order[1:] + self.branch_order[:1]
        self.attempts: list[dict] = []

    # -- timing / disk ----------------------------------------------------
    def add_time(self, segment: str, seconds: float) -> None:
        self.timing[segment] = round(self.timing.get(segment, 0.0) + seconds, 2)

    def timeit(self, segment: str):
        engine = self

        class _Block:
            def __enter__(self):
                self.started = timer()
                return self

            def __exit__(self, *exc):
                engine.add_time(segment, timer() - self.started)
                return False

        return _Block()

    def require_disk(self, stage: str) -> float:
        free = free_disk_gb(self.out if self.out.exists() else ROOT)
        minimum = float(self.plan.get('min_free_disk_gb',
                                             PROTOCOL['min_free_disk_gb']))
        if free < minimum:
            raise RuntimeError(
                f'{stage}: only {free} GB free under {self.out}; the frozen protocol '
                f'requires at least {minimum} GB before this stage')
        return free

    def evaluate(self, workspace: Path, task: str, destination: Path,
                 regression_only: bool = False) -> dict:
        """Run the host test and remove its scoring copy after digesting it.

        ``validation_tasks_v45.evaluate`` copies the whole workspace to
        ``destination/host-workspace`` and applies the host patch there.  A batch
        with five evaluations and three branches would otherwise keep a 122 MB
        Django copy per call, which the frozen disk budget does not allow.  The
        scoring copy is a derived artifact - the evidence is the test output and
        the returned verdict, both of which are kept.
        """
        started = timer()
        result = self.evaluator(workspace, task, destination, regression_only=regression_only)
        self.add_time('host_test_seconds', timer() - started)
        scratch = Path(destination) / 'host-workspace'
        if scratch.is_dir():
            removal = timer()
            files = sum(1 for path in scratch.rglob('*') if path.is_file())
            result['scoring_copy'] = {
                'file_count': files, 'removed_after_evaluation': True,
                'reason': ('derived scoring copy; the verdict and test.txt are the evidence and '
                           'the frozen disk budget does not keep one Django tree per '
                           'evaluation')}
            shutil.rmtree(scratch, ignore_errors=True)
            self.add_time('scoring_copy_cleanup_seconds', timer() - removal)
        return result

    # -- workspace -------------------------------------------------------
    def prepare(self, task: str | None = None) -> dict:
        """Create the workspace for ``task``, reusing a verified check baseline.

        The zero-API check prepares and hashes every frozen task's baseline.  An
        existing workspace is reused only when its complete cache-free file map
        is byte-identical to the digest the check recorded **for this task**;
        otherwise it is removed and recreated from the frozen upstream tree.
        """
        task = task or self.current_task
        started = timer()
        reused = False
        if self.config.prepare_workspace:
            expected = None
            check_path = self.out / 'check.json'
            if check_path.is_file():
                expected = ((load(check_path).get('tasks') or {}).get(task) or {}).get(
                    'baseline_hashes_digest')
            if expected and self.workspace.is_dir():
                observed = hashes(self.workspace)
                if digest_map(observed) == expected:
                    reused = True
                    original = observed
            if not reused:
                if self.workspace.exists():
                    shutil.rmtree(self.workspace)
                original = prepare(ROOT / '.tooling/upstream', self.workspace, task)
        else:
            original = hashes(self.workspace)
        self.add_time('prepare_reuse_verify_seconds' if reused else 'prepare_copy_hash_seconds',
                      timer() - started)
        self.prepared = {'reused_gate_workspace': reused, 'file_count': len(original),
                         'task': task}
        return original

    def bind_tools(self, edit_guard) -> None:
        from integrations.openhands.fork_tools_v54 import bind_tool_context
        self.tool_ctx.update(
            workspace=self.workspace,
            allowed=[self.workspace / p for p in TASKS[self.current_task]['allowed']],
            edit_guard=edit_guard,
        )
        bind_tool_context(**self.tool_ctx)

    # -- shared pieces ---------------------------------------------------
    def make_llm(self, kind: str, sample_name: str, ledger, policy):
        from pydantic import SecretStr
        from bounded_llm_v30 import BudgetAwareLLM
        llm = BudgetAwareLLM(
            model=PROTOCOL['model'], api_key=SecretStr(self.config.api_key or 'unused'),
            base_url='https://api.deepseek.com', temperature=0, num_retries=0, timeout=60,
            max_output_tokens=PROTOCOL['max_output_tokens'],
            litellm_extra_body={'thinking': {'type': 'disabled'}},
            usage_id=f'{sample_name}-{kind}')
        llm._ledger = ledger
        llm._sample = self.out / sample_name
        llm._kind = kind
        llm._policy = policy
        llm._max_summary_calls = PROTOCOL['max_summary_calls_per_sample']
        return llm

    def new_policy(self, *, work_cap=None, prefix_used_agent_calls=0,
                   prefix_used_estimated_input=0):
        from budget_policy_v54 import PrefixCarryingBudgetPolicy
        return PrefixCarryingBudgetPolicy(
            lambda: code_revision(self.workspace, self.current_task),
            prefix_used_agent_calls=prefix_used_agent_calls,
            prefix_used_estimated_input=prefix_used_estimated_input,
            initial_work_requests=work_cap or PROTOCOL['initial_work_requests'],
            correction_call_limit=PROTOCOL['host_feedback_correction_reserve'],
            max_agent_calls=PROTOCOL['max_agent_calls_per_sample'],
            reserve_requests=PROTOCOL['closing_request_reserve'],
            max_single_input=PROTOCOL['max_single_estimated_input'],
            max_total_input=PROTOCOL['max_total_estimated_input_per_sample'],
            single_input_headroom=PROTOCOL['single_input_headroom'])

    def make_condenser(self, arm: str, summary_llm, task: str | None = None):
        from openhands.sdk.context.condenser import NoOpCondenser, LLMSummarizingCondenser
        from context_pruner.adapters.openhands_v51 import ContextPrunerCondenserV51
        task = task or self.current_task
        if arm == 'none':
            return NoOpCondenser()
        if arm == 'native_summary':
            return LLMSummarizingCondenser(
                llm=summary_llm, max_size=PROTOCOL['native_max_events'],
                max_tokens=PROTOCOL['native_max_tokens'], keep_first=2,
                hard_context_reset_max_retries=1)
        return ContextPrunerCondenserV51(
            trigger_tokens=PROTOCOL['trigger_input_tokens'],
            target_tokens=PROTOCOL['pruner_target_tokens'],
            hard_tokens=PROTOCOL['pruner_hard_tokens'],
            protected_contract='Edit only: ' + ', '.join(TASKS[task]['allowed']) +
                               '. All other source files are read-only.')

    def agent_for(self, arm: str, agent_llm, condenser, sample_name: str):
        from openhands.sdk import Agent
        from openhands.sdk.tool import Tool
        from integrations.openhands.fork_tools_v54 import (
            EDITOR_TOOL_NAME, SYMBOLS_TOOL_NAME, TESTS_TOOL_NAME)
        import budget_policy_v54
        # The policy allow-list and the registered tools must agree by name, or
        # a work-phase request silently loses the editor.
        assert {EDITOR_TOOL_NAME, SYMBOLS_TOOL_NAME, TESTS_TOOL_NAME} == \
            set(budget_policy_v54.TOOL_NAMES)
        return Agent(
            llm=agent_llm,
            tools=[Tool(name=EDITOR_TOOL_NAME), Tool(name=SYMBOLS_TOOL_NAME),
                   Tool(name=TESTS_TOOL_NAME,
                        params={'workspace': str(self.workspace), 'task': self.current_task,
                                'destination': str(self.out / sample_name / 'tool-tests')})],
            include_default_tools=['FinishTool'], condenser=condenser)

    # -- accounting helpers ---------------------------------------------
    @staticmethod
    def usage(metrics: dict) -> dict:
        totals = metrics['accumulated_token_usage']
        return {'prompt': totals['prompt_tokens'], 'completion': totals['completion_tokens']}

    def complete_total_tokens(self, agent_metrics: dict, summary_metrics: dict) -> int:
        agent = self.usage(agent_metrics)
        summary = self.usage(summary_metrics)
        return agent['prompt'] + agent['completion'] + summary['prompt'] + summary['completion']

    def condensation_count(self, conversation) -> int:
        if conversation is None:
            return 0
        return sum(type(event).__name__ == 'Condensation'
                   for event in conversation.state.events)

    def prefix_event_ids(self) -> list[str]:
        return [str(event.id) for event in self.source.state.events]

    def host_feedback(self, result: dict, evaluation_dir: Path) -> str:
        if result['passed']:
            return 'All host acceptance and selected upstream regression tests passed.'
        from integrations.openhands.feedback_v42 import format_correction_feedback
        return ('Host tests failed. Read-only test feedback follows:\n' +
                format_correction_feedback(
                    (evaluation_dir / 'test.txt').read_text(encoding='utf-8'),
                    workspace_root=self.workspace))

    # -- trigger prediction ---------------------------------------------
    def predict_trigger(self, sample_name: str, *, task: str, host_result: dict,
                        evaluation_dir: Path, ledger: dict | None = None) -> dict:
        """Zero-API: would the configured plugin condenser condense here?"""
        from integrations.openhands import prefix_selection_v55 as selection
        ledger = ledger if ledger is not None else self.ledger
        feedback = self.host_feedback(host_result, evaluation_dir)
        text = correction_prompt(task, feedback)
        state_render = ''
        if self.task_state is not None:
            state_render = self.task_state.render(code_revision(self.workspace, task))
        started = timer()
        if self.config.predictor is not None:
            prediction = self.config.predictor(
                events=list(self.source.state.events), protocol=self.plan,
                task=task, allowed=list(TASKS[task]['allowed']), correction_text=text,
                current_state=state_render,
                ledger_last_estimated_input=selection.ledger_last_estimated_input(ledger))
        else:
            prediction = selection.predict_trigger(
                events=list(self.source.state.events), protocol=self.plan, task=task,
                allowed=list(TASKS[task]['allowed']), correction_text=text,
                condenser_factory=lambda: self.make_condenser('pruner_v1', None, task=task),
                current_state=state_render,
                ledger_last_estimated_input=selection.ledger_last_estimated_input(ledger))
        self.add_time('trigger_prediction_seconds', timer() - started)
        prediction['sample'] = sample_name
        return prediction

    # -- prefix attempts -------------------------------------------------
    def run_attempt(self, task: str, rung: int, work_cap: int, index: int) -> dict:
        """One pre-registered phase-1 attempt of the corpus arm on ``task``."""
        from integrations.openhands.fork_tools_v54 import register_v54_tools
        from openhands.sdk import Conversation

        register_v54_tools()
        self.require_disk(f'prefix attempt {index} ({task}, rung {rung})')
        self.current_task = task
        original = self.prepare(task)

        from edit_retry_guard_v33 import EditRetryGuard
        from integrations.openhands.source_navigation_v42 import SourceNavigatorV42
        from integrations.openhands.task_state_v42 import TaskState
        from same_prefix_fork_v54 import capture_workspace, tree_hashes

        self.tool_ctx['navigator'] = SourceNavigatorV42(
            self.workspace, [self.workspace / p for p in TASKS[task]['research_files']],
            TASKS[task]['problem'])
        self.bind_tools(EditRetryGuard())
        self.task_state = TaskState(TASKS[task]['problem'][:900],
                                    ', '.join(TASKS[task]['allowed']))

        sample_name = f'attempt-{index}-{task}-rung{rung}'
        sample_rel = f'prefix-select/{sample_name}'
        sample_dir = self.out / sample_rel
        sample_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = {'calls': [], 'estimated_input': 0}
        self.policy = self.new_policy(work_cap=work_cap)
        self.prefix_llm = self.make_llm('agent', sample_rel, self.ledger, self.policy)
        self.prefix_summary_llm = self.make_llm('summary', sample_rel, self.ledger, self.policy)
        self.prefix_condenser = self.make_condenser(FROZEN_ARM, self.prefix_summary_llm, task=task)
        agent = self.agent_for(FROZEN_ARM, self.prefix_llm, self.prefix_condenser, sample_rel)

        events: list[dict] = []

        def callback(event):
            self.policy.observe_event(event)
            events.append({'type': type(event).__name__, 'id': str(event.id)})

        report = {'sample': sample_name, 'kind': 'prefix_attempt', 'task': task, 'arm': FROZEN_ARM,
                  'rung': rung, 'work_cap': work_cap,
                  'phase1_request_ceiling': PROTOCOL['phase1_request_ceiling_per_rung'][rung],
                  'condenser': CONDENSER_CLASS[FROZEN_ARM], 'success': False,
                  'index': index}
        started = timer()
        # A rejected attempt's conversation is not the shared prefix: close its
        # handles before a new one is created in the same persistence directory.
        if self.source is not None:
            try:
                self.source.close()
            except Exception:
                pass
            self.source = None
        try:
            self.source = Conversation(
                agent=agent, workspace=str(self.workspace), callbacks=[callback],
                persistence_dir=str(self.out / 'sdk-persistence' / 'prefix'),
                delete_on_close=False, visualizer=None,
                max_iteration_per_run=PROTOCOL['max_sdk_steps_per_phase'])
            model_started = timer()
            self.source.send_message(prefix_prompt(self.workspace, task, work_cap))
            self.source.run()
            self.add_time('prefix_model_wait_seconds', timer() - model_started)
            report['sdk_status'] = str(self.source.state.execution_status.value)
        except Exception as exc:  # preserved, never silently retried
            import traceback
            report['error_type'] = type(exc).__name__
            report['diagnostic'] = traceback.format_exc().replace(
                self.config.api_key or 'no-key', '[REDACTED]')
        finally:
            report['elapsed_seconds'] = round(timer() - started, 2)
            report['timing_seconds'] = dict(self.timing)
            report['agent_requests'] = sum(c['kind'] == 'agent' for c in self.ledger['calls'])
            report['summary_requests'] = sum(c['kind'] == 'summary' for c in self.ledger['calls'])
            report['estimated_input'] = self.ledger['estimated_input']
            report['condensation_events'] = self.condensation_count(self.source)
            report['event_count'] = len(events)
            report['event_id_digest'] = digest_ids([row['id'] for row in events])
            report['ledger'] = {'calls': self.ledger['calls'],
                                'estimated_input': self.ledger['estimated_input'],
                                'agent_requests': report['agent_requests'],
                                'summary_requests': report['summary_requests'],
                                'final_call_estimated_input':
                                    self.ledger['calls'][-1]['estimated_input']
                                    if self.ledger['calls'] else None}
            report['agent_metrics'] = self.prefix_llm.metrics.model_dump(mode='json')
            report['summary_metrics'] = self.prefix_summary_llm.metrics.model_dump(mode='json')
            report['complete_total_tokens'] = self.complete_total_tokens(
                report['agent_metrics'], report['summary_metrics'])
            report['first_phase_within_budget'] = (
                report['agent_requests'] <= report['phase1_request_ceiling'])
            report['workspace_preparation'] = dict(self.prepared)
            save(sample_dir / 'report.json', report)
            save(sample_dir / 'ledger.json', self.ledger)
            save(sample_dir / 'events.json', {'events': events,
                                              'event_id_digest': report['event_id_digest']})
        if report.get('error_type'):
            raise RuntimeError(f'{sample_name} failed: {report["error_type"]}')

        # The host's acceptance decides whether this end state needs correction.
        host = self.evaluate(self.workspace, task, sample_dir / 'evaluation-host')
        report['host_evaluation'] = host
        report['host_passed'] = bool(host['passed'])
        report['needs_correction'] = not bool(host['passed'])
        self.task_state.record_verification(
            code_revision(self.workspace, task),
            'Host acceptance passed' if host['passed'] else
            'Host acceptance failed: ' + str(host['failures']))
        report['attempt_timing_seconds'] = dict(self.timing)
        report['disk_free_gb_after'] = free_disk_gb(self.out)
        # Freeze this attempt's result before the prediction, so the recorded
        # verdict can never be rewritten by it.
        save(sample_dir / 'report.json', report)
        report['attempt_dir'] = str(sample_dir.relative_to(self.out))
        if not host['passed']:
            report['trigger_prediction'] = self.predict_trigger(
                sample_name, task=task, host_result=host,
                evaluation_dir=sample_dir / 'evaluation-host')
        else:
            report['trigger_prediction'] = {
                'triggered': False, 'skipped': 'host target test passed; this end state '
                                               'needs no correction',
                'measured_view_tokens': None,
                'ledger_last_estimated_input':
                    report['ledger']['final_call_estimated_input']}
        save(sample_dir / 'report.json', report)
        return report

    def run_select(self) -> dict:
        """Walk the frozen ladder, freeze the selected prefix (or report failure)."""
        from integrations.openhands import prefix_selection_v55 as selection

        selection_dir = self.out / 'prefix-select'
        selection_dir.mkdir(parents=True, exist_ok=True)
        self.require_disk('prefix selection')
        plan = selection.attempt_plan(self.plan)
        index = 0
        while True:
            step = selection.next_step(self.plan, self.attempts)
            if step is None:
                break
            rung, work_cap, task = step
            index += 1
            report = self.run_attempt(task, rung, work_cap, index)
            self.attempts.append(report)
            save(selection_dir / 'attempts.json', self.attempts)
            save(self.out / 'timing-select.json', self.timing)
            print(json.dumps({'stage': 'attempt', 'index': index, 'task': task, 'rung': rung,
                              'host_passed': report['host_passed'],
                              'agent_requests': report['agent_requests'],
                              'measured_view_tokens': (report['trigger_prediction'] or {}).get(
                                  'measured_view_tokens'),
                              'prediction_triggered': (report['trigger_prediction'] or {}).get(
                                  'triggered'),
                              'error_type': report.get('error_type')}, indent=2), flush=True)
            # Attempts run in the frozen order and the decision is "first
            # qualifying attempt in that order", so the walk stops as soon as one
            # attempt qualifies; nothing later can overtake it.
            if selection.selection_decision(self.plan, self.attempts)['selected']:
                break
        decision = selection.selection_decision(self.plan, self.attempts)
        record = {
            'rule': SELECTION_RULE,
            'rule_fingerprint': rule_fingerprint(self.plan),
            'plan': [{'rung': rung, 'work_cap': cap, 'task': task}
                     for rung, cap, task in plan],
            'attempts': self.attempts,
            'decision': decision,
            'selected': bool(decision['selected']),
            'reason': decision['reason'],
            'complete': bool(decision['selected']),
        }
        save(selection_dir / 'selection.json', record)
        save(self.out / 'select.json', record)
        if not decision['selected']:
            print(json.dumps({'stage': 'select', 'selected': False,
                              'reason': decision['reason'],
                              'considered': decision['considered']}, indent=2), flush=True)
            return record

        # ---- freeze the selected prefix ------------------------------------
        task = decision['task']
        self.current_task = task
        selection_dir_report = self.out / 'prefix'
        selection_dir_report.mkdir(parents=True, exist_ok=True)
        source_hashes = hashes(self.workspace)
        if self.snapshot.exists():
            shutil.rmtree(self.snapshot)
        with self.timeit('prefix_snapshot_seconds'):
            from same_prefix_fork_v54 import capture_workspace, tree_hashes
            frozen_tree = capture_workspace(self.workspace, self.snapshot,
                                            experiment_root=self.experiment_root)
        with self.timeit('snapshot_verify_seconds'):
            if tree_hashes(self.snapshot) != frozen_tree:
                raise RuntimeError('frozen snapshot does not match the prefix workspace')
        attempt = decision['attempt']
        host = attempt['host_evaluation']
        feedback = self.host_feedback(host, self.out / attempt['attempt_dir'] / 'evaluation-host')
        freeze = {
            'task': task, 'arm': FROZEN_ARM, 'rung': decision['rung'],
            'work_cap': decision['work_cap'],
            'attempt': attempt['sample'], 'attempt_dir': attempt['attempt_dir'],
            'conversation_id': str(self.source.state.id),
            'event_count': len(self.prefix_event_ids()),
            'event_id_digest': digest_ids(self.prefix_event_ids()),
            'workspace': str(self.workspace), 'workspace_file_count': len(frozen_tree),
            'workspace_tree_digest': digest_map(frozen_tree),
            'workspace_source_digest': digest_map(source_hashes),
            'workspace_file_count_source': len(source_hashes),
            'snapshot': str(self.snapshot),
            'host_test_passed': bool(host['passed']),
            'prefix_needs_correction': not bool(host['passed']),
            'host_evaluation': host,
            'correction_feedback': feedback,
            'agent_requests': attempt['agent_requests'],
            'summary_requests': attempt['summary_requests'],
            'estimated_input': attempt['estimated_input'],
            'complete_total_tokens': attempt['complete_total_tokens'],
            'policy_state': self.policy.capture_prefix_state(),
            'first_phase_within_budget': attempt['first_phase_within_budget'],
            'trigger_prediction': attempt['trigger_prediction'],
            'selection_rule_fingerprint': record['rule_fingerprint'],
            'selection_decision': decision,
            'selection_attempts_summary': [
                {'sample': row['sample'], 'task': row['task'], 'rung': row['rung'],
                 'host_passed': row['host_passed'],
                 'trigger_prediction_triggered': (row['trigger_prediction'] or {}).get('triggered'),
                 'agent_requests': row['agent_requests'],
                 'complete_total_tokens': row['complete_total_tokens']}
                for row in self.attempts],
            'selection_tokens_counted_once': sum(row['complete_total_tokens']
                                                 for row in self.attempts),
        }
        if not freeze['prefix_needs_correction']:
            raise RuntimeError('the selected prefix does not need correction; the frozen '
                               'selection rule cannot have selected it')
        if not (freeze['trigger_prediction'] or {}).get('triggered'):
            raise RuntimeError('the selected prefix did not satisfy the frozen trigger '
                               'prediction; refusing to fork')
        save(selection_dir_report / 'frozen-hashes.json', frozen_tree)
        save(self.out / 'frozen-hashes.json', frozen_tree)
        save(self.out / 'frozen-source-hashes.json', source_hashes)
        save(selection_dir_report / 'report.json', attempt)
        save(selection_dir_report / 'ledger.json', load(Path(self.out) / attempt['attempt_dir']
                                                        / 'ledger.json'))
        save(selection_dir_report / 'events.json', load(Path(self.out) / attempt['attempt_dir']
                                                        / 'events.json'))
        # The exact correction prompt bytes every branch receives, frozen with the
        # prefix so the audit can replay the condenser on the same input offline.
        (selection_dir_report / 'correction-prompt.txt').write_text(
            correction_prompt(task, feedback), encoding='utf-8')
        save(self.out / 'prefix-freeze.json', freeze)
        print(json.dumps({'stage': 'select', 'selected': True, 'task': task,
                          'rung': decision['rung'],
                          'agent_requests': freeze['agent_requests'],
                          'complete_total_tokens': freeze['complete_total_tokens'],
                          'measured_view_tokens':
                              freeze['trigger_prediction']['measured_view_tokens'],
                          'event_count': freeze['event_count']}, indent=2), flush=True)
        return record

    # -- branches --------------------------------------------------------
    def branch_dirs(self) -> list[Path]:
        return [self.out / f'branch-{position}-{arm}'
                for position, arm in enumerate(self.branch_order)]

    def write_batch_index(self) -> dict:
        """Pin every per-branch artifact file once the branch stage is done."""
        from integrations.openhands import branch_artifacts_v55 as artifacts
        return artifacts.write_batch_index(
            self.out, [path for path in self.branch_dirs() if (path / 'report.json').is_file()])

    def rebuild_prefix_conversation(self, freeze: dict) -> None:
        """Reopen the frozen prefix from SDK persistence before forking."""
        from openhands.sdk import Agent, Conversation, LLM
        from same_prefix_fork_v54 import tree_hashes
        from integrations.openhands.fork_tools_v54 import (
            EDITOR_TOOL_NAME, SYMBOLS_TOOL_NAME, TESTS_TOOL_NAME, register_v54_tools)
        from openhands.sdk.tool import Tool
        from edit_retry_guard_v33 import EditRetryGuard
        from integrations.openhands.source_navigation_v42 import SourceNavigatorV42
        from integrations.openhands.task_state_v42 import TaskState

        self.current_task = freeze['task']
        register_v54_tools()
        self.tool_ctx['navigator'] = SourceNavigatorV42(
            self.workspace, [self.workspace / p for p in TASKS[self.current_task]['research_files']],
            TASKS[self.current_task]['problem'])
        self.bind_tools(EditRetryGuard())
        self.task_state = TaskState(TASKS[self.current_task]['problem'][:900],
                                    ', '.join(TASKS[self.current_task]['allowed']))
        host_llm = LLM(model=PROTOCOL['model'], api_key='unused')
        host_agent = Agent(llm=host_llm, tools=[
            Tool(name=EDITOR_TOOL_NAME), Tool(name=SYMBOLS_TOOL_NAME),
            Tool(name=TESTS_TOOL_NAME,
                 params={'workspace': str(self.workspace), 'task': self.current_task,
                         'destination': str(self.out / 'prefix' / 'tool-tests-rebuild')})],
            include_default_tools=['FinishTool'])
        self.source = Conversation(
            agent=host_agent, workspace=str(self.workspace),
            persistence_dir=str(self.out / 'sdk-persistence' / 'prefix'),
            conversation_id=uuid.UUID(freeze['conversation_id']), delete_on_close=False,
            visualizer=None, max_iteration_per_run=PROTOCOL['max_sdk_steps_per_phase'])
        ids = self.prefix_event_ids()
        if len(ids) != freeze['event_count'] or digest_ids(ids) != freeze['event_id_digest']:
            raise RuntimeError('rebuilt prefix event sequence differs from the frozen prefix')
        if tree_hashes(self.workspace) != load(self.out / 'frozen-hashes.json'):
            raise RuntimeError('prefix workspace changed after the prefix stage')

    def run_branch(self, arm: str, freeze: dict, frozen: dict, position: int) -> dict:
        from edit_retry_guard_v33 import EditRetryGuard
        from same_prefix_fork_v54 import (attach_branch_callback, branch_ledger_from_prefix,
                                          restore_workspace, tree_hashes)
        from integrations.openhands import branch_artifacts_v55 as artifacts

        task = freeze['task']
        self.current_task = task
        allowed = list(TASKS[task]['allowed'])
        frozen_source = load(self.out / 'frozen-source-hashes.json')
        sample_name = f'branch-{position}-{arm}'
        sample_dir = self.out / sample_name
        sample_dir.mkdir(parents=True, exist_ok=True)
        report = {'sample': sample_name, 'kind': 'branch', 'task': task, 'arm': arm,
                  'condenser': CONDENSER_CLASS[arm], 'position': position,
                  'success': False, 'prefix_needs_correction': freeze['prefix_needs_correction']}

        self.require_disk(f'branch {sample_name}')
        # 1. same absolute path, restored from the read-only snapshot.
        with self.timeit('restore_workspace_seconds'):
            restore_workspace(self.workspace, self.snapshot, experiment_root=self.experiment_root,
                              expected_hashes=frozen)
        with self.timeit('restore_hash_verify_seconds'):
            observed = tree_hashes(self.workspace)
        report['restore_hash_equal'] = observed == frozen
        if not report['restore_hash_equal']:
            raise RuntimeError(f'{sample_name}: restored workspace differs from the frozen prefix')

        # 2. independent ledger + policy that continue the prefix's counters.
        self.bind_tools(EditRetryGuard())
        ledger = branch_ledger_from_prefix(self.ledger)
        policy = self.new_policy(prefix_used_agent_calls=freeze['agent_requests'],
                                 prefix_used_estimated_input=freeze['estimated_input'])
        policy.check_prefix_state(freeze['policy_state'])
        if policy.correction_stop_at != min(
                PROTOCOL['max_agent_calls_per_sample'],
                freeze['agent_requests'] + PROTOCOL['host_feedback_correction_reserve']):
            raise RuntimeError('branch correction boundary does not continue the prefix')

        agent_llm = self.make_llm('agent', sample_name, ledger, policy)
        summary_llm = self.make_llm('summary', sample_name, ledger, policy)
        condenser = self.make_condenser(arm, summary_llm, task=task)
        if arm == 'pruner_v1':
            condenser.set_current_state(self.task_state.render(code_revision(self.workspace, task)))
        agent = self.agent_for(arm, agent_llm, condenser, sample_name)

        events: list[dict] = []

        def callback(event):
            policy.observe_event(event)
            if arm == 'pruner_v1' and type(event).__name__ == 'ObservationEvent':
                condenser.set_current_state(
                    self.task_state.render(code_revision(self.workspace, task)))
            events.append({'type': type(event).__name__, 'id': str(event.id)})

        from same_prefix_fork_v54 import fork_with_agent
        fork = fork_with_agent(self.source, agent)
        attach_branch_callback(fork, callback)
        prefix_ids = self.prefix_event_ids()
        if [str(e.id) for e in fork.state.events] != prefix_ids:
            raise RuntimeError(f'{sample_name}: forked events differ from the shared prefix')
        report['prefix_event_count'] = len(prefix_ids)
        report['shared_prefix_calls'] = ledger['shared_prefix_call_count']
        report['shared_prefix_estimated_input'] = ledger['shared_prefix_estimated_input']
        report['conversation_id'] = str(fork.state.id)

        rounds = []
        started = timer()
        try:
            # The fork point is the boundary between the shared prefix and the
            # host-feedback correction phase: every branch opens its correction
            # window from the prefix's cumulative position, so the ceiling is
            # "prefix + correction reserve", never a fresh 36-request allowance.
            ledger_used_calls = sum(c['kind'] == 'agent' for c in ledger['calls'])
            policy.begin_correction(ledger_used_calls)
            report['correction_boundary_after_begin'] = policy.correction_stop_at
            # Zero-API: re-measure the frozen prediction on the fork's own events
            # before the branch spends anything.  The forked events were just
            # verified to equal the frozen prefix events, so `self.source` holds
            # exactly the events this branch will send its first request with;
            # the ledger rule is evaluated on the *prefix* ledger, because the
            # shared prefix is what the frozen prediction was computed from.
            report['trigger_prediction_at_fork'] = self.predict_trigger(
                sample_name, task=task, host_result=freeze['host_evaluation'],
                evaluation_dir=self.out / freeze['attempt_dir'] / 'evaluation-host',
                ledger=self.ledger)
            report['trigger_prediction_matches_freeze'] = (
                bool(report['trigger_prediction_at_fork']['triggered'])
                == bool(freeze['trigger_prediction']['triggered']))
            feedback = freeze['correction_feedback']
            for round_index in range(PROTOCOL['host_feedback_rounds_per_branch']):
                if round_index:
                    used_calls = sum(c['kind'] == 'agent' for c in ledger['calls'])
                    if not policy.can_correct(used_calls, ledger['estimated_input']):
                        report['stopped_reason'] = (
                            'no unreserved work capacity for another host round; the '
                            'correction reserve is what the prefix left')
                        break
                if arm == 'pruner_v1':
                    condenser.set_current_state(
                        self.task_state.render(code_revision(self.workspace, task)))
                model_started = timer()
                fork.send_message(correction_prompt(task, feedback))
                fork.run()
                self.add_time('branch_model_wait_seconds', timer() - model_started)
                report['sdk_status'] = str(fork.state.execution_status.value)
                result = self.evaluate(self.workspace, task,
                                       sample_dir / f'evaluation-round{round_index + 1}')
                rounds.append({'round': round_index + 1, 'host': result,
                               'agent_requests_used': sum(c['kind'] == 'agent'
                                                          for c in ledger['calls'])})
                self.task_state.record_verification(
                    code_revision(self.workspace, task),
                    'Host acceptance passed' if result['passed'] else
                    'Host acceptance failed: ' + str(result['failures']))
                if result['passed']:
                    break
                feedback = self.host_feedback(
                    result, sample_dir / f'evaluation-round{round_index + 1}')
                self.task_state.record_feedback(code_revision(self.workspace, task),
                                                feedback[:1000])
            report['host_rounds'] = rounds
        except Exception as exc:
            import traceback
            report['error_type'] = type(exc).__name__
            report['diagnostic'] = traceback.format_exc().replace(
                self.config.api_key or 'no-key', '[REDACTED]')
        finally:
            report['elapsed_seconds'] = round(timer() - started, 2)
            report['timing_seconds'] = dict(self.timing)
            report['events'] = events
            suffix = [str(e.id) for e in fork.state.events][len(prefix_ids):]
            report['branch_event_ids'] = suffix
            report['branch_events_persisted_match_observation'] = (
                [e['id'] for e in events if e['id'] not in prefix_ids] == suffix)
            report['source_prefix_unchanged'] = self.prefix_event_ids() == prefix_ids
            report['ledger'] = branch_ledger_view(ledger, freeze)
            report['agent_metrics'] = agent_llm.metrics.model_dump(mode='json')
            report['summary_metrics'] = summary_llm.metrics.model_dump(mode='json')
            report['complete_total_tokens'] = self.complete_total_tokens(
                report['agent_metrics'], report['summary_metrics'])
            report['post_fork_agent_requests'] = report['ledger']['post_fork_agent_requests']
            report['post_fork_total_requests'] = report['ledger']['post_fork_total_requests']
            report['condensation_events'] = self.condensation_count(fork)
            report['condensation_counted_from_suffix'] = sum(
                e['type'] == 'Condensation' for e in events)
            report['mechanism_triggered'] = report['condensation_events'] > 0
            report['workflow_verification_ok'] = policy.is_verified
            report['budget_state'] = {
                'correction_stop_at': policy.correction_stop_at,
                'closing_reason': policy.closing_reason,
                'in_correction': policy.in_correction}
            if arm == 'pruner_v1':
                save(sample_dir / 'pruner-state.json',
                     condenser.finalize({'success': report['success']}))
            save(sample_dir / 'ledger.json', ledger)
            fork.close()
        rounds = report.get('host_rounds') or []
        final = rounds[-1]['host'] if rounds else None
        report['final_host_passed'] = bool(final and final['passed'])
        # 3. persist this branch's own final bytes + hashes + event stream, so the
        #    audit never has to assume the shared path still holds them.
        with self.timeit('branch_persist_seconds'):
            record = artifacts.persist_branch_artifacts(
                sample_dir, self.workspace, frozen_source=frozen_source, allowed=allowed,
                task=task, arm=arm, position=position,
                prefix_event_count=len(prefix_ids), conversation_id=report['conversation_id'],
                events=events,
                extra={'final_host_passed': report['final_host_passed'],
                       'condensation_events': report['condensation_events']})
        report['changed_files'] = record['changed_files']
        report['file_boundary_ok'] = record['file_boundary_ok']
        report['final_source_digest'] = record['final_source_digest']
        report['branch_artifacts'] = record
        report['artifact_success'] = bool(report['final_host_passed'] and report['file_boundary_ok'])
        report['success'] = bool(report['artifact_success']
                                 and report.get('sdk_status') == 'finished'
                                 and report['workflow_verification_ok']
                                 and not report.get('error_type'))
        report['disk_free_gb_after'] = free_disk_gb(self.out)
        save(sample_dir / 'report.json', report)
        return report

    # -- check -----------------------------------------------------------
    def run_check(self) -> dict:
        """Zero-API gate for every frozen task: baseline workspace + host baseline."""
        for path in (self.out / 'workspaces', self.out / 'baseline'):
            if path.exists():
                shutil.rmtree(path)
        tasks = list(self.plan.get('check_order') or list(reversed(FROZEN_TASKS)))
        results = {}
        for task in tasks:
            self.require_disk(f'zero-API check ({task})')
            self.current_task = task
            started = timer()
            if self.config.prepare_workspace:
                if self.workspace.exists():
                    shutil.rmtree(self.workspace)
                prepare(ROOT / '.tooling/upstream', self.workspace, task)
            self.add_time('gate_prepare_seconds', timer() - started)
            started = timer()
            baseline = hashes(self.workspace)
            self.add_time('gate_hash_seconds', timer() - started)
            baseline_dir = self.out / 'baseline' / task
            save(baseline_dir / 'original-hashes.json', baseline)

            # The frozen edit scope must equal what the dataset's reference patch touches.
            instance_id = INSTANCES[task]['instance_id']
            patch = (ROOT / '.tooling/swebench-verified'
                     / f'django-{instance_id.split("-")[-1]}' / 'reference.patch')
            touched = sorted({line.split(' b/')[-1].replace('\\', '/')
                              for line in patch.read_text(encoding='utf-8',
                                                          errors='replace').splitlines()
                              if line.startswith('diff --git')})
            assert TASKS[task]['allowed'] == EXPECTED_ALLOWED[task], TASKS[task]['allowed']
            assert touched == sorted(EXPECTED_ALLOWED[task]), (touched, EXPECTED_ALLOWED[task])

            expected = EXPECTED_BASELINE[task]
            regression = self.evaluate(self.workspace, task, baseline_dir / 'regression',
                                       regression_only=True)
            assert (regression['passed'] is expected['public_passed']
                    and regression['ran'] == expected['public_ran']
                    and regression['failures'] == expected['public_failures']
                    and regression['workspace_unchanged_by_test']), (task, regression)
            result = self.evaluate(self.workspace, task, baseline_dir / 'evaluation')
            assert (not result['passed'] and result['ran'] == expected['host_ran']
                    and result['failures'] == expected['host_failures']
                    and result['workspace_unchanged_by_test']), (task, result)
            results[task] = {
                'task': task, 'allowed': TASKS[task]['allowed'],
                'reference_patch_files': touched,
                'public_regression': regression, 'host_acceptance': result,
                'baseline_hashes_digest': digest_map(baseline),
                'baseline_file_count': len(baseline)}
            save(self.out / 'check.json',
                 {'tasks': results, 'check_order': tasks,
                  'frozen_expectations': EXPECTED_BASELINE,
                  'disk_free_gb_after': free_disk_gb(self.out)})
            print(json.dumps({'stage': 'check', 'task': task,
                              'public_ran': regression['ran'],
                              'host_ran': result['ran'],
                              'host_failures': result['failures'],
                              'baseline_file_count': len(baseline)}), flush=True)
        gate = load(self.out / 'check.json')
        return gate


def branch_ledger_view(ledger: dict, freeze: dict) -> dict:
    """Branch-local totals plus the carried-over prefix counters."""
    agent_calls = sum(c['kind'] == 'agent' for c in ledger['calls'])
    summary_calls = sum(c['kind'] == 'summary' for c in ledger['calls'])
    return {
        'total_requests': len(ledger['calls']),
        'agent_requests': agent_calls, 'summary_requests': summary_calls,
        'estimated_input': ledger['estimated_input'],
        'post_fork_agent_requests': agent_calls,
        'post_fork_summary_requests': summary_calls,
        'post_fork_total_requests': len(ledger['calls']),
        'post_fork_estimated_input': ledger['estimated_input'],
        'carried_over_agent_requests': ledger['shared_prefix_call_count'],
        'carried_over_estimated_input': ledger['shared_prefix_estimated_input'],
        'shared_prefix_agent_requests': freeze['agent_requests'],
        'shared_prefix_summary_requests': freeze['summary_requests'],
        'failed_requests': [c for c in ledger['calls'] if c['status'] != 'returned'],
        'calls': ledger['calls'],
    }


# --------------------------------------------------------------------------
# manifest / sources
# --------------------------------------------------------------------------

def source_paths() -> list[Path]:
    paths = [
        Path(__file__), HERE / 'run_validation_windows_v56.py',
        HERE / 'prefix_selection_v55.py', HERE / 'branch_artifacts_v55.py',
        HERE / 'audit_validation_v56.py', HERE / 'PILOT_PROTOCOL_V56.md',
        HERE / 'PILOT_PROTOCOL_V55.md', HERE / 'run_validation_v55.py',
        HERE / 'run_validation_windows_v55.py', HERE / 'audit_validation_v55.py',
        HERE / 'PILOT_PROTOCOL_V54.md', HERE / 'fork_tools_v54.py',
        HERE / 'budget_policy_v54.py', HERE / 'same_prefix_fork_v54.py',
        HERE / 'run_validation_v54.py', HERE / 'audit_validation_v54.py',
        HERE / 'validation_tasks_v49.py', HERE / 'validation_tasks_v45.py',
        HERE / 'verification_tool_django_v49.py', HERE / 'edit_scope_guard_v41.py',
        HERE / 'edit_retry_guard_v33.py', HERE / 'task_suite.py',
        HERE / 'bounded_llm_v30.py', HERE / 'budget_policy_v30.py',
        HERE / 'budget_policy_v31.py', HERE / 'budget_policy_v39.py',
        HERE / 'budget_policy_v42.py', HERE / 'source_navigation_v31.py',
        HERE / 'source_navigation_v42.py', HERE / 'symbol_tool_v31.py',
        HERE / 'symbol_tool_v35.py', HERE / 'symbol_tool_v42.py', HERE / 'symbol_tool_v44.py',
        HERE / 'feedback_v40.py', HERE / 'feedback_v42.py', HERE / 'task_state_v42.py',
        HERE / 'audit_v53_fast_core.py',
        ROOT / 'tests/test_openhands_v56_formal_runner_gate.py',
        ROOT / 'tests/test_openhands_v55_formal_runner_gate.py',
        ROOT / 'tests/test_openhands_v54_formal_runner_gate.py',
        ROOT / 'tests/test_openhands_same_prefix_fork_v54.py',
        ROOT / 'tests/test_openhands_v54_branch_loopback.py',
        ROOT / 'tests/test_openhands_symbol_v44.py',
        ROOT / 'tests/test_openhands_budget_v39.py',
        ROOT / 'tests/test_openhands_budget_v42.py',
        ROOT / 'tests/test_openhands_feedback_v40.py',
        ROOT / 'tests/test_openhands_v41_scope.py',
        ROOT / 'tests/test_openhands_v42_offline.py',
        ROOT / 'context_pruner/adapters/openhands_v51.py',
        ROOT / 'context_pruner/adapters/openhands_v42.py',
        ROOT / 'context_pruner/adapters/openhands_v41.py',
        ROOT / 'context_pruner/adapters/openhands_v4.py',
        ROOT / 'context_pruner/adapters/openhands_v2.py',
        ROOT / 'context_pruner/adapters/openhands_v5.py',
    ]
    paths += sorted(p for p in (ROOT / 'context_pruner').rglob('*.py'))
    return list(dict.fromkeys(paths))


def build_manifest(engine: Engine) -> dict:
    manifest = dict(PROTOCOL)
    manifest['versions'] = {name: importlib.metadata.version(name) for name in
                            ('openhands-sdk', 'openhands-tools', 'litellm', 'context-pruner')}
    files = source_paths()
    for task in FROZEN_TASKS:
        files.append(ROOT / GATE_FILES[task])
        instance_id = INSTANCES[task]['instance_id']
        instance_dir = ROOT / '.tooling/swebench-verified' / (
            'django-' + instance_id.split('-')[-1])
        export_name = 'export_' + instance_id.replace('__', '_') + '.json'
        files += [instance_dir / 'instance.json', instance_dir / 'host-tests.patch',
                  instance_dir / 'reference.patch', ROOT / '.tooling' / export_name]
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise SystemExit(f'manifest source missing: {missing}')
    manifest['source_hashes'] = {
        str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files}
    manifest['source_hash_count'] = len(manifest['source_hashes'])
    manifest['branch_order'] = engine.branch_order
    manifest['arm_alias'] = dict(ARM_ALIAS)
    return manifest


def comparison(out: Path) -> dict:
    """Post-fork and whole-run totals.

    The whole-run total counts **every** prefix-selection attempt exactly once -
    including the attempts that were rejected - plus the branch's own post-fork
    increment.  The shared prefix is therefore never charged twice, and the cost
    of finding a prefix that needs correction is not hidden.
    """
    out = Path(out)
    select = load(out / 'select.json')
    freeze = load(out / 'prefix-freeze.json')
    prefix_total = freeze['complete_total_tokens']
    selection_total = select['decision']['attempt']['complete_total_tokens']
    all_attempts_total = sum(row['complete_total_tokens'] for row in select['attempts'] if row)
    branches = [load(path / 'report.json') for path in sorted(out.glob('branch-*/'))
                if (path / 'report.json').is_file()]
    rows = {}
    for report in branches:
        rows[report['arm']] = {
            'arm': report['arm'], 'position': report['position'],
            'post_fork_tokens': report['complete_total_tokens'],
            'post_fork_tokens_plus_selected_prefix':
                prefix_total + report['complete_total_tokens'],
            'whole_run_tokens_all_selection_counted_once':
                all_attempts_total + report['complete_total_tokens'],
            'final_host_passed': report['final_host_passed'],
            'artifact_success': report['artifact_success'],
            'file_boundary_ok': report['file_boundary_ok'],
            'post_fork_agent_requests': report['post_fork_agent_requests'],
            'post_fork_total_requests': report['post_fork_total_requests'],
            'carried_over_agent_requests': report['ledger']['carried_over_agent_requests'],
            'condensation_events': report['condensation_events'],
            'mechanism_triggered': report['mechanism_triggered'],
            'restore_hash_equal': report['restore_hash_equal'],
            'final_source_digest': report['final_source_digest'],
            'error_type': report.get('error_type'),
        }
    baseline = rows.get('none')
    if baseline:
        for arm, row in rows.items():
            if arm == 'none':
                continue
            denominator_post = baseline['post_fork_tokens']
            denominator_whole = baseline['whole_run_tokens_all_selection_counted_once']
            row['post_fork_savings_vs_none'] = (
                round((denominator_post - row['post_fork_tokens']) / denominator_post, 6)
                if denominator_post else None)
            row['whole_run_savings_vs_none'] = (
                round((denominator_whole - row['whole_run_tokens_all_selection_counted_once'])
                      / denominator_whole, 6) if denominator_whole else None)
    return {'selected_task': freeze['task'], 'selected_rung': freeze['rung'],
            'selected_attempt': freeze['attempt'],
            'prefix': {'complete_total_tokens': prefix_total,
                       'host_test_passed': freeze['host_test_passed'],
                       'prefix_needs_correction': freeze['prefix_needs_correction'],
                       'agent_requests': freeze['agent_requests'],
                       'summary_requests': freeze['summary_requests'],
                       'event_count': freeze['event_count'],
                       'measured_view_tokens':
                           freeze['trigger_prediction'].get('measured_view_tokens'),
                       'trigger_prediction': freeze['trigger_prediction']},
            'selection': {'attempts': len(select['attempts']),
                          'selected_attempt_tokens': selection_total,
                          'all_attempts_tokens_counted_once': all_attempts_total,
                          'per_attempt': [{'sample': row['sample'], 'task': row['task'],
                                           'rung': row['rung'],
                                           'host_passed': row['host_passed'],
                                           'agent_requests': row['agent_requests'],
                                           'complete_total_tokens': row['complete_total_tokens']}
                                          for row in select['attempts']]},
            'branches': rows,
            'branch_order': [load(path / 'report.json')['arm']
                             for path in sorted(out.glob('branch-*/'))
                             if (path / 'report.json').is_file()],
            'timing_seconds': timing_summary(out, branches),
            'mechanism_triggered_plugin': bool(rows.get('pruner_v1', {}).get('mechanism_triggered')),
            'note': ('post-fork totals are the branch-local provider total including every '
                     'summary/native request; the whole-run total adds every prefix-selection '
                     'attempt exactly once, so no attempt is duplicated across arms and no arm '
                     'is credited with the shared prefix twice')}


def timing_summary(out: Path, branches: list[dict]) -> dict:
    stages: dict[str, dict[str, float]] = {'select_seconds': {}, 'branches_seconds': {},
                                           'total_seconds': {}}
    select_report = None
    if (out / 'select.json').is_file():
        attempts = load(out / 'select.json')['attempts']
        for row in attempts:
            for segment, seconds in (row.get('attempt_timing_seconds') or {}).items():
                stages['select_seconds'][segment] = round(
                    stages['select_seconds'].get(segment, 0.0) + seconds, 2)
                stages['total_seconds'][segment] = round(
                    stages['total_seconds'].get(segment, 0.0) + seconds, 2)
        select_report = {'elapsed': sum(row['elapsed_seconds'] for row in attempts)}
    for report in branches:
        for segment, seconds in (report.get('timing_seconds') or {}).items():
            stages['branches_seconds'][segment] = round(
                stages['branches_seconds'].get(segment, 0.0) + seconds, 2)
            stages['total_seconds'][segment] = round(
                stages['total_seconds'].get(segment, 0.0) + seconds, 2)
    stages['wall_clock_seconds'] = {
        'select_attempts_seconds': round((select_report or {}).get('elapsed', 0.0), 2),
        'branch_samples_seconds': round(sum(b.get('elapsed_seconds', 0.0) for b in branches), 2),
    }
    return stages


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

CHECK_ARTIFACTS = {'sdk-persistence', 'windows-compatibility.json', 'manifest.json',
                   'baseline', 'check.json', '.runtime-tmp', 'timing-check.json'}
SELECT_ARTIFACTS = CHECK_ARTIFACTS | {'prefix-select', 'select.json', 'prefix', 'prefix-snapshot',
                                      'frozen-hashes.json', 'frozen-source-hashes.json',
                                      'prefix-freeze.json', 'workspaces', 'timing-select.json'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--stage', default=None,
                        choices=['check', 'select', 'branches', 'run'],
                        help='check = zero-API gate; run = select then branches')
    parser.add_argument('--check', action='store_true',
                        help='alias for --stage check (the zero-API gate)')
    parser.add_argument('--run', action='store_true',
                        help='alias for --stage run (prefix selection then the three branches)')
    parser.add_argument('--rotate', action='store_true',
                        help='rotate the frozen branch order by one position')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--repeats', type=int, default=1, choices=[1],
                        help='a same-prefix pilot is one prefix and one repetition')
    parser.add_argument('--accept-post-gate-tooling', action='store_true',
                        help='record (never hide) source-hash drift of tooling files that were '
                             'repaired after this batch\'s gate run; protocol fields must not '
                             'change, and the drift is written to post-gate-tooling.json')
    parser.add_argument('--post-gate-reason', default='')
    args = parser.parse_args(argv)
    if args.check and args.run:
        raise SystemExit('--check and --run are mutually exclusive')
    if args.check:
        args.stage = 'check'
    elif args.run:
        args.stage = 'run'
    elif args.stage is None:
        args.stage = 'run'

    out = Path(args.out).resolve()
    if not out.is_relative_to(ROOT):
        raise SystemExit(f'refusing to write outside the project root: {out}')
    if out.exists() and not args.resume:
        unexpected = sorted(p.name for p in out.iterdir() if p.name not in CHECK_ARTIFACTS)
        if args.stage == 'check':
            if unexpected or (out / 'check.json').exists():
                raise SystemExit('--stage check writes into a fresh batch directory '
                                 f'(unexpected entries: {unexpected or "check.json"})')
        elif unexpected:
            raise SystemExit('Output exists; use --resume for later stages of the same '
                             f'batch (found {unexpected}).')
    if args.stage == 'check' and (out / 'prefix-freeze.json').exists():
        raise SystemExit('This batch already holds a frozen prefix; run the gate in a fresh '
                         'batch directory so the frozen prefix is never overwritten.')
    out.mkdir(parents=True, exist_ok=True)

    runtime_temp = out / '.runtime-tmp'
    runtime_temp.mkdir(parents=True, exist_ok=True)
    for name in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[name] = str(runtime_temp)
    os.environ['TIKTOKEN_CACHE_DIR'] = str(ROOT / '.tooling/tiktoken')
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'

    import validation_tasks_v49 as vt
    frozen_environment = vt._test_environment

    def redistributed(test_workspace):
        environment = frozen_environment(test_workspace)
        scratch = runtime_temp / Path(test_workspace).name
        scratch.mkdir(parents=True, exist_ok=True)
        for name in ('TEMP', 'TMP', 'TMPDIR'):
            environment[name] = str(scratch)
        return environment

    vt._test_environment = redistributed

    paid = args.stage in {'select', 'branches', 'run'}
    config = EngineConfig(out=out, stage=args.stage, rotate=args.rotate, resume=args.resume,
                          api_key=os.environ.get('DEEPSEEK_API_KEY'))
    engine = Engine(config)
    engine.plan = dict(PROTOCOL)
    manifest = build_manifest(engine)
    if (out / 'manifest.json').exists():
        frozen_manifest = load(out / 'manifest.json')
        frozen_fields = {k: v for k, v in frozen_manifest.items() if k != 'source_hashes'}
        current_fields = {k: v for k, v in manifest.items() if k != 'source_hashes'}
        if frozen_fields != current_fields:
            differing = sorted(k for k in set(frozen_fields) | set(current_fields)
                               if frozen_fields.get(k) != current_fields.get(k))
            raise SystemExit(f'Frozen protocol changed (fields: {differing}); a protocol change '
                             f'needs a new version and a new batch directory')
        drift = {name: {'frozen': digest, 'current': manifest['source_hashes'].get(name)}
                 for name, digest in (frozen_manifest.get('source_hashes') or {}).items()
                 if manifest['source_hashes'].get(name) != digest}
        if drift:
            if not args.accept_post_gate_tooling:
                raise SystemExit(f'Frozen protocol changed (sources: {sorted(drift)}); pass '
                                 f'--accept-post-gate-tooling to record a documented repair '
                                 f'instead of hiding it')
            save(out / 'post-gate-tooling.json', {
                'sources': drift, 'accepted': True, 'reason': args.post_gate_reason,
                'scope': 'source hashes only; every protocol field is unchanged',
                'note': ('this batch\'s paid run uses the current revision of these tooling '
                         'files; the frozen revision is the one recorded in manifest.json')})
    else:
        save(out / 'manifest.json', manifest)

    for task, gate_rel in GATE_FILES.items():
        gate = load(ROOT / gate_rel)
        assert gate['instance'] == INSTANCES[task]['instance_id'], (task, gate['instance'])
        assert gate['base_commit'] == COMMIT[task], (task, gate['base_commit'])
        assert not gate['base_host']['passed'], (task, 'gate: base host unexpectedly passes')
        assert gate['reference_host']['passed'], (task, 'gate: reference host must pass')

    if args.stage == 'check':
        gate = engine.run_check()
        save(out / 'timing-check.json', engine.timing)
        print(json.dumps({'tasks': list(gate['tasks']), 'disk_free_gb':
                          gate.get('disk_free_gb_after')}, indent=2))
        return 0

    if paid and not config.api_key:
        raise SystemExit('DEEPSEEK_API_KEY unavailable')

    freeze_path = out / 'prefix-freeze.json'
    if args.stage in {'select', 'run'}:
        if freeze_path.exists() and not args.resume:
            raise SystemExit('A frozen prefix already exists; use --stage branches --resume.')
        record = engine.run_select()
        if not record['selected']:
            save(out / 'timing-select.json', engine.timing)
            print(json.dumps({'stage': 'select', 'mechanism_triggered': False,
                              'reason': record['decision']['reason']}, indent=2), flush=True)
            return 3
        save(out / 'timing-select.json', engine.timing)
        if args.stage == 'select':
            return 0

    if args.stage in {'branches', 'run'}:
        if not freeze_path.exists():
            raise SystemExit('prefix-freeze.json missing; run --stage select first.')
        freeze = load(freeze_path)
        frozen = load(out / 'frozen-hashes.json')
        if args.stage == 'branches':
            engine.current_task = freeze['task']
            engine.ledger = load(Path(out) / freeze['attempt_dir'] / 'ledger.json')
            engine.rebuild_prefix_conversation(freeze)
        reports = []
        for position, arm in enumerate(engine.branch_order):
            sample_dir = out / f'branch-{position}-{arm}'
            if (sample_dir / 'report.json').exists():
                reports.append(load(sample_dir / 'report.json'))
                continue
            if sample_dir.exists() and any(sample_dir.iterdir()):
                raise SystemExit(f'Interrupted branch needs audit before rerun: {sample_dir.name}')
            report = engine.run_branch(arm, freeze, frozen, position)
            reports.append(report)
            save(out / 'timing-branches.json', engine.timing)
            print(json.dumps({'stage': 'branch', 'arm': arm, 'position': position,
                              'success': report['success'],
                              'final_host_passed': report['final_host_passed'],
                              'post_fork_tokens': report['complete_total_tokens'],
                              'condensation_events': report['condensation_events'],
                              'error_type': report.get('error_type')}), flush=True)
        from integrations.openhands import branch_artifacts_v55 as artifacts
        index = engine.write_batch_index()
        print(json.dumps({'stage': 'branch-index', 'branches': sorted(index['branches'])}),
              flush=True)
        result = comparison(out)
        result['timing'] = engine.timing
        save(out / 'comparison.json', result)
        print(json.dumps(result, indent=2))
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
