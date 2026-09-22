"""Shared capability contract for heterogeneous Agent integrations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable


class AgentCategory(str, Enum):
    CUSTOM_LOOP = "custom_loop"
    WORKFLOW_GRAPH = "workflow_graph"
    AGENT_SDK = "agent_sdk"
    MULTI_AGENT = "multi_agent"
    COMMERCIAL_PRODUCT = "commercial_product"
    LOW_CODE_CLOUD = "low_code_cloud"


class IntegrationLevel(str, Enum):
    NATIVE = "native"
    BRIDGE = "bridge"
    EXPLICIT = "explicit"
    UNSUPPORTED = "unsupported"


class HistoryVisibility(str, Enum):
    FULL = "full"
    CONDITIONAL = "conditional"
    INCREMENTAL = "incremental"
    EXPORTED = "exported"
    NONE = "none"


class ValidationLevel(str, Enum):
    REAL_API = "real_api"
    LOCAL_RUNNER = "local_runner"
    CONTRACT_ONLY = "contract_only"
    NOT_VALIDATED = "not_validated"


@dataclass(frozen=True)
class AdapterCapabilities:
    history_visibility: HistoryVisibility
    pre_model_transform: bool
    model_output_observation: bool
    tool_event_observation: bool
    state_persistence: bool
    lossless_tool_groups: bool
    multi_agent_namespaces: bool
    server_managed_history_safe: bool
    streaming_safe: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["history_visibility"] = self.history_visibility.value
        return data


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    display_name: str
    category: AgentCategory
    integration_level: IntegrationLevel
    frameworks: tuple[str, ...]
    capabilities: AdapterCapabilities
    validation_level: ValidationLevel
    implemented: bool = True
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "category": self.category.value,
            "integration_level": self.integration_level.value,
            "frameworks": list(self.frameworks),
            "capabilities": self.capabilities.to_dict(),
            "validation_level": self.validation_level.value,
            "implemented": self.implemented,
            "limitations": list(self.limitations),
        }


@runtime_checkable
class AdapterDescriptorProvider(Protocol):
    descriptor: AdapterDescriptor


@dataclass(frozen=True)
class AgentSurface:
    """Observable extension surface of an Agent product or framework."""

    full_history_visible: bool = False
    pre_model_hook: bool = False
    tool_events_visible: bool = False
    state_persistence: bool = False
    configurable_model_endpoint: bool = False
    exported_history: bool = False
    multi_agent: bool = False
    server_managed_history: bool = False


@dataclass(frozen=True)
class CompatibilityAssessment:
    integration_level: IntegrationLevel
    automatic_context_control: bool
    reasons: tuple[str, ...]
    required_components: tuple[str, ...]
    safety_constraints: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "integration_level": self.integration_level.value,
            "automatic_context_control": self.automatic_context_control,
            "reasons": list(self.reasons),
            "required_components": list(self.required_components),
            "safety_constraints": list(self.safety_constraints),
        }


def assess_agent_surface(surface: AgentSurface) -> CompatibilityAssessment:
    """Select the safest integration depth supported by an Agent surface."""
    if surface.full_history_visible and surface.pre_model_hook:
        constraints: list[str] = []
        components = ["native adapter", "ContextPrunerMiddleware"]
        if surface.server_managed_history:
            constraints.append(
                "verify that the hook receives merged history rather than only a server-side delta"
            )
        if not surface.tool_events_visible:
            constraints.append(
                "protect tool-call structures at the model boundary because tool events are not observable"
            )
        if not surface.state_persistence:
            constraints.append(
                "persist exported lifecycle state outside the Agent before restart or handoff"
            )
        if surface.multi_agent:
            components.append("per-agent and shared-context namespace coordinator")
            constraints.append(
                "separate private, shared, and handoff context namespaces before team-level use"
            )
        return CompatibilityAssessment(
            integration_level=IntegrationLevel.NATIVE,
            automatic_context_control=True,
            reasons=("full history and a pre-model mutation hook are available",),
            required_components=tuple(components),
            safety_constraints=tuple(constraints),
        )
    if surface.pre_model_hook:
        constraints = [
            "do not claim compression of history that remains on the provider server",
        ]
        if not surface.state_persistence:
            constraints.append("store bridge lifecycle state outside the Agent")
        if surface.multi_agent:
            constraints.append(
                "keep each Agent's incremental stream in a distinct namespace"
            )
        return CompatibilityAssessment(
            integration_level=IntegrationLevel.BRIDGE,
            automatic_context_control=True,
            reasons=("a pre-model hook exists but complete history is not guaranteed",),
            required_components=("incremental bridge adapter",),
            safety_constraints=tuple(constraints),
        )
    if surface.configurable_model_endpoint:
        constraints = [
            "the proxy can manage only messages actually present in each request",
            "streaming and tool schemas require provider-specific conformance tests",
        ]
        if surface.multi_agent:
            constraints.append(
                "derive a stable Agent and conversation namespace from authenticated request metadata"
            )
        return CompatibilityAssessment(
            integration_level=IntegrationLevel.BRIDGE,
            automatic_context_control=True,
            reasons=("the model endpoint can be routed through a context sidecar or proxy",),
            required_components=("HTTP sidecar or OpenAI-compatible model proxy",),
            safety_constraints=tuple(constraints),
        )
    if surface.exported_history:
        return CompatibilityAssessment(
            integration_level=IntegrationLevel.EXPLICIT,
            automatic_context_control=False,
            reasons=("history can be exported but model calls cannot be intercepted",),
            required_components=("ExplicitHistoryAdapter or offline CLI",),
            safety_constraints=(
                "compression occurs only when the user or Agent explicitly invokes it",
            ),
        )
    return CompatibilityAssessment(
        integration_level=IntegrationLevel.UNSUPPORTED,
        automatic_context_control=False,
        reasons=("no usable pre-model hook, configurable endpoint, or history export is visible",),
        required_components=(),
        safety_constraints=("do not claim internal context interception",),
    )
