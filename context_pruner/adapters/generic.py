"""Native adapter for custom loops, ReAct agents, and user-built agents."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ..archive import ArchiveStore
from ..middleware import ContextPrunerMiddleware
from ..plugin import ContextPluginConfig, PluginHookResult
from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AgentCategory,
    HistoryVisibility,
    IntegrationLevel,
    ValidationLevel,
)


class GenericAgentAdapter:
    """Framework-neutral lifecycle hooks for a developer-controlled Agent loop."""

    descriptor = AdapterDescriptor(
        adapter_id="generic_loop",
        display_name="Generic Python/ReAct Agent Loop",
        category=AgentCategory.CUSTOM_LOOP,
        integration_level=IntegrationLevel.NATIVE,
        frameworks=("custom Python Agent", "ReAct", "Plan-and-Execute"),
        capabilities=AdapterCapabilities(
            history_visibility=HistoryVisibility.FULL,
            pre_model_transform=True,
            model_output_observation=True,
            tool_event_observation=True,
            state_persistence=True,
            lossless_tool_groups=False,
            multi_agent_namespaces=False,
            server_managed_history_safe=False,
        ),
        validation_level=ValidationLevel.REAL_API,
        limitations=(
            "the host Agent must call lifecycle hooks at model and tool boundaries",
        ),
    )

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        task_state: str = "",
        archive_store: ArchiveStore | None = None,
        token_counter: Callable[[str], int] | None = None,
        middleware: ContextPrunerMiddleware | None = None,
    ) -> None:
        self.middleware = middleware or ContextPrunerMiddleware(
            config,
            task_state=task_state,
            archive_store=archive_store,
            token_counter=token_counter,
        )

    def before_model(
        self,
        messages: Sequence[Any],
        *,
        task_state: str | None = None,
        reserved_tokens: int = 0,
    ) -> PluginHookResult:
        return self.middleware.before_model(
            messages,
            task_state=task_state,
            reserved_tokens=reserved_tokens,
        )

    def after_model(self, output: Any | Sequence[Any]) -> PluginHookResult:
        return self.middleware.after_model(output)

    def after_tool(self, output: Any | Sequence[Any]) -> PluginHookResult:
        return self.middleware.after_tool(output)

    def on_error(
        self,
        error: BaseException | str,
        *,
        query: str = "",
        recover: bool = True,
    ) -> PluginHookResult:
        return self.middleware.on_error(error, query=query, recover=recover)

    def finalize(self, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.middleware.finalize(metadata)

    def metrics_dict(self) -> dict[str, Any]:
        return self.middleware.metrics_dict()

    def export_state(self) -> dict[str, Any]:
        return self.middleware.export_state()

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, Any] | None,
        *,
        config: ContextPluginConfig | None = None,
        task_state: str = "",
        archive_store: ArchiveStore | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> "GenericAgentAdapter":
        return cls(
            middleware=ContextPrunerMiddleware.from_state(
                state,
                config=config,
                task_state=task_state,
                archive_store=archive_store,
                token_counter=token_counter,
            )
        )
