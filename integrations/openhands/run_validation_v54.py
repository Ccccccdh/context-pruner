"""v54 formal runner: one shared real prefix, three sequential same-path branches.

Design (frozen; see ``integrations/openhands/PILOT_PROTOCOL_V54.md``):

* ``--stage prefix`` runs the corpus arm once, with no condenser, on a real
  Django task.  It ends by freezing the workspace state (full-file hashes, a
  read-only snapshot, the SDK event-ID sequence, the request ledger and the
  budget-policy state) plus the host's first target-test result.
* ``--stage branches`` restores that frozen state, rebuilds the *same* prefix
  conversation from SDK persistence, and forks it three times - ``none``,
  ``native_summary``, ``pruner_v1`` - **sequentially at the same absolute
  workspace path**, rotating the order and verifying full-hash equality before
  each branch.  Every branch receives the identical host failure feedback, the
  identical tool boundary and the prefix's already-consumed requests.
* ``--stage run`` does both, in that order, in one process.
* ``--stage check`` is the zero-API gate: workspace preparation plus the
  negative/positive host baseline that must hold before any model request.

No model request is issued unless ``--stage`` is ``prefix``, ``branches`` or
``run`` and a provider key is available.
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
#: The corpus arm and the plugin arm run the same condenser on purpose.
CONDENSER_CLASS = {'none': 'NoOpCondenser', 'native_summary': 'LLMSummarizingCondenser',
                   'pruner_v1': 'ContextPrunerCondenserV51'}
DEFAULT_BRANCH_ORDER = ('none', 'native_summary', 'pruner_v1')
ARM_ALIAS = {'pruner_v1': 'pruner_v51'}

#: Frozen before the paid run.  A pilot is one task and one prefix; the
#: instance and the instance-order rule are fixed here rather than chosen after
#: seeing which prefix looks favourable.
FROZEN_TASK = 'django_referenced_window_wrapping'
FROZEN_ARM = 'none'

#: Per-task negative/positive host gate, produced by the zero-API ``--check``
#: before any model request (same artifacts and rule as v52/v53).
GATE_FILES = {FROZEN_TASK: '.tooling/gates/v49-django-17084.json'}
EXPECTED_BASELINE = {
    FROZEN_TASK: {'public_ran': 124, 'host_ran': 125, 'host_failures': 'errors=1'},
}
#: The single file the reference fix touches; verified against the dataset
#: patch file list in ``--check`` and enforced by the editor executor.
EXPECTED_ALLOWED = {FROZEN_TASK: ['django/db/models/sql/query.py']}

PROTOCOL = {
    'version': 'openhands-django-v54-same-prefix-fork',
    'model': 'openai/deepseek-v4-flash', 'temperature': 0,
    'thinking': 'disabled', 'api_retries': 0,
    'source_commit': COMMIT[FROZEN_TASK], 'tasks': [FROZEN_TASK],
    'arms': list(ARMS), 'repeats': 1,
    'prefix_arm': FROZEN_ARM,
    'prefix_condenser': CONDENSER_CLASS[FROZEN_ARM],
    'branch_condensers': {arm: CONDENSER_CLASS[arm] for arm in ARMS},
    'branch_order': list(DEFAULT_BRANCH_ORDER),
    'branch_rotation': 'fixed prefix order for batch 01; --rotate is frozen per batch',
    'host_feedback_rounds_per_branch': 2,
    'trigger_input_tokens': 28000, 'pruner_target_tokens': 22400,
    'pruner_hard_tokens': 39200, 'native_max_tokens': 33600,
    'native_max_events': 240,
    'max_agent_calls_per_sample': 36, 'max_summary_calls_per_sample': 16,
    'initial_work_requests': 24, 'first_phase_max_requests': 26,
    'host_feedback_correction_reserve': 10, 'closing_request_reserve': 2,
    'max_output_tokens': 3072, 'max_single_estimated_input': 80000,
    'max_total_estimated_input_per_sample': 2000000,
    'single_input_headroom': 12000,
    'phase_plan': ['shared_prefix_read_and_implement',
                   'host_feedback_correction_only_if_failed'],
    'tools': ['scoped_editor', 'scoped_symbols', 'scoped_tests', 'finish'],
    'max_sdk_steps_per_phase': 200,
    'tool_boundary': 'editor executor refuses every edit outside the frozen allowed list; '
                     'symbol search and file view are restricted to the frozen research set; '
                     'no shell is registered; the same boundary applies to the prefix and all branches',
    'host_feedback_channel': 'host target test on a scoring copy (host-tests.patch applied there only)',
    'prefix_selection': 'the committed corpus task and its committed arm, run once; the prefix is '
                        'whatever that run produces, and it is not re-selected',
    'prefix_reuse': 'the branches stage rebuilds the prefix conversation from SDK persistence and '
                    'must reproduce the frozen event-ID sequence and full-file workspace hashes; '
                    'a mismatch aborts before any branch request',
    'branch_workspace_policy': 'one absolute workspace path; branches are strictly sequential; '
                               'full-hash restore from a read-only snapshot before each branch',
    'primary_statistic': 'post-fork incremental complete-total provider tokens per branch '
                         '(including every summary/native request); failures and cap hits retained',
    'secondary_statistic': 'whole-run complete-total tokens counting the shared prefix exactly once',
    'compaction_rule': 'if the plugin branch never condenses after the fork, the pilot reports '
                       '"mechanism not triggered" and no compression effect is claimed',
    'cost': 'provider tokens only; money unknown, SDK model price not mapped',
    'acceptance': 'host target test on the final workspace + selected unchanged upstream regression '
                  'module + file boundary',
    'upstream_test_limitation': 'selected module only; not the full upstream suite',
    'data': 'public Django SWE-bench-verified instance; no synthetic payload',
    'boundary_statement': 'one task x one prefix x three arms cannot estimate the plugin average '
                          'effect on any task population',
    'v54_changes': [
        'Same-prefix fork instead of three independent starts: the common prefix is produced once '
        'and every arm continues it from identical events and identical workspace bytes',
        'Sequential same-absolute-path execution with a read-only snapshot restored and full-hash '
        'verified before each branch',
        'Branch ledgers deep-copy the prefix request/estimated-input counters, so no branch is given '
        'a fresh 36-request allowance',
        'Per-stage timing for prepare (copy+hash), workspace restore, model wait, host tests and the '
        'final audit',
        'The plugin arm must actually condense after the fork for the pilot to report an effect',
    ],
    'v53_carryover': 'Task set, model, endpoint, prompts, tools, navigation, budget policy values, '
                     'per-sample request limits, condenser implementation and success criteria are '
                     'unchanged from v53; only the comparison design (same prefix) changes.',
}


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def timer() -> float:
    return time.perf_counter()


# --------------------------------------------------------------------------
# Prompts (frozen literals; the branch prompt text is identical for all arms)
# --------------------------------------------------------------------------

def prefix_prompt(workspace: Path, task: str) -> str:
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
        'In this first phase, use up to 24 requests. Having requests left over is normal and is not a '
        'reason to stop early; stop only when the request ceiling is reached or the work is truly done. '
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
    """Everything the fork flow needs, so the zero-API gate can drive it."""

    out: Path
    task: str = FROZEN_TASK
    stage: str = 'check'
    rotate: bool = False
    resume: bool = False
    api_key: str | None = None
    # Injectable seams (the gate passes synthetic ones; the CLI keeps defaults).
    workspace: Path | None = None
    prepare_workspace: bool = True
    #: Digest of the zero-API check's prepared workspace; when it is present and
    #: matches, the existing workspace is reused instead of re-copied.
    baseline_digest: str | None = None
    evaluator: Callable[..., dict] | None = None
    snapshot: Path | None = None
    plan: dict = field(default_factory=dict)


class Engine:
    """Runs the prefix, the snapshot and the three sequential branches."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.out = Path(config.out)
        self.task = config.task
        self.workspace = Path(config.workspace) if config.workspace else self.out / 'workspaces' / 'prefix'
        self.snapshot = Path(config.snapshot) if config.snapshot else self.out / 'prefix-snapshot'
        self.experiment_root = self.out
        self.evaluator = config.evaluator or evaluate
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
        self.branch_order = list(DEFAULT_BRANCH_ORDER)
        if config.rotate:
            # Rotation shifts which arm runs first; it never changes the set.
            self.branch_order = self.branch_order[1:] + self.branch_order[:1]

    # -- timing ----------------------------------------------------------
    def add_time(self, segment: str, seconds: float) -> None:
        self.timing[segment] = round(self.timing.get(segment, 0.0) + seconds, 2)

    # -- workspace -------------------------------------------------------
    def prepare(self) -> dict:
        """Create the prefix workspace, reusing a verified gate workspace.

        The zero-API check already prepares this exact workspace and records the
        digest of that baseline.  Re-copying 20,128 files would spend minutes of
        the paid window on work whose result is already proven, so an existing
        workspace is **reused only when its complete per-file hash map is
        byte-identical** to the check's baseline; otherwise it is removed and
        recreated from the frozen upstream tree.  Either way the hashes recorded
        are the hashes of the workspace that the experiment actually uses.
        """
        started = timer()
        reused = False
        if self.config.prepare_workspace:
            expected = self.config.baseline_digest
            if expected is None and (self.out / 'check.json').is_file():
                expected = load(self.out / 'check.json').get('baseline_hashes_digest')
            if expected and self.workspace.is_dir():
                from audit_v53_fast_core import file_map, map_digest
                observed = file_map(self.workspace)
                if map_digest(observed) == expected:
                    reused = True
                    original = observed
            if not reused:
                if self.workspace.exists():
                    shutil.rmtree(self.workspace)
                original = prepare(ROOT / '.tooling/upstream', self.workspace, self.task)
        else:
            original = hashes(self.workspace)
        self.add_time('prepare_reuse_verify_seconds' if reused else 'prepare_copy_hash_seconds',
                      timer() - started)
        self.prepared = {'reused_gate_workspace': reused, 'file_count': len(original)}
        save(self.out / 'original-hashes.json', original)
        return original

    def bind_tools(self, edit_guard) -> None:
        from integrations.openhands.fork_tools_v54 import bind_tool_context
        self.tool_ctx.update(
            workspace=self.workspace,
            allowed=[self.workspace / p for p in TASKS[self.task]['allowed']],
            edit_guard=edit_guard,
        )
        bind_tool_context(**self.tool_ctx)

    # -- shared pieces ---------------------------------------------------
    def make_llm(self, kind: str, sample_name: str, ledger, policy):
        from pydantic import SecretStr
        from bounded_llm_v30 import BudgetAwareLLM
        validate_estimated_input = self.plan.get('max_single_estimated_input',
                                                 PROTOCOL['max_single_estimated_input'])
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
        assert validate_estimated_input  # protocol values must exist before use
        return llm

    def new_policy(self, *, prefix_used_agent_calls=0, prefix_used_estimated_input=0):
        from budget_policy_v54 import PrefixCarryingBudgetPolicy
        return PrefixCarryingBudgetPolicy(
            lambda: code_revision(self.workspace, self.task),
            prefix_used_agent_calls=prefix_used_agent_calls,
            prefix_used_estimated_input=prefix_used_estimated_input,
            initial_work_requests=PROTOCOL['initial_work_requests'],
            correction_call_limit=PROTOCOL['host_feedback_correction_reserve'],
            max_agent_calls=PROTOCOL['max_agent_calls_per_sample'],
            reserve_requests=PROTOCOL['closing_request_reserve'],
            max_single_input=PROTOCOL['max_single_estimated_input'],
            max_total_input=PROTOCOL['max_total_estimated_input_per_sample'],
            single_input_headroom=PROTOCOL['single_input_headroom'])

    def make_condenser(self, arm: str, summary_llm):
        from openhands.sdk.context.condenser import NoOpCondenser, LLMSummarizingCondenser
        from context_pruner.adapters.openhands_v51 import ContextPrunerCondenserV51
        if arm == 'none':
            return NoOpCondenser()
        if arm == 'native_summary':
            return LLMSummarizingCondenser(
                llm=summary_llm, max_size=PROTOCOL['native_max_events'],
                max_tokens=PROTOCOL['native_max_tokens'], keep_first=2,
                hard_context_reset_max_retries=1)
        condenser = ContextPrunerCondenserV51(
            trigger_tokens=PROTOCOL['trigger_input_tokens'],
            target_tokens=PROTOCOL['pruner_target_tokens'],
            hard_tokens=PROTOCOL['pruner_hard_tokens'],
            protected_contract='Edit only: ' + ', '.join(TASKS[self.task]['allowed']) +
                               '. All other source files are read-only.')
        return condenser

    def agent_for(self, arm: str, agent_llm, condenser, sample_name: str):
        from openhands.sdk import Agent
        from openhands.sdk.tool import Tool
        from integrations.openhands.fork_tools_v54 import (
            EDITOR_TOOL_NAME, SYMBOLS_TOOL_NAME, TESTS_TOOL_NAME)
        # The policy allow-list and the registered tools must agree by name, or
        # a work-phase request silently loses the editor.
        assert {EDITOR_TOOL_NAME, SYMBOLS_TOOL_NAME, TESTS_TOOL_NAME} == set(
            __import__('budget_policy_v54', fromlist=['TOOL_NAMES']).TOOL_NAMES)
        return Agent(
            llm=agent_llm,
            tools=[Tool(name=EDITOR_TOOL_NAME), Tool(name=SYMBOLS_TOOL_NAME),
                   Tool(name=TESTS_TOOL_NAME,
                        params={'workspace': str(self.workspace), 'task': self.task,
                                'destination': str(self.out / sample_name / 'tool-tests')})],
            include_default_tools=['FinishTool'], condenser=condenser)

    # -- accounting helpers ---------------------------------------------
    @staticmethod
    def usage(metrics: dict) -> dict:
        totals = metrics['accumulated_token_usage']
        return {'prompt': totals['prompt_tokens'], 'completion': totals['completion_tokens']}

    def ledger_view(self, ledger: dict) -> dict:
        agent_calls = sum(c['kind'] == 'agent' for c in ledger['calls'])
        summary_calls = sum(c['kind'] == 'summary' for c in ledger['calls'])
        return {
            'agent_requests': agent_calls, 'summary_requests': summary_calls,
            'total_requests': len(ledger['calls']),
            'estimated_input': ledger['estimated_input'],
            'failed_requests': [c for c in ledger['calls'] if c['status'] != 'returned'],
            'shared_prefix_call_count': ledger.get('shared_prefix_call_count'),
            'shared_prefix_estimated_input': ledger.get('shared_prefix_estimated_input'),
        }

    def complete_total_tokens(self, agent_metrics: dict, summary_metrics: dict) -> int:
        agent = self.usage(agent_metrics)
        summary = self.usage(summary_metrics)
        return agent['prompt'] + agent['completion'] + summary['prompt'] + summary['completion']

    def condensation_count(self, conversation) -> int:
        if conversation is None:
            return 0
        return sum(type(event).__name__ == 'Condensation'
                   for event in conversation.state.events)

    # -- prefix ----------------------------------------------------------
    def run_prefix(self) -> dict:
        from integrations.openhands.fork_tools_v54 import register_v54_tools
        from openhands.sdk import Conversation

        register_v54_tools()

        original = self.prepare()
        from edit_retry_guard_v33 import EditRetryGuard
        from integrations.openhands.source_navigation_v42 import SourceNavigatorV42
        from integrations.openhands.task_state_v42 import TaskState
        from same_prefix_fork_v54 import capture_workspace, tree_hashes

        self.tool_ctx['navigator'] = SourceNavigatorV42(
            self.workspace, [self.workspace / p for p in TASKS[self.task]['research_files']],
            TASKS[self.task]['problem'])
        self.bind_tools(EditRetryGuard())
        self.task_state = TaskState(TASKS[self.task]['problem'][:900],
                                    ', '.join(TASKS[self.task]['allowed']))

        self.ledger = {'calls': [], 'estimated_input': 0}
        self.policy = self.new_policy()
        sample_name = 'prefix'
        sample_dir = self.out / sample_name
        sample_dir.mkdir(parents=True, exist_ok=True)
        self.prefix_llm = self.make_llm('agent', sample_name, self.ledger, self.policy)
        self.prefix_summary_llm = self.make_llm('summary', sample_name, self.ledger, self.policy)
        self.prefix_condenser = self.make_condenser(FROZEN_ARM, self.prefix_summary_llm)
        agent = self.agent_for(FROZEN_ARM, self.prefix_llm, self.prefix_condenser, sample_name)

        events = []

        def callback(event):
            self.policy.observe_event(event)
            events.append({'type': type(event).__name__, 'id': str(event.id)})

        report = {'sample': sample_name, 'kind': 'prefix', 'task': self.task, 'arm': FROZEN_ARM,
                  'condenser': CONDENSER_CLASS[FROZEN_ARM], 'success': False}
        started = timer()
        try:
            self.source = Conversation(
                agent=agent, workspace=str(self.workspace), callbacks=[callback],
                persistence_dir=str(self.out / 'sdk-persistence' / 'prefix'),
                delete_on_close=False, visualizer=None,
                max_iteration_per_run=PROTOCOL['max_sdk_steps_per_phase'])
            model_started = timer()
            self.source.send_message(prefix_prompt(self.workspace, self.task))
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
            report['events'] = events
            report['ledger'] = self.ledger_view(self.ledger)
            report['agent_metrics'] = self.prefix_llm.metrics.model_dump(mode='json')
            report['summary_metrics'] = self.prefix_summary_llm.metrics.model_dump(mode='json')
            report['complete_total_tokens'] = self.complete_total_tokens(
                report['agent_metrics'], report['summary_metrics'])
            save(sample_dir / 'report.json', report)
            save(sample_dir / 'ledger.json', self.ledger)

        # The host's first acceptance decides the feedback every branch receives.
        host_started = timer()
        first = self.evaluator(self.workspace, self.task, sample_dir / 'evaluation-prefix')
        self.add_time('host_test_seconds', timer() - host_started)
        report['host_evaluation'] = first
        report['host_test_passed'] = bool(first['passed'])
        report['prefix_needs_correction'] = not bool(first['passed'])
        self.task_state.record_verification(
            code_revision(self.workspace, self.task),
            'Host acceptance passed' if first['passed'] else
            'Host acceptance failed: ' + str(first['failures']))
        report['correction_feedback'] = self.host_feedback(first, sample_dir / 'evaluation-prefix')
        if not first['passed']:
            self.task_state.record_feedback(code_revision(self.workspace, self.task),
                                            report['correction_feedback'][:1000])
        save(sample_dir / 'report.json', report)

        # Freeze the workspace bytes the branches must start from.
        if self.snapshot.exists():
            shutil.rmtree(self.snapshot)
        snap_started = timer()
        frozen = capture_workspace(self.workspace, self.snapshot, experiment_root=self.experiment_root)
        self.add_time('prefix_snapshot_seconds', timer() - snap_started)
        verified_started = timer()
        if tree_hashes(self.snapshot) != frozen:
            raise RuntimeError('frozen snapshot does not match the prefix workspace')
        self.add_time('snapshot_verify_seconds', timer() - verified_started)
        changed = sorted(k for k in set(original) | set(frozen) if original.get(k) != frozen.get(k))
        report['changed_files'] = changed
        report['file_boundary_ok'] = set(changed) <= set(TASKS[self.task]['allowed'])
        report['event_id_digest'] = digest_ids(self.prefix_event_ids())
        # Why the prefix's first phase ended is part of the frozen record: a
        # prefix stopped by the SDK iteration cap is not the same evidence as one
        # that closed under the request budget.
        report['first_phase_max_requests'] = PROTOCOL['first_phase_max_requests']
        report['first_phase_within_budget'] = (
            report['agent_requests'] <= PROTOCOL['first_phase_max_requests'])
        report['prefix_summary_calls'] = report['summary_requests']
        report['workspace_preparation'] = dict(self.prepared)
        # The snapshot was compared against the live workspace above; reaching
        # this line means the frozen bytes equal the prefix bytes.
        # The freeze must describe the prefix that was actually run: a mismatch
        # here would fork a different history than the one recorded.
        freeze_budget = {
            'agent_requests': report['agent_requests'],
            'summary_requests': report['summary_requests'],
            'estimated_input': report['estimated_input'],
        }
        assert (sum(c['kind'] == 'agent' for c in self.ledger['calls']),
                sum(c['kind'] == 'summary' for c in self.ledger['calls']),
                self.ledger['estimated_input']) == (
            freeze_budget['agent_requests'], freeze_budget['summary_requests'],
            freeze_budget['estimated_input']), 'prefix ledger and prefix report disagree'
        save(sample_dir / 'report.json', report)
        save(self.out / 'prefix-freeze.json', {
            'task': self.task, 'arm': FROZEN_ARM,
            'conversation_id': str(self.source.state.id),
            'event_count': len(self.prefix_event_ids()),
            'event_id_digest': report['event_id_digest'],
            'workspace': str(self.workspace), 'workspace_file_count': len(frozen),
            'workspace_digest': digest_map(frozen),
            'snapshot': str(self.snapshot),
            'host_test_passed': report['host_test_passed'],
            'prefix_needs_correction': report['prefix_needs_correction'],
            # The exact feedback bytes every branch receives, frozen with the
            # prefix so no branch can be given different host information.
            'correction_feedback': report['correction_feedback'],
            'host_evaluation': report['host_evaluation'],
            **freeze_budget,
            'complete_total_tokens': report['complete_total_tokens'],
            'policy_state': self.policy.capture_prefix_state(),
            'first_phase_within_budget': report['first_phase_within_budget'],
        })
        save(sample_dir / 'frozen-hashes.json', frozen)
        save(self.out / 'frozen-hashes.json', frozen)
        return report

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

    # -- branches --------------------------------------------------------
    def rebuild_prefix_conversation(self, freeze: dict) -> None:
        """Reopen the frozen prefix from SDK persistence before forking.

        The branch stage must not re-run the prefix (that would create a
        different shared history).  It rebuilds the *same* conversation: the
        event log is loaded from the persistence directory the prefix wrote,
        and the event-ID sequence and workspace hashes are checked against the
        frozen values before any branch exists.
        """
        from openhands.sdk import Agent, Conversation, LLM
        from same_prefix_fork_v54 import tree_hashes
        from integrations.openhands.fork_tools_v54 import (
            EDITOR_TOOL_NAME, SYMBOLS_TOOL_NAME, TESTS_TOOL_NAME,
            register_v54_tools)
        from openhands.sdk.tool import Tool
        from edit_retry_guard_v33 import EditRetryGuard
        from integrations.openhands.source_navigation_v42 import SourceNavigatorV42
        from integrations.openhands.task_state_v42 import TaskState

        register_v54_tools()
        self.tool_ctx['navigator'] = SourceNavigatorV42(
            self.workspace, [self.workspace / p for p in TASKS[self.task]['research_files']],
            TASKS[self.task]['problem'])
        self.bind_tools(EditRetryGuard())
        self.task_state = TaskState(TASKS[self.task]['problem'][:900],
                                    ', '.join(TASKS[self.task]['allowed']))
        # The prefix agent is only a persistence host: it never makes a request
        # again, and `ConversationState.create` verifies the tool names match.
        host_llm = LLM(model=PROTOCOL['model'], api_key='unused')
        host_agent = Agent(llm=host_llm, tools=[
            Tool(name=EDITOR_TOOL_NAME), Tool(name=SYMBOLS_TOOL_NAME),
            Tool(name=TESTS_TOOL_NAME,
                 params={'workspace': str(self.workspace), 'task': self.task,
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
        from same_prefix_fork_v54 import (attach_branch_callback,
                                          branch_ledger_from_prefix,
                                          restore_workspace, tree_hashes)

        sample_name = f'branch-{position}-{arm}'
        sample_dir = self.out / sample_name
        sample_dir.mkdir(parents=True, exist_ok=True)
        report = {'sample': sample_name, 'kind': 'branch', 'task': self.task, 'arm': arm,
                  'condenser': CONDENSER_CLASS[arm], 'position': position,
                  'success': False, 'prefix_needs_correction': freeze['prefix_needs_correction']}

        # 1. same absolute path, restored from the read-only snapshot.
        restore_started = timer()
        restore_workspace(self.workspace, self.snapshot, experiment_root=self.experiment_root,
                          expected_hashes=frozen)
        self.add_time('restore_workspace_seconds', timer() - restore_started)
        verify_started = timer()
        observed = tree_hashes(self.workspace)
        self.add_time('restore_hash_verify_seconds', timer() - verify_started)
        report['restore_hash_equal'] = observed == frozen
        if not report['restore_hash_equal']:
            raise RuntimeError(f'{sample_name}: restored workspace differs from the frozen prefix')

        # 2. independent ledger + policy that continue the prefix's counters.
        self.bind_tools(EditRetryGuard())
        ledger = branch_ledger_from_prefix(self.ledger)
        policy = self.new_policy(prefix_used_agent_calls=freeze['agent_requests'],
                                 prefix_used_estimated_input=freeze['estimated_input'])
        # The prefix's own phase state is evidence, not branch input: the branch
        # starts its accounting at the fork point (see budget_policy_v54).
        policy.check_prefix_state(freeze['policy_state'])
        if policy.correction_stop_at != min(
                PROTOCOL['max_agent_calls_per_sample'],
                freeze['agent_requests'] + PROTOCOL['host_feedback_correction_reserve']):
            raise RuntimeError('branch correction boundary does not continue the prefix')

        agent_llm = self.make_llm('agent', sample_name, ledger, policy)
        summary_llm = self.make_llm('summary', sample_name, ledger, policy)
        condenser = self.make_condenser(arm, summary_llm)
        if arm == 'pruner_v1':
            condenser.set_current_state(self.task_state.render(code_revision(self.workspace, self.task)))
        agent = self.agent_for(arm, agent_llm, condenser, sample_name)

        events = []

        def callback(event):
            policy.observe_event(event)
            if arm == 'pruner_v1' and type(event).__name__ == 'ObservationEvent':
                condenser.set_current_state(
                    self.task_state.render(code_revision(self.workspace, self.task)))
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
                        self.task_state.render(code_revision(self.workspace, self.task)))
                model_started = timer()
                fork.send_message(correction_prompt(self.task, feedback))
                fork.run()
                self.add_time('branch_model_wait_seconds', timer() - model_started)
                report['sdk_status'] = str(fork.state.execution_status.value)
                host_started = timer()
                result = self.evaluator(self.workspace, self.task,
                                        sample_dir / f'evaluation-round{round_index + 1}')
                self.add_time('host_test_seconds', timer() - host_started)
                rounds.append({'round': round_index + 1, 'host': result,
                               'agent_requests_used': sum(c['kind'] == 'agent' for c in ledger['calls'])})
                self.task_state.record_verification(
                    code_revision(self.workspace, self.task),
                    'Host acceptance passed' if result['passed'] else
                    'Host acceptance failed: ' + str(result['failures']))
                if result['passed']:
                    break
                feedback = self.host_feedback(result, sample_dir / f'evaluation-round{round_index + 1}')
                self.task_state.record_feedback(code_revision(self.workspace, self.task),
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
            # The branch callback must persist exactly the branch suffix; the
            # shared user message was emitted before the fork and is deliberately
            # not re-delivered as a branch event.
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
            final_hashes = hashes(self.workspace)
            changed = sorted(k for k in set(frozen) | set(final_hashes)
                             if frozen.get(k) != final_hashes.get(k))
            report['changed_files'] = changed
            report['file_boundary_ok'] = set(changed) <= set(TASKS[self.task]['allowed'])
            save(sample_dir / 'ledger.json', ledger)
            fork.close()
            save(sample_dir / 'report.json', report)

        rounds = report.get('host_rounds') or []
        final = rounds[-1]['host'] if rounds else None
        report['final_host_passed'] = bool(final and final['passed'])
        report['artifact_success'] = bool(report['final_host_passed'] and report['file_boundary_ok'])
        report['success'] = bool(report['artifact_success']
                                 and report.get('sdk_status') == 'finished'
                                 and report['workflow_verification_ok']
                                 and not report.get('error_type'))
        save(sample_dir / 'report.json', report)
        return report

    # -- stages ----------------------------------------------------------
    def run_check(self) -> dict:
        for path in (self.out / 'workspaces', self.out / 'baseline'):
            if path.exists():
                shutil.rmtree(path)
        started = timer()
        if self.config.prepare_workspace:
            prepare(ROOT / '.tooling/upstream', self.workspace, self.task)
        self.add_time('gate_prepare_seconds', timer() - started)
        hash_started = timer()
        baseline_hashes = hashes(self.workspace)
        self.add_time('gate_hash_seconds', timer() - hash_started)
        save(self.out / 'baseline' / 'original-hashes.json', baseline_hashes)

        # The frozen edit scope must equal what the dataset's reference patch touches.
        patch = (ROOT / '.tooling/swebench-verified'
                 / f'django-{INSTANCES[self.task]["instance_id"].split("-")[-1]}' / 'reference.patch')
        touched = sorted({line.split(' b/')[-1].replace('\\', '/')
                          for line in patch.read_text(encoding='utf-8', errors='replace').splitlines()
                          if line.startswith('diff --git')})
        assert TASKS[self.task]['allowed'] == EXPECTED_ALLOWED[self.task], TASKS[self.task]['allowed']
        assert touched == sorted(EXPECTED_ALLOWED[self.task]), (touched, EXPECTED_ALLOWED[self.task])

        started = timer()
        regression = self.evaluator(self.workspace, self.task, self.out / 'baseline' / 'regression',
                                    regression_only=True)
        self.add_time('gate_host_test_seconds', timer() - started)
        expected = EXPECTED_BASELINE[self.task]
        assert (regression['passed'] and regression['ran'] == expected['public_ran']
                and regression['workspace_unchanged_by_test']), regression
        started = timer()
        result = self.evaluator(self.workspace, self.task, self.out / 'baseline' / 'evaluation')
        self.add_time('gate_host_test_seconds', timer() - started)
        assert (not result['passed'] and result['ran'] == expected['host_ran']
                and result['failures'] == expected['host_failures']
                and result['workspace_unchanged_by_test']), result
        gate = {'task': self.task, 'allowed': TASKS[self.task]['allowed'],
                'reference_patch_files': touched, 'public_regression': regression,
                'host_acceptance': result, 'baseline_hashes_digest': digest_map(baseline_hashes),
                'workspace_file_count': len(baseline_hashes)}
        save(self.out / 'check.json', gate)
        return gate


def branch_ledger_view(ledger: dict, freeze: dict) -> dict:
    """Branch-local totals plus the carried-over prefix counters.

    The branch ledger holds only the branch's own requests, so the post-fork
    increment is the ledger itself and the prefix's consumption is reported
    from the carried counters rather than by subtraction.
    """
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


def digest_ids(ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest()


def digest_map(mapping: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# --------------------------------------------------------------------------
# manifest / sources
# --------------------------------------------------------------------------

def source_paths() -> list[Path]:
    paths = [
        Path(__file__), HERE / 'fork_tools_v54.py', HERE / 'budget_policy_v54.py',
        HERE / 'same_prefix_fork_v54.py', HERE / 'audit_validation_v54.py',
        HERE / 'run_validation_windows_v54.py', HERE / 'PILOT_PROTOCOL_V54.md',
        HERE / 'PILOT_PROTOCOL_V54_DRAFT.md', HERE / 'PILOT_PROTOCOL_V53.md',
        HERE / 'validation_tasks_v49.py', HERE / 'validation_tasks_v45.py',
        HERE / 'verification_tool_django_v49.py', HERE / 'edit_scope_guard_v41.py',
        HERE / 'edit_retry_guard_v33.py', HERE / 'task_suite.py',
        HERE / 'bounded_llm_v30.py', HERE / 'budget_policy_v30.py',
        HERE / 'budget_policy_v31.py', HERE / 'budget_policy_v39.py',
        HERE / 'budget_policy_v42.py', HERE / 'source_navigation_v31.py',
        HERE / 'source_navigation_v42.py', HERE / 'symbol_tool_v31.py',
        HERE / 'symbol_tool_v35.py', HERE / 'symbol_tool_v42.py', HERE / 'symbol_tool_v44.py',
        HERE / 'feedback_v40.py', HERE / 'feedback_v42.py', HERE / 'task_state_v42.py',
        HERE / 'audit_optimizations.py', HERE / 'audit_v53_fast_core.py',
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
    # De-duplicate: one file may be reachable through more than one pattern, and
    # a duplicated entry would make the manifest hash count ambiguous.
    return list(dict.fromkeys(paths))


def build_manifest(engine: Engine) -> dict:
    manifest = dict(PROTOCOL)
    manifest['versions'] = {name: importlib.metadata.version(name) for name in
                            ('openhands-sdk', 'openhands-tools', 'litellm', 'context-pruner')}
    files = source_paths()
    files += [ROOT / rel for rel in GATE_FILES.values()]
    for task in (engine.task,):
        instance_id = INSTANCES[task]['instance_id']
        instance_dir = ROOT / '.tooling/swebench-verified' / (
            'django-' + instance_id.split('-')[-1])
        # The provenance record uses the dataset id with double underscores
        # collapsed (django__django-17084 -> export_django_django-17084.json).
        export_name = 'export_' + instance_id.replace('__', '_') + '.json'
        files += [instance_dir / 'instance.json',
                  instance_dir / 'host-tests.patch', instance_dir / 'reference.patch',
                  ROOT / '.tooling' / export_name]
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
    """Post-fork and whole-run totals; the shared prefix is counted once."""
    prefix = load(out / 'prefix' / 'report.json')
    branches = [load(path / 'report.json') for path in sorted(out.glob('branch-*/'))]
    prefix_total = prefix['complete_total_tokens']
    rows = {}
    for report in branches:
        row = {
            'arm': report['arm'], 'position': report['position'],
            'post_fork_tokens': report['complete_total_tokens'],
            'whole_run_tokens_shared_prefix_counted_once': prefix_total + report['complete_total_tokens'],
            'final_host_passed': report['final_host_passed'],
            'artifact_success': report['artifact_success'],
            'file_boundary_ok': report['file_boundary_ok'],
            'post_fork_agent_requests': report['post_fork_agent_requests'],
            'post_fork_total_requests': report['post_fork_total_requests'],
            'carried_over_agent_requests': report['ledger']['carried_over_agent_requests'],
            'condensation_events': report['condensation_events'],
            'mechanism_triggered': report['mechanism_triggered'],
            'restore_hash_equal': report['restore_hash_equal'],
            'error_type': report.get('error_type'),
        }
        rows[report['arm']] = row
    baseline = rows.get('none')
    if baseline:
        for arm, row in rows.items():
            if arm == 'none':
                continue
            denominator_post = baseline['post_fork_tokens']
            denominator_whole = baseline['whole_run_tokens_shared_prefix_counted_once']
            row['post_fork_savings_vs_none'] = (
                round((denominator_post - row['post_fork_tokens']) / denominator_post, 6)
                if denominator_post else None)
            row['whole_run_savings_vs_none'] = (
                round((denominator_whole - row['whole_run_tokens_shared_prefix_counted_once'])
                      / denominator_whole, 6) if denominator_whole else None)
    return {'prefix': {'complete_total_tokens': prefix_total,
                       'host_test_passed': prefix['host_test_passed'],
                       'prefix_needs_correction': prefix['prefix_needs_correction'],
                       'agent_requests': prefix['agent_requests'],
                       'summary_requests': prefix['summary_requests'],
                       'condensation_events': prefix['condensation_events'],
                       'elapsed_seconds': prefix['elapsed_seconds'],
                       'timing_seconds': prefix.get('timing_seconds', {})},
            'branches': rows,
            'branch_order': [load(path / 'report.json')['arm']
                             for path in sorted(out.glob('branch-*/'))],
            'timing_seconds': timing_summary(prefix, branches),
            'mechanism_triggered_plugin': bool(rows.get('pruner_v1', {}).get('mechanism_triggered')),
            'note': ('post-fork totals are the branch-local provider total including every '
                     'summary/native request; whole-run totals add the shared prefix once, so no '
                     'prefix cost is duplicated across arms and no arm is credited with it twice')}


def timing_summary(prefix: dict, branches: list[dict]) -> dict:
    """Per-stage wall time, split into the prefix stage and the branch stage.

    Kept separate on purpose: the prefix stage pays the workspace preparation
    and the snapshot, and the branch stage pays three snapshot restores. Merging
    them into one number would hide which stage a future optimisation helps.
    """
    stages: dict[str, dict[str, float]] = {'prefix_seconds': {}, 'branches_seconds': {},
                                           'total_seconds': {}}
    for report, bucket in ((prefix, 'prefix_seconds'), *[(b, 'branches_seconds') for b in branches]):
        for segment, seconds in (report.get('timing_seconds') or {}).items():
            stages[bucket][segment] = round(stages[bucket].get(segment, 0.0) + seconds, 2)
            stages['total_seconds'][segment] = round(
                stages['total_seconds'].get(segment, 0.0) + seconds, 2)
    stages['wall_clock_seconds'] = {
        'prefix_sample_seconds': round(prefix.get('elapsed_seconds', 0.0), 2),
        'branch_samples_seconds': round(sum(b.get('elapsed_seconds', 0.0) for b in branches), 2),
    }
    return stages


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--stage', default='run', choices=['check', 'prefix', 'branches', 'run'],
                        help='check = zero-API gate; run = prefix then branches')
    parser.add_argument('--rotate', action='store_true',
                        help='rotate the frozen branch order by one position')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--repeats', type=int, default=1, choices=[1],
                        help='a same-prefix pilot is one prefix and one repetition')
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    if not out.is_relative_to(ROOT):
        raise SystemExit(f'refusing to write outside the project root: {out}')
    # The zero-API --check legitimately leaves a small set of gate artifacts in
    # the batch directory, and the prefix stage then runs in the same place.  A
    # paid sample or a second batch must never be reused, so anything else - in
    # particular prefix/, branch-*/ or comparison.json - requires --resume.
    # The zero-API check must run in a fresh batch directory: it rebuilds the
    # scratch workspaces from the frozen upstream baseline, and the paid prefix
    # is prepared the same way from the same baseline, so neither may inherit a
    # directory whose contents are not accounted for.
    CHECK_ARTIFACTS = {'sdk-persistence', 'windows-compatibility.json', 'manifest.json',
                       'baseline', 'check.json', 'original-hashes.json',
                       'timing-check.json', '.runtime-tmp'}
    if out.exists() and not args.resume:
        unexpected = sorted(p.name for p in out.iterdir() if p.name not in CHECK_ARTIFACTS)
        if args.stage == 'check':
            if unexpected or (out / 'check.json').exists():
                raise SystemExit(f'--stage check writes into a fresh batch directory '
                                 f'(unexpected entries: {unexpected or "check.json"})')
        elif unexpected:
            raise SystemExit(f'Output exists; use --resume for the branch stage of the same '
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

    paid = args.stage in {'prefix', 'branches', 'run'}
    config = EngineConfig(out=out, stage=args.stage, rotate=args.rotate, resume=args.resume,
                          api_key=os.environ.get('DEEPSEEK_API_KEY'))
    engine = Engine(config)
    engine.plan = dict(PROTOCOL)
    manifest = build_manifest(engine)
    if (out / 'manifest.json').exists():
        assert load(out / 'manifest.json') == manifest, 'Frozen protocol changed'
    else:
        save(out / 'manifest.json', manifest)

    for task, gate_rel in GATE_FILES.items():
        gate = load(ROOT / gate_rel)
        assert gate['instance'] == INSTANCES[task]['instance_id'], (task, gate['instance'])
        assert gate['base_commit'] == COMMIT[task], (task, gate['base_commit'])
        assert not gate['base_host']['passed'], (task, 'gate: base host unexpectedly passes')
        assert gate['reference_host']['passed'], (task, 'gate: reference host must pass')

    if args.stage == 'check':
        print(json.dumps(engine.run_check(), indent=2))
        save(out / 'timing-check.json', engine.timing)
        return 0

    if paid and not config.api_key:
        raise SystemExit('DEEPSEEK_API_KEY unavailable')

    freeze_path = out / 'prefix-freeze.json'
    if args.stage in {'prefix', 'run'}:
        if freeze_path.exists() and not args.resume:
            raise SystemExit('A frozen prefix already exists; use --stage branches --resume.')
        report = engine.run_prefix()
        print(json.dumps({'stage': 'prefix', 'host_test_passed': report['host_test_passed'],
                          'agent_requests': report['agent_requests'],
                          'complete_total_tokens': report['complete_total_tokens'],
                          'error_type': report.get('error_type')}, indent=2), flush=True)
        save(out / 'timing-prefix.json', engine.timing)
        if report.get('error_type'):
            raise SystemExit('Prefix failed; the frozen prefix was not produced. '
                             'Fix the cause and use a fresh output directory.')
        if not report.get('first_phase_within_budget'):
            raise SystemExit(
                'Prefix first phase exceeded the frozen request cap '
                f"({report['agent_requests']} > {PROTOCOL['first_phase_max_requests']}); "
                'the shared prefix would not be the frozen design. Preserved for audit; '
                'do not fork it. Diagnose and use a fresh batch directory.')
        if args.stage == 'prefix':
            return 0

    if args.stage in {'branches', 'run'}:
        if not freeze_path.exists():
            raise SystemExit('prefix-freeze.json missing; run --stage prefix first.')
        freeze = load(freeze_path)
        assert freeze['task'] == engine.task, freeze['task']
        frozen = load(out / 'frozen-hashes.json')
        if args.stage == 'branches':
            # A fresh process rebuilds the same prefix conversation from SDK
            # persistence; nothing about the prefix is re-requested.
            engine.ledger = load(out / 'prefix' / 'ledger.json')
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
        result = comparison(out)
        result['timing'] = engine.timing
        save(out / 'comparison.json', result)
        print(json.dumps(result, indent=2))
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
