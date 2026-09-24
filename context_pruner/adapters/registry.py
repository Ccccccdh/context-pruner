"""Catalog of implemented and planned Agent integration surfaces."""

from __future__ import annotations

from .autogen import AutoGenContextPruner
from .crewai import CrewAIContextAdapter
from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AgentCategory,
    HistoryVisibility,
    IntegrationLevel,
    ValidationLevel,
)
from .explicit import ExplicitHistoryAdapter
from .generic import GenericAgentAdapter
from .langgraph import LangGraphContextAdapter
from .microsoft_agent_framework import MicrosoftAgentFrameworkContextMiddleware
from .multi_agent import AutoGenTeamContextCoordinator
from .openai_agents import OpenAIAgentsContextFilter


HTTP_SIDECAR_DESCRIPTOR = AdapterDescriptor(
    adapter_id="http_sidecar",
    display_name="Language-Neutral HTTP Context Sidecar",
    category=AgentCategory.LOW_CODE_CLOUD,
    integration_level=IntegrationLevel.BRIDGE,
    frameworks=("Dify", "Coze", "n8n", "Flowise", "custom JavaScript/Java/C# Agents"),
    capabilities=AdapterCapabilities(
        history_visibility=HistoryVisibility.CONDITIONAL,
        pre_model_transform=True,
        model_output_observation=True,
        tool_event_observation=True,
        state_persistence=True,
        lossless_tool_groups=False,
        multi_agent_namespaces=False,
        server_managed_history_safe=False,
    ),
    validation_level=ValidationLevel.REAL_API,
    implemented=True,
    limitations=(
        "can manage only history and events explicitly sent by the host platform",
        "this lifecycle API is not an OpenAI-compatible model proxy",
        "streaming model payload transformation is not implemented",
        "Sidecar host validation uses DeepSeek; provider-neutral core A/B also covers GLM-5.3-Flash",
        "JavaScript, Java, and C# clients are executable; Dify Workflow passed lifecycle and DeepSeek model-call A/B validation on synthetic tasks; Flowise remains contract-level",
    ),
)


_IMPLEMENTED = (
    GenericAgentAdapter.descriptor,
    LangGraphContextAdapter.descriptor,
    OpenAIAgentsContextFilter.descriptor,
    AutoGenContextPruner.descriptor,
    AutoGenTeamContextCoordinator.descriptor,
    ExplicitHistoryAdapter.descriptor,
    HTTP_SIDECAR_DESCRIPTOR,
    MicrosoftAgentFrameworkContextMiddleware.descriptor,
    CrewAIContextAdapter.descriptor,
)

_PLANNED: tuple[AdapterDescriptor, ...] = ()


def list_adapter_descriptors(*, include_planned: bool = False) -> tuple[AdapterDescriptor, ...]:
    descriptors = _IMPLEMENTED + (_PLANNED if include_planned else ())
    return tuple(sorted(descriptors, key=lambda item: (item.category.value, item.adapter_id)))


def get_adapter_descriptor(adapter_id: str) -> AdapterDescriptor:
    for descriptor in _IMPLEMENTED + _PLANNED:
        if descriptor.adapter_id == adapter_id:
            return descriptor
    raise KeyError(f"unknown adapter: {adapter_id}")
