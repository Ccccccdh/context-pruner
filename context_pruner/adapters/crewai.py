"""CrewAI 1.x adapter built on the public model/tool hook surface.

CrewAI exposes its executor message list by reference and requires hooks to
mutate that list in place.  This adapter therefore keeps an authoritative
shadow while a compressed model view is installed, restores it after textual
model calls, and reconciles appended native tool messages on the next call when
CrewAI deliberately skips post-model hooks for structured tool payloads.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence

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

try:  # Keep the base package importable without the isolated CrewAI extra.
    from crewai.hooks import (
        register_after_tool_call_hook,
        register_after_llm_call_hook,
        register_before_llm_call_hook,
        unregister_after_tool_call_hook,
        unregister_after_llm_call_hook,
        unregister_before_llm_call_hook,
    )

    _CREWAI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in the main multi-framework venv
    register_after_llm_call_hook = register_before_llm_call_hook = None
    unregister_after_llm_call_hook = unregister_before_llm_call_hook = None
    register_after_tool_call_hook = unregister_after_tool_call_hook = None
    _CREWAI_AVAILABLE = False


CREWAI_ADAPTER_STATE_VERSION = 1


@dataclass(frozen=True)
class ProtectedCrewAIGroup:
    """One structured CrewAI/OpenAI message unit restored by identity."""

    group_id: str
    messages: tuple[Any, ...]
    token_count: int


@dataclass
class CrewAIAdapterMetrics:
    before_llm_calls: int = 0
    after_llm_calls: int = 0
    after_tool_calls: int = 0
    compressed_calls: int = 0
    filtered_calls: int = 0
    pending_reconciliations: int = 0
    authoritative_resync_count: int = 0
    protected_group_count: int = 0
    protected_message_count: int = 0
    unmatched_call_count: int = 0
    opaque_reserved_tokens_total: int = 0
    group_restore_failure_count: int = 0
    active_pending_view_count: int = 0


@dataclass
class _PendingView:
    messages: list[Any]
    authoritative: list[Any]
    served: list[Any]


class CrewAIContextAdapter:
    """Install scoped CrewAI hooks that expose only a compressed model view.

    CrewAI hook registration is process-global, so callers should use
    ``with adapter.attached():`` and optionally filter by ``agent_roles`` or an
    exact ``crew`` object.  One adapter intentionally represents one lifecycle
    namespace; create one adapter per Agent when private team histories must be
    separated.
    """

    descriptor = AdapterDescriptor(
        adapter_id="crewai",
        display_name="CrewAI 1.x Lifecycle Hook Adapter",
        category=AgentCategory.MULTI_AGENT,
        integration_level=IntegrationLevel.NATIVE,
        frameworks=("CrewAI",),
        capabilities=AdapterCapabilities(
            history_visibility=HistoryVisibility.CONDITIONAL,
            pre_model_transform=True,
            model_output_observation=True,
            tool_event_observation=True,
            state_persistence=True,
            lossless_tool_groups=True,
            multi_agent_namespaces=False,
            server_managed_history_safe=False,
            streaming_safe=False,
        ),
        validation_level=ValidationLevel.LOCAL_RUNNER,
        limitations=(
            "the hook sees only the current executor's composed messages",
            "CrewAI hook registries are global; attach in a bounded context and filter targets",
            "use one adapter per Agent for private histories; shared/handoff routing is not provided",
            "structured native tool responses skip CrewAI post-model hooks and are reconciled on the next pre-model hook",
            "model input outside context.messages must be budgeted by the host through fixed_reserved_tokens",
            "streaming execution has not been validated",
            "CrewAI 1.15.x conflicts with the current OpenAI Agents and AutoGen dependency set, so use an isolated environment",
        ),
    )

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        task_state: str = "",
        fixed_reserved_tokens: int = 0,
        token_counter: Callable[[str], int] | None = None,
        middleware: ContextPrunerMiddleware | None = None,
        agent_roles: Sequence[str] | None = None,
        crew: Any | None = None,
    ) -> None:
        if not _CREWAI_AVAILABLE:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "CrewAI is not installed; use a dedicated environment and install context-pruner[crewai]"
            )
        self.config = config or ContextPluginConfig()
        self.task_state = str(task_state)
        self.fixed_reserved_tokens = max(0, int(fixed_reserved_tokens))
        self.token_counter = token_counter or estimate_tokens
        self.middleware = middleware or ContextPrunerMiddleware(
            self.config,
            task_state=self.task_state,
            token_counter=self.token_counter,
        )
        self.agent_roles = frozenset(str(role) for role in (agent_roles or ()))
        self.crew = crew
        self.metrics = CrewAIAdapterMetrics()
        self._pending: dict[int, _PendingView] = {}
        self._registered = False
        self._lock = threading.RLock()

    def before_llm_call(self, context: Any) -> bool | None:
        """Compress ``context.messages`` in place immediately before the model."""
        if not self._matches(context):
            self.metrics.filtered_calls += 1
            return None
        messages = getattr(context, "messages", None)
        if not isinstance(messages, list):
            return None
        with self._lock:
            self.metrics.before_llm_calls += 1
            key = self._execution_key(context, messages)
            authoritative = self._authoritative_for(key, messages)
            prepared, groups = _protect_tool_groups(authoritative, self.token_counter)
            placeholder_tokens = sum(
                self.token_counter(str(_placeholder(group)["content"]))
                for group in groups
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
                self.metrics.group_restore_failure_count += 1
                restored = list(authoritative)
            elif restored != list(authoritative):
                self.metrics.compressed_calls += 1
            messages[:] = restored
            self._pending[key] = _PendingView(
                messages=messages,
                authoritative=list(authoritative),
                served=list(restored),
            )
            self.metrics.protected_group_count += len(groups)
            self.metrics.protected_message_count += sum(
                len(group.messages) for group in groups
            )
            self.metrics.unmatched_call_count += _unmatched_call_count(authoritative)
            self.metrics.opaque_reserved_tokens_total += opaque_reserve
            self.metrics.active_pending_view_count = len(self._pending)
        return None

    def after_llm_call(self, context: Any) -> str | None:
        """Observe the response and restore the executor's authoritative history."""
        if not self._matches(context):
            return None
        messages = getattr(context, "messages", None)
        if not isinstance(messages, list):
            return None
        with self._lock:
            self.metrics.after_llm_calls += 1
            key = self._execution_key(context, messages)
            pending = self._pending.pop(key, None)
            if pending is not None:
                authoritative, resynced = _reconcile_pending(pending, messages)
                if resynced:
                    self.metrics.authoritative_resync_count += 1
                messages[:] = authoritative
            response = getattr(context, "response", None)
            if response is not None:
                self.middleware.after_model(str(response))
            self.metrics.active_pending_view_count = len(self._pending)
        return None

    def after_tool_call(self, context: Any) -> str | None:
        """Record a CrewAI tool result without changing the tool's return value."""
        if not self._matches(context):
            return None
        with self._lock:
            self.metrics.after_tool_calls += 1
            self.middleware.after_tool(
                {
                    "role": "tool",
                    "content": str(getattr(context, "tool_result", "") or ""),
                    "name": str(getattr(context, "tool_name", "") or ""),
                    "input": dict(getattr(context, "tool_input", {}) or {}),
                }
            )
        return None

    def register(self) -> None:
        """Register bounded global hooks once."""
        with self._lock:
            if self._registered:
                return
            register_before_llm_call_hook(self.before_llm_call)  # type: ignore[misc]
            register_after_llm_call_hook(self.after_llm_call)  # type: ignore[misc]
            register_after_tool_call_hook(self.after_tool_call)  # type: ignore[misc]
            self._registered = True

    def unregister(self) -> None:
        """Restore pending views and remove only this adapter's hooks."""
        with self._lock:
            self.restore_authoritative_views()
            if not self._registered:
                return
            unregister_before_llm_call_hook(self.before_llm_call)  # type: ignore[misc]
            unregister_after_llm_call_hook(self.after_llm_call)  # type: ignore[misc]
            unregister_after_tool_call_hook(self.after_tool_call)  # type: ignore[misc]
            self._registered = False

    @contextmanager
    def attached(self) -> Iterator["CrewAIContextAdapter"]:
        """Register for one bounded CrewAI execution and always clean up."""
        self.register()
        try:
            yield self
        finally:
            self.unregister()

    def restore_authoritative_views(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                authoritative, resynced = _reconcile_pending(
                    pending,
                    pending.messages,
                )
                if resynced:
                    self.metrics.authoritative_resync_count += 1
                pending.messages[:] = authoritative
            self._pending.clear()
            self.metrics.active_pending_view_count = 0

    def on_error(self, error: BaseException | str, *, query: str = "") -> None:
        with self._lock:
            self.restore_authoritative_views()
            self.middleware.on_error(error, query=query, recover=True)

    def export_state(self) -> dict[str, Any]:
        """Export lifecycle state between executions, never an active model view."""
        with self._lock:
            self.restore_authoritative_views()
            return {
                "schema_version": CREWAI_ADAPTER_STATE_VERSION,
                "task_state": self.task_state,
                "fixed_reserved_tokens": self.fixed_reserved_tokens,
                "middleware": self.middleware.export_state(),
                "metrics": asdict(self.metrics),
            }

    def restore_state(self, state: Mapping[str, Any]) -> None:
        with self._lock:
            if int(state.get("schema_version", 0)) != CREWAI_ADAPTER_STATE_VERSION:
                raise ValueError("unsupported CrewAI adapter state version")
            self.restore_authoritative_views()
            self.task_state = str(state.get("task_state", self.task_state))
            self.fixed_reserved_tokens = max(
                0,
                int(state.get("fixed_reserved_tokens", self.fixed_reserved_tokens)),
            )
            self.middleware = ContextPrunerMiddleware.from_state(
                state.get("middleware"),
                config=self.config,
                task_state=self.task_state,
                token_counter=self.token_counter,
            )
            known = CrewAIAdapterMetrics.__dataclass_fields__
            raw = dict(state.get("metrics") or {})
            raw["active_pending_view_count"] = 0
            self.metrics = CrewAIAdapterMetrics(
                **{key: int(value) for key, value in raw.items() if key in known}
            )

    def metrics_dict(self) -> dict[str, Any]:
        data = self.middleware.metrics_dict()
        data.update({f"crewai_{key}": value for key, value in asdict(self.metrics).items()})
        data["crewai_registered"] = self._registered
        return data

    def finalize(self, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self.restore_authoritative_views()
            self.middleware.finalize(metadata)
            return self.metrics_dict()

    def _matches(self, context: Any) -> bool:
        if self.crew is not None and getattr(context, "crew", None) is not self.crew:
            return False
        if self.agent_roles:
            role = str(getattr(getattr(context, "agent", None), "role", ""))
            if role not in self.agent_roles:
                return False
        return True

    @staticmethod
    def _execution_key(context: Any, messages: list[Any]) -> int:
        executor = getattr(context, "executor", None)
        return id(executor) if executor is not None else id(messages)

    def _authoritative_for(self, key: int, current: list[Any]) -> list[Any]:
        pending = self._pending.pop(key, None)
        if pending is None:
            return list(current)
        self.metrics.pending_reconciliations += 1
        authoritative, resynced = _reconcile_pending(pending, current)
        if resynced:
            self.metrics.authoritative_resync_count += 1
        return authoritative


def create_crewai_context_adapter(
    config: ContextPluginConfig | None = None,
    **kwargs: Any,
) -> CrewAIContextAdapter:
    return CrewAIContextAdapter(config, **kwargs)


def _reconcile_pending(
    pending: _PendingView,
    current: Sequence[Any],
) -> tuple[list[Any], bool]:
    served = pending.served
    if len(current) >= len(served) and all(
        current[index] is served[index] or current[index] == served[index]
        for index in range(len(served))
    ):
        return [*pending.authoritative, *list(current[len(served) :])], False
    return list(current), True


def _protect_tool_groups(
    messages: Sequence[Any],
    token_counter: Callable[[str], int],
) -> tuple[list[Any], list[ProtectedCrewAIGroup]]:
    prepared: list[Any] = []
    groups: list[ProtectedCrewAIGroup] = []
    occurrences: Counter[str] = Counter()
    index = 0
    while index < len(messages):
        message = messages[index]
        if not _is_structured_message(message) and not _is_text_tool_trace(message):
            prepared.append(message)
            index += 1
            continue
        grouped = [message]
        call_ids = set(_message_call_ids(message))
        index += 1
        while index < len(messages):
            candidate = messages[index]
            result_id = _result_call_id(candidate)
            if result_id and (not call_ids or result_id in call_ids):
                grouped.append(candidate)
                index += 1
                continue
            break
        encoded = json.dumps(grouped, ensure_ascii=False, sort_keys=True, default=str)
        identity = json.dumps(grouped[0], ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        occurrence = occurrences[digest]
        occurrences[digest] += 1
        group = ProtectedCrewAIGroup(
            group_id=f"crewai-{digest}-{occurrence}",
            messages=tuple(grouped),
            token_count=token_counter(encoded),
        )
        groups.append(group)
        prepared.append(_placeholder(group))
    return prepared, groups


def _restore_tool_groups(
    messages: Sequence[Any],
    groups: Sequence[ProtectedCrewAIGroup],
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


def _placeholder(group: ProtectedCrewAIGroup) -> dict[str, str]:
    return {
        "role": "system",
        "content": f"[protected CrewAI tool group: {group.group_id}]",
        "_context_pruner_crewai_group": group.group_id,
    }


def _placeholder_group_id(message: Any) -> str | None:
    if isinstance(message, Mapping):
        value = message.get("_context_pruner_crewai_group")
    else:
        value = getattr(message, "_context_pruner_crewai_group", None)
    return str(value) if value else None


def _is_structured_message(message: Any) -> bool:
    if not isinstance(message, Mapping):
        return True
    content = message.get("content", "")
    return bool(
        message.get("tool_calls")
        or message.get("tool_call_id")
        or message.get("function_call")
        or not isinstance(content, str)
    )


def _is_text_tool_trace(message: Any) -> bool:
    """Detect CrewAI's merged text-ReAct action/observation message.

    CrewAI stores the actual tool observation inside the assistant text message
    that also contains ``Action`` and ``Action Input``.  Splitting or pruning
    that message makes the model repeat an already executed tool, so it must be
    treated as the text equivalent of a native call/result group.
    """

    if not isinstance(message, Mapping):
        return False
    content = message.get("content", "")
    if not isinstance(content, str):
        return False
    lowered = content.lower()
    return (
        str(message.get("role", "")).lower() == "assistant"
        and "action:" in lowered
        and "action input:" in lowered
        and "observation:" in lowered
    )


def _message_call_ids(message: Any) -> tuple[str, ...]:
    if not isinstance(message, Mapping):
        return ()
    calls = message.get("tool_calls") or ()
    return tuple(
        str(call.get("id"))
        for call in calls
        if isinstance(call, Mapping) and call.get("id")
    )


def _result_call_id(message: Any) -> str:
    if not isinstance(message, Mapping):
        return ""
    return str(message.get("tool_call_id") or "")


def _unmatched_call_count(messages: Sequence[Any]) -> int:
    calls: Counter[str] = Counter()
    results: Counter[str] = Counter()
    for message in messages:
        calls.update(_message_call_ids(message))
        result_id = _result_call_id(message)
        if result_id:
            results[result_id] += 1
    return sum(abs(calls[key] - results[key]) for key in calls.keys() | results.keys())
