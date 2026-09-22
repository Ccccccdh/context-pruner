"""Optional framework adapters for Context-Pruner."""

from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AdapterDescriptorProvider,
    AgentCategory,
    AgentSurface,
    CompatibilityAssessment,
    HistoryVisibility,
    IntegrationLevel,
    ValidationLevel,
    assess_agent_surface,
)
from .explicit import ExplicitHistoryAdapter
from .generic import GenericAgentAdapter
from .langgraph import LangGraphAdapterConfig, LangGraphContextAdapter
from .autogen import (
    AutoGenContextPruner,
    ProtectedAutoGenGroup,
    create_autogen_model_context,
)
from .multi_agent import (
    AutoGenTeamContextCoordinator,
    ContextScope,
    ScopedContextEvent,
    TeamContextMetrics,
)
from .microsoft_agent_framework import (
    MicrosoftAgentFrameworkContextMiddleware,
    MicrosoftAgentFrameworkMetrics,
    ProtectedMicrosoftAgentFrameworkGroup,
    create_microsoft_agent_framework_middleware,
)
from .crewai import (
    CrewAIAdapterMetrics,
    CrewAIContextAdapter,
    ProtectedCrewAIGroup,
    create_crewai_context_adapter,
)
from .openai_agents import (
    OpenAIAgentsContextFilter,
    OpenAIAgentsFilterOutcome,
    ProtectedResponsesGroup,
    create_call_model_input_filter,
)
from .registry import get_adapter_descriptor, list_adapter_descriptors

__all__ = [
    "AdapterCapabilities",
    "AdapterDescriptor",
    "AdapterDescriptorProvider",
    "AgentCategory",
    "AgentSurface",
    "CompatibilityAssessment",
    "HistoryVisibility",
    "IntegrationLevel",
    "ValidationLevel",
    "assess_agent_surface",
    "ExplicitHistoryAdapter",
    "GenericAgentAdapter",
    "AutoGenContextPruner",
    "ProtectedAutoGenGroup",
    "create_autogen_model_context",
    "AutoGenTeamContextCoordinator",
    "ContextScope",
    "ScopedContextEvent",
    "TeamContextMetrics",
    "MicrosoftAgentFrameworkContextMiddleware",
    "MicrosoftAgentFrameworkMetrics",
    "ProtectedMicrosoftAgentFrameworkGroup",
    "create_microsoft_agent_framework_middleware",
    "CrewAIAdapterMetrics",
    "CrewAIContextAdapter",
    "ProtectedCrewAIGroup",
    "create_crewai_context_adapter",
    "LangGraphAdapterConfig",
    "LangGraphContextAdapter",
    "OpenAIAgentsContextFilter",
    "OpenAIAgentsFilterOutcome",
    "ProtectedResponsesGroup",
    "create_call_model_input_filter",
    "get_adapter_descriptor",
    "list_adapter_descriptors",
]
