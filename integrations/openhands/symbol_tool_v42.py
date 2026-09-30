"""v42 read-only definition search plus explicit class-member existence check."""
import json

from pydantic import Field
from typing import Literal
from openhands.sdk.tool.tool import Action, Observation, ToolDefinition, ToolExecutor, ToolAnnotations

from integrations.openhands.source_navigation_v42 import SourceNavigatorV42


class ScopedSymbolsActionV42(Action):
    path: str = Field(description='Absolute path to an authorized Python source file.')
    query: str = Field(default='', description='Definition name or part of it; 2–64 characters for search.')
    class_name: str = Field(default='', description='For API check, exact class name in this file.')
    member_name: str = Field(default='', description='For API check, exact method name.')
    command: Literal['view'] | None = Field(default=None)


class ScopedSymbolsObservationV42(Observation):
    pass


class ScopedSymbolsExecutorV42(ToolExecutor):
    def __init__(self, navigator: SourceNavigatorV42):
        self.navigator = navigator

    def __call__(self, action, conversation=None):
        try:
            if action.class_name or action.member_name:
                if not (action.class_name and action.member_name):
                    raise ValueError('Supply both class_name and member_name for an API check')
                result = self.navigator.inspect_member(action.path, action.class_name, action.member_name)
            else:
                result = self.navigator.search(action.path, action.query)
        except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
            return ScopedSymbolsObservationV42.from_text(text=str(exc), is_error=True)
        return ScopedSymbolsObservationV42.from_text(text=json.dumps(result, ensure_ascii=False))


class ScopedSymbolsToolV42(ToolDefinition[ScopedSymbolsActionV42, ScopedSymbolsObservationV42]):
    @classmethod
    def create(cls, conv_state, navigator):
        return [cls(action_type=ScopedSymbolsActionV42,
                    observation_type=ScopedSymbolsObservationV42,
                    description='Search definitions by query or check whether an exact class method is defined in one allowed source file. Reports source revision and does not infer inherited methods.',
                    executor=ScopedSymbolsExecutorV42(navigator),
                    annotations=ToolAnnotations(title='scoped_symbols', readOnlyHint=True,
                                                destructiveHint=False, openWorldHint=False))]
