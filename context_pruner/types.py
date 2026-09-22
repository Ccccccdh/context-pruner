"""Context-Pruner 的统一运行时数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ContextType(str, Enum):
    SYSTEM = "system"
    TASK_STATE = "task_state"
    DIALOGUE = "dialogue"
    TOOL_INTERACTION = "tool"
    LONG_TERM_MEMORY = "memory"


class ContextEventKind(str, Enum):
    TASK_STARTED = "task_started"
    MODEL_INPUT = "model_input"
    MODEL_OUTPUT = "model_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    PARSE_ERROR = "parse_error"
    TASK_FINISHED = "task_finished"


class TaskPhase(str, Enum):
    EXPLORATION = "exploration"
    KEY_DECISION = "key_decision"
    CONVERGENCE = "convergence"


class BudgetPressure(str, Enum):
    NORMAL = "normal"
    SOFT = "soft"
    HARD = "hard"


class CompressionAction(str, Enum):
    KEEP = "keep"
    TRUNCATE = "truncate"
    SUMMARIZE = "summarize"
    MERGE = "merge"
    ARCHIVE = "archive"
    RESTORE = "restore"


class ContextStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    COMPRESSED = "compressed"
    ARCHIVED = "archived"
    RECOVERED = "recovered"
    DISCARDED = "discarded"


class LifecycleEvent(str, Enum):
    GENERATED = "generated"
    PHASE_CHANGED = "phase_changed"
    EVALUATED = "evaluated"
    COMPRESSED = "compressed"
    ARCHIVED = "archived"
    RECOVERED = "recovered"
    DISCARDED = "discarded"
    BUDGET_PRESSURE = "budget_pressure"
    BUDGET_VIOLATION = "budget_violation"
    CHECKPOINTED = "checkpointed"


@dataclass(frozen=True)
class ContextBudget:
    """模型上下文的软/硬预算，以及压缩后的期望目标。"""

    soft_limit_tokens: int
    hard_limit_tokens: int
    target_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.soft_limit_tokens < 0 or self.hard_limit_tokens < 0:
            raise ValueError("上下文预算不能为负数")
        if self.soft_limit_tokens > self.hard_limit_tokens:
            raise ValueError("soft_limit_tokens 不能大于 hard_limit_tokens")
        target = self.soft_limit_tokens if self.target_tokens is None else self.target_tokens
        if target < 0 or target > self.hard_limit_tokens:
            raise ValueError("target_tokens 必须位于 0 与 hard_limit_tokens 之间")
        object.__setattr__(self, "target_tokens", target)

    def pressure(self, tokens: int) -> BudgetPressure:
        if tokens > self.hard_limit_tokens:
            return BudgetPressure.HARD
        if tokens > self.soft_limit_tokens:
            return BudgetPressure.SOFT
        return BudgetPressure.NORMAL

    def reserve(self, fixed_tokens: int) -> "ContextBudget":
        """扣除 system/task 等不可压缩 token，得到可供历史使用的预算。"""
        fixed = max(0, int(fixed_tokens))
        hard = max(0, self.hard_limit_tokens - fixed)
        soft = min(hard, max(0, self.soft_limit_tokens - fixed))
        target = min(hard, max(0, int(self.target_tokens or 0) - fixed))
        return ContextBudget(soft, hard, target)


def estimate_tokens(text: str) -> int:
    """无 tokenizer 时的稳定近似；实验层可替换成具体模型 tokenizer。"""
    if not text:
        return 0
    cn = sum(1 for ch in text if "一" <= ch <= "鿿")
    rest = len(text) - cn
    return cn + int(rest * 0.7) + 4


@dataclass(frozen=True)
class ContextEvent:
    """Agent 运行过程中产生的不可变原始事件。"""

    event_id: str
    kind: ContextEventKind
    role: str
    content: str
    turn: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_turn(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "_event_id": self.event_id,
            "_event_kind": self.kind.value,
            "_turn": self.turn,
            "_metadata": dict(self.metadata),
        }


@dataclass
class ContextItem:
    """由一个或多个原始事件派生出的工作上下文单元。"""

    chunk_id: str
    ctype: ContextType
    text: str
    source: str = ""
    role: str = "user"
    source_turn: int = 0
    source_event_ids: tuple[str, ...] = ()
    status: ContextStatus = ContextStatus.ACTIVE
    parent_chunk_id: str | None = None
    importance: float | None = None
    action: CompressionAction = CompressionAction.KEEP
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_event_ids:
            self.source_event_ids = (self.chunk_id,)
        if not self.token_count:
            self.token_count = estimate_tokens(self.text)


# 兼容原有公开 API；新代码以 ContextItem 为规范名称。
ContextChunk = ContextItem


@dataclass
class CompressedResult:
    phase: TaskPhase
    chunks: list[ContextItem] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    archived_chunks: list[ContextItem] = field(default_factory=list)
    budget_pressure: BudgetPressure = BudgetPressure.NORMAL
    budget_target_tokens: int | None = None
    hard_budget_exceeded: bool = False

    @property
    def compression_ratio(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return 1 - self.tokens_after / self.tokens_before


@dataclass
class LifecycleRecord:
    event: LifecycleEvent
    turn: int
    phase: TaskPhase
    chunk_id: str | None = None
    detail: str = ""
    source_event_ids: tuple[str, ...] = ()
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextSnapshot:
    turn: int
    phase: TaskPhase
    chunks: list[ContextItem] = field(default_factory=list)
    tokens: int = 0
    archived_items: int = 0
    recovered_items: int = 0
    budget_pressure: BudgetPressure = BudgetPressure.NORMAL
    budget_limit_tokens: int | None = None
    budget_exceeded: bool = False
    checkpoint_count: int = 0
