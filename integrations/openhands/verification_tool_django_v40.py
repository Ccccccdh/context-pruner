"""Fixed public regression tool with complete bounded failure indexing."""
from pathlib import Path
import json

from pydantic import Field
from openhands.sdk.tool.tool import Action, Observation, ToolDefinition, ToolExecutor, ToolAnnotations

from feedback_v40 import format_test_feedback
from validation_tasks_v40 import code_revision, evaluate, TASKS


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
            text += '\n' + format_test_feedback(
                (out / 'test.txt').read_text(encoding='utf-8'),
                workspace_root=self.workspace)
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
                'Run the fixed public upstream regression module (Django bulk_create). '
                'No commands, paths or test ids can be selected. At most three runs for changed '
                'code; unchanged code returns a cached result. Failures list every test name '
                'with bounded diagnostics. Host feature acceptance follows Finish.'),
            executor=ScopedTestsExecutor(workspace, task, destination),
            annotations=ToolAnnotations(
                title='scoped_tests', readOnlyHint=True, destructiveHint=False,
                openWorldHint=False),
        )]
