"""Microsoft Agent Framework chat-middleware adapter.

The framework-owned history remains authoritative.  This middleware replaces
only the model-facing ``ChatContext.messages`` list and stores Context-Pruner
state in ``AgentSession.state`` so framework session serialization also carries
the lifecycle state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
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

try:  # Keep the base package importable without this optional framework.
    from agent_framework import ChatContext, ChatMiddleware, ChatResponse, Message

    _MICROSOFT_AGENT_FRAMEWORK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by minimal installations
    ChatContext = ChatResponse = Message = Any  # type: ignore[assignment,misc]
    ChatMiddleware = object  # type: ignore[assignment,misc]
    _MICROSOFT_AGENT_FRAMEWORK_AVAILABLE = False


MICROSOFT_AGENT_FRAMEWORK_STATE_VERSION = 1
_GROUP_KEY = "_context_pruner_microsoft_group"
_ORIGINAL_INDEX_KEY = "_context_pruner_microsoft_original_index"


@dataclass(frozen=True)
class ProtectedMicrosoftAgentFrameworkGroup:
    """Opaque framework messages restored as their exact original objects."""

    group_id: str
    messages: tuple[Any, ...]
    token_count: int


@dataclass
class MicrosoftAgentFrameworkMetrics:
    chat_calls: int = 0
    compressed_calls: int = 0
    streaming_calls: int = 0
    protected_group_count: int = 0
    protected_message_count: int = 0
    unmatched_call_count: int = 0
    opaque_reserved_tokens_total: int = 0
    group_restore_failure_count: int = 0
    observed_model_output_count: int = 0
    error_count: int = 0
    last_skip_reason: str | None = None


class MicrosoftAgentFrameworkContextMiddleware(ChatMiddleware):  # type: ignore[misc]
    """Native ``ChatMiddleware`` for Microsoft Agent Framework 1.x.

    Text-only messages can be compacted.  Messages containing function calls,
    function results, reasoning, images, or other structured content are
    replaced by protected placeholders and restored losslessly before the
    framework client sees them.
    """

    descriptor = AdapterDescriptor(
        adapter_id="microsoft_agent_framework",
        display_name="Microsoft Agent Framework Chat Middleware",
        category=AgentCategory.AGENT_SDK,
        integration_level=IntegrationLevel.NATIVE,
        frameworks=("Microsoft Agent Framework",),
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
            "full-history compaction requires a caller-managed history provider",
            "this middleware is per-agent and does not provide multi-agent namespace routing",
            "do not stack it with another compaction strategy without a controlled comparison",
            "streaming input transformation is supported but streaming lifecycle finalization is not yet validated",
        ),
    )

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        task_state: str = "",
        fixed_reserved_tokens: int = 0,
        state_key: str = "context_pruner",
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        if not _MICROSOFT_AGENT_FRAMEWORK_AVAILABLE:  # pragma: no cover
            raise RuntimeError(
                "Microsoft Agent Framework is not installed; "
                "install context-pruner[microsoft-agent-framework]"
            )
        self.config = config or ContextPluginConfig()
        self.task_state = str(task_state)
        self.fixed_reserved_tokens = max(0, int(fixed_reserved_tokens))
        self.state_key = str(state_key).strip() or "context_pruner"
        self.token_counter = token_counter or estimate_tokens
        self._default_middleware = self._new_middleware()
        self._default_metrics = MicrosoftAgentFrameworkMetrics()
        self._default_lock = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def process(
        self,
        context: Any,
        call_next: Callable[[], Any],
    ) -> None:
        """Compress the model view, invoke the next middleware, and persist state."""
        session = getattr(context, "session", None)
        lock = self._lock_for(session)
        async with lock:
            middleware, metrics = self._load_runtime(session)
            original = list(getattr(context, "messages", ()) or ())
            metrics.chat_calls += 1
            if bool(getattr(context, "stream", False)):
                metrics.streaming_calls += 1

            prepared, groups = _prepare_messages(original, self.token_counter)
            unmatched = _unmatched_call_count(original)
            placeholder_tokens = sum(
                self.token_counter(str(_placeholder(group)["content"]))
                for group in groups
            )
            opaque_tokens = sum(group.token_count for group in groups)
            opaque_reserve = max(0, opaque_tokens - placeholder_tokens)
            reserved = (
                self.fixed_reserved_tokens
                + opaque_reserve
                + _options_token_reserve(
                    getattr(context, "options", None), self.token_counter
                )
            )
            hook = middleware.before_model(
                prepared,
                task_state=self.task_state,
                reserved_tokens=reserved,
            )
            restored = _restore_messages(hook.messages, groups, original)
            if restored is None:
                metrics.group_restore_failure_count += 1
                metrics.last_skip_reason = "protected_microsoft_group_restore_failed"
                context.messages = original
            else:
                metrics.compressed_calls += 1
                metrics.protected_group_count += len(groups)
                metrics.protected_message_count += sum(
                    len(group.messages) for group in groups
                )
                metrics.unmatched_call_count += unmatched
                metrics.opaque_reserved_tokens_total += opaque_reserve
                metrics.last_skip_reason = None
                context.messages = restored

            self._persist_runtime(session, middleware, metrics)
            self._publish_metrics(context, middleware, metrics)

            if bool(getattr(context, "stream", False)):
                async def observe_stream_result(response: Any) -> Any:
                    self._observe_response(middleware, metrics, response)
                    self._persist_runtime(session, middleware, metrics)
                    return response

                context.stream_result_hooks.append(observe_stream_result)

            try:
                await call_next()
            except BaseException as error:
                metrics.error_count += 1
                middleware.on_error(error, query=self.task_state, recover=True)
                self._persist_runtime(session, middleware, metrics)
                self._publish_metrics(context, middleware, metrics)
                raise

            if not bool(getattr(context, "stream", False)):
                self._observe_response(
                    middleware,
                    metrics,
                    getattr(context, "result", None),
                )
                self._persist_runtime(session, middleware, metrics)
                self._publish_metrics(context, middleware, metrics)

    def metrics_dict(self, session: Any | None = None) -> dict[str, Any]:
        middleware, metrics = self._load_runtime(session)
        data = middleware.metrics_dict()
        data.update(
            {f"microsoft_agent_framework_{key}": value for key, value in asdict(metrics).items()}
        )
        return data

    def export_state(self, session: Any | None = None) -> dict[str, Any]:
        middleware, metrics = self._load_runtime(session)
        return self._runtime_state(middleware, metrics)

    def restore_state(
        self,
        state: Mapping[str, Any],
        session: Any | None = None,
    ) -> None:
        middleware, metrics = self._decode_runtime_state(state)
        if session is None:
            self._default_middleware = middleware
            self._default_metrics = metrics
        else:
            session.state[self.state_key] = self._runtime_state(middleware, metrics)

    def _new_middleware(self) -> ContextPrunerMiddleware:
        return ContextPrunerMiddleware(
            self.config,
            task_state=self.task_state,
            token_counter=self.token_counter,
        )

    def _lock_for(self, session: Any | None) -> asyncio.Lock:
        if session is None:
            return self._default_lock
        session_id = str(getattr(session, "session_id", id(session)))
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    def _load_runtime(
        self,
        session: Any | None,
    ) -> tuple[ContextPrunerMiddleware, MicrosoftAgentFrameworkMetrics]:
        if session is None:
            return self._default_middleware, self._default_metrics
        raw = getattr(session, "state", {}).get(self.state_key)
        if not raw:
            return self._new_middleware(), MicrosoftAgentFrameworkMetrics()
        if not isinstance(raw, Mapping):
            raise ValueError("invalid Microsoft Agent Framework adapter state")
        return self._decode_runtime_state(raw)

    def _decode_runtime_state(
        self,
        state: Mapping[str, Any],
    ) -> tuple[ContextPrunerMiddleware, MicrosoftAgentFrameworkMetrics]:
        if int(state.get("schema_version", 0)) != MICROSOFT_AGENT_FRAMEWORK_STATE_VERSION:
            raise ValueError("unsupported Microsoft Agent Framework adapter state version")
        middleware = ContextPrunerMiddleware.from_state(
            state.get("middleware"),
            config=self.config,
            task_state=self.task_state,
            token_counter=self.token_counter,
        )
        known = MicrosoftAgentFrameworkMetrics.__dataclass_fields__
        raw_metrics = dict(state.get("metrics") or {})
        metrics = MicrosoftAgentFrameworkMetrics(
            **{key: value for key, value in raw_metrics.items() if key in known}
        )
        return middleware, metrics

    def _persist_runtime(
        self,
        session: Any | None,
        middleware: ContextPrunerMiddleware,
        metrics: MicrosoftAgentFrameworkMetrics,
    ) -> None:
        if session is None:
            self._default_middleware = middleware
            self._default_metrics = metrics
            return
        session.state[self.state_key] = self._runtime_state(middleware, metrics)

    @staticmethod
    def _runtime_state(
        middleware: ContextPrunerMiddleware,
        metrics: MicrosoftAgentFrameworkMetrics,
    ) -> dict[str, Any]:
        return {
            "schema_version": MICROSOFT_AGENT_FRAMEWORK_STATE_VERSION,
            "middleware": middleware.export_state(),
            "metrics": asdict(metrics),
        }

    def _publish_metrics(
        self,
        context: Any,
        middleware: ContextPrunerMiddleware,
        metrics: MicrosoftAgentFrameworkMetrics,
    ) -> None:
        data = middleware.metrics_dict()
        data.update(
            {f"microsoft_agent_framework_{key}": value for key, value in asdict(metrics).items()}
        )
        context.metadata["context_pruner"] = data

    @staticmethod
    def _observe_response(
        middleware: ContextPrunerMiddleware,
        metrics: MicrosoftAgentFrameworkMetrics,
        response: Any,
    ) -> None:
        if response is None or not isinstance(response, ChatResponse):
            return
        observable: list[dict[str, str]] = []
        for message in list(getattr(response, "messages", ()) or ()):
            if _is_text_only(message):
                observable.append(
                    {
                        "role": _normalize_role(getattr(message, "role", "assistant")),
                        "content": str(getattr(message, "text", "") or ""),
                    }
                )
        if observable:
            middleware.after_model(observable)
            metrics.observed_model_output_count += len(observable)


def create_microsoft_agent_framework_middleware(
    config: ContextPluginConfig | None = None,
    **kwargs: Any,
) -> MicrosoftAgentFrameworkContextMiddleware:
    """Create middleware suitable for ``Agent(middleware=[...])``."""
    return MicrosoftAgentFrameworkContextMiddleware(config, **kwargs)


def _prepare_messages(
    messages: Sequence[Any],
    token_counter: Callable[[str], int],
) -> tuple[list[dict[str, Any]], list[ProtectedMicrosoftAgentFrameworkGroup]]:
    prepared: list[dict[str, Any]] = []
    groups: list[ProtectedMicrosoftAgentFrameworkGroup] = []
    occurrences: Counter[str] = Counter()
    index = 0
    while index < len(messages):
        message = messages[index]
        if not isinstance(message, Message):
            raise TypeError("Microsoft Agent Framework context contains a non-Message item")
        if _is_text_only(message):
            prepared.append(
                {
                    "role": _normalize_role(message.role),
                    "content": message.text,
                    _ORIGINAL_INDEX_KEY: index,
                }
            )
            index += 1
            continue

        group_messages = [message]
        call_ids = set(_message_call_ids(message, "function_call"))
        if call_ids and index + 1 < len(messages):
            following = messages[index + 1]
            if isinstance(following, Message):
                result_ids = set(_message_call_ids(following, "function_result"))
                if call_ids & result_ids:
                    group_messages.append(following)
                    index += 1
        encoded = json.dumps(
            [_jsonable_message(item) for item in group_messages],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        identity = json.dumps(
            _jsonable_message(group_messages[0]),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        occurrence = occurrences[digest]
        occurrences[digest] += 1
        group = ProtectedMicrosoftAgentFrameworkGroup(
            group_id=f"microsoft-{digest}-{occurrence}",
            messages=tuple(group_messages),
            token_count=token_counter(encoded),
        )
        groups.append(group)
        prepared.append(_placeholder(group))
        index += 1
    return prepared, groups


def _restore_messages(
    messages: Sequence[Any],
    groups: Sequence[ProtectedMicrosoftAgentFrameworkGroup],
    originals: Sequence[Any],
) -> list[Any] | None:
    by_id = {group.group_id: group for group in groups}
    seen_groups: set[str] = set()
    seen_originals: set[int] = set()
    restored: list[Any] = []
    for message in messages:
        if isinstance(message, Message):
            restored.append(message)
            continue
        if not isinstance(message, Mapping):
            return None
        group_id = message.get(_GROUP_KEY)
        if group_id:
            key = str(group_id)
            group = by_id.get(key)
            if group is None or key in seen_groups:
                return None
            restored.extend(group.messages)
            seen_groups.add(key)
            continue
        original_index = message.get(_ORIGINAL_INDEX_KEY)
        if original_index is not None:
            try:
                position = int(original_index)
            except (TypeError, ValueError):
                return None
            if (
                position < 0
                or position >= len(originals)
                or position in seen_originals
                or not isinstance(originals[position], Message)
            ):
                return None
            restored.append(originals[position])
            seen_originals.add(position)
            continue
        restored.append(_to_framework_message(message))
    if seen_groups != set(by_id):
        return None
    return restored


def _placeholder(
    group: ProtectedMicrosoftAgentFrameworkGroup,
) -> dict[str, Any]:
    return {
        "role": "system",
        "content": f"[protected Microsoft Agent Framework group: {group.group_id}]",
        _GROUP_KEY: group.group_id,
    }


def _to_framework_message(message: Mapping[str, Any]) -> Any:
    role = _normalize_role(message.get("role", "user"))
    content = str(message.get("content") or "")
    return Message(role, [content])


def _normalize_role(role: Any) -> str:
    value = str(role or "user").lower()
    return {
        "developer": "system",
        "function": "tool",
    }.get(value, value if value in {"system", "user", "assistant", "tool"} else "user")


def _is_text_only(message: Any) -> bool:
    if not isinstance(message, Message):
        return False
    return all(str(getattr(content, "type", "")) == "text" for content in message.contents)


def _message_call_ids(message: Any, content_type: str) -> list[str]:
    if not isinstance(message, Message):
        return []
    return [
        str(getattr(content, "call_id", "") or "")
        for content in message.contents
        if str(getattr(content, "type", "")) == content_type
        and getattr(content, "call_id", None)
    ]


def _unmatched_call_count(messages: Sequence[Any]) -> int:
    calls: Counter[str] = Counter()
    results: Counter[str] = Counter()
    for message in messages:
        calls.update(_message_call_ids(message, "function_call"))
        results.update(_message_call_ids(message, "function_result"))
    return sum(abs(calls[key] - results[key]) for key in calls.keys() | results.keys())


def _jsonable_message(message: Any) -> Any:
    to_dict = getattr(message, "to_dict", None)
    return to_dict() if callable(to_dict) else str(message)


def _options_token_reserve(
    options: Any,
    token_counter: Callable[[str], int],
) -> int:
    if not options:
        return 0
    try:
        encoded = json.dumps(options, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return 0
    return token_counter(encoded)
