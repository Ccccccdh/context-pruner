"""Read-only AST symbol locator restricted to evaluator-authorized source files."""
from pydantic import Field
from typing import Literal
from openhands.sdk.tool.tool import Action, Observation, ToolDefinition, ToolExecutor, ToolAnnotations

from source_navigation_v31 import SourceNavigator


class ScopedSymbolsAction(Action):
    path: str = Field(description='Absolute path to an authorized Python source file in the workspace.')
    query: str = Field(description='Definition name or part of it, 2–64 characters.')
    command: Literal['view'] | None = Field(default=None, description='Optional read-only compatibility verb; only view is accepted.')


class ScopedSymbolsObservation(Observation):
    pass


class ScopedSymbolsExecutor(ToolExecutor):
    def __init__(self, navigator: SourceNavigator):
        self.navigator = navigator

    def __call__(self, action, conversation=None):
        import json
        try:
            result = self.navigator.search(action.path, action.query)
        except (OSError, UnicodeError, ValueError) as exc:
            return ScopedSymbolsObservation.from_text(text=str(exc), is_error=True)
        return ScopedSymbolsObservation.from_text(text=json.dumps(result, ensure_ascii=False))


class ScopedSymbolsTool(ToolDefinition[ScopedSymbolsAction, ScopedSymbolsObservation]):
    @classmethod
    def create(cls, conv_state, navigator):
        return [cls(action_type=ScopedSymbolsAction, observation_type=ScopedSymbolsObservation,
                    description='Find up to eight Python class/function definitions by name in one allowed source file. Return exact line spans and source revision. Read-only; no shell or arbitrary file search.',
                    executor=ScopedSymbolsExecutor(navigator),
                    annotations=ToolAnnotations(title='scoped_symbols', readOnlyHint=True,
                                                destructiveHint=False, openWorldHint=False))]
