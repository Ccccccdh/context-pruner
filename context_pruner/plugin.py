"""Framework-neutral lifecycle plugin built on the frozen v0.3.11 core."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .archive import ArchiveStore
from .lifecycle import ContextLifecycleManager
from .pipeline import ContextPruner
from .pipeline_v1 import ContextPrunerV1
from .types import (
    BudgetPressure,
    CompressionAction,
    ContextBudget,
    ContextEventKind,
    ContextSnapshot,
    ContextStatus,
    estimate_tokens,
)


PLUGIN_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ContextPluginConfig:
    """Portable configuration shared by framework adapters.

    ``enabled=False`` is the baseline switch.  The default budget is deliberately
    conservative; production adapters should set it from the target model's usable
    context window after reserving space for the response and tool schemas.
    """

    enabled: bool = True
    method: str = "pruner_v1"
    budget: ContextBudget = field(
        default_factory=lambda: ContextBudget(6_000, 8_000, 5_000)
    )
    recovery_limit: int = 3
    recovery_ttl_events: int = 4
    capture_checkpoints: bool = True

    def __post_init__(self) -> None:
        if self.method not in {"none", "pruner_v0", "pruner_v1"}:
            raise ValueError(f"不支持的插件方法：{self.method}")
        if self.recovery_limit <= 0:
            raise ValueError("recovery_limit 必须大于 0")
        if self.recovery_ttl_events <= 0:
            raise ValueError("recovery_ttl_events 必须大于 0")

    @property
    def active(self) -> bool:
        return self.enabled and self.method != "none"


@dataclass
class ContextPluginMetrics:
    before_model_calls: int = 0
    full_context_tokens_total: int = 0
    served_context_tokens_total: int = 0
    gross_input_tokens_saved: int = 0
    peak_full_context_tokens: int = 0
    peak_served_context_tokens: int = 0
    compression_count: int = 0
    recovery_count: int = 0
    budget_pressure_count: int = 0
    budget_violation_count: int = 0
    resync_count: int = 0
    stale_evidence_filtered_count: int = 0
    workspace_mutation_epoch: int = 0
    last_full_context_tokens: int = 0
    last_served_context_tokens: int = 0


@dataclass
class PluginHookResult:
    messages: list[Any]
    metrics: dict[str, Any]
    lifecycle_state: dict[str, Any]


class ContextLifecyclePlugin:
    """A small, serializable plugin facade for Agent framework integrations.

    The framework keeps the authoritative, append-only message history.  This
    plugin mirrors it as immutable lifecycle events, renders a compressed model
    view before each call, and exports all runtime state as JSON-compatible data.
    No framework-specific message class is imported here.
    """

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        task_state: str = "",
        archive_store: ArchiveStore | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.config = config or ContextPluginConfig()
        self.task_state = str(task_state)
        self.archive_store = archive_store
        self.token_counter = token_counter or estimate_tokens
        self.manager = self._new_manager()
        self.observed_message_keys: list[str] = []
        self.metrics = ContextPluginMetrics()
        self._finalized = False

    def before_model(
        self,
        messages: Sequence[Any],
        *,
        task_state: str | None = None,
        budget: ContextBudget | None = None,
    ) -> PluginHookResult:
        """Synchronize full history and return the model-facing context view."""
        raw_messages = list(messages)
        if task_state is not None:
            self.task_state = str(task_state)
            self.manager.task_state = self.task_state
        self._synchronize(raw_messages)

        full_tokens = _messages_tokens(raw_messages, self.token_counter)
        if self.config.active:
            snapshot = self.manager.compress(
                reason="framework_before_model",
                budget=budget or self.config.budget,
            )
            served = self._render_snapshot(snapshot, raw_messages)
            for item in snapshot.chunks:
                if not item.metadata.get("structured_memory"):
                    continue
                self.metrics.stale_evidence_filtered_count = max(
                    self.metrics.stale_evidence_filtered_count,
                    int(item.metadata.get("stale_evidence_filtered_count", 0)),
                )
                self.metrics.workspace_mutation_epoch = max(
                    self.metrics.workspace_mutation_epoch,
                    int(item.metadata.get("workspace_mutation_epoch", 0)),
                )
            self.metrics.compression_count += 1
            if snapshot.budget_pressure != BudgetPressure.NORMAL:
                self.metrics.budget_pressure_count += 1
            if snapshot.budget_exceeded:
                self.metrics.budget_violation_count += 1
        else:
            snapshot = self.manager.snapshot()
            served = raw_messages

        served_tokens = _messages_tokens(served, self.token_counter)
        self.metrics.before_model_calls += 1
        self.metrics.full_context_tokens_total += full_tokens
        self.metrics.served_context_tokens_total += served_tokens
        self.metrics.gross_input_tokens_saved += full_tokens - served_tokens
        self.metrics.peak_full_context_tokens = max(
            self.metrics.peak_full_context_tokens,
            full_tokens,
        )
        self.metrics.peak_served_context_tokens = max(
            self.metrics.peak_served_context_tokens,
            served_tokens,
        )
        self.metrics.last_full_context_tokens = full_tokens
        self.metrics.last_served_context_tokens = served_tokens
        self.manager.record_boundary(
            ContextEventKind.MODEL_INPUT,
            {
                "adapter": "context_lifecycle_plugin",
                "full_context_tokens": full_tokens,
                "served_context_tokens": served_tokens,
                "enabled": self.config.active,
            },
        )
        return self._result(served)

    def after_model(self, output: Any | Sequence[Any]) -> PluginHookResult:
        """Record one or more messages produced by a model node."""
        messages = _coerce_message_sequence(output)
        self._append_new(messages, forced_kind=ContextEventKind.MODEL_OUTPUT)
        return self._result(messages)

    def after_tool(self, output: Any | Sequence[Any]) -> PluginHookResult:
        """Record one or more messages produced by a tool node."""
        messages = _coerce_message_sequence(output)
        self._append_new(messages, forced_kind=ContextEventKind.TOOL_RESULT)
        return self._result(messages)

    def on_error(
        self,
        error: BaseException | str,
        *,
        query: str = "",
        recover: bool = True,
    ) -> PluginHookResult:
        """Record a framework error and optionally activate selective recovery."""
        self.manager.record_boundary(
            ContextEventKind.PARSE_ERROR,
            {"error_type": type(error).__name__, "error": str(error)},
        )
        if recover and self.config.active:
            before = self.manager.recovery_count
            self.manager.recover(
                reason="framework_error",
                query=query or self.task_state,
                limit=self.config.recovery_limit,
            )
            self.metrics.recovery_count += self.manager.recovery_count - before
        snapshot = self.manager.snapshot()
        return self._result(self._render_snapshot(snapshot, []))

    def finalize(self, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self._finalized:
            self.manager.record_boundary(
                ContextEventKind.TASK_FINISHED,
                {"adapter": "context_lifecycle_plugin", **dict(metadata or {})},
            )
            self._finalized = True
        return self.metrics_dict()

    def metrics_dict(self) -> dict[str, Any]:
        data = asdict(self.metrics)
        full = self.metrics.full_context_tokens_total
        data.update(
            {
                "enabled": self.config.active,
                "method": self.config.method,
                "gross_input_savings_rate": (
                    self.metrics.gross_input_tokens_saved / full if full else 0.0
                ),
                "archive_count": self.manager.archive_count,
                "lifecycle_recovery_count": self.manager.recovery_count,
                "checkpoint_count": self.manager.checkpoint_count,
                "lifecycle_event_count": len(self.manager.records),
                "runtime_event_count": len(self.manager.events),
            }
        )
        return data

    def export_state(self) -> dict[str, Any]:
        return {
            "schema_version": PLUGIN_STATE_SCHEMA_VERSION,
            "task_state": self.task_state,
            "observed_message_keys": list(self.observed_message_keys),
            "metrics": asdict(self.metrics),
            "finalized": self._finalized,
            "manager": self.manager.export_state(include_archive=True),
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
    ) -> "ContextLifecyclePlugin":
        if not state:
            return cls(
                config,
                task_state=task_state,
                archive_store=archive_store,
                token_counter=token_counter,
            )
        if int(state.get("schema_version", 0)) != PLUGIN_STATE_SCHEMA_VERSION:
            raise ValueError("不支持的插件状态版本")
        plugin = cls(
            config,
            task_state=str(state.get("task_state", task_state)),
            archive_store=archive_store,
            token_counter=token_counter,
        )
        plugin.manager = ContextLifecycleManager.from_state(
            dict(state.get("manager") or {}),
            pruner=plugin._make_pruner(),
            archive_store=archive_store,
            recovery_limit=plugin.config.recovery_limit,
            recovery_ttl_events=plugin.config.recovery_ttl_events,
        )
        plugin.observed_message_keys = [
            str(value) for value in state.get("observed_message_keys", [])
        ]
        known = {item.name for item in ContextPluginMetrics.__dataclass_fields__.values()}
        metrics = {
            key: int(value)
            for key, value in dict(state.get("metrics") or {}).items()
            if key in known
        }
        plugin.metrics = ContextPluginMetrics(**metrics)
        plugin._finalized = bool(state.get("finalized", False))
        return plugin

    def _new_manager(self) -> ContextLifecycleManager:
        return ContextLifecycleManager(
            task_state=self.task_state,
            pruner=self._make_pruner(),
            archive_store=self.archive_store,
            recovery_limit=self.config.recovery_limit,
            recovery_ttl_events=self.config.recovery_ttl_events,
            capture_checkpoints=self.config.capture_checkpoints,
        )

    def _make_pruner(self):
        if self.config.method == "pruner_v1":
            return ContextPrunerV1()
        return ContextPruner()

    def _synchronize(self, messages: list[Any]) -> None:
        current_keys = [_message_key(message) for message in messages]
        observed = self.observed_message_keys
        if current_keys[: len(observed)] == observed:
            self._append_new(messages[len(observed) :])
            return

        # LangGraph's add_messages normally keeps an append-only prefix.  A human
        # edit or state rewind can replace older IDs; rebuilding avoids silently
        # attaching provenance to the wrong message.
        self.manager = self._new_manager()
        self.observed_message_keys = []
        self.metrics.resync_count += 1
        self._append_new(messages)

    def _append_new(
        self,
        messages: Iterable[Any],
        *,
        forced_kind: ContextEventKind | None = None,
    ) -> None:
        for message in messages:
            key = _message_key(message)
            turn = message_to_turn(message)
            kind = forced_kind or _event_kind(turn)
            self.manager.observe_turn(
                turn,
                task_state=self.task_state,
                kind=kind,
                metadata={"framework_message_key": key, "adapter": "generic"},
            )
            self.observed_message_keys.append(key)

    def _render_snapshot(
        self,
        snapshot: ContextSnapshot,
        raw_messages: Sequence[Any],
    ) -> list[Any]:
        original_by_event: dict[str, Any] = {}
        for event, message in zip(self.manager.context_events, raw_messages):
            original_by_event[event.event_id] = message

        rendered: list[Any] = []
        for item in snapshot.chunks:
            # Archive references are lifecycle provenance, not useful model input.
            # Keeping dozens of ``[archived:event]`` markers can make smaller
            # models echo a marker as the answer. The archive and source IDs stay
            # available in lifecycle state and can still be selectively recovered.
            if item.action == CompressionAction.ARCHIVE or item.metadata.get(
                "archive_reference"
            ):
                continue
            if (
                item.action == CompressionAction.KEEP
                and item.status == ContextStatus.ACTIVE
                and len(item.source_event_ids) == 1
                and item.source_event_ids[0] in original_by_event
            ):
                rendered.append(original_by_event[item.source_event_ids[0]])
                continue
            role = _normalize_role(item.role)
            content = item.text
            if role in {"tool", "function"}:
                role = "user"
                content = f"[历史工具上下文] {content}"
            rendered.append({"role": role, "content": content})
        return rendered

    def _result(self, messages: list[Any]) -> PluginHookResult:
        return PluginHookResult(
            messages=list(messages),
            metrics=self.metrics_dict(),
            lifecycle_state=self.export_state(),
        )


def message_to_turn(message: Any) -> dict[str, str]:
    """Convert OpenAI dictionaries or LangChain messages without importing either."""
    if isinstance(message, Mapping):
        role = message.get("role") or message.get("type") or "user"
        content = message.get("content", "")
        tool_calls = message.get("tool_calls") or message.get("additional_kwargs", {}).get(
            "tool_calls"
        )
    else:
        role = getattr(message, "role", None) or getattr(message, "type", "user")
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            extra = getattr(message, "additional_kwargs", {}) or {}
            tool_calls = extra.get("tool_calls") if isinstance(extra, Mapping) else None
    text = _content_text(content)
    if tool_calls:
        encoded = json.dumps(tool_calls, ensure_ascii=False, default=str, sort_keys=True)
        text = f"{text}\n[tool_calls] {encoded}".strip()
    return {"role": _normalize_role(str(role)), "content": text}


def _coerce_message_sequence(value: Any | Sequence[Any]) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping):
                parts.append(str(block.get("text") or block.get("content") or block))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _normalize_role(role: str) -> str:
    return {
        "human": "user",
        "humanmessage": "user",
        "usermessage": "user",
        "ai": "assistant",
        "aimessage": "assistant",
        "assistantmessage": "assistant",
        "developer": "system",
        "systemmessage": "system",
        "function": "tool",
        "functionmessage": "tool",
        "functionexecutionresultmessage": "tool",
        "toolmessage": "tool",
    }.get(role.lower(), role.lower() or "user")


def _event_kind(turn: Mapping[str, str]) -> ContextEventKind:
    if turn.get("role") == "tool":
        return ContextEventKind.TOOL_RESULT
    return ContextEventKind.MODEL_OUTPUT


def _message_key(message: Any) -> str:
    turn = message_to_turn(message)
    payload = json.dumps(turn, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    # LangGraph's add_messages assigns an ID when a dict is deserialized.  The
    # plugin observes the dict before the reducer and a BaseMessage afterwards,
    # so framework-generated IDs are not stable across that boundary.  Content
    # plus list position is stable and still detects an edited prefix.
    return digest


def _messages_tokens(
    messages: Sequence[Any],
    token_counter: Callable[[str], int],
) -> int:
    return sum(token_counter(message_to_turn(message)["content"]) for message in messages)
