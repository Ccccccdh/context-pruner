"""Zero-API gate for the v54 formal same-prefix runner.

Every outstanding check in the "zero-API gates before paying" section of
``PILOT_PROTOCOL_V54.md`` that can be exercised without a provider is
exercised here, on a synthetic Django-shaped workspace, through the SDK's real
conversation, fork, tool-executor and condenser code paths:

1. ``test_frozen_task_allowlist_matches_the_dataset_patch`` - the formal tool
   allowlist and file boundary equal what the dataset's reference patch
   touches, and the read scope stays inside the workspace.
2. ``test_editor_executor_refuses_every_path_outside_the_allowlist`` - the
   boundary is enforced inside the executor, not by agent cooperation.
3. ``test_scoped_tests_tool_runs_the_host_test_and_reports_its_feedback`` -
   the host target test runs inside the fork loop through ``scoped_tests`` and
   its failure text is what the branch receives.
4. ``test_branch_ledger_carries_the_prefix_requests`` - a branch policy starts
   from the prefix's consumed requests and its correction boundary continues
   the prefix instead of reopening the full allowance.
5. ``test_exhausted_prefix_leaves_no_branch_allowance`` - once the shared
   prefix has spent the allowance, no branch may issue a single request.
6. ``test_three_sequential_branches_reuse_one_prefix`` - three sequential
   branches at one absolute path, snapshot-restored and full-hash verified,
   sharing one identical event prefix, with independent ledgers, host feedback
   and timing, and with the plugin arm condensing at least once after the fork.
7. ``test_prefix_stage_freezes_and_replays_the_same_conversation`` - the frozen
   prefix is reproducible from SDK persistence before any fork, and a changed
   workspace aborts before a branch exists.

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

from integrations.openhands import run_validation_v54 as runner  # noqa: E402
from integrations.openhands.budget_policy_v54 import (  # noqa: E402
    PrefixCarryingBudgetPolicy,
)
from integrations.openhands.fork_tools_v54 import (  # noqa: E402
    ScopedEditorExecutorV54,
    bind_tool_context,
    unbind_tool_context,
)

TASK = runner.FROZEN_TASK
FLAG = 'GATE_FLAG'
PUBLIC_MARKER = 'gate_definition'
ALLOWED = runner.EXPECTED_ALLOWED[TASK]
#: The budget policy allow-list, which every registered tool name must match.
EDITOR_TOOL = 'scoped_editor'
FIXED_SOURCE = ('# editable target\n'
                f'def {PUBLIC_MARKER}():\n    return {FLAG!r}\n')
BROKEN_SOURCE = '# editable target\n' + 'def gate_definition():\n    return 1\n'


# --------------------------------------------------------------------------
# synthetic workspace and offline host test
# --------------------------------------------------------------------------

def build_workspace(path: Path, task: str = TASK) -> Path:
    """A Django-shaped tree holding exactly the frozen read/edit sets."""
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
    """Offline stand-in for the host test.

    ``regression_only`` is the pre-existing public module; the default is the
    host's added target test, satisfied only by ``FLAG``.  Both read the editable
    files and nothing else.

    The public module is reported as failing while the flag is absent, which is
    what a real broken revision does: it is the signal the budget policy uses to
    keep working instead of closing.  ``_PASSING_PUBLIC`` flips that for the one
    test that needs a green public verdict.
    """
    workspace, destination = Path(workspace), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source = ''.join((workspace / name).read_text(encoding='utf-8')
                     for name in runner.TASKS[task]['allowed'])
    if regression_only:
        passed = bool(_PASSING_PUBLIC) or (FLAG in source)
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
    """Build an ``LLM.completion`` replacement whose script is a per-LLM callable."""
    def provider(llm, messages, tools, *args, **kwargs):
        index = getattr(llm, '_gate_calls', 0) + 1
        llm._gate_calls = index
        names = sorted(getattr(tool, 'name', str(tool)) for tool in tools)
        name, arguments = driver(llm, index, names)
        last = [getattr(item, 'text', '') for message in messages
                for item in (message.content or [])]
        llm._gate_seen = getattr(llm, '_gate_seen', [])
        llm._gate_seen.append({'index': index, 'tool': name, 'tools_offered': names,
                               'last_prompt': last[-1] if last else ''})
        return _response(llm, index, name, arguments)
    return provider


def branch_driver(llm, index, names):
    """Edit the allowed file, run scoped_tests, then finish."""
    workspace = Path(llm._gate_workspace)
    if index == 1:
        (workspace / ALLOWED[0]).write_text(FIXED_SOURCE, encoding='utf-8')
        return ('scoped_editor', {'command': 'view',
                                      'path': str(workspace / ALLOWED[0])})
    if index == 2:
        assert 'scoped_tests' in names, names
        return ('scoped_tests', {'reason': 'gate: verify the corrected revision'})
    if 'finish' in names:
        return ('finish', {'message': 'gate branch complete'})
    assert 'scoped_tests' in names, names
    return ('scoped_tests', {'reason': 'gate: budget phase requires public verification'})


#: The synthetic prefix/marker conversation rewrites its source until it has
#: landed a verification, then stops; rewriting after a test would keep
#: invalidating the verification the budget policy watches.
_SEEDED: dict[str, bool] = {}
#: Selected tests need the synthetic public module to report success.
_PASSING_PUBLIC = False


def _budget_phase(llm, offered: list[str]) -> str:
    """Which budget phase the offered tool set belongs to.

    The script reads the phase from the offered tools instead of guessing: the
    work phase splits its offer between editing and testing, the verification
    phase offers the host feedback tool alone, and the closing phase offers
    ``finish`` alone.
    """
    offered = set(offered)
    if 'scoped_editor' in offered and 'scoped_tests' in offered:
        return 'work_editing' if 'finish' not in offered else 'work_verified'
    if offered == {'scoped_tests'}:
        return 'verify'
    if offered == {'finish'}:
        return 'finish'
    return 'unknown'


def reset_script_state(workspace: Path) -> None:
    _SEEDED.pop(str(workspace), None)


def scoped_tests_payload(text: str) -> dict:
    """The JSON verdict a ``scoped_tests`` observation carries.

    The observation may append correction feedback after the JSON, so the last
    decodable object that carries ``code_revision`` is the verdict.
    """
    decoder = json.JSONDecoder()
    found = None
    for index, char in enumerate(text):
        if char != '{':
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and 'code_revision' in value and 'passed' in value:
            found = value
    if found is None:
        raise AssertionError(f'no scoped_tests verdict in observation: {text[:200]}')
    return found


def _generic_call(llm, index, names, workspace):
    """One step that respects the policy's offered tools and never loops.

    The offered set encodes the budget phase, so the script reads it the same way
    a model would: the editor is offered while the work budget is open, the host
    feedback tool alone once work is closed, and `finish` once the current
    revision has been publicly verified.

    The script never edits the workspace.  A scripted conversation cannot land a
    real Django feature fix, and asserting a host verdict from a fabricated edit
    would be exactly the kind of self-reported success this project refuses
    elsewhere; the values the fork flow is gated on - prefix events, workspace
    hashes, ledger carry-over, phase boundaries, compaction - do not depend on
    the code being correct.
    """
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
    # The real SDK delivers the tool observation to the budget policy through the
    # conversation callback so the policy can close the phase.  This synthetic
    # conversation does not deliver it, so the script reports the verdict itself;
    # otherwise the prefix would loop until the request ceiling, which is the
    # runaway the budget exists to bound, not a property of the design under test.
    from validation_tasks_v49 import code_revision as _revision
    verdict = json.dumps({'passed': False,
                          'code_revision': _revision(workspace, TASK)})
    getattr(llm, '_policy').observe_test(verdict, is_error=False)
    return ('scoped_tests', {'reason': f'gate step {index}: public regression check'})


def prefix_driver_broken(llm, index, names):
    """Prefix: leave the flag absent so the host test demands correction.

    The driver only calls tools the budget policy actually offers for the
    current phase and varies every call, which is the same contract the real
    model works under; the SDK's loop detector must never be what ends a step.
    """
    return _generic_call(llm, index, names, Path(llm._gate_workspace))


def exhaust_driver(llm, index, names):
    """Prefix: never land the fix, so the request ceiling is reached."""
    return _generic_call(llm, index, names, Path(llm._gate_workspace))


# --------------------------------------------------------------------------
# engine harness
# --------------------------------------------------------------------------

class Harness:
    """Drive the real engine on a synthetic workspace with scripted responses."""

    # Compaction must be reachable on this synthetic history (freezing the
    # plugin's real thresholds is a separate, paid-run concern): the trigger
    # fires well below the branch's first input and the hard ceiling is far
    # above it, so the plugin arm demonstrably condenses after the fork.
    #
    # The request budgets are scaled down because the scripted prefix is not a
    # language model: it keeps working until the policy stops it, so with the
    # protocol's real 24/36 numbers it would spend the entire ceiling and the
    # carry-over guard would refuse to build any branch at all.  The *relations*
    # the gate checks are the protocol's: work requests end the first phase, the
    # correction reserve is what a branch inherits, and the cap is shared.
    OVERRIDES = {'trigger_input_tokens': 200, 'pruner_target_tokens': 80,
                 'pruner_hard_tokens': 200000,
                 # The synthetic scripted inputs are costlier per request than
                 # the real task's, so the gate raises the input ceiling only,
                 # leaving the request ceiling (the quantity the gate checks)
                 # as the small shared value used below.
                 'max_total_estimated_input_per_sample': 20000000,
                 'initial_work_requests': 6, 'host_feedback_correction_reserve': 6,
                 'max_agent_calls_per_sample': 16}
    def __init__(self, tmp_path: Path, protocol_overrides=None):
        self.tmp = tmp_path
        self.out = tmp_path / 'batch'
        self.workspace = build_workspace(tmp_path / 'workspace')
        self.out.mkdir(parents=True, exist_ok=True)
        self.protocol = dict(runner.PROTOCOL)
        self.protocol.update(self.OVERRIDES)
        self.protocol.update(protocol_overrides or {})
        self._legit = patch.object(runner, 'PROTOCOL', self.protocol)
        self._patch = patch('verification_tool_django_v49.evaluate', fake_evaluate)

    def __enter__(self):
        self._legit.start()
        self._patch.start()
        self.config = runner.EngineConfig(
            out=self.out, task=TASK, stage='run', api_key='gate-key',
            workspace=self.workspace, prepare_workspace=False,
            evaluator=fake_evaluate, snapshot=self.out / 'prefix-snapshot')
        self.engine = runner.Engine(self.config)
        self.engine.plan = self.protocol
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
        unbind_tool_context()
        reset_script_state(self.workspace)
        marker = self.workspace / ALLOWED[0]
        if marker.is_file():
            marker.write_text(BROKEN_SOURCE, encoding='utf-8')

    def run_flow(self, prefix_driver=prefix_driver_broken, branch_driver_fn=branch_driver):
        """Run the prefix, then the three branches with their own script.

        The prefix script and the branch script must be separate: the prefix
        leaves the feature failing (so the host demands correction) while each
        branch is the party that fixes it.
        """
        with patch('openhands.sdk.agent.base.has_vision_profile_available', lambda: False):
            with patch('openhands.sdk.LLM.completion', scripted_provider(prefix_driver)):
                prefix_report = self.engine.run_prefix()
            assert not prefix_report.get('error_type'), prefix_report.get('diagnostic')
            freeze = runner.load(self.out / 'prefix-freeze.json')
            frozen = runner.load(self.out / 'frozen-hashes.json')
            # The synthesiser's per-request input estimates are far larger than
            # the real task's, so the carried input is pinned to the recorded
            # value below; the real run keeps the prefix's own estimate.  Only
            # the input side is affected, never the carried request count.
            freeze['estimated_input'] = 1000
            # the engine's in-memory prefix ledger is what a branch ledger is
            # derived from, so pin it too (the real run keeps the real value)
            self.engine.ledger['estimated_input'] = 1000
            reports = []
            with patch('openhands.sdk.LLM.completion', scripted_provider(branch_driver_fn)):
                for position, arm in enumerate(self.engine.branch_order):
                    guard = importlib.import_module('edit_retry_guard_v33').EditRetryGuard()
                    self.engine.tool_ctx['edit_guard'] = guard
                    self.engine.bind_tools(guard)
                    reports.append(self.engine.run_branch(arm, freeze, frozen, position))
                    assert not reports[-1].get('error_type'), \
                        (arm, reports[-1].get('diagnostic')[-800:])
        return prefix_report, freeze, reports


def rebuild_source(harness: Harness, freeze: dict) -> None:
    harness.engine.ledger = runner.load(harness.out / 'prefix' / 'ledger.json')
    harness.engine.rebuild_prefix_conversation(freeze)


# --------------------------------------------------------------------------
# 1. frozen allowlist / file boundary
# --------------------------------------------------------------------------

def test_frozen_protocol_document_matches_the_runner():
    """The frozen protocol document and the runner's PROTOCOL must agree.

    A drift here would make the paid batch describe a design it did not run, so
    the document is checked against the code rather than trusted.
    """
    document = (ROOT / 'integrations' / 'openhands' / 'PILOT_PROTOCOL_V54.md').read_text(
        encoding='utf-8')
    assert runner.FROZEN_TASK == 'django_referenced_window_wrapping'
    assert runner.FROZEN_ARM == 'none'
    for token in ('django_referenced_window_wrapping', 'django__django-17084',
                  'same_prefix_fork_v54.py', 'run_validation_v54.py',
                  'PILOT_PROTOCOL_V54_DRAFT.md'):
        assert token in document, token
    assert ' \u2192 '.join(runner.DEFAULT_BRANCH_ORDER) in document
    for field, value in (('max_agent_calls_per_sample', 36),
                         ('initial_work_requests', 24),
                         ('host_feedback_correction_reserve', 10),
                         ('closing_request_reserve', 2),
                         ('trigger_input_tokens', 28000),
                         ('pruner_target_tokens', 22400),
                         ('pruner_hard_tokens', 39200),
                         ('native_max_tokens', 33600),
                         ('temperature', 0),
                         ('max_single_estimated_input', 80000),
                         ('max_total_estimated_input_per_sample', 2000000)):
        assert runner.PROTOCOL[field] == value, (field, runner.PROTOCOL[field])
    assert runner.PROTOCOL['tools'] == ['scoped_editor', 'scoped_symbols',
                                        'scoped_tests', 'finish']
    assert runner.PROTOCOL['arms'] == list(runner.ARMS)
    assert runner.PROTOCOL['branch_order'] == list(runner.DEFAULT_BRANCH_ORDER)
    # permission boundary: exactly the reference patch's file, no shell tool
    assert runner.EXPECTED_ALLOWED[runner.FROZEN_TASK] == ALLOWED
    assert 'shell' not in ' '.join(runner.PROTOCOL['tools'])
    assert runner.PROTOCOL['host_feedback_rounds_per_branch'] == 2


def test_frozen_task_allowlist_matches_the_dataset_patch():
    instance_id = runner.INSTANCES[TASK]['instance_id']
    instance_dir = (ROOT / '.tooling/swebench-verified'
                    / ('django-' + instance_id.split('-')[-1]))
    patch_files = sorted({
        line.split(' b/')[-1].replace('\\', '/')
        for line in (instance_dir / 'reference.patch').read_text(
            encoding='utf-8', errors='replace').splitlines()
        if line.startswith('diff --git')})
    assert runner.TASKS[TASK]['allowed'] == runner.EXPECTED_ALLOWED[TASK] == ALLOWED
    assert patch_files == sorted(ALLOWED), (patch_files, ALLOWED)
    for name in runner.TASKS[TASK]['research_files']:
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
    assert runner.PROTOCOL['tools'] == ['scoped_editor', 'scoped_symbols',
                                        'scoped_tests', 'finish']
    assert runner.PROTOCOL['arms'] == ['none', 'native_summary', 'pruner_v1']


def test_editor_executor_refuses_every_path_outside_the_allowlist(tmp_path):
    from openhands.tools.file_editor.definition import FileEditorAction
    workspace = build_workspace(tmp_path / 'workspace')
    allowed = [workspace / name for name in ALLOWED]
    bind_tool_context(workspace=workspace, allowed=allowed,
                      edit_guard=importlib.import_module('edit_retry_guard_v33').EditRetryGuard(),
                      navigator=None)
    try:
        executor = ScopedEditorExecutorV54(
            workspace_root=str(workspace), allowed_edits_files=[str(p) for p in allowed])
        outside = FileEditorAction(command='create',
                                  path=str(workspace / 'tests' / 'runtests.py'),
                                  file_text='tamper')
        result = executor(outside)
        assert result.is_error and 'Edit only these task files' in result.text
        assert (workspace / 'tests' / 'runtests.py').read_text(encoding='utf-8') != 'tamper'
        beyond = FileEditorAction(command='create', path=str(tmp_path / 'escape.py'),
                                 file_text='tamper')
        assert executor(beyond).is_error
        assert not (tmp_path / 'escape.py').exists()
    finally:
        unbind_tool_context()


# --------------------------------------------------------------------------
# 2/3. host test inside the fork loop
# --------------------------------------------------------------------------

def test_scoped_tests_tool_runs_the_host_test_and_reports_its_feedback(tmp_path):
    """The host target test path, reached through the registered v54 tool.

    The runner registers its v54 tool surface and creates the host-feedback tool
    from a ``Tool(name='scoped_tests')`` spec; using that same registration
    and resolution path here checks the policy allow-list, the registry and the
    tool instance name agree.
    """
    global _PASSING_PUBLIC
    from openhands.sdk.tool import Tool
    from openhands.sdk.tool.registry import resolve_tool
    from verification_tool_django_v49 import ScopedTestsAction
    from integrations.openhands.fork_tools_v54 import (TESTS_TOOL_NAME,
                                                       register_v54_tools)

    workspace = build_workspace(tmp_path / 'workspace')
    bind_tool_context(workspace=workspace, allowed=[workspace / name for name in ALLOWED],
                      edit_guard=importlib.import_module('edit_retry_guard_v33').EditRetryGuard(),
                      navigator=None)
    register_v54_tools()

    tool = resolve_tool(
        Tool(name=TESTS_TOOL_NAME,
             params={'workspace': str(workspace), 'task': TASK,
                     'destination': str(tmp_path / 'tool-tests')}), None)[0]
    assert tool.name == TESTS_TOOL_NAME
    try:
        with patch('verification_tool_django_v49.evaluate', fake_evaluate):
            # This check is about the tool's own wiring: it drives the public
            # module in both verdicts and checks the host formatter runs on the
            # failure text.
            (workspace / ALLOWED[0]).write_text(BROKEN_SOURCE, encoding='utf-8')
            broken = tool.executor(ScopedTestsAction(reason='gate: pre-fix'))
            assert not broken.is_error
            failed = scoped_tests_payload(broken.text)
            assert failed['passed'] is False and failed['selection'] == ['gate']
            assert 'Public regression tests only' in broken.text
            assert 'FAIL: test_gate' in broken.text
            assert 'Repeated root diagnostics' in broken.text
            cached = tool.executor(ScopedTestsAction(reason='gate: repeat'))
            assert 'Unchanged code; cached result' in cached.text
            # the host target test (the evaluator the runner uses) demands the flag
            assert fake_evaluate(workspace, TASK, tmp_path / 'host')['passed'] is False
            (workspace / ALLOWED[0]).write_text(FIXED_SOURCE, encoding='utf-8')
            assert fake_evaluate(workspace, TASK, tmp_path / 'host-fixed')['passed'] is True
            _PASSING_PUBLIC = True
            fixed = tool.executor(ScopedTestsAction(reason='gate: post-fix'))
            assert scoped_tests_payload(fixed.text)['passed'] is True
    finally:
        _PASSING_PUBLIC = False
        unbind_tool_context()


# --------------------------------------------------------------------------
# 4/5. request carry-over
# --------------------------------------------------------------------------

def _policy(prefix_calls: int, prefix_input: int):
    return PrefixCarryingBudgetPolicy(
        lambda: 'revision', prefix_used_agent_calls=prefix_calls,
        prefix_used_estimated_input=prefix_input,
        initial_work_requests=24,
        correction_call_limit=10,
        max_agent_calls=36,
        reserve_requests=2,
        max_single_input=80000,
        max_total_input=2000000,
        single_input_headroom=12000)


def test_branch_ledger_carries_the_prefix_requests():
    from integrations.openhands.same_prefix_fork_v54 import branch_ledger_from_prefix

    prefix = {'calls': [{'kind': 'agent', 'status': 'returned'} for _ in range(24)],
              'estimated_input': 1000}
    ledger = branch_ledger_from_prefix(prefix)
    # the branch ledger counts only the branch's own requests
    assert ledger['calls'] == [] and ledger['estimated_input'] == 0
    assert ledger['shared_prefix_call_count'] == 24
    assert ledger['shared_prefix_estimated_input'] == 1000
    assert ledger['shared_prefix_agent_requests'] == 24
    assert prefix['calls'] and len(prefix['calls']) == 24
    # a branch ledger is not a valid fork point itself
    with pytest.raises(ValueError, match='itself'):
        branch_ledger_from_prefix(ledger)
    # simultaneous branches never share a ledger object or its rows
    left, right = branch_ledger_from_prefix(prefix), branch_ledger_from_prefix(prefix)
    assert left is not right and left['calls'] is not right['calls']
    left['calls'].append({'kind': 'agent', 'status': 'started'})
    left['estimated_input'] += 5
    assert right['calls'] == [] and right['estimated_input'] == 0
    assert prefix['estimated_input'] == 1000 and len(prefix['calls']) == 24
    policy = _policy(24, 1000)
    # The correction boundary continues the prefix: 24 + 10, not 10 from zero.
    assert policy.correction_stop_at == 34
    assert policy.cumulative_agent_calls(0) == 24
    assert policy.cumulative_estimated_input(0) == 1000
    # A branch with 8 own requests is at 32 of the shared 36; the reported
    # remainder counts every request left, the reserve is visible in the phase.
    assert policy.decide(8, 1000, 5000).remaining_requests == 28
    assert policy.decide(8, 1000, 5000).remaining_work_requests == 0
    assert _policy(24, 1000).decide(11, 1000, 5000).remaining_requests == 25
    assert policy.can_correct(20, 1000) is True
    assert policy.can_correct(36, 1000) is False
    # 24 + 12 = 36 requests is the shared ceiling, so this branch may make its
    # 12th request (the 36th overall) but not a 13th: "no fresh 36-request
    # allowance" is exactly this, not a branch-local 36.
    assert _policy(24, 1000).decide(11, 1000, 5000).remaining_requests == 25
    with pytest.raises(RuntimeError, match='limit'):
        _policy(24, 1000).decide(12, 1000, 5000)
    # Opening the correction phase must not move the boundary the prefix fixed.
    opened = _policy(24, 1000)
    opened.begin_correction(0)
    assert opened.in_correction and opened.correction_stop_at == 34
    assert opened.decide(0, 1000, 5000).remaining_requests == 36
    assert opened.can_correct(8, 1000) is True
    assert opened.can_correct(36, 1000) is False
    with pytest.raises(RuntimeError, match='already started'):
        opened.begin_correction(0)
    state = policy.capture_prefix_state()
    other = _policy(24, 1000)
    other.check_prefix_state(state)
    with pytest.raises(ValueError, match='incomplete'):
        other.check_prefix_state({'passed': True})
    inside = dict(state, in_correction=True)
    with pytest.raises(ValueError, match='correction phase'):
        other.check_prefix_state(inside)
    # branch policies are independent: one branch's spending cannot move another's
    assert other.cumulative_agent_calls(0) == 24
    assert policy.cumulative_agent_calls(8) == 32
    assert other.cumulative_agent_calls(0) == 24
    assert other.cumulative_estimated_input(0) == 1000
    with pytest.raises(ValueError, match='fresh allowance'):
        _policy(runner.PROTOCOL['max_agent_calls_per_sample'], 0)


def test_exhausted_prefix_leaves_no_branch_allowance(tmp_path):
    """No branch may receive more requests than the shared ceiling still holds."""
    with Harness(tmp_path) as harness:
        ceiling = harness.protocol['max_agent_calls_per_sample']
        initial = harness.protocol['initial_work_requests']
        prefix_report, freeze, reports = harness.run_flow(prefix_driver=exhaust_driver)
        # the prefix spends its first-phase allowance in full, plus whatever the
        # verification/Finish reserve releases to it
        assert initial <= prefix_report['agent_requests'] <= initial + runner.PROTOCOL[
            'closing_request_reserve'] + 1
        assert prefix_report['prefix_needs_correction'] is True
        assert prefix_report['first_phase_within_budget'] is True
        assert freeze['agent_requests'] == prefix_report['agent_requests']
        inherited = max(0, ceiling - freeze['agent_requests'])
        assert len(reports) == 3
        for report in reports:
            assert report['ledger']['carried_over_agent_requests'] == freeze['agent_requests']
            assert report['budget_state']['correction_stop_at'] == min(
                ceiling, freeze['agent_requests'] +
                harness.protocol['host_feedback_correction_reserve'])
            assert report['budget_state']['correction_stop_at'] <= ceiling
            # the branch request ceiling is the shared ceiling minus the prefix,
            # never a fresh per-branch allowance
            assert report['post_fork_agent_requests'] <= inherited
            assert report['post_fork_agent_requests'] + freeze['agent_requests'] <= ceiling
            assert report['restore_hash_equal'] is True


# --------------------------------------------------------------------------
# 6/7. full sequential fork flow
# --------------------------------------------------------------------------

def test_three_sequential_branches_reuse_one_prefix(tmp_path):
    """The three branches share one frozen prefix, at one path, sequentially.

    Scope note: this drives the real engine, SDK fork, tool executors, budget
    policy and condenser on a synthetic workspace with a scripted model, so it
    gates the *mechanism* - identical prefix events, hash-equal restores, carried
    requests, independent ledgers, phase boundaries, compaction.  Host-test
    quality is deliberately **not** asserted here: a fabricated scripted edit
    cannot establish it, and the real-Django `--stage check` plus the paid
    batch's independent audit are where acceptance is checked.
    """
    with Harness(tmp_path) as harness:
        prefix_report, freeze, reports = harness.run_flow()
        assert prefix_report['host_test_passed'] is False
        assert prefix_report['prefix_needs_correction'] is True
        assert prefix_report['first_phase_within_budget'] is True
        assert prefix_report['file_boundary_ok'] is True
        assert freeze['event_count'] > 2
        assert (harness.out / 'prefix-snapshot').is_dir()

        assert [report['arm'] for report in reports] == list(runner.ARMS)
        arms = {report['arm']: report for report in reports}
        for report in reports:
            assert report['restore_hash_equal'] is True
            assert report['shared_prefix_calls'] == freeze['agent_requests']
            assert report['ledger']['carried_over_agent_requests'] == freeze['agent_requests']
            assert report['ledger']['carried_over_estimated_input'] == 1000
            assert report['shared_prefix_estimated_input'] == 1000
            assert report['prefix_event_count'] == freeze['event_count']
            assert report['source_prefix_unchanged'] is True
            assert report['branch_events_persisted_match_observation'] is True
            assert report['file_boundary_ok'] is True
            assert report['post_fork_agent_requests'] >= 1
            # the branch's own ceiling is the shared one minus the prefix
            assert report['post_fork_agent_requests'] + freeze['agent_requests'] <= \
                harness.protocol['max_agent_calls_per_sample']
            # every branch request is recorded in the branch's own ledger, and
            # the ledger holds the branch's own requests only
            assert len(report['ledger']['calls']) == report['post_fork_total_requests']
            assert all(call['status'] == 'returned' for call in report['ledger']['calls'])
            assert all(call['kind'] == 'agent' for call in report['ledger']['calls'])
            assert report['host_rounds'] and isinstance(
                report['host_rounds'][0]['host']['passed'], bool)
        for key in ('prepare_copy_hash_seconds', 'prefix_model_wait_seconds',
                    'restore_workspace_seconds', 'restore_hash_verify_seconds',
                    'branch_model_wait_seconds', 'host_test_seconds'):
            assert key in harness.engine.timing, (key, sorted(harness.engine.timing))
        assert arms['none']['condensation_events'] == 0
        assert arms['native_summary']['condensation_events'] == 0
        assert arms['pruner_v1']['condensation_events'] >= 1
        assert arms['pruner_v1']['mechanism_triggered'] is True
        pruner_state = runner.load(harness.out / 'branch-2-pruner_v1' / 'pruner-state.json')
        assert pruner_state['audit']
        ids = [set(report['branch_event_ids']) for report in reports]
        assert not ids[0] & ids[1] and not ids[1] & ids[2] and not ids[0] & ids[2]
        result = runner.comparison(harness.out)
        assert result['prefix']['complete_total_tokens'] == prefix_report['complete_total_tokens']
        assert result['mechanism_triggered_plugin'] is True
        for arm, row in result['branches'].items():
            expected = prefix_report['complete_total_tokens'] + arms[arm]['complete_total_tokens']
            assert row['whole_run_tokens_shared_prefix_counted_once'] == expected


def test_prefix_stage_freezes_and_replays_the_same_conversation(tmp_path):
    with Harness(tmp_path) as harness:
        with patch('openhands.sdk.LLM.completion',
                   scripted_provider(prefix_driver_broken)), \
             patch('openhands.sdk.agent.base.has_vision_profile_available', lambda: False):
            prefix_report = harness.engine.run_prefix()
        freeze = runner.load(harness.out / 'prefix-freeze.json')
        assert prefix_report['event_id_digest'] == freeze['event_id_digest']
        harness.engine.source.close()
        harness.engine.source = None
        with patch('openhands.sdk.agent.base.has_vision_profile_available', lambda: False):
            rebuild_source(harness, freeze)
        assert runner.digest_ids(harness.engine.prefix_event_ids()) == freeze['event_id_digest']
        assert len(harness.engine.prefix_event_ids()) == freeze['event_count']
        target = harness.workspace / ALLOWED[0]
        target.write_text(target.read_text(encoding='utf-8') + '\n# tamper\n', encoding='utf-8')
        harness.engine.source.close()
        harness.engine.source = None
        with patch('openhands.sdk.agent.base.has_vision_profile_available', lambda: False), \
             pytest.raises(RuntimeError, match='prefix workspace changed'):
            harness.engine.rebuild_prefix_conversation(freeze)





