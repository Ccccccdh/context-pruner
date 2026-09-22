"""Explicit exported-history bridge for closed commercial Agent products."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..plugin import ContextPluginConfig
from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AgentCategory,
    HistoryVisibility,
    IntegrationLevel,
    ValidationLevel,
)
from .generic import GenericAgentAdapter


class ExplicitHistoryAdapter:
    """Compress a history only when a user or Agent explicitly supplies it."""

    descriptor = AdapterDescriptor(
        adapter_id="explicit_history",
        display_name="Commercial Agent Exported-History Bridge",
        category=AgentCategory.COMMERCIAL_PRODUCT,
        integration_level=IntegrationLevel.EXPLICIT,
        frameworks=("Codex skill", "exported JSON history", "closed Agent products"),
        capabilities=AdapterCapabilities(
            history_visibility=HistoryVisibility.EXPORTED,
            pre_model_transform=False,
            model_output_observation=False,
            tool_event_observation=False,
            state_persistence=True,
            lossless_tool_groups=False,
            multi_agent_namespaces=False,
            server_managed_history_safe=False,
        ),
        validation_level=ValidationLevel.CONTRACT_ONLY,
        limitations=(
            "cannot intercept or replace a commercial product's private context pipeline",
            "runs only on explicitly supplied history",
        ),
    )

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        task_state: str = "",
        state: Mapping[str, Any] | None = None,
    ) -> None:
        self.adapter = GenericAgentAdapter.from_state(
            state,
            config=config,
            task_state=task_state,
        )

    def prepare_export(
        self,
        payload: Sequence[Any] | Mapping[str, Any],
        *,
        task_state: str = "",
        reserved_tokens: int = 0,
    ) -> dict[str, Any]:
        if isinstance(payload, Mapping):
            raw_messages = payload.get("messages")
            if not isinstance(raw_messages, list):
                raise ValueError("exported history object must contain a messages list")
            messages = list(raw_messages)
            embedded_task = str(payload.get("task_state") or "")
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            messages = list(payload)
            embedded_task = ""
        else:
            raise ValueError("exported history must be a message list or object")
        result = self.adapter.before_model(
            messages,
            task_state=task_state or embedded_task,
            reserved_tokens=reserved_tokens,
        )
        return {
            "messages": result.messages,
            "metrics": result.metrics,
            "lifecycle_state": result.lifecycle_state,
            "adapter": self.descriptor.to_dict(),
            "automatic_context_control": False,
        }

    def metrics_dict(self) -> dict[str, Any]:
        return self.adapter.metrics_dict()

    def export_state(self) -> dict[str, Any]:
        return self.adapter.export_state()
