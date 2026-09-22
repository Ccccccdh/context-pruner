"""Optional AutoGen AgentChat model-context adapter.

The authoritative AutoGen history remains untouched in ``_messages``.  Only
the view returned by ``get_messages`` is compressed.  Function-call requests
and their execution results are represented by protected placeholders during
compression and restored as the exact original AutoGen objects afterwards.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..middleware import ContextPrunerMiddleware
from ..plugin import ContextPluginConfig
from ..types import estimate_tokens
from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AgentCategory,
    HistoryVisibility,
    IntegrationLevel,
    ValidationLevel,
)

try:  # Keep the base package importable without the optional AutoGen extra.
    from autogen_core.model_context import ChatCompletionContext
    from autogen_core.models import (
        AssistantMessage,
        FunctionExecutionResultMessage,
        SystemMessage,
        UserMessage,
    )

    _AUTOGEN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in installations without extra
    ChatCompletionContext = object  # type: ignore[assignment,misc]
    AssistantMessage = FunctionExecutionResultMessage = SystemMessage = UserMessage = None
    _AUTOGEN_AVAILABLE = False


AUTOGEN_ADAPTER_STATE_VERSION = 1


@dataclass(frozen=True)
class ProtectedAutoGenGroup:
    """One opaque AutoGen unit that must survive compression losslessly."""

    group_id: str
    messages: tuple[Any, ...]
    token_count: int


class AutoGenContextPruner(ChatCompletionContext):  # type: ignore[misc]
    """A drop-in ``AssistantAgent(model_context=...)`` context implementation."""

    descriptor = AdapterDescriptor(
        adapter_id="autogen_agentchat",
        display_name="AutoGen AgentChat Model Context",
        category=AgentCategory.MULTI_AGENT,
        integration_level=IntegrationLevel.NATIVE,
        frameworks=("AutoGen AgentChat",),
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
            "this single-AssistantAgent adapter does not route team-level namespaces; use AutoGenTeamContextCoordinator",
        ),
    )

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        initial_messages: Sequence[Any] | None = None,
        task_state: str = "",
        fixed_reserved_tokens: int = 0,
        token_counter: Callable[[str], int] | None = None,
        middleware: ContextPrunerMiddleware | None = None,
    ) -> None:
        if not _AUTOGEN_AVAILABLE:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "AutoGen is not installed; install context-pruner[autogen]"
            )
        super().__init__(initial_messages=list(initial_messages or []))
        self.config = config or ContextPluginConfig()
        self.task_state = str(task_state)
        self.fixed_reserved_tokens = max(0, int(fixed_reserved_tokens))
        self.token_counter = token_counter or estimate_tokens
        self.middleware = middleware or ContextPrunerMiddleware(
            self.config,
            task_state=self.task_state,
            token_counter=self.token_counter,
        )
        self.get_messages_calls = 0
        self.compressed_calls = 0
        self.protected_group_count = 0
        self.protected_message_count = 0
        self.unmatched_call_count = 0
        self.opaque_reserved_tokens_total = 0
        self.group_restore_failure_count = 0
        self.last_skip_reason: str | None = None

    async def get_messages(self) -> list[Any]:
        """Return a compressed, type-correct model view of the full history."""
        original = list(self._messages)
        self.get_messages_calls += 1
        prepared, groups = _protect_tool_groups(original, self.token_counter)
        unmatched = _unmatched_call_count(original)
        placeholder_tokens = sum(
            self.token_counter(str(_placeholder(group)["content"])) for group in groups
        )
        opaque_tokens = sum(group.token_count for group in groups)
        opaque_reserve = max(0, opaque_tokens - placeholder_tokens)
        hook = self.middleware.before_model(
            prepared,
            task_state=self.task_state,
            reserved_tokens=self.fixed_reserved_tokens + opaque_reserve,
        )
        restored = _restore_tool_groups(hook.messages, groups)
        if restored is None:
            return self._fallback(original, "protected_autogen_group_restore_failed")
        try:
            typed = [_to_llm_message(message) for message in restored]
        except (TypeError, ValueError):
            return self._fallback(original, "compressed_message_conversion_failed")

        self.compressed_calls += 1
        self.protected_group_count += len(groups)
        self.protected_message_count += sum(len(group.messages) for group in groups)
        self.unmatched_call_count += unmatched
        self.opaque_reserved_tokens_total += opaque_reserve
        self.last_skip_reason = None
        return typed

    async def clear(self) -> None:
        await super().clear()
        self.middleware = ContextPrunerMiddleware(
            self.config,
            task_state=self.task_state,
            token_counter=self.token_counter,
        )
        self._restore_adapter_metrics({})

    async def save_state(self) -> Mapping[str, Any]:
        state = dict(await super().save_state())
        state["context_pruner"] = {
            "schema_version": AUTOGEN_ADAPTER_STATE_VERSION,
            "task_state": self.task_state,
            "fixed_reserved_tokens": self.fixed_reserved_tokens,
            "middleware": self.middleware.export_state(),
            "metrics": self._adapter_metrics(),
        }
        return state

    async def load_state(self, state: Mapping[str, Any]) -> None:
        await super().load_state({"messages": list(state.get("messages") or [])})
        adapter_state = dict(state.get("context_pruner") or {})
        if adapter_state:
            if int(adapter_state.get("schema_version", 0)) != AUTOGEN_ADAPTER_STATE_VERSION:
                raise ValueError("unsupported AutoGen adapter state version")
            self.task_state = str(adapter_state.get("task_state", self.task_state))
            self.fixed_reserved_tokens = max(
                0,
                int(adapter_state.get("fixed_reserved_tokens", self.fixed_reserved_tokens)),
            )
            self.middleware = ContextPrunerMiddleware.from_state(
                adapter_state.get("middleware"),
                config=self.config,
                task_state=self.task_state,
                token_counter=self.token_counter,
            )
            self._restore_adapter_metrics(adapter_state.get("metrics") or {})
        else:
            self.middleware = ContextPrunerMiddleware(
                self.config,
                task_state=self.task_state,
                token_counter=self.token_counter,
            )
            self._restore_adapter_metrics({})

    def metrics_dict(self) -> dict[str, Any]:
        metrics = self.middleware.metrics_dict()
        metrics.update(self._adapter_metrics())
        return metrics

    def _fallback(self, original: list[Any], reason: str) -> list[Any]:
        self.group_restore_failure_count += 1
        self.last_skip_reason = reason
        return original

    def _adapter_metrics(self) -> dict[str, Any]:
        return {
            "autogen_get_messages_calls": self.get_messages_calls,
            "autogen_compressed_calls": self.compressed_calls,
            "autogen_protected_group_count": self.protected_group_count,
            "autogen_protected_message_count": self.protected_message_count,
            "autogen_unmatched_call_count": self.unmatched_call_count,
            "autogen_opaque_reserved_tokens_total": self.opaque_reserved_tokens_total,
            "autogen_group_restore_failure_count": self.group_restore_failure_count,
            "autogen_last_skip_reason": self.last_skip_reason,
        }

    def _restore_adapter_metrics(self, metrics: Mapping[str, Any]) -> None:
        self.get_messages_calls = int(metrics.get("autogen_get_messages_calls", 0))
        self.compressed_calls = int(metrics.get("autogen_compressed_calls", 0))
        self.protected_group_count = int(
            metrics.get("autogen_protected_group_count", 0)
        )
        self.protected_message_count = int(
            metrics.get("autogen_protected_message_count", 0)
        )
        self.unmatched_call_count = int(metrics.get("autogen_unmatched_call_count", 0))
        self.opaque_reserved_tokens_total = int(
            metrics.get("autogen_opaque_reserved_tokens_total", 0)
        )
        self.group_restore_failure_count = int(
            metrics.get("autogen_group_restore_failure_count", 0)
        )
        reason = metrics.get("autogen_last_skip_reason")
        self.last_skip_reason = str(reason) if reason else None


def create_autogen_model_context(
    config: ContextPluginConfig | None = None,
    **kwargs: Any,
) -> AutoGenContextPruner:
    """Create a context suitable for ``AssistantAgent(model_context=...)``."""
    return AutoGenContextPruner(config, **kwargs)


def _protect_tool_groups(
    messages: Sequence[Any],
    token_counter: Callable[[str], int],
) -> tuple[list[Any], list[ProtectedAutoGenGroup]]:
    prepared: list[Any] = []
    groups: list[ProtectedAutoGenGroup] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if _is_scoped_context_message(message):
            group = _make_group([message], len(groups), token_counter)
            groups.append(group)
            prepared.append(_placeholder(group))
        elif _is_tool_call_message(message):
            group_messages = [message]
            if index + 1 < len(messages) and _is_tool_result_message(messages[index + 1]):
                group_messages.append(messages[index + 1])
                index += 1
            group = _make_group(group_messages, len(groups), token_counter)
            groups.append(group)
            prepared.append(_placeholder(group))
        elif _is_tool_result_message(message):
            group = _make_group([message], len(groups), token_counter)
            groups.append(group)
            prepared.append(_placeholder(group))
        else:
            prepared.append(message)
        index += 1
    return prepared, groups


def _make_group(
    messages: Sequence[Any],
    occurrence: int,
    token_counter: Callable[[str], int],
) -> ProtectedAutoGenGroup:
    encoded = json.dumps(
        [_jsonable(message) for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    identity = json.dumps(
        _jsonable(messages[0]),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return ProtectedAutoGenGroup(
        group_id=f"autogen-{digest}-{occurrence}",
        messages=tuple(messages),
        token_count=token_counter(encoded),
    )


def _restore_tool_groups(
    messages: Sequence[Any],
    groups: Sequence[ProtectedAutoGenGroup],
) -> list[Any] | None:
    by_id = {group.group_id: group for group in groups}
    seen: set[str] = set()
    restored: list[Any] = []
    for message in messages:
        group_id = _placeholder_group_id(message)
        if group_id is None:
            restored.append(message)
            continue
        group = by_id.get(group_id)
        if group is None or group_id in seen:
            return None
        restored.extend(group.messages)
        seen.add(group_id)
    if seen != set(by_id):
        return None
    return restored


def _placeholder(group: ProtectedAutoGenGroup) -> dict[str, str]:
    return {
        "role": "system",
        "content": f"[protected AutoGen group: {group.group_id}]",
        "_context_pruner_autogen_group": group.group_id,
    }


def _placeholder_group_id(message: Any) -> str | None:
    if isinstance(message, Mapping):
        value = message.get("_context_pruner_autogen_group")
        return str(value) if value else None
    return None


def _is_tool_call_message(message: Any) -> bool:
    if not _AUTOGEN_AVAILABLE or not isinstance(message, AssistantMessage):
        return False
    content = getattr(message, "content", None)
    return isinstance(content, list) and bool(content)


def _is_scoped_context_message(message: Any) -> bool:
    if not _AUTOGEN_AVAILABLE or not isinstance(message, UserMessage):
        return False
    content = getattr(message, "content", None)
    return isinstance(content, str) and content.startswith("[Context-Pruner scope=")


def _is_tool_result_message(message: Any) -> bool:
    return bool(_AUTOGEN_AVAILABLE and isinstance(message, FunctionExecutionResultMessage))


def _call_ids(message: Any) -> list[str]:
    if _is_tool_call_message(message):
        return [str(getattr(call, "id", "")) for call in message.content]
    if _is_tool_result_message(message):
        return [str(getattr(result, "call_id", "")) for result in message.content]
    return []


def _unmatched_call_count(messages: Sequence[Any]) -> int:
    calls: Counter[str] = Counter()
    results: Counter[str] = Counter()
    for message in messages:
        target = calls if _is_tool_call_message(message) else results
        if not (_is_tool_call_message(message) or _is_tool_result_message(message)):
            continue
        for call_id in _call_ids(message):
            if call_id:
                target[call_id] += 1
    return sum(abs(calls[key] - results[key]) for key in calls.keys() | results.keys())


def _to_llm_message(message: Any) -> Any:
    if isinstance(
        message,
        (SystemMessage, UserMessage, AssistantMessage, FunctionExecutionResultMessage),
    ):
        return message
    if not isinstance(message, Mapping):
        raise TypeError("unsupported compressed AutoGen message")
    role = str(message.get("role") or "user").lower()
    content = str(message.get("content") or "")
    if role in {"system", "developer"}:
        return SystemMessage(content=content)
    if role == "assistant":
        return AssistantMessage(
            content=content,
            source=str(message.get("source") or "assistant"),
        )
    return UserMessage(
        content=content,
        source=str(message.get("source") or "user"),
    )


def _jsonable(message: Any) -> Any:
    model_dump = getattr(message, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    if isinstance(message, Mapping):
        return dict(message)
    return str(message)
