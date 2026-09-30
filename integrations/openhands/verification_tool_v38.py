"""Fixed public-test tool for the v38 Django task.

Same shape as verification_tool_v36.ScopedTestsTool: the model supplies only a
short `reason`, tests are fixed to the public `aggregation` module, and there is
no way to select arbitrary tests, paths or shell commands.

v38 differences vs v36:

* The "unchanged code" short-circuit keys on a revision hash of the four
  *editable* source files (`validation_tasks_v38.code_revision`), not on a full
  workspace hash computed on every call (v36 hashed the whole tree per model
  request).
* A full workspace hash is taken around each test run by the task evaluator
  to detect test-side edits. Cached calls avoid that scan.
* Candidate-source immutability is asserted through the evaluator's
  `workspace_unchanged_by_test` result.
"""
from pathlib import Path
import json

from pydantic import Field
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolDefinition,
    ToolExecutor,
    ToolAnnotations,
)

from validation_tasks_v38 import code_revision, evaluate, TASKS


class ScopedTestsAction(Action):
    reason: str = Field(description='Briefly explain which implementation you are ready to verify.')


class ScopedTestsObservation(Observation):
    pass


class ScopedTestsExecutor(ToolExecutor):
    def __init__(self, workspace, task, destination):
        self.workspace = Path(workspace).resolve()
        self.task = task
        self.destination = Path(destination).resolve()
        self.calls = 0
        self.previous = None
        self.cached = None

    def __call__(self, action, conversation=None):
        revision = code_revision(self.workspace, self.task)
        if revision == self.previous:
            return ScopedTestsObservation.from_text(
                text='Unchanged code; cached result:\n' + self.cached)
        if self.calls >= 3:
            return ScopedTestsObservation.from_text(
                text='Public-test budget exhausted. Use finish; external host acceptance follows.',
                is_error=True)
        self.calls += 1
        out = self.destination / f'public-test-{self.calls}'
        result = evaluate(self.workspace, self.task, out, regression_only=True)
        result['code_revision'] = revision
        if not result.get('workspace_unchanged_by_test'):
            return ScopedTestsObservation.from_text(
                text='Test execution changed workspace files; verification invalid.', is_error=True)
        text = ('Public regression tests only; feature acceptance is performed separately by the host.\n'
                + json.dumps(result))
        if not result['passed']:
            text += '\n' + (out / 'test.txt').read_text(encoding='utf-8')[-4000:]
        self.previous, self.cached = revision, text
        return ScopedTestsObservation.from_text(text=text)


class ScopedTestsTool(ToolDefinition[ScopedTestsAction, ScopedTestsObservation]):
    @classmethod
    def create(cls, conv_state, workspace, task, destination):
        assert task in TASKS
        return [cls(
            action_type=ScopedTestsAction,
            observation_type=ScopedTestsObservation,
            description=(
                'Run the fixed public upstream regression module (Django aggregation) for this '
                'workspace. No arguments can select commands, paths or test ids. At most three '
                'runs for changed code; unchanged code returns a cached result. After your ready '
                'code passes, use finish so the host can run feature acceptance.'),
            executor=ScopedTestsExecutor(workspace, task, destination),
            annotations=ToolAnnotations(
                title='scoped_tests', readOnlyHint=True, destructiveHint=False,
                openWorldHint=False),
        )]
