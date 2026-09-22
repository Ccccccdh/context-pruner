"""Context-Pruner：面向长程 Agent 任务的自适应熵减上下文压缩系统。"""

from .pipeline import ContextPruner
from .pipeline_v1 import ContextPrunerV1
from .lifecycle import ContextLifecycleManager
from .archive import (
    ArchiveEntry,
    ArchivePolicy,
    ArchiveStore,
    InMemoryArchiveStore,
    SQLiteArchiveStore,
    redact_sensitive_text,
)
from .parser_v1 import parse_context_semantic
from .evaluator_v1 import AdaptiveScorer, EntropyScorer, HybridScorer
from .scheduler_v1 import schedule_adaptive
from .semantic import ExtractiveSummarizer, LocalHashEmbedding, extract_provenance_metadata
from .checkpoint import (
    BranchOutcome,
    ContextCheckpoint,
    CounterfactualEvaluation,
    InMemoryCheckpointStore,
    evaluate_checkpoint,
)
from .plugin import (
    ContextLifecyclePlugin,
    ContextPluginConfig,
    ContextPluginMetrics,
    PluginHookResult,
)
from .middleware import ContextPrunerMiddleware, MiddlewareMetrics
from .types import (
    CompressedResult,
    BudgetPressure,
    ContextBudget,
    CompressionAction,
    ContextEvent,
    ContextEventKind,
    ContextItem,
    ContextChunk,
    ContextSnapshot,
    ContextStatus,
    ContextType,
    LifecycleEvent,
    LifecycleRecord,
    TaskPhase,
)

__version__ = "0.13.1"

__all__ = [
    "ContextPruner",
    "ContextPrunerV1",
    "ContextLifecycleManager",
    "ArchiveEntry",
    "ArchivePolicy",
    "ArchiveStore",
    "InMemoryArchiveStore",
    "SQLiteArchiveStore",
    "redact_sensitive_text",
    "parse_context_semantic",
    "HybridScorer",
    "EntropyScorer",
    "AdaptiveScorer",
    "schedule_adaptive",
    "ExtractiveSummarizer",
    "LocalHashEmbedding",
    "extract_provenance_metadata",
    "BranchOutcome",
    "ContextCheckpoint",
    "CounterfactualEvaluation",
    "InMemoryCheckpointStore",
    "evaluate_checkpoint",
    "ContextLifecyclePlugin",
    "ContextPluginConfig",
    "ContextPluginMetrics",
    "PluginHookResult",
    "ContextPrunerMiddleware",
    "MiddlewareMetrics",
    "CompressedResult",
    "BudgetPressure",
    "ContextBudget",
    "CompressionAction",
    "ContextEvent",
    "ContextEventKind",
    "ContextItem",
    "ContextChunk",
    "ContextSnapshot",
    "ContextStatus",
    "ContextType",
    "LifecycleEvent",
    "LifecycleRecord",
    "TaskPhase",
]
