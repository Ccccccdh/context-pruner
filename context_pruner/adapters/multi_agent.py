"""Scoped private/shared/handoff context routing for AutoGen teams."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..plugin import ContextPluginConfig
from ..types import estimate_tokens
from .autogen import AutoGenContextPruner
from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AgentCategory,
    HistoryVisibility,
    IntegrationLevel,
    ValidationLevel,
)

try:  # Keep the base package importable without the optional AutoGen extra.
    from autogen_core.models import UserMessage

    _AUTOGEN_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency path
    UserMessage = None
    _AUTOGEN_AVAILABLE = False


AUTOGEN_TEAM_STATE_VERSION = 1
_AGENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class ContextScope(str, Enum):
    PRIVATE = "private"
    SHARED = "shared"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class ScopedContextEvent:
    event_id: str
    sequence: int
    scope: ContextScope
    source_agent: str
    target_agents: tuple[str, ...]
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope"] = self.scope.value
        data["target_agents"] = list(self.target_agents)
        data["metadata"] = _json_safe(self.metadata)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScopedContextEvent":
        return cls(
            event_id=str(data["event_id"]),
            sequence=int(data["sequence"]),
            scope=ContextScope(str(data["scope"])),
            source_agent=str(data["source_agent"]),
            target_agents=tuple(str(value) for value in data.get("target_agents") or ()),
            content=str(data.get("content") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class TeamContextMetrics:
    private_event_count: int = 0
    shared_event_count: int = 0
    handoff_event_count: int = 0
    delivery_count: int = 0
    replay_delivery_count: int = 0


class AutoGenTeamContextCoordinator:
    """Own one pruned context per Agent and route explicitly scoped events.

    Private events are delivered only to their owner, shared events to every
    registered Agent, and handoffs only to their target.  The ledger is
    authoritative for routing while each AutoGen context remains authoritative
    for the messages that Agent actually received.
    """

    descriptor = AdapterDescriptor(
        adapter_id="autogen_team",
        display_name="AutoGen Team Scoped Context Coordinator",
        category=AgentCategory.MULTI_AGENT,
        integration_level=IntegrationLevel.NATIVE,
        frameworks=("AutoGen AgentChat teams", "custom multi-Agent orchestrators"),
        capabilities=AdapterCapabilities(
            history_visibility=HistoryVisibility.FULL,
            pre_model_transform=True,
            model_output_observation=True,
            tool_event_observation=True,
            state_persistence=True,
            lossless_tool_groups=True,
            multi_agent_namespaces=True,
            server_managed_history_safe=False,
            streaming_safe=False,
        ),
        validation_level=ValidationLevel.REAL_API,
        limitations=(
            "real-API team validation currently uses one model provider despite 33 preregistered pairs across nine task definitions",
            "routing isolation cannot stop a model from repeating private content in its own shared output",
        ),
    )

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        team_id: str,
        task_state: str = "",
        fixed_reserved_tokens: int = 0,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        if not _AUTOGEN_AVAILABLE:  # pragma: no cover - optional dependency path
            raise RuntimeError("AutoGen is not installed; install context-pruner[autogen]")
        self.team_id = _validate_agent_id(team_id, field_name="team_id")
        self.config = config or ContextPluginConfig()
        self.task_state = str(task_state)
        self.fixed_reserved_tokens = max(0, int(fixed_reserved_tokens))
        self.token_counter = token_counter or estimate_tokens
        self._contexts: dict[str, AutoGenContextPruner] = {}
        self._events: list[ScopedContextEvent] = []
        self._delivered_by_agent: dict[str, set[str]] = {}
        self._sequence = 0
        self.metrics = TeamContextMetrics()

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return tuple(self._contexts)

    @property
    def events(self) -> tuple[ScopedContextEvent, ...]:
        return tuple(self._events)

    def register_agent(
        self,
        agent_id: str,
        *,
        initial_messages: Sequence[Any] | None = None,
        task_state: str | None = None,
    ) -> AutoGenContextPruner:
        agent_id = _validate_agent_id(agent_id)
        if agent_id in self._contexts:
            raise ValueError(f"Agent already registered: {agent_id}")
        visible = self.visible_events(agent_id)
        routed = [_render_event(event) for event in visible]
        context = AutoGenContextPruner(
            self.config,
            initial_messages=[*(initial_messages or ()), *routed],
            task_state=self.task_state if task_state is None else str(task_state),
            fixed_reserved_tokens=self.fixed_reserved_tokens,
            token_counter=self.token_counter,
        )
        self._contexts[agent_id] = context
        self._delivered_by_agent[agent_id] = {event.event_id for event in visible}
        self.metrics.delivery_count += len(visible)
        self.metrics.replay_delivery_count += len(visible)
        return context

    def get_context(self, agent_id: str) -> AutoGenContextPruner:
        try:
            return self._contexts[str(agent_id)]
        except KeyError as exc:
            raise KeyError(f"unknown Agent: {agent_id}") from exc

    def visible_events(self, agent_id: str) -> tuple[ScopedContextEvent, ...]:
        agent_id = _validate_agent_id(agent_id)
        return tuple(
            event for event in self._events if _event_visible_to(event, agent_id)
        )

    async def publish_private(
        self,
        agent_id: str,
        content: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ScopedContextEvent:
        self._require_agent(agent_id)
        return await self._publish(
            ContextScope.PRIVATE,
            source_agent=agent_id,
            target_agents=(agent_id,),
            content=content,
            metadata=metadata,
        )

    async def publish_shared(
        self,
        source_agent: str,
        content: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ScopedContextEvent:
        self._require_agent(source_agent)
        return await self._publish(
            ContextScope.SHARED,
            source_agent=source_agent,
            target_agents=("*",),
            content=content,
            metadata=metadata,
        )

    async def publish_handoff(
        self,
        source_agent: str,
        target_agent: str,
        content: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> ScopedContextEvent:
        self._require_agent(source_agent)
        self._require_agent(target_agent)
        if source_agent == target_agent:
            raise ValueError("handoff source and target must be different Agents")
        return await self._publish(
            ContextScope.HANDOFF,
            source_agent=source_agent,
            target_agents=(target_agent,),
            content=content,
            metadata=metadata,
        )

    def metrics_dict(self) -> dict[str, Any]:
        return {
            **asdict(self.metrics),
            "team_id": self.team_id,
            "registered_agent_count": len(self._contexts),
            "scoped_event_count": len(self._events),
        }

    async def save_state(self) -> dict[str, Any]:
        contexts: dict[str, Any] = {}
        for agent_id, context in self._contexts.items():
            contexts[agent_id] = dict(await context.save_state())
        return {
            "schema_version": AUTOGEN_TEAM_STATE_VERSION,
            "team_id": self.team_id,
            "task_state": self.task_state,
            "fixed_reserved_tokens": self.fixed_reserved_tokens,
            "sequence": self._sequence,
            "events": [event.to_dict() for event in self._events],
            "delivered_by_agent": {
                agent_id: sorted(event_ids)
                for agent_id, event_ids in self._delivered_by_agent.items()
            },
            "contexts": contexts,
            "metrics": asdict(self.metrics),
        }

    @classmethod
    async def from_state(
        cls,
        state: Mapping[str, Any],
        *,
        config: ContextPluginConfig | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> "AutoGenTeamContextCoordinator":
        if int(state.get("schema_version", 0)) != AUTOGEN_TEAM_STATE_VERSION:
            raise ValueError("unsupported AutoGen team coordinator state version")
        coordinator = cls(
            config,
            team_id=str(state.get("team_id") or ""),
            task_state=str(state.get("task_state") or ""),
            fixed_reserved_tokens=int(state.get("fixed_reserved_tokens", 0)),
            token_counter=token_counter,
        )
        coordinator._events = [
            ScopedContextEvent.from_dict(value)
            for value in state.get("events") or ()
        ]
        coordinator._sequence = int(state.get("sequence", 0))
        if coordinator._events:
            coordinator._sequence = max(
                coordinator._sequence,
                max(event.sequence for event in coordinator._events),
            )
        raw_metrics = dict(state.get("metrics") or {})
        known_metrics = TeamContextMetrics.__dataclass_fields__
        coordinator.metrics = TeamContextMetrics(
            **{
                key: int(value)
                for key, value in raw_metrics.items()
                if key in known_metrics
            }
        )
        delivered = dict(state.get("delivered_by_agent") or {})
        for agent_id, context_state in dict(state.get("contexts") or {}).items():
            normalized_id = _validate_agent_id(str(agent_id))
            context = AutoGenContextPruner(
                coordinator.config,
                task_state=coordinator.task_state,
                fixed_reserved_tokens=coordinator.fixed_reserved_tokens,
                token_counter=coordinator.token_counter,
            )
            await context.load_state(dict(context_state or {}))
            coordinator._contexts[normalized_id] = context
            coordinator._delivered_by_agent[normalized_id] = {
                str(value) for value in delivered.get(normalized_id, ())
            }
        coordinator._validate_state()
        return coordinator

    async def _publish(
        self,
        scope: ContextScope,
        *,
        source_agent: str,
        target_agents: tuple[str, ...],
        content: str,
        metadata: Mapping[str, Any] | None,
    ) -> ScopedContextEvent:
        normalized_content = str(content).strip()
        if not normalized_content:
            raise ValueError("scoped context content cannot be empty")
        self._sequence += 1
        event = ScopedContextEvent(
            event_id=_event_id(
                self.team_id,
                self._sequence,
                scope,
                source_agent,
                target_agents,
                normalized_content,
            ),
            sequence=self._sequence,
            scope=scope,
            source_agent=source_agent,
            target_agents=target_agents,
            content=normalized_content,
            metadata=dict(metadata or {}),
        )
        self._events.append(event)
        if scope == ContextScope.PRIVATE:
            self.metrics.private_event_count += 1
        elif scope == ContextScope.SHARED:
            self.metrics.shared_event_count += 1
        else:
            self.metrics.handoff_event_count += 1
        for agent_id, context in self._contexts.items():
            if not _event_visible_to(event, agent_id):
                continue
            await context.add_message(_render_event(event))
            self._delivered_by_agent.setdefault(agent_id, set()).add(event.event_id)
            self.metrics.delivery_count += 1
        return event

    def _require_agent(self, agent_id: str) -> None:
        normalized = _validate_agent_id(agent_id)
        if normalized not in self._contexts:
            raise KeyError(f"unknown Agent: {normalized}")

    def _validate_state(self) -> None:
        event_ids = {event.event_id for event in self._events}
        if len(event_ids) != len(self._events):
            raise ValueError("duplicate scoped context event ID")
        for agent_id, delivered in self._delivered_by_agent.items():
            unknown = delivered - event_ids
            if unknown:
                raise ValueError(
                    f"Agent {agent_id} references unknown scoped events: {sorted(unknown)}"
                )
            invisible = {
                event.event_id
                for event in self._events
                if event.event_id in delivered and not _event_visible_to(event, agent_id)
            }
            if invisible:
                raise ValueError(
                    f"Agent {agent_id} received out-of-scope events: {sorted(invisible)}"
                )


def _event_visible_to(event: ScopedContextEvent, agent_id: str) -> bool:
    if event.scope == ContextScope.SHARED:
        # Shared events are team history and are replayed to Agents that join later.
        return True
    return agent_id in event.target_agents


def _render_event(event: ScopedContextEvent) -> Any:
    if not _AUTOGEN_AVAILABLE:  # pragma: no cover
        raise RuntimeError("AutoGen is not installed")
    targets = ",".join(event.target_agents)
    header = (
        f"[Context-Pruner scope={event.scope.value} event={event.event_id} "
        f"source={event.source_agent} targets={targets}]"
    )
    # Use a normal user message because OpenAI-compatible clients may reject
    # system messages inserted after conversational history. AutoGenContextPruner
    # recognizes this prefix and restores the exact object losslessly.
    return UserMessage(
        content=f"{header}\n{event.content}",
        source="context_pruner",
    )


def _event_id(
    team_id: str,
    sequence: int,
    scope: ContextScope,
    source_agent: str,
    target_agents: tuple[str, ...],
    content: str,
) -> str:
    payload = json.dumps(
        [team_id, sequence, scope.value, source_agent, list(target_agents), content],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"ctx-{sequence:06d}-{digest}"


def _validate_agent_id(value: str, *, field_name: str = "agent_id") -> str:
    normalized = str(value).strip()
    if not _AGENT_ID.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must contain 1-128 letters, digits, '.', '_' or '-'"
        )
    return normalized


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
