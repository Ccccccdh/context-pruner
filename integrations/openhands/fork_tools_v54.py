"""v54 tool surface: the frozen v53 tool boundary, bound to the current engine.

The v53 runner defined its scoped tools as closures inside ``main()``, which
cannot be reused by a module that must also be importable by a zero-API gate.
These classes are the same three-way boundary, restated as importable classes:

* ``ScopedEditorToolV54`` - file editor whose executor refuses any edit outside
  the frozen allowed list (``edit_scope_guard_v41.scope_error``) and any
  unchanged replacement (``edit_retry_guard_v33.EditRetryGuard``), and that
  appends the revision-aware navigation hint after a successful view.
* ``ScopedSymbolsToolV54`` - read-only AST symbol search restricted to the
  frozen research file set through the navigator's own allowlist.

The active engine's binding is passed through ``bind_tool_context`` before any
conversation is built; a tool created without a binding raises instead of
silently exposing an unbounded workspace.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openhands.sdk.llm import TextContent
from openhands.sdk.tool import ToolDefinition
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.file_editor.definition import FileEditorObservation
from openhands.tools.file_editor.impl import FileEditorExecutor

from edit_scope_guard_v41 import scope_error
from integrations.openhands.symbol_tool_v44 import (
    ScopedSymbolsActionV44,
    ScopedSymbolsObservationV44,
    ScopedSymbolsExecutorV44,
)

#: The single active binding.  One sample runs at a time by construction: the
#: v54 experiment never runs two branches concurrently at the same path.
_CONTEXT: dict[str, Any] = {}


def bind_tool_context(**values) -> None:
    unknown = set(values) - {'workspace', 'allowed', 'edit_guard', 'navigator'}
    if unknown:
        raise ValueError(f'unknown tool context keys: {sorted(unknown)}')
    _CONTEXT.update(values)


def register_v54_tools(*, strict: bool = True) -> dict[str, bool]:
    """Register the v54 tools once; never silently replace another module's tool.

    The SDK registry is a process-global name->resolver map that overwrites an
    existing entry with only a warning.  The v54 runner is the experiment's
    owner of the three names below, so it (re)registers them, but reports which
    names another module had already claimed: on a shared interpreter (for
    example a pytest session that also imports an older symbol-tool revision)
    the SDK's own class registry may then refuse to build an agent, and the
    caller needs to know which names are not usable rather than discovering it
    as a validation error mid-conversation.

    Returns a mapping of tool name to ``True`` when this module owns it.
    """
    from openhands.sdk.tool import register_tool
    from openhands.sdk.tool.registry import _MODULE_QUALNAMES

    owners = {name: module for name, module in ((EDITOR_TOOL_NAME, ScopedEditorToolV54.__module__),
                                                (SYMBOLS_TOOL_NAME, ScopedSymbolsToolV54.__module__),
                                                (TESTS_TOOL_NAME, _tests_tool_class().__module__))}
    claimed = {}
    for name, tool in ((EDITOR_TOOL_NAME, ScopedEditorToolV54),
                       (SYMBOLS_TOOL_NAME, ScopedSymbolsToolV54),
                       (TESTS_TOOL_NAME, _tests_tool_class())):
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'tool class {tool.__name__} has no name')
        previous = _MODULE_QUALNAMES.get(name)
        claimed[name] = previous not in (None, owners[name])
        register_tool(name, tool)
    for name, module in owners.items():
        if _MODULE_QUALNAMES.get(name) != module:
            raise RuntimeError(f'tool {name!r} is not bound to the v54 implementation')
    return {name: not taken for name, taken in claimed.items()}


def unbind_tool_context() -> None:
    _CONTEXT.clear()


def _bound(name: str) -> Any:
    if name not in _CONTEXT:
        raise RuntimeError(f'tool context is not bound ({name}); refusing an unbounded tool')
    return _CONTEXT[name]


class ScopedEditorExecutorV54(FileEditorExecutor):
    def __init__(self, **kwargs):
        self.scope = Path(kwargs['workspace_root']).resolve()
        super().__init__(**kwargs)

    def __call__(self, action, conversation=None):
        allowed = _bound('allowed')
        hint = scope_error(action.command, action.path, self.scope, allowed)
        if hint:
            return FileEditorObservation.from_text(text=hint, command=action.command,
                                                   is_error=True)
        retry_hint = _bound('edit_guard').check(action)
        if retry_hint:
            return FileEditorObservation.from_text(text=retry_hint, command=action.command,
                                                   is_error=True)
        observation = super().__call__(action, conversation)
        if action.command == 'view' and not observation.is_error:
            navigator = _CONTEXT.get('navigator')
            if navigator is not None:
                view_hint = navigator.view_hint(action.path, action.view_range)
                if view_hint:
                    observation = observation.model_copy(update={
                        'content': [*observation.content, TextContent(text=view_hint)]})
        return observation


#: The budget policy allow-list uses these exact names
#: (``budget_policy_v54.TOOL_NAMES``).  They are set explicitly because the SDK
#: derives a name from the class name, and for ``ScopedEditorToolV54`` that would
#: be ``scoped_editor_tool_v54`` - a name no budget phase allows, which silently
#: removed the editor from every work-phase request.  A v54 gate asserts the
#: names the policy allows, the names the registry holds and the names the agent
#: offers all agree.
EDITOR_TOOL_NAME = 'scoped_editor'
SYMBOLS_TOOL_NAME = 'scoped_symbols'
TESTS_TOOL_NAME = 'scoped_tests'


def _tests_tool_class():
    """The frozen v49 host-feedback tool class used by the v54 agent.

    The class is frozen in the v49 module, so its own name is untouched; the v54
    runner refers to it by that name, and ``Tool(name=...)`` resolves to an
    instance that keeps it.
    """
    from verification_tool_django_v49 import ScopedTestsTool
    return ScopedTestsTool


class ScopedEditorToolV54(FileEditorTool):
    """``scoped_editor``: identical behaviour, importable and bindable."""

    name = EDITOR_TOOL_NAME

    @classmethod
    def create(cls, conv_state):
        workspace = _bound('workspace')
        allowed = _bound('allowed')
        executor = ScopedEditorExecutorV54(
            workspace_root=str(workspace), allowed_edits_files=[str(p) for p in allowed])
        return [tool.set_executor(executor) for tool in super().create(conv_state)]


class ScopedSymbolsToolV54(ToolDefinition[ScopedSymbolsActionV44,
                                          ScopedSymbolsObservationV44]):
    """``scoped_symbols``: read-only, restricted to the frozen research set."""

    name = SYMBOLS_TOOL_NAME

    @classmethod
    def create(cls, conv_state):
        return [cls(action_type=ScopedSymbolsActionV44,
                    observation_type=ScopedSymbolsObservationV44,
                    description=('Search definitions by query or check whether an exact class '
                                 'method is defined in one allowed source file. Reports source '
                                 'revision and does not infer inherited methods.'),
                    executor=ScopedSymbolsExecutorV44(_bound('navigator')))]
