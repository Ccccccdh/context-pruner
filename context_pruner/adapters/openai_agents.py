"""Optional adapter for the OpenAI Agents SDK model-input filter."""

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


@dataclass(frozen=True)
class OpenAIAgentsFilterOutcome:
    input_items: list[Any]
    instructions: str | None
    metrics: dict[str, Any]
    compressed: bool
    skip_reason: str | None = None
    protected_group_count: int = 0
    protected_item_count: int = 0
    unmatched_call_count: int = 0


@dataclass(frozen=True)
class ProtectedResponsesGroup:
    """An ordered Responses item group that must be restored losslessly."""

    group_id: str
    items: tuple[Any, ...]
    token_count: int


class OpenAIAgentsContextFilter:
    """Adapt Context-Pruner to ``RunConfig.call_model_input_filter``.

    Responses reasoning and tool items are replaced with protected placeholders
    during compression and restored losslessly before the SDK sees the input.
    Each group runs from the first non-message item through the assistant-side
    items that follow it, ending before the next user/system/developer turn.
    """

    descriptor = AdapterDescriptor(
        adapter_id="openai_agents",
        display_name="OpenAI Agents SDK Input Filter",
        category=AgentCategory.AGENT_SDK,
        integration_level=IntegrationLevel.NATIVE,
        frameworks=("OpenAI Agents SDK",),
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
        validation_level=ValidationLevel.REAL_API,
        limitations=(
            "conversation_id and previous_response_id may expose only incremental input",
            "full-history compression requires client-managed or manually supplied history",
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
    ) -> None:
        self.token_counter = token_counter or estimate_tokens
        self.middleware = middleware or ContextPrunerMiddleware(
            config,
            task_state=task_state,
            token_counter=self.token_counter,
        )
        self.task_state = str(task_state)
        self.fixed_reserved_tokens = max(0, int(fixed_reserved_tokens))
        self.filter_calls = 0
        self.compressed_calls = 0
        self.skipped_non_message_inputs = 0
        self.protected_group_count = 0
        self.protected_item_count = 0
        self.unmatched_call_count = 0
        self.opaque_reserved_tokens_total = 0
        self.group_restore_failure_count = 0
        self.last_skip_reason: str | None = None

    def filter_items(
        self,
        input_items: Sequence[Any],
        instructions: str | None = None,
    ) -> OpenAIAgentsFilterOutcome:
        items = list(input_items)
        self.filter_calls += 1
        prepared, groups = _protect_responses_groups(items, self.token_counter)
        unmatched = _unmatched_call_count(items)
        placeholder_tokens = sum(
            self.token_counter(_placeholder(group).get("content", ""))
            for group in groups
        )
        opaque_tokens = sum(group.token_count for group in groups)
        opaque_reserve = max(0, opaque_tokens - placeholder_tokens)
        reserved = (
            self.fixed_reserved_tokens
            + self.token_counter(instructions or "")
            + opaque_reserve
        )
        hook = self.middleware.before_model(
            prepared,
            task_state=self.task_state,
            reserved_tokens=reserved,
        )
        restored = _restore_responses_groups(hook.messages, groups)
        if restored is None:
            self.group_restore_failure_count += 1
            self.skipped_non_message_inputs += 1
            self.last_skip_reason = "protected_responses_group_restore_failed"
            return OpenAIAgentsFilterOutcome(
                input_items=items,
                instructions=instructions,
                metrics=self.metrics_dict(),
                compressed=False,
                skip_reason=self.last_skip_reason,
                protected_group_count=len(groups),
                protected_item_count=sum(len(group.items) for group in groups),
                unmatched_call_count=unmatched,
            )

        self.compressed_calls += 1
        self.protected_group_count += len(groups)
        self.protected_item_count += sum(len(group.items) for group in groups)
        self.unmatched_call_count += unmatched
        self.opaque_reserved_tokens_total += opaque_reserve
        self.last_skip_reason = None
        return OpenAIAgentsFilterOutcome(
            input_items=restored,
            instructions=instructions,
            metrics=self.metrics_dict(),
            compressed=True,
            protected_group_count=len(groups),
            protected_item_count=sum(len(group.items) for group in groups),
            unmatched_call_count=unmatched,
        )

    def __call__(self, data: Any) -> Any:
        """Return the exact SDK ``ModelInputData`` shape when the SDK is installed."""
        try:
            from agents.run import ModelInputData
        except ImportError as error:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "OpenAI Agents SDK is not installed; install context-pruner[openai-agents]"
            ) from error
        model_data = data.model_data
        outcome = self.filter_items(model_data.input, model_data.instructions)
        return ModelInputData(
            input=outcome.input_items,
            instructions=outcome.instructions,
        )

    def metrics_dict(self) -> dict[str, Any]:
        metrics = self.middleware.metrics_dict()
        metrics.update(
            {
                "openai_agents_filter_calls": self.filter_calls,
                "openai_agents_compressed_calls": self.compressed_calls,
                "openai_agents_skipped_non_message_inputs": (
                    self.skipped_non_message_inputs
                ),
                "openai_agents_protected_group_count": self.protected_group_count,
                "openai_agents_protected_item_count": self.protected_item_count,
                "openai_agents_unmatched_call_count": self.unmatched_call_count,
                "openai_agents_opaque_reserved_tokens_total": (
                    self.opaque_reserved_tokens_total
                ),
                "openai_agents_group_restore_failure_count": (
                    self.group_restore_failure_count
                ),
                "openai_agents_last_skip_reason": self.last_skip_reason,
            }
        )
        return metrics


def create_call_model_input_filter(
    config: ContextPluginConfig | None = None,
    **kwargs: Any,
) -> OpenAIAgentsContextFilter:
    """Create a callable suitable for ``RunConfig.call_model_input_filter``."""
    return OpenAIAgentsContextFilter(config, **kwargs)


def _is_message_item(item: Any) -> bool:
    if isinstance(item, Mapping):
        item_type = str(item.get("type") or "").lower()
        if item_type and item_type not in {"message", "input_text"}:
            return False
        return "role" in item and "content" in item
    role = getattr(item, "role", None)
    content_present = hasattr(item, "content")
    item_type = str(getattr(item, "type", "") or "").lower()
    return bool(role) and content_present and item_type in {"", "message"}


def _protect_responses_groups(
    items: Sequence[Any],
    token_counter: Callable[[str], int],
) -> tuple[list[Any], list[ProtectedResponsesGroup]]:
    prepared: list[Any] = []
    groups: list[ProtectedResponsesGroup] = []
    current: list[Any] = []
    occurrences: Counter[str] = Counter()

    def flush() -> None:
        if not current:
            return
        encoded = json.dumps(
            [_jsonable_item(item) for item in current],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        # The group may grow across consecutive model calls in one Runner turn.
        # Deriving identity only from its first immutable item keeps the
        # placeholder prefix stable and avoids a false lifecycle resync.
        identity = json.dumps(
            _jsonable_item(current[0]),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        occurrence = occurrences[digest]
        occurrences[digest] += 1
        group = ProtectedResponsesGroup(
            group_id=f"responses-{digest}-{occurrence}",
            items=tuple(current),
            token_count=token_counter(encoded),
        )
        groups.append(group)
        prepared.append(_placeholder(group))
        current.clear()

    for item in items:
        if not current:
            if _is_message_item(item):
                prepared.append(item)
            else:
                current.append(item)
            continue
        if _is_message_item(item) and _message_role(item) in {
            "user",
            "system",
            "developer",
        }:
            flush()
            prepared.append(item)
        else:
            current.append(item)
    flush()
    return prepared, groups


def _restore_responses_groups(
    messages: Sequence[Any],
    groups: Sequence[ProtectedResponsesGroup],
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
        restored.extend(group.items)
        seen.add(group_id)
    if seen != set(by_id):
        return None
    return restored


def _placeholder(group: ProtectedResponsesGroup) -> dict[str, str]:
    return {
        "role": "system",
        "content": f"[protected Responses item group: {group.group_id}]",
        "_context_pruner_responses_group": group.group_id,
    }


def _placeholder_group_id(message: Any) -> str | None:
    if isinstance(message, Mapping):
        value = message.get("_context_pruner_responses_group")
        return str(value) if value else None
    value = getattr(message, "_context_pruner_responses_group", None)
    return str(value) if value else None


def _message_role(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("role") or "").lower()
    return str(getattr(item, "role", "") or "").lower()


def _item_type(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("type") or "").lower()
    return str(getattr(item, "type", "") or "").lower()


def _item_call_id(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("call_id") or "")
    return str(getattr(item, "call_id", "") or "")


def _unmatched_call_count(items: Sequence[Any]) -> int:
    calls: Counter[str] = Counter()
    outputs: Counter[str] = Counter()
    for item in items:
        call_id = _item_call_id(item)
        item_type = _item_type(item)
        if not call_id or "call" not in item_type:
            continue
        if item_type.endswith("_output"):
            outputs[call_id] += 1
        else:
            calls[call_id] += 1
    return sum(abs(calls[key] - outputs[key]) for key in calls.keys() | outputs.keys())


def _jsonable_item(item: Any) -> Any:
    if isinstance(item, Mapping):
        return dict(item)
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return model_dump(exclude_none=True)
    return item
