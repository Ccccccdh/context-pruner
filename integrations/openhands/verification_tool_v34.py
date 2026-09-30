"""Fixed public-test tool: no arbitrary commands, no credentials, bounded calls."""
from pathlib import Path
import json
import hashlib
from pydantic import Field
from openhands.sdk.tool.tool import Action, Observation, ToolDefinition, ToolExecutor, ToolAnnotations
from validation_tasks_v34 import evaluate, hashes, TASKS

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
        before = hashes(self.workspace)
        if before == self.previous:
            return ScopedTestsObservation.from_text(text='Unchanged code; cached result:\n' + self.cached)
        if self.calls >= 3:
            return ScopedTestsObservation.from_text(text='Public-test budget exhausted. Use finish; external host acceptance follows.', is_error=True)
        self.calls += 1
        out = self.destination / f'public-test-{self.calls}'
        result = evaluate(self.workspace, self.task, out, regression_only=True)
        result['code_revision'] = hashlib.sha256(json.dumps({p: before.get(p) for p in TASKS[self.task]['allowed']}, sort_keys=True).encode()).hexdigest()
        if hashes(self.workspace) != before:
            return ScopedTestsObservation.from_text(text='Test execution changed workspace files; verification invalid.', is_error=True)
        text = 'Public regression tests only; feature acceptance is performed separately by the host.\n' + json.dumps(result)
        if not result['passed']:
            text += '\n' + (out / 'pytest.txt').read_text(encoding='utf-8')[-4000:]
        self.previous, self.cached = before, text
        return ScopedTestsObservation.from_text(text=text)

class ScopedTestsTool(ToolDefinition[ScopedTestsAction, ScopedTestsObservation]):
    @classmethod
    def create(cls, conv_state, workspace, task, destination):
        assert task in TASKS
        return [cls(action_type=ScopedTestsAction, observation_type=ScopedTestsObservation,
                    description='Run the fixed selected public upstream regression tests for this workspace. No arguments can select commands or paths. At most three changed-code test runs; unchanged code returns a cached result. After ready code passes, use finish so the host can run feature acceptance.',
                    executor=ScopedTestsExecutor(workspace, task, destination),
                    annotations=ToolAnnotations(title='scoped_tests', readOnlyHint=True, destructiveHint=False, openWorldHint=False))]
