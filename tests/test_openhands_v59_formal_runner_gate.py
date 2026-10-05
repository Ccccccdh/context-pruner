"""Zero-API gate for the v59 pre-registered prefix selection and branch persistence.

Everything here runs without a provider request, on a synthetic Django-shaped
workspace, through the SDK's real conversation, fork, tool-executor and condenser
code paths.  What is gated:

1. ``test_frozen_v55_protocol_document_matches_the_runner`` - the frozen protocol
   document, the runner's ``PROTOCOL`` and the unchanged v54 mechanism thresholds
   agree.
2. ``test_pre_registered_attempt_plan_and_decision_rule`` - the ladder order is
   the pre-registered one (rung-major, frozen task order), a task that already
   passed is not re-attempted, and the decision is the first qualifying attempt.
3. ``test_selection_rejects_host_passing_and_non_triggering_attempts`` - the two
   ways an attempt can be rejected, including "host still fails but the plugin
   would not condense".
4. ``test_select_stage_refuses_to_fork_when_prediction_does_not_trigger`` - the
   engine-level abort: no frozen prefix, no branch directory, no branch request.
5. ``test_trigger_prediction_reproduces_the_v54_no_compaction_outcome`` - the
   offline replay is checked against the frozen v54 batch, whose prefix measured
   22,213 condenser-view tokens against a 28,000 trigger and recorded zero
   condensations.  This is why v55 gates on a measured replay instead of on the
   ledger's estimated input.
6. ``test_three_sequential_branches_share_one_prefix_and_persist_their_artifacts``
   - the full flow: pre-registered selection, one frozen prefix, three sequential
   same-path branches restored from the read-only snapshot, per-branch persisted
   bytes/hashes/events/ledger/report, a pinned batch artifact index, and at least
   one post-fork condensation in the plugin arm.
7. ``test_tampered_branch_artifacts_are_rejected`` - a tampered byte, a tampered
   index, a modified final hash map, an added file where the index records a
   deletion and an artifact file removed from the batch all raise.
8. ``test_audit_rebuilds_and_rescores_every_branch_from_its_own_bytes`` - each
   branch's final workspace is rebuilt from the frozen snapshot plus that
   branch's own persisted bytes and the host target test is re-run there, for
   every branch, including the arms that changed a file.
9. ``test_branch_ledgers_continue_the_prefix_without_a_fresh_allowance`` - the
   carried request count and the shared ceiling, with no branch given a fresh
   allowance.

No network access and no model request are performed by this module.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations' / 'openhands'))

from integrations.openhands import branch_artifacts_v55 as artifacts  # noqa: E402
from integrations.openhands import prefix_selection_v55 as selection  # noqa: E402
from integrations.openhands import run_validation_v59 as runner  # noqa: E402
from integrations.openhands.branch_artifacts_v55 import (  # noqa: E402
    BranchArtifactError,
    reconstruct_branch_workspace,
    sha256_file,
    verify_batch_index,
    verify_branch_artifacts,
)

TASK = runner.FROZEN_TASKS[0]
FLAG = 'GATE_FLAG'
PUBLIC_MARKER = 'gate_definition'
ALLOWED = runner.EXPECTED_ALLOWED[TASK]
FIXED_SOURCE = ('# editable target\n'
                f'def {PUBLIC_MARKER}():\n    return {FLAG!r}\n')
BROKEN_SOURCE = '# editable target\n' + 'def gate_definition():\n    return 1\n'
V54_BATCH = ROOT / 'runs/stage5-openhands/django-multitask-v54-prefix-fork-01'


# --------------------------------------------------------------------------
# synthetic workspace and offline host test (same shape as the v54 gate)
# --------------------------------------------------------------------------

def build_workspace(path: Path, task: str = TASK) -> Path:
    spec = runner.TASKS[task]
    for name in sorted(set(spec['research_files']) | set(spec['allowed'])):
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith('runtests.py'):
            target.write_text('if __name__ == "__main__":\n    pass\n', encoding='utf-8')
        elif name.endswith('tests.py'):
            target.write_text('class GateCase:\n    def test_gate(self):\n        pass\n',
                              encoding='utf-8')
        else:
            target.write_text('def gate_definition():\n    return 1\n', encoding='utf-8')
    for name in spec['allowed']:
        (path / name).write_text(BROKEN_SOURCE, encoding='utf-8')
    (path / 'TASK.md').write_text(spec['problem'], encoding='utf-8')
    return path


def fake_evaluate(workspace: Path, task: str, destination: Path, regression_only: bool = False,
                  target_tests_only: bool = False) -> dict:
    """Offline stand-in for the host target test (satisfied only by ``FLAG``)."""
    workspace, destination = Path(workspace), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source = ''.join((workspace / name).read_text(encoding='utf-8')
                     for name in runner.TASKS[task]['allowed'])
    if regression_only:
        passed = True
    else:
        passed = FLAG in source
    (destination / 'test.txt').write_text(
        'FAIL: test_gate (tests.gate.GateCase)\nAssertionError: gate flag missing\n'
        if not passed else 'Ran 1 test\nOK\n', encoding='utf-8')
    return {'passed': passed, 'returncode': 0 if passed else 1, 'ran': 1,
            'failures': None if passed else 'failures=1',
            'summary': ['OK'] if passed else ['FAILED'], 'elapsed_seconds': 0.0,
            'host_tests_applied': not regression_only, 'selection': ['gate'],
            'workspace_unchanged_by_test': True}


# --------------------------------------------------------------------------
# scripted provider bound to the SDK LLM seam
# --------------------------------------------------------------------------

def _response(llm, index, name, arguments):
    from litellm.types.utils import ModelResponse
    from openhands.sdk.llm import Message, MessageToolCall
    from openhands.sdk.llm.llm_response import LLMResponse
    from openhands.sdk.llm.utils.metrics import MetricsSnapshot
    return LLMResponse(
        message=Message(role='assistant', content=[], tool_calls=[MessageToolCall(
            id=f'gate-{llm.usage_id}-{index}', name=name,
            arguments=json.dumps(arguments), origin='completion')]),
        metrics=MetricsSnapshot(),
        raw_response=ModelResponse(id=f'gate-{llm.usage_id}-{index}', choices=[]),
    )


def scripted_provider(driver):
    def provider(llm, messages, tools, *args, **kwargs):
        index = getattr(llm, '_gate_calls', 0) + 1
        llm._gate_calls = index
        names = sorted(getattr(tool, 'name', str(tool)) for tool in tools)
        name, arguments = driver(llm, index, names)
        return _response(llm, index, name, arguments)
    return provider


_SEEDED: dict[str, bool] = {}


def _generic_call(llm, index, names, workspace):
    """One step that respects the policy's offered tools and never loops."""
    key = str(workspace)
    if not _SEEDED.get(key, False):
        (workspace / ALLOWED[0]).write_text(BROKEN_SOURCE, encoding='utf-8')
    if 'scoped_editor' in names:
        window = [1, 1 + index] if index % 2 else [1, 3]
        return ('scoped_editor', {'command': 'view',
                                  'path': str(workspace / ALLOWED[0]),
                                  'view_range': window})
    if 'finish' in names:
        return ('finish', {'message': 'gate step: feature still failing'})
    assert 'scoped_tests' in names, names
    _SEEDED[key] = True
    from validation_tasks_v49 import code_revision as _revision
    verdict = json.dumps({'passed': True, 'code_revision': _revision(workspace, TASK)})
    getattr(llm, '_policy').observe_test(verdict, is_error=False)
    return ('scoped_tests', {'reason': f'gate step {index}: public regression check'})


def prefix_driver_broken(llm, index, names):
    """Prefix: leave the flag absent, so the host target test demands correction."""
    return _generic_call(llm, index, names, Path(llm._gate_workspace))


def branch_driver_edits(llm, index, names):
    """Branch: land the fix on the first step, then verify and finish."""
    workspace = Path(llm._gate_workspace)
    if index == 1:
        (workspace / ALLOWED[0]).write_text(FIXED_SOURCE, encoding='utf-8')
        return ('scoped_editor', {'command': 'view', 'path': str(workspace / ALLOWED[0])})
    if index == 2:
        assert 'scoped_tests' in names, names
        return ('scoped_tests', {'reason': 'gate: verify the corrected revision'})
    if 'finish' in names:
        return ('finish', {'message': 'gate branch complete'})
    assert 'scoped_tests' in names, names
    return ('scoped_tests', {'reason': 'gate: budget phase requires public verification'})


def branch_driver_noop(llm, index, names):
    """Branch: do nothing to the code, just close out (a branch that cannot fix it)."""
    workspace = Path(llm._gate_workspace)
    (workspace / ALLOWED[0]).write_text(BROKEN_SOURCE, encoding='utf-8')
    if 'scoped_editor' in names:
        return ('scoped_editor', {'command': 'view', 'path': str(workspace / ALLOWED[0]),
                                  'view_range': [1, 2 + index]})
    if 'finish' in names:
        return ('finish', {'message': 'gate branch: no change landed'})
    assert 'scoped_tests' in names, names
    return ('scoped_tests', {'reason': 'gate: public regression check'})


def reset_script_state(workspace: Path) -> None:
    _SEEDED.pop(str(workspace), None)


# --------------------------------------------------------------------------
# engine harness
# --------------------------------------------------------------------------

class Harness:
    """Drive the real v55 engine on a synthetic workspace with scripted responses.

    The overrides keep the request budgets small (a scripted driver is not a
    language model and would otherwise spend the whole shared ceiling) and put the
    plugin's trigger, target and hard budgets inside the synthetic history's
    reach, so the *relations* the gate checks are the protocol's: work requests
    end the first phase, the correction reserve is what a branch inherits, the cap
    is shared, and compaction must actually happen after the fork.  The frozen
    production values (28,000 / 22,400 / 39,200 and the 2,000-token prediction
    margin) are asserted separately by the protocol test.
    """

    OVERRIDES = {'trigger_input_tokens': 200, 'pruner_target_tokens': 80,
                 'pruner_hard_tokens': 200000, 'native_max_tokens': 200000,
                 'max_total_estimated_input_per_sample': 20000000,
                 'trigger_prediction_margin_tokens': 0,
                 'trigger_prediction_ledger_margin_tokens': 0,
                 'phase1_work_request_ladder': [6, 7, 8],
                 'phase1_request_ceiling_per_rung': [8, 9, 10],
                 'initial_work_requests': 6, 'first_phase_max_requests': 8,
                 # The v59 arithmetic requires
                 # branch_request_reserve >= host_feedback_correction_reserve and
                 # prefix_budget_cap == ceiling - reserve x arms; the synthetic values
                 # keep those relations (9 x 3 = 27, 13 + 27 = 40).  The production
                 # values (14 / 10 / 42 / 84) are asserted by the protocol test.
                 'host_feedback_correction_reserve': 8,
                 'max_agent_calls_per_sample': 40,
                 'prefix_budget_cap': 13,
                 'minimum_prefix_requests_to_trigger': 3,
                 'prefix_host_feedback_rounds': 2,
                 'prefix_correction_call_limit': 6,
                 'branch_request_reserve': 9,
                 'tasks': [TASK], 'task_order': [TASK], 'check_order': [TASK]}

    def __init__(self, tmp_path: Path, protocol_overrides=None, predictor=None):
        self.tmp = Path(tmp_path)
        self.out = self.tmp / 'batch'
        self.workspace = build_workspace(self.tmp / 'workspace')
        self.out.mkdir(parents=True, exist_ok=True)
        self.protocol = dict(runner.PROTOCOL)
        self.protocol.update(self.OVERRIDES)
        self.protocol.update(protocol_overrides or {})
        self._legit = patch.object(runner, 'PROTOCOL', self.protocol)
        self._patch = patch('verification_tool_django_v49.evaluate', fake_evaluate)
        self.predictor = predictor

    def __enter__(self):
        self._legit.start()
        self._patch.start()
        self.config = runner.EngineConfig(
            out=self.out, task=TASK, stage='run', api_key='gate-key',
            workspace=self.workspace, prepare_workspace=False,
            evaluator=fake_evaluate, snapshot=self.out / 'prefix-snapshot',
            plan=dict(self.protocol), predictor=self.predictor)
        self.engine = runner.Engine(self.config)
        self.engine.plan = dict(self.protocol)
        self.engine.experiment_root = self.tmp
        original = self.engine.make_llm

        def make_llm(kind, sample_name, ledger, policy):
            llm = original(kind, sample_name, ledger, policy)
            object.__setattr__(llm, '_gate_workspace', self.engine.workspace)
            return llm

        self.engine.make_llm = make_llm
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        self._legit.stop()
        from integrations.openhands.fork_tools_v54 import unbind_tool_context
        unbind_tool_context()
        reset_script_state(self.workspace)
        marker = self.workspace / ALLOWED[0]
        if marker.is_file():
            marker.write_text(BROKEN_SOURCE, encoding='utf-8')

    def run_select(self):
        with patch('openhands.sdk.agent.base.has_vision_profile_available', lambda: False), \
             patch('openhands.sdk.LLM.completion', scripted_provider(prefix_driver_broken)):
            return self.engine.run_select()

    def run_branches(self, branch_driver=branch_driver_edits):
        freeze = runner.load(self.out / 'prefix-freeze.json')
        frozen = runner.load(self.out / 'frozen-hashes.json')
        # The synthesiser's per-request input estimates are far larger than the
        # real task's; pin the carried input as the v54 gate does (the carried
        # *request* count, which is what the budget shares, is untouched).
        freeze['estimated_input'] = 1000
        self.engine.ledger['estimated_input'] = 1000
        reports = []
        with patch('openhands.sdk.agent.base.has_vision_profile_available', lambda: False), \
             patch('openhands.sdk.LLM.completion', scripted_provider(branch_driver)):
            for position, arm in enumerate(self.engine.branch_order):
                guard = importlib.import_module('edit_retry_guard_v33').EditRetryGuard()
                self.engine.tool_ctx['edit_guard'] = guard
                self.engine.bind_tools(guard)
                reports.append(self.engine.run_branch(arm, freeze, frozen, position))
                assert not reports[-1].get('error_type'), \
                    (arm, reports[-1].get('diagnostic', '')[-800:])
        self.engine.write_batch_index()
        return freeze, reports


@pytest.fixture(scope='module')
def flow(tmp_path_factory):
    """One full synthetic v55 flow, reused by the artifact and reconstruction gates."""
    harness = Harness(tmp_path_factory.mktemp('v55flow'))
    with harness:
        record = harness.run_select()
        assert record['selected'] is True, record['decision']
        freeze, reports = harness.run_branches()
    return harness, record, freeze, reports


# --------------------------------------------------------------------------
# 1. frozen protocol / mechanism thresholds
# --------------------------------------------------------------------------

def test_frozen_v59_protocol_document_matches_the_runner():
    document = (ROOT / 'integrations' / 'openhands' / 'PILOT_PROTOCOL_V59.md').read_text(
        encoding='utf-8')
    assert runner.FROZEN_TASKS == (
        'django_referenced_window_wrapping',
        'django_lookup_allowed_foreign_primary',
        'django_list_editable_atomicity')
    assert runner.FROZEN_ARM == 'none'
    for token in ('django_referenced_window_wrapping', 'django_lookup_allowed_foreign_primary',
                  'django_list_editable_atomicity', 'run_validation_v59.py',
                  'audit_validation_v59.py', 'prefix_selection_v55.py',
                  'branch_artifacts_v55.py', 'django__django-17084', 'django__django-16661',
                  'django__django-16100'):
        assert token in document, token
    assert ' \u2192 '.join(runner.DEFAULT_BRANCH_ORDER) in document
    # The mechanism thresholds are the frozen v53/v54 values: v55 changes the
    # prefix selection and the persistence, never the compression mechanism.
    for field, value in (('trigger_input_tokens', 28000),
                         ('pruner_target_tokens', 22400),
                         ('pruner_hard_tokens', 39200),
                         ('native_max_tokens', 33600),
                         ('max_agent_calls_per_sample', 84),
                         ('initial_work_requests', 24),
                         ('first_phase_max_requests', 26),
                         ('host_feedback_correction_reserve', 10),
                         ('closing_request_reserve', 2),
                         ('trigger_prediction_margin_tokens', 0),
                         ('trigger_prediction_ledger_margin_tokens', 2000),
                         ('temperature', 0),
                         ('max_single_estimated_input', 80000),
                         ('max_total_estimated_input_per_sample', 4670000),
                         ('prefix_budget_cap', 42),
                         ('branch_request_reserve', 14),
                         ('minimum_prefix_requests_to_trigger', 30)):
        assert runner.PROTOCOL[field] == value, (field, runner.PROTOCOL[field])
    assert runner.PROTOCOL['prefix_host_feedback_rounds'] == 3
    # batch 03 keeps the pre-registered cap arithmetic > prefix_correction_call_limit so
    # the prefix can actually reach its budget cap (batch 02 was bound at 22 = 6 + 16)
    assert runner.PROTOCOL['prefix_correction_call_limit'] == 36
    assert runner.PROTOCOL['prefix_correction_call_limit'] <= \
        runner.PROTOCOL['prefix_budget_cap']
    assert runner.PROTOCOL['phase1_work_request_ladder'] == [24, 26, 28]
    assert runner.PROTOCOL['phase1_request_ceiling_per_rung'] == [26, 28, 30]
    assert runner.PROTOCOL['tools'] == ['scoped_editor', 'scoped_symbols',
                                        'scoped_tests', 'finish']
    assert runner.PROTOCOL['arms'] == list(runner.ARMS)
    assert runner.PROTOCOL['branch_order'] == list(runner.DEFAULT_BRANCH_ORDER)
    assert 'shell' not in ' '.join(runner.PROTOCOL['tools'])
    assert runner.PROTOCOL['host_feedback_rounds_per_branch'] == 2
    # no rung may exceed the shared sample ceiling
    for cap in runner.PROTOCOL['phase1_request_ceiling_per_rung']:
        assert cap <= runner.PROTOCOL['max_agent_calls_per_sample']
    # the protocol document must state the frozen margins and the rule
    assert '2,000' in document or '2000' in document
    assert '28000' in document or '28,000' in document
    # v59: the pre-registered cap arithmetic is stated in the document, not only in code
    for token in ('84', '42', '14', '28,000', '22,400', '39,200', '33,600',
                  'branch_host_verdicts_measured', 'frozen_experiment_sources_valid',
                  'accept-post-gate-tooling'):
        assert token in document, token


# --------------------------------------------------------------------------
# 1b. v59 cap arithmetic and the branch-reserve guarantee
# --------------------------------------------------------------------------

def test_v59_cap_arithmetic_gives_every_branch_a_verdict():
    """The whole v59 protocol is this identity; nothing else may carry the guarantee."""
    protocol = dict(runner.PROTOCOL)
    arms = len(protocol['arms'])
    ceiling = protocol['max_agent_calls_per_sample']
    reserve = protocol['branch_request_reserve']
    cap = protocol['prefix_budget_cap']
    window = protocol['host_feedback_correction_reserve']
    assert cap + reserve * arms == ceiling, (cap, reserve, arms, ceiling)
    assert cap == ceiling - reserve * arms
    # the prefix may reach its budget cap and still leave every branch its reserve
    assert ceiling - cap == reserve * arms
    # a branch's own correction window is what has to fit inside the reserve
    assert reserve >= window, (reserve, window)
    # and the measured prefix length that crosses the frozen 28,000-token trigger has
    # to fit inside the prefix budget cap, or the round is pre-registered to fail
    assert protocol['minimum_prefix_requests_to_trigger'] <= cap
    # the input ledger was scaled by the same factor as the request ceiling, so the
    # extra requests are not silently blocked by the input cap instead
    assert protocol['max_total_estimated_input_per_sample'] == pytest.approx(
        round(2000000 * ceiling / 36), rel=0.01)
    # the identity is what the engine recomputes at run time
    engine = runner.Engine(runner.EngineConfig(out=Path('.'), plan=protocol))
    engine.plan = dict(protocol)
    row = engine.prefix_arithmetic()
    assert row['identity_holds'] is True and row['reserve_identity_holds'] is True
    assert row['reserve_covers_branch_window'] is True
    assert row['branch_verdict_reachable_by_arithmetic'] is True
    assert row['branch_total'] == 42
    # the reserve guarantee, as the runner checks it at the fork
    assert engine.ensure_prefix_reserve(0)['branch_requests_each'] == 28
    assert engine.ensure_prefix_reserve(cap)['branch_requests_each'] == reserve


def test_v59_engine_refuses_a_prefix_that_would_starve_the_branches():
    """A prefix past its budget cap or into the reserve raises; it is never narrowed."""
    protocol = dict(runner.PROTOCOL)

    def engine_for(plan: dict) -> 'runner.Engine':
        engine = runner.Engine(runner.EngineConfig(out=Path('.'), plan=plan))
        engine.plan = dict(plan)
        return engine

    engine = engine_for(protocol)
    assert engine.ensure_prefix_reserve(42)['reserve_satisfied'] is True
    assert engine.ensure_prefix_reserve(42)['branch_requests_each'] == 14
    assert engine.ensure_prefix_reserve(30)['branch_requests_each'] == 18
    with pytest.raises(RuntimeError, match='pre-registered branch reserve'):
        engine.ensure_prefix_reserve(43)
    with pytest.raises(RuntimeError, match='pre-registered branch reserve'):
        engine.ensure_prefix_reserve(84)
    # an arithmetic that does not add up is a protocol error, not a smaller reserve
    with pytest.raises(RuntimeError, match='cap arithmetic does not hold'):
        engine_for(dict(protocol, prefix_budget_cap=41)).ensure_prefix_reserve(0)
    # a reserve smaller than the branch correction window cannot promise a verdict
    thin = dict(protocol, branch_request_reserve=8, prefix_budget_cap=60)
    assert thin['prefix_budget_cap'] + thin['branch_request_reserve'] * 3 == \
        thin['max_agent_calls_per_sample']
    with pytest.raises(RuntimeError, match='smaller than'):
        engine_for(thin).ensure_prefix_reserve(0)


def test_v59_ledger_records_a_budget_cap_breach(tmp_path):
    """The live guard raises when the prefix has spent past its pre-registered cap."""
    protocol = dict(runner.PROTOCOL)
    engine = runner.Engine(runner.EngineConfig(out=tmp_path, plan=protocol))
    engine.plan = dict(protocol)
    engine.ledger = {'calls': [{'kind': 'agent', 'status': 'returned'}] * 43,
                     'estimated_input': 0}
    with pytest.raises(RuntimeError, match='past its pre-registered budget cap'):
        engine.prefix_reserve_guard()
    engine.ledger = {'calls': [{'kind': 'agent', 'status': 'returned'}] * 42,
                     'estimated_input': 0}
    guard = engine.prefix_reserve_guard()
    assert guard['budget_exhausted'] is True
    assert guard['reserve_satisfied'] is True
    assert guard['remaining_after_prefix'] == 42
    assert guard['branch_requests_each'] == 14


def test_v59_selection_rule_is_byte_identical_to_v58():
    """The ladder, the trigger gate and the margins are carried over unchanged."""
    from integrations.openhands import run_validation_v58 as v58
    protocol = dict(runner.PROTOCOL)
    assert runner.rule_fingerprint(protocol) == v58.rule_fingerprint(protocol)
    assert runner.SELECTION_RULE == v58.SELECTION_RULE
    assert runner.PROTOCOL['phase1_work_request_ladder'] == \
        v58.PROTOCOL['phase1_work_request_ladder']
    for field in ('trigger_input_tokens', 'pruner_target_tokens', 'pruner_hard_tokens',
                  'native_max_tokens', 'trigger_prediction_margin_tokens',
                  'trigger_prediction_ledger_margin_tokens', 'host_feedback_correction_reserve',
                  'closing_request_reserve', 'max_single_estimated_input',
                  'max_output_tokens', 'max_summary_calls_per_sample', 'prefix_arm',
                  'prefix_condenser', 'branch_condensers', 'tools', 'model', 'temperature'):
        assert runner.PROTOCOL[field] == v58.PROTOCOL[field], field
    changed = {key for key in set(runner.PROTOCOL) | set(v58.PROTOCOL)
               if runner.PROTOCOL.get(key) != v58.PROTOCOL.get(key)}
    # exactly the v59 cap arithmetic, the derived ceilings, the batch-03 correction
    # window and the new bookkeeping
    assert changed == {
        'version', 'max_agent_calls_per_sample', 'branch_request_reserve',
        'prefix_budget_cap', 'minimum_prefix_requests_to_trigger',
        'minimum_prefix_requests_note', 'branch_reserve_arithmetic',
        'prefix_correction_call_limit',
        'max_total_estimated_input_per_sample', 'v58_carryover_marker', 'v59_changes',
        'v57_carryover_marker', 'v58_changes'}, sorted(changed)


def test_v59_audit_freezes_itself_before_the_run():
    """The auditor is a frozen source and it refuses the post-gate escape hatch."""
    from integrations.openhands import audit_validation_v59 as audit
    source = (ROOT / 'integrations' / 'openhands' / 'audit_validation_v59.py').read_text(
        encoding='utf-8')
    assert 'assert not args.accept_post_gate_tooling' in source
    frozen = [str(path.relative_to(ROOT)).replace('\\', '/') for path in runner.source_paths()]
    assert 'integrations/openhands/audit_validation_v59.py' in frozen, \
        'the runner does not freeze the auditor in its manifest sources'
    assert 'tests/test_openhands_v59_formal_runner_gate.py' in frozen
    assert audit.POST_GATE_TOOLING == (
        'integrations/openhands/run_validation_v59.py',
        'integrations/openhands/run_validation_windows_v59.py',
        'integrations/openhands/audit_validation_v59.py',
        'integrations/openhands/fork_tools_v54.py',
        'integrations/openhands/budget_policy_v54.py',
        'integrations/openhands/branch_artifacts_v55.py',
        'integrations/openhands/prefix_selection_v55.py',
        'tests/test_openhands_v59_formal_runner_gate.py')


# --------------------------------------------------------------------------
# 2/3. the pre-registered ladder and the decision rule
# --------------------------------------------------------------------------

def test_pre_registered_attempt_plan_and_decision_rule():
    protocol = dict(runner.PROTOCOL)
    plan = selection.attempt_plan(protocol)
    expected = [(0, 24, 'django_referenced_window_wrapping'),
                (0, 24, 'django_lookup_allowed_foreign_primary'),
                (0, 24, 'django_list_editable_atomicity'),
                (1, 26, 'django_referenced_window_wrapping'),
                (1, 26, 'django_lookup_allowed_foreign_primary'),
                (1, 26, 'django_list_editable_atomicity'),
                (2, 28, 'django_referenced_window_wrapping'),
                (2, 28, 'django_lookup_allowed_foreign_primary'),
                (2, 28, 'django_list_editable_atomicity')]
    assert plan == expected
    # rung-major: every frozen task is attempted at the first rung before the
    # phase-1 budget is extended, which is the pre-registered rule.
    assert [rung for rung, _cap, _task in plan] == [0, 0, 0, 1, 1, 1, 2, 2, 2]

    # a task that already passed the host test is not re-attempted at a later rung
    attempts = [{'task': expected[0][2], 'rung': 0, 'work_cap': 24, 'host_passed': True,
                 'trigger_prediction': {'triggered': False}},
                {'task': expected[1][2], 'rung': 0, 'work_cap': 24, 'host_passed': True,
                 'trigger_prediction': {'triggered': False}}]
    assert selection.next_step(protocol, attempts) == (0, 24, expected[2][2])
    attempts.append({'task': expected[2][2], 'rung': 0, 'work_cap': 24, 'host_passed': False,
                     'trigger_prediction': {'triggered': False}})
    # two tried at rung 0 and all passed/rejected: only the failed one is retried
    assert selection.next_step(protocol, attempts) == (1, 26, expected[2][2])
    attempts.append({'task': expected[2][2], 'rung': 1, 'work_cap': 26, 'host_passed': False,
                     'trigger_prediction': {'triggered': False}})
    # the third rung is the last one: a short attempt on a failing task is retried
    # inside the frozen ceiling, and the ladder then ends
    assert selection.next_step(protocol, attempts) == (2, 28, expected[2][2])
    attempts.append({'task': expected[2][2], 'rung': 2, 'work_cap': 28, 'host_passed': False,
                     'trigger_prediction': {'triggered': False}})
    assert selection.next_step(protocol, attempts) is None


def test_selection_rejects_host_passing_and_non_triggering_attempts():
    protocol = dict(runner.PROTOCOL)
    passing = {'task': 'django_referenced_window_wrapping', 'rung': 0, 'work_cap': 24,
               'host_passed': True, 'trigger_prediction': {'triggered': True}}
    not_triggering = {'task': 'django_lookup_allowed_foreign_primary', 'rung': 0, 'work_cap': 24,
                      'host_passed': False, 'trigger_prediction': {'triggered': False}}
    qualifying = {'task': 'django_list_editable_atomicity', 'rung': 0, 'work_cap': 24,
                  'host_passed': False, 'trigger_prediction': {'triggered': True}}
    decision = selection.selection_decision(protocol, [passing, not_triggering, qualifying])
    assert decision['selected'] is True
    assert decision['task'] == 'django_list_editable_atomicity'
    assert decision['rung'] == 0
    rejections = {row['task']: row.get('rejection') for row in decision['considered']}
    assert 'passed' in rejections['django_referenced_window_wrapping']
    assert 'NOT to trigger' in rejections['django_lookup_allowed_foreign_primary']
    # order of the *record* must not change the decision: the frozen order wins
    shuffled = selection.selection_decision(protocol, [qualifying, not_triggering, passing])
    assert shuffled['task'] == decision['task']
    # nothing qualifies -> the round must report "mechanism still not triggered"
    empty = selection.selection_decision(protocol, [passing, not_triggering])
    assert empty['selected'] is False
    assert 'mechanism still not triggered' in empty['reason']
    assert empty['task'] is None


# --------------------------------------------------------------------------
# 4. the engine refuses to fork without a triggering prediction
# --------------------------------------------------------------------------

def injected_no_trigger(**kwargs):
    ledger_input = kwargs.get('ledger_last_estimated_input')
    return {'trigger_input_tokens': kwargs['protocol']['trigger_input_tokens'],
            'view_event_count': len(kwargs['events']) + 1,
            'measured_view_tokens': 10,
            'measured_margin_tokens': 10 - kwargs['protocol']['trigger_input_tokens'],
            'required_measured_margin_tokens':
                kwargs['protocol']['trigger_prediction_margin_tokens'],
            'measured_ok': False,
            'ledger_last_estimated_input': ledger_input,
            'required_ledger_margin_tokens':
                kwargs['protocol']['trigger_prediction_ledger_margin_tokens'],
            'ledger_ok': False,
            'condenser_replay_result': 'View',
            'condenser_replay_audit': [],
            'triggered': False, 'reasons': ['gate: injected non-triggering prediction'],
            'method': 'gate injection'}


def test_select_stage_refuses_to_fork_when_prediction_does_not_trigger(tmp_path):
    with Harness(tmp_path, predictor=injected_no_trigger) as harness:
        record = harness.run_select()
    assert record['selected'] is False
    assert record['decision']['selected'] is False
    assert 'mechanism still not triggered' in record['decision']['reason']
    assert not (harness.out / 'prefix-freeze.json').exists()
    assert not (harness.out / 'frozen-hashes.json').exists()
    assert not (harness.out / 'prefix-snapshot').exists()
    assert not list(harness.out.glob('branch-*')), 'a branch directory was created anyway'
    # the whole frozen ladder was walked, and every attempt failed the host test
    assert len(record['attempts']) == 3
    assert all(row['host_passed'] is False for row in record['attempts'])
    assert all((row['trigger_prediction'] or {}).get('triggered') is False
               for row in record['attempts'])
    # the recorded selection file is the honest one, with the frozen fingerprint
    saved = runner.load(harness.out / 'select.json')
    assert saved['complete'] is False
    assert saved['rule_fingerprint'] == runner.rule_fingerprint(harness.protocol)
    assert 'rung 0 (work cap 24), then rung 1 (work cap 26), then rung 2 (work cap 28)' in \
        saved['rule']


# --------------------------------------------------------------------------
# 5. the prediction, checked against the frozen v54 batch
# --------------------------------------------------------------------------

def test_trigger_prediction_reproduces_the_v54_no_compaction_outcome():
    """The v54 prefix measured 22,213 condenser-view tokens against a 28,000 trigger.

    Adding the frozen correction user message - the state the branch's first step
    actually sees - gives 22,303.  v54 instead compared the ledger's *estimated
    input* (28,275) with the trigger and concluded it was above it; the condenser
    counts SDK view tokens, so a ~6,000-token gap made the plugin arm condense
    zero times.  This test replays the frozen v54 prefix through the same
    prediction the v55 gate uses and requires the gate to say "not triggered" -
    which is what actually happened - so v55 cannot repeat v54's mistake.
    """
    freeze_path = V54_BATCH / 'prefix-freeze.json'
    if not freeze_path.is_file():
        pytest.skip('frozen v54 batch is not present in this checkout')
    from openhands.sdk.event import Event
    from integrations.openhands.fork_tools_v54 import register_v54_tools
    register_v54_tools()
    freeze = runner.load(freeze_path)
    persistence = V54_BATCH / 'sdk-persistence' / 'prefix'
    events = None
    for candidate in sorted(path for path in persistence.iterdir() if path.is_dir()):
        files = sorted((candidate / 'events').glob('event-*.json'),
                       key=lambda p: int(p.name.split('-')[1]))
        loaded = [Event.model_validate_json(path.read_text(encoding='utf-8'))
                  for path in files]
        if runner.digest_ids([str(event.id) for event in loaded]) == freeze['event_id_digest']:
            events = loaded
            break
    assert events is not None, 'no persisted conversation matches the frozen v54 prefix'
    import run_validation_v54 as v54_runner
    prediction = selection.predict_trigger(
        events=events, protocol=runner.PROTOCOL, task=freeze['task'],
        allowed=list(runner.TASKS[freeze['task']]['allowed']),
        correction_text=v54_runner.correction_prompt(freeze['task'],
                                                     freeze['correction_feedback']),
        condenser_factory=lambda: __import__(
            'context_pruner.adapters.openhands_v51', fromlist=['ContextPrunerCondenserV51']
        ).ContextPrunerCondenserV51(
            trigger_tokens=28000, target_tokens=22400, hard_tokens=39200,
            protected_contract='Edit only: ' + ', '.join(
                runner.TASKS[freeze['task']]['allowed']) +
                '. All other source files are read-only.'),
        ledger_last_estimated_input=28275)
    assert prediction['triggered'] is False
    assert prediction['condenser_replay_result'] == 'View', prediction
    assert prediction['measured_view_tokens'] < prediction['trigger_input_tokens']
    assert prediction['measured_view_tokens'] == 22303, (
        'the frozen v54 prefix\'s condenser-view token count changed; the recorded value is '
        '22,303 (prefix events plus the frozen correction prompt; 22,213 for the prefix alone)',
        prediction['measured_view_tokens'])
    # v54 recorded zero condensations for the plugin arm: the gate agrees with
    # the observation rather than with the ledger arithmetic.
    v54_comparison = runner.load(V54_BATCH / 'comparison.json')
    assert v54_comparison['mechanism_triggered_plugin'] is False
    assert v54_comparison['branches']['pruner_v1']['condensation_events'] == 0


# --------------------------------------------------------------------------
# 6. the full flow and per-branch persistence
# --------------------------------------------------------------------------

def test_three_sequential_branches_share_one_prefix_and_persist_their_artifacts(flow):
    harness, record, freeze, reports = flow
    assert record['selected'] is True
    assert freeze['prefix_needs_correction'] is True
    assert freeze['host_test_passed'] is False
    assert freeze['task'] == TASK and freeze['rung'] == 0
    assert freeze['trigger_prediction']['triggered'] is True
    assert freeze['trigger_prediction']['condenser_replay_result'] == 'Condensation'
    assert (harness.out / 'prefix-snapshot').is_dir()
    assert freeze['event_count'] > 2

    assert [report['arm'] for report in reports] == list(runner.ARMS)
    arms = {report['arm']: report for report in reports}
    index = runner.load(harness.out / 'branch-artifact-index.json')
    assert set(index['branches']) == {report['sample'] for report in reports}
    for report in reports:
        sample_dir = harness.out / report['sample']
        # every branch directory carries its own bytes, hashes, events, ledger,
        # report and artifact index
        for name in ('final-files', 'final-hashes.json', 'events.json', 'ledger.json',
                     'report.json', 'artifacts.json'):
            assert (sample_dir / name).exists(), (report['sample'], name)
        assert report['restore_hash_equal'] is True
        assert report['ledger']['carried_over_agent_requests'] == freeze['agent_requests']
        assert report['ledger']['carried_over_estimated_input'] == 1000
        assert report['prefix_event_count'] == freeze['event_count']
        assert report['source_prefix_unchanged'] is True
        assert report['branch_events_persisted_match_observation'] is True
        assert report['trigger_prediction_matches_freeze'] is True
        assert report['file_boundary_ok'] is True
        assert report['post_fork_agent_requests'] >= 1
        assert report['post_fork_agent_requests'] + freeze['agent_requests'] <= \
            harness.protocol['max_agent_calls_per_sample']
        assert report['branch_artifacts'] == runner.load(sample_dir / 'artifacts.json')
        # v59: the branch's own measured host verdict is recorded explicitly, so a
        # branch that never reached a host round can never be read as "failed"
        assert report['host_rounds_completed'] >= 1, report['sample']
        assert report['host_verdict_measured'] is True
        assert report['final_host_verdict'] in ('passed', 'failed')
        assert report['final_host_verdict'] == (
            'passed' if report['final_host_passed'] else 'failed')
        assert report['host_rounds_completed'] == len(report['host_rounds'])
        # the persisted bytes reproduce the recorded final map exactly
        verified = verify_branch_artifacts(
            sample_dir, runner.load(harness.out / 'frozen-source-hashes.json'),
            allowed=ALLOWED, index_entry=index['branches'][report['sample']], report=report)
        assert verified['record']['final_source_digest'] == report['final_source_digest']
    # v59: the pre-registered branch reserve was intact at the fork and every branch
    # landed at least one edit
    assert freeze['prefix_reserve_at_fork']['reserve_satisfied'] is True
    assert freeze['prefix_reserve_at_fork']['branch_requests_each'] >= \
        harness.protocol['host_feedback_correction_reserve']
    assert all(report['changed_files'] for report in reports), \
        [(report['sample'], report['changed_files']) for report in reports]
    # the plugin arm condensed after the fork; the other two did not
    assert arms['pruner_v1']['condensation_events'] >= 1
    assert arms['pruner_v1']['mechanism_triggered'] is True
    assert arms['none']['condensation_events'] == 0
    ids = [set(report['branch_event_ids']) for report in reports]
    assert not ids[0] & ids[1] and not ids[1] & ids[2] and not ids[0] & ids[2]
    for key in ('prepare_copy_hash_seconds', 'prefix_model_wait_seconds',
                'trigger_prediction_seconds', 'restore_workspace_seconds',
                'restore_hash_verify_seconds', 'branch_model_wait_seconds',
                'host_test_seconds', 'branch_persist_seconds'):
        assert key in harness.engine.timing, (key, sorted(harness.engine.timing))


# --------------------------------------------------------------------------
# 7. tamper rejection
# --------------------------------------------------------------------------

def test_tampered_branch_artifacts_are_rejected(flow, tmp_path):
    harness, _record, _freeze, reports = flow
    frozen_source = runner.load(harness.out / 'frozen-source-hashes.json')
    index = runner.load(harness.out / 'branch-artifact-index.json')
    source = harness.out / reports[2]['sample']
    assert source.is_dir()
    import shutil

    def copy_branch(name: str) -> Path:
        target = tmp_path / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        return target

    # a) a persisted byte is changed
    tampered = copy_branch('tampered-bytes')
    allowed_file = tampered / 'final-files' / ALLOWED[0]
    original = allowed_file.read_bytes()
    allowed_file.write_bytes(original + b'# tampered\n')
    with pytest.raises(BranchArtifactError, match='persisted file changed'):
        verify_branch_artifacts(tampered, frozen_source, allowed=ALLOWED,
                                index_entry=index['branches'][reports[2]['sample']])
    allowed_file.write_bytes(original)

    # b) the artifact index itself is edited to match the tampered byte
    tampered = copy_branch('tampered-index')
    allowed_file = tampered / 'final-files' / ALLOWED[0]
    allowed_file.write_bytes(allowed_file.read_bytes() + b'# tampered\n')
    record = runner.load(tampered / 'artifacts.json')
    record['final_files'][ALLOWED[0]] = sha256_file(allowed_file)
    artifacts.save(tampered / 'artifacts.json', record)
    with pytest.raises(BranchArtifactError, match='artifact file changed'):
        verify_branch_artifacts(tampered, frozen_source, allowed=ALLOWED,
                                index_entry=index['branches'][reports[2]['sample']])

    # c) the final hash map is edited
    tampered = copy_branch('tampered-hashes')
    final = runner.load(tampered / 'final-hashes.json')
    final[ALLOWED[0]] = '0' * 64
    artifacts.save(tampered / 'final-hashes.json', final)
    with pytest.raises(BranchArtifactError):
        verify_branch_artifacts(tampered, frozen_source, allowed=ALLOWED,
                                index_entry=index['branches'][reports[2]['sample']])

    # d) an artifact file pinned by the batch index is removed
    tampered = copy_branch('removed-artifact')
    (tampered / 'events.json').unlink()
    with pytest.raises(BranchArtifactError, match='artifact file removed'):
        verify_branch_artifacts(tampered, frozen_source, allowed=ALLOWED,
                                index_entry=index['branches'][reports[2]['sample']])

    # e) a file the index records as deleted is restored
    tampered = copy_branch('added-file')
    record = runner.load(tampered / 'artifacts.json')
    record['final_files']['django/db/models/sql/extra.py'] = None
    artifacts.save(tampered / 'artifacts.json', record)
    (tampered / 'final-files' / 'django/db/models/sql/extra.py').write_text(
        'tamper\n', encoding='utf-8')
    with pytest.raises(BranchArtifactError, match='recorded as deleted'):
        verify_branch_artifacts(tampered, frozen_source, allowed=ALLOWED)

    # f) the batch index must cover every branch and every pinned file
    tampered_out = tmp_path / 'batch-with-missing-branch'
    if tampered_out.exists():
        shutil.rmtree(tampered_out)
    shutil.copytree(harness.out, tampered_out,
                    ignore=shutil.ignore_patterns('workspaces', 'prefix-snapshot'))
    shutil.rmtree(tampered_out / reports[0]['sample'])
    with pytest.raises(BranchArtifactError):
        verify_batch_index(tampered_out,
                           [tampered_out / report['sample'] for report in reports],
                           frozen_source)


# --------------------------------------------------------------------------
# 8. per-branch independent reconstruction and host re-run
# --------------------------------------------------------------------------

def test_audit_rebuilds_and_rescores_every_branch_from_its_own_bytes(flow, tmp_path):
    harness, _record, freeze, reports = flow
    frozen_tree = runner.load(harness.out / 'frozen-hashes.json')
    frozen_source = runner.load(harness.out / 'frozen-source-hashes.json')
    index = runner.load(harness.out / 'branch-artifact-index.json')
    snapshot = Path(freeze['snapshot'])
    # An experiment root that contains both the snapshot and the rebuild targets,
    # which is the same containment rule the runner and the audit use.
    experiment_root = tmp_path.parent
    changed_arms = {report['arm'] for report in reports if report['changed_files']}
    assert 'pruner_v1' in changed_arms, 'the plugin branch changed nothing; nothing to verify'
    for report in reports:
        sample_dir = harness.out / report['sample']
        target = tmp_path / f'rebuild-{report["arm"]}'
        if target.exists():
            import shutil
            shutil.rmtree(target)
        verified = reconstruct_branch_workspace(
            target, snapshot, frozen_tree=frozen_tree, frozen_source=frozen_source,
            artifacts_dir=sample_dir, experiment_root=experiment_root, allowed=ALLOWED,
            index_entry=index['branches'][report['sample']], report=report)
        assert verified['record']['changed_files'] == sorted(report['changed_files'])
        # the rebuilt workspace is this branch's own final state, not the last
        # branch's and not the frozen prefix's
        rebuilt = (target / ALLOWED[0]).read_text(encoding='utf-8')
        expected = (sample_dir / 'final-files' / ALLOWED[0]).read_text(encoding='utf-8')
        assert rebuilt == expected
        if report['arm'] == 'pruner_v1':
            assert FLAG in rebuilt
        # the host target test re-run on the rebuilt bytes agrees with the record
        destination = tmp_path / f'host-{report["arm"]}'
        result = fake_evaluate(target, TASK, destination)
        recorded = (report.get('host_rounds') or [{}])[-1].get('host', {})
        assert result['passed'] == recorded.get('passed')
        assert result['passed'] == report['final_host_passed']
    # reconstructing a *different* branch's bytes cannot pass as this one
    with pytest.raises(BranchArtifactError):
        reconstruct_branch_workspace(
            tmp_path / 'mismatch', snapshot, frozen_tree=frozen_tree,
            frozen_source=frozen_source, artifacts_dir=harness.out / reports[0]['sample'],
            experiment_root=experiment_root, allowed=ALLOWED,
            index_entry=index['branches'][reports[2]['sample']], report=reports[2])


# --------------------------------------------------------------------------
# 9. carried requests / no fresh allowance
# --------------------------------------------------------------------------

def test_branch_ledgers_continue_the_prefix_without_a_fresh_allowance(flow):
    harness, _record, freeze, reports = flow
    from integrations.openhands.same_prefix_fork_v54 import branch_ledger_from_prefix
    assert freeze['agent_requests'] >= 1
    for report in reports:
        ledger = runner.load(Path(harness.out / report['sample'] / 'ledger.json'))
        assert ledger['shared_prefix_call_count'] == freeze['agent_requests']
        assert ledger['shared_prefix_agent_requests'] == freeze['agent_requests']
        assert len(ledger['calls']) == report['post_fork_total_requests']
        assert all(call['status'] == 'returned' for call in ledger['calls'])
        # a branch ledger is branch-local: it carries the prefix's consumption,
        # never its rows, and never a fresh allowance
        other = branch_ledger_from_prefix({'calls': [{'kind': 'agent', 'status': 'returned'}],
                                           'estimated_input': 5})
        assert other['calls'] == [] and other['estimated_input'] == 0
        assert other['shared_prefix_call_count'] == 1
        with pytest.raises(ValueError, match='itself'):
            branch_ledger_from_prefix(ledger)
        assert report['budget_state']['correction_stop_at'] == min(
            harness.protocol['max_agent_calls_per_sample'],
            freeze['agent_requests'] + harness.protocol['host_feedback_correction_reserve'])
        assert report['post_fork_agent_requests'] + freeze['agent_requests'] <= \
            harness.protocol['max_agent_calls_per_sample']
    # the shared prefix ledger itself is not a branch ledger
    prefix_ledger = runner.load(harness.out / 'prefix' / 'ledger.json')
    assert 'shared_prefix_call_count' not in prefix_ledger


def test_v59_burst_error_ends_one_attempt_instead_of_the_batch(tmp_path):
    """A rejected provider batch must not abort the pre-registered ladder.

    v59's paid batch 01 hit exactly this: the budget guard rejected a response that
    mixed ``finish`` with a real edit, the exception crossed the runner's
    ``ConversationRunError`` boundary and the whole batch died at attempt 1 with no
    frozen prefix and no branch request.  The pre-registered ladder exists to walk on,
    so the burst is recorded against the attempt and the attempt is never selected.
    """
    with Harness(tmp_path) as harness:
        calls = {'n': 0}
        original = harness.engine.run_attempt

        def exploding(task, rung, work_cap, index):
            call = original(task, rung, work_cap, index)
            if index == 1:
                call['burst_error_type'] = 'ConversationRunError'
                call['burst_diagnostic'] = 'Budget policy blocked an unexpected tool before execution'
                call['host_passed'] = None
                call['needs_correction'] = None
                call['host_evaluation'] = None
                call['final_evaluation_dir'] = None
                call['trigger_prediction'] = {
                    'triggered': False,
                    'skipped': 'this attempt ended in a burst error before a host verdict'}
                runner.save(harness.out / call['attempt_dir'] / 'report.json', call)
            calls['n'] += 1
            return call

        harness.engine.run_attempt = exploding
        record = harness.run_select()
    # the ladder walked on and still selected a clean attempt
    assert record['selected'] is True, record['decision']
    assert calls['n'] >= 2, calls
    first = record['attempts'][0]
    assert first['burst_error_type'] == 'ConversationRunError'
    assert first['host_passed'] is None
    assert first['trigger_prediction']['triggered'] is False
    assert record['decision']['task'] is not None
    # the selection never depends on an attempt that died mid-burst
    assert record['decision']['attempt'].get('burst_error_type') is None


# --------------------------------------------------------------------------
# 10. v59 batch-level verdicts: a measured verdict for every branch, or none
# --------------------------------------------------------------------------

def test_v59_batch_reports_a_measured_host_verdict_for_every_branch(flow):
    harness, _record, freeze, reports = flow
    rows = {report['arm']: report for report in reports}
    block = runner.branch_verdicts(rows, harness.out)
    assert block['branch_host_verdicts_measured'] is True
    assert block['quality_verdict_obtained'] is True
    assert block['branches_measured'] == block['branches_expected'] == len(runner.ARMS)
    assert 'measured per-branch host verdicts' in block['quality_verdict']
    per_branch = block['per_branch']
    assert set(per_branch) == set(runner.ARMS)
    for arm, row in per_branch.items():
        assert row['host_verdict_measured'] is True
        assert row['final_host_verdict'] in ('passed', 'failed')
        assert row['post_fork_agent_requests'] >= 1
        assert row['changed_files'], (arm, row['changed_files'])
        assert row['host_rounds_completed'] >= 1
    # the arithmetic that carries the guarantee travels with the verdict
    assert block['cap_arithmetic']['identity_holds'] is True
    assert block['cap_arithmetic']['prefix_budget_cap'] == harness.protocol['prefix_budget_cap']
    assert block['cap_arithmetic']['sum'] == harness.protocol['max_agent_calls_per_sample']
    assert freeze['prefix_reserve_at_fork']['reserve_satisfied'] is True


def test_v59_batch_reports_no_quality_result_without_measured_verdicts(flow, tmp_path):
    """One unmeasured branch is enough: the batch must claim no quality result."""
    import shutil
    harness, _record, _freeze, _reports = flow
    clone = tmp_path / 'unmeasured'
    if clone.exists():
        shutil.rmtree(clone)
    shutil.copytree(harness.out, clone,
                    ignore=shutil.ignore_patterns('workspaces', 'prefix-snapshot'))
    rows = {report['arm']: report for report in
            (runner.load(path / 'report.json')
             for path in sorted(clone.glob('branch-*/'))
             if (path / 'report.json').is_file())}
    # reproduce the v58 failure mode on one branch: no host round, no measured verdict
    victim = clone / rows['pruner_v1']['sample'] / 'report.json'
    report = runner.load(victim)
    report['host_rounds'] = []
    report['host_rounds_completed'] = 0
    report['host_verdict_measured'] = False
    report['final_host_verdict'] = None
    victim.write_text(json.dumps(report, indent=2), encoding='utf-8')
    block = runner.branch_verdicts(rows, clone)
    assert block['branch_host_verdicts_measured'] is False
    assert block['quality_verdict_obtained'] is False
    assert block['branches_measured'] == len(runner.ARMS) - 1
    assert 'quality verdict not obtained' in block['quality_verdict']
    assert 'no quality result is claimed' in block['quality_verdict']
    assert block['per_branch']['pruner_v1']['host_verdict_measured'] is False
    assert block['per_branch']['pruner_v1']['final_host_verdict'] is None
    assert block['per_branch']['none']['host_verdict_measured'] is True
