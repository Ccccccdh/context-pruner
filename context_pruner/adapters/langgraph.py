"""LangGraph adapter with no mandatory LangGraph import at package import time."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import wraps
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..plugin import ContextLifecyclePlugin, ContextPluginConfig
from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AgentCategory,
    HistoryVisibility,
    IntegrationLevel,
    ValidationLevel,
)


@dataclass(frozen=True)
class LangGraphAdapterConfig:
    messages_key: str = "messages"
    task_key: str = "task"
    context_messages_key: str = "context_messages"
    plugin_state_key: str = "context_pruner_state"
    metrics_key: str = "context_pruner_metrics"
    error_key: str = "context_pruner_error"


class LangGraphContextAdapter:
    """Bridge ContextLifecyclePlugin to StateGraph nodes.

    State fields other than ``messages`` use LangGraph's default overwrite
    semantics.  The adapter never overwrites the graph's authoritative message
    channel; wrapped model nodes receive a shallow state copy whose ``messages``
    value is the compressed model view.
    """

    descriptor = AdapterDescriptor(
        adapter_id="langgraph",
        display_name="LangGraph StateGraph Adapter",
        category=AgentCategory.WORKFLOW_GRAPH,
        integration_level=IntegrationLevel.NATIVE,
        frameworks=("LangGraph", "LangChain agents backed by StateGraph"),
        capabilities=AdapterCapabilities(
            history_visibility=HistoryVisibility.FULL,
            pre_model_transform=True,
            model_output_observation=True,
            tool_event_observation=True,
            state_persistence=True,
            lossless_tool_groups=True,
            multi_agent_namespaces=False,
            server_managed_history_safe=False,
            streaming_safe=False,
        ),
        validation_level=ValidationLevel.REAL_API,
        limitations=(
            "the graph must expose its message state and model/tool nodes",
        ),
    )

    def __init__(
        self,
        plugin_config: ContextPluginConfig | None = None,
        adapter_config: LangGraphAdapterConfig | None = None,
        token_counter: Callable[[str], int] | None = None,
        budget_resolver: Callable[[Mapping[str, Any]], int] | None = None,
    ) -> None:
        self.plugin_config = plugin_config or ContextPluginConfig()
        self.config = adapter_config or LangGraphAdapterConfig()
        self.token_counter = token_counter
        self.budget_resolver = budget_resolver

    def before_model_node(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plugin = self._plugin(state)
        result = plugin.before_model(
            list(state.get(self.config.messages_key) or []),
            task_state=str(state.get(self.config.task_key) or ""),
            budget=self._budget_for_state(state),
        )
        return self._state_update(plugin, result.messages)

    def after_model_node(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plugin = self._plugin(state)
        messages = list(state.get(self.config.messages_key) or [])
        if messages:
            plugin.after_model(messages[-1])
        return self._state_update(plugin)

    def after_tool_node(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plugin = self._plugin(state)
        messages = list(state.get(self.config.messages_key) or [])
        if messages:
            plugin.after_tool(messages[-1])
        return self._state_update(plugin)

    def on_error_node(
        self,
        state: Mapping[str, Any],
        *,
        recover: bool = True,
    ) -> dict[str, Any]:
        """Record a graph error and recover only when the caller needs evidence.

        Pure output-format errors do not imply missing context.  Frameworks can
        therefore pass ``recover=False`` to avoid injecting unrelated archived
        evidence and increasing the next prompt.
        """
        plugin = self._plugin(state)
        error = state.get(self.config.error_key, "LangGraph node error")
        result = plugin.on_error(
            error,
            query=str(state.get(self.config.task_key) or ""),
            recover=recover,
        )
        return self._state_update(plugin, result.messages)

    def finalize_node(self, state: Mapping[str, Any]) -> dict[str, Any]:
        plugin = self._plugin(state)
        plugin.finalize()
        return self._state_update(plugin)

    def model_messages(self, state: Mapping[str, Any]) -> list[Any]:
        return list(
            state.get(self.config.context_messages_key)
            or state.get(self.config.messages_key)
            or []
        )

    def wrap_model_node(self, node: Callable[..., Mapping[str, Any]]):
        """Wrap a synchronous model node and persist plugin state atomically."""
        if inspect.iscoroutinefunction(node):
            raise TypeError("异步模型节点请使用 awrap_model_node")

        @wraps(node)
        def wrapped(state: Mapping[str, Any], *args, **kwargs):
            plugin = self._plugin(state)
            hook = plugin.before_model(
                list(state.get(self.config.messages_key) or []),
                task_state=str(state.get(self.config.task_key) or ""),
                budget=self._budget_for_state(state),
            )
            prepared = dict(state)
            prepared[self.config.messages_key] = hook.messages
            prepared[self.config.context_messages_key] = hook.messages
            prepared[self.config.plugin_state_key] = hook.lifecycle_state
            prepared[self.config.metrics_key] = hook.metrics
            try:
                result = node(prepared, *args, **kwargs)
            except Exception as error:
                plugin.on_error(
                    error,
                    query=str(state.get(self.config.task_key) or ""),
                )
                raise
            output = _mapping_result(result)
            plugin.after_model(output.get(self.config.messages_key))
            output.update(self._state_update(plugin))
            return output

        return wrapped

    def wrap_tool_node(self, node: Callable[..., Mapping[str, Any]]):
        """Wrap a synchronous tool node and record tool results."""
        if inspect.iscoroutinefunction(node):
            raise TypeError("异步工具节点请使用 awrap_tool_node")

        @wraps(node)
        def wrapped(state: Mapping[str, Any], *args, **kwargs):
            plugin = self._plugin(state)
            try:
                result = node(state, *args, **kwargs)
            except Exception as error:
                plugin.on_error(
                    error,
                    query=str(state.get(self.config.task_key) or ""),
                )
                raise
            output = _mapping_result(result)
            plugin.after_tool(output.get(self.config.messages_key))
            output.update(self._state_update(plugin))
            return output

        return wrapped

    def awrap_model_node(self, node: Callable[..., Awaitable[Mapping[str, Any]]]):
        @wraps(node)
        async def wrapped(state: Mapping[str, Any], *args, **kwargs):
            plugin = self._plugin(state)
            hook = plugin.before_model(
                list(state.get(self.config.messages_key) or []),
                task_state=str(state.get(self.config.task_key) or ""),
                budget=self._budget_for_state(state),
            )
            prepared = dict(state)
            prepared[self.config.messages_key] = hook.messages
            prepared[self.config.context_messages_key] = hook.messages
            prepared[self.config.plugin_state_key] = hook.lifecycle_state
            prepared[self.config.metrics_key] = hook.metrics
            try:
                result = await node(prepared, *args, **kwargs)
            except Exception as error:
                plugin.on_error(error, query=str(state.get(self.config.task_key) or ""))
                raise
            output = _mapping_result(result)
            plugin.after_model(output.get(self.config.messages_key))
            output.update(self._state_update(plugin))
            return output

        return wrapped

    def awrap_tool_node(self, node: Callable[..., Awaitable[Mapping[str, Any]]]):
        @wraps(node)
        async def wrapped(state: Mapping[str, Any], *args, **kwargs):
            plugin = self._plugin(state)
            try:
                result = await node(state, *args, **kwargs)
            except Exception as error:
                plugin.on_error(error, query=str(state.get(self.config.task_key) or ""))
                raise
            output = _mapping_result(result)
            plugin.after_tool(output.get(self.config.messages_key))
            output.update(self._state_update(plugin))
            return output

        return wrapped

    def _plugin(self, state: Mapping[str, Any]) -> ContextLifecyclePlugin:
        return ContextLifecyclePlugin.from_state(
            state.get(self.config.plugin_state_key),
            config=self.plugin_config,
            task_state=str(state.get(self.config.task_key) or ""),
            token_counter=self.token_counter,
        )

    def _budget_for_state(self, state: Mapping[str, Any]):
        if self.budget_resolver is None:
            return self.plugin_config.budget
        reserved = max(0, int(self.budget_resolver(state)))
        return self.plugin_config.budget.reserve(reserved)

    def _state_update(
        self,
        plugin: ContextLifecyclePlugin,
        context_messages: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        update = {
            self.config.plugin_state_key: plugin.export_state(),
            self.config.metrics_key: plugin.metrics_dict(),
        }
        if context_messages is not None:
            update[self.config.context_messages_key] = list(context_messages)
        return update


def _mapping_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if not isinstance(result, Mapping):
        raise TypeError("被适配的 LangGraph 节点必须返回 mapping 或 None")
    return dict(result)
