"""Framework-neutral middleware facade for model and tool call loops."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from .archive import ArchiveStore
from .plugin import (
    ContextLifecyclePlugin,
    ContextPluginConfig,
    PluginHookResult,
)
from .types import estimate_tokens


MIDDLEWARE_STATE_SCHEMA_VERSION = 1


@dataclass
class MiddlewareMetrics:
    """Metrics owned by the integration boundary rather than the core pruner."""

    reserved_tokens_total: int = 0
    peak_reserved_tokens: int = 0
    peak_model_input_tokens: int = 0
    model_input_budget_violation_count: int = 0


class ContextPrunerMiddleware:
    """Portable stateful middleware for custom Agent implementations.

    ``reserved_tokens`` represents model input that is not part of the mutable
    history, such as instructions, tool schemas and an output-token reserve.
    It is subtracted before compression and added back for strict final-budget
    accounting.
    """

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        task_state: str = "",
        archive_store: ArchiveStore | None = None,
        token_counter: Callable[[str], int] | None = None,
        plugin_state: Mapping[str, Any] | None = None,
        middleware_metrics: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = config or ContextPluginConfig()
        self.token_counter = token_counter or estimate_tokens
        self.plugin = ContextLifecyclePlugin.from_state(
            plugin_state,
            config=self.config,
            task_state=task_state,
            archive_store=archive_store,
            token_counter=self.token_counter,
        )
        known = MiddlewareMetrics.__dataclass_fields__
        raw_metrics = dict(middleware_metrics or {})
        self.metrics = MiddlewareMetrics(
            **{
                key: int(value)
                for key, value in raw_metrics.items()
                if key in known
            }
        )

    def before_model(
        self,
        messages: Sequence[Any],
        *,
        task_state: str | None = None,
        reserved_tokens: int = 0,
    ) -> PluginHookResult:
        reserved = max(0, int(reserved_tokens))
        hook = self.plugin.before_model(
            messages,
            task_state=task_state,
            budget=self.config.budget.reserve(reserved),
        )
        self.metrics.reserved_tokens_total += reserved
        self.metrics.peak_reserved_tokens = max(
            self.metrics.peak_reserved_tokens,
            reserved,
        )
        final_tokens = int(hook.metrics.get("last_served_context_tokens", 0)) + reserved
        self.metrics.peak_model_input_tokens = max(
            self.metrics.peak_model_input_tokens,
            final_tokens,
        )
        if final_tokens > self.config.budget.hard_limit_tokens:
            self.metrics.model_input_budget_violation_count += 1
        return self._result(hook.messages)

    def after_model(self, output: Any | Sequence[Any]) -> PluginHookResult:
        hook = self.plugin.after_model(output)
        return self._result(hook.messages)

    def after_tool(self, output: Any | Sequence[Any]) -> PluginHookResult:
        hook = self.plugin.after_tool(output)
        return self._result(hook.messages)

    def on_error(
        self,
        error: BaseException | str,
        *,
        query: str = "",
        recover: bool = True,
    ) -> PluginHookResult:
        hook = self.plugin.on_error(error, query=query, recover=recover)
        return self._result(hook.messages)

    def finalize(self, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.plugin.finalize(metadata)
        return self.metrics_dict()

    def metrics_dict(self) -> dict[str, Any]:
        metrics = self.plugin.metrics_dict()
        metrics.update(asdict(self.metrics))
        return metrics

    def export_state(self) -> dict[str, Any]:
        return {
            "schema_version": MIDDLEWARE_STATE_SCHEMA_VERSION,
            "middleware_metrics": asdict(self.metrics),
            "plugin": self.plugin.export_state(),
        }

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, Any] | None,
        *,
        config: ContextPluginConfig | None = None,
        task_state: str = "",
        archive_store: ArchiveStore | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> "ContextPrunerMiddleware":
        if not state:
            return cls(
                config,
                task_state=task_state,
                archive_store=archive_store,
                token_counter=token_counter,
            )
        if int(state.get("schema_version", 0)) != MIDDLEWARE_STATE_SCHEMA_VERSION:
            raise ValueError("unsupported middleware state version")
        return cls(
            config,
            task_state=task_state,
            archive_store=archive_store,
            token_counter=token_counter,
            plugin_state=state.get("plugin"),
            middleware_metrics=state.get("middleware_metrics"),
        )

    def _result(self, messages: Sequence[Any]) -> PluginHookResult:
        return PluginHookResult(
            messages=list(messages),
            metrics=self.metrics_dict(),
            lifecycle_state=self.export_state(),
        )
