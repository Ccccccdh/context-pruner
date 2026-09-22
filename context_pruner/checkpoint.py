"""压缩决策的事件级检查点与反事实分支评估。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from .types import CompressionAction, ContextItem, ContextStatus, ContextType, TaskPhase


@dataclass(frozen=True)
class ContextCheckpoint:
    """同一压缩边界上的完整上下文与处理后上下文。"""

    checkpoint_id: str
    turn: int
    phase: TaskPhase
    task_state: str
    baseline_chunks: tuple[ContextItem, ...]
    treated_chunks: tuple[ContextItem, ...]
    archived_chunks: tuple[ContextItem, ...] = ()
    actions: dict[str, str] = field(default_factory=dict)
    baseline_tokens: int = 0
    treated_tokens: int = 0
    reason: str = "before_model"

    @property
    def token_savings(self) -> int:
        return self.baseline_tokens - self.treated_tokens


@dataclass(frozen=True)
class BranchOutcome:
    """由调用方的任务判分器给出的一个分支结果。"""

    quality_score: float
    token_cost: int
    success: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CounterfactualEvaluation:
    """在相同检查点上比较完整历史分支与压缩分支。"""

    checkpoint_id: str
    baseline: BranchOutcome
    treated: BranchOutcome
    quality_delta: float
    token_delta: int
    token_savings_rate: float
    accepted: bool


BranchEvaluator = Callable[[list[ContextItem], str], BranchOutcome | dict[str, Any]]


def evaluate_checkpoint(
    checkpoint: ContextCheckpoint,
    evaluator: BranchEvaluator,
    *,
    quality_tolerance: float = 0.0,
) -> CounterfactualEvaluation:
    """用同一个判分器执行两条分支，避免任务和起点差异污染结论。"""

    baseline = _coerce_outcome(
        evaluator(deepcopy(list(checkpoint.baseline_chunks)), "baseline")
    )
    treated = _coerce_outcome(
        evaluator(deepcopy(list(checkpoint.treated_chunks)), "treated")
    )
    quality_delta = treated.quality_score - baseline.quality_score
    token_delta = treated.token_cost - baseline.token_cost
    savings_rate = -token_delta / max(1, baseline.token_cost)
    accepted = quality_delta >= -abs(quality_tolerance) and token_delta <= 0
    return CounterfactualEvaluation(
        checkpoint_id=checkpoint.checkpoint_id,
        baseline=baseline,
        treated=treated,
        quality_delta=quality_delta,
        token_delta=token_delta,
        token_savings_rate=savings_rate,
        accepted=accepted,
    )


class InMemoryCheckpointStore:
    """轻量检查点仓库；保存和读取都复制数据，防止评测分支污染原状态。"""

    def __init__(self) -> None:
        self._entries: list[ContextCheckpoint] = []
        self._sequence = 0

    def capture(
        self,
        *,
        turn: int,
        phase: TaskPhase,
        task_state: str,
        baseline_chunks: list[ContextItem],
        treated_chunks: list[ContextItem],
        archived_chunks: list[ContextItem] | None = None,
        actions: dict[str, str] | None = None,
        baseline_tokens: int = 0,
        treated_tokens: int = 0,
        reason: str = "before_model",
    ) -> ContextCheckpoint:
        checkpoint = ContextCheckpoint(
            checkpoint_id=f"ckpt_{self._sequence:06d}",
            turn=turn,
            phase=phase,
            task_state=task_state,
            baseline_chunks=tuple(deepcopy(baseline_chunks)),
            treated_chunks=tuple(deepcopy(treated_chunks)),
            archived_chunks=tuple(deepcopy(archived_chunks or [])),
            actions=dict(actions or {}),
            baseline_tokens=baseline_tokens,
            treated_tokens=treated_tokens,
            reason=reason,
        )
        self._sequence += 1
        self._entries.append(checkpoint)
        return deepcopy(checkpoint)

    def put(self, checkpoint: ContextCheckpoint) -> None:
        self._entries.append(deepcopy(checkpoint))
        numeric = checkpoint.checkpoint_id.removeprefix("ckpt_")
        if numeric.isdigit():
            self._sequence = max(self._sequence, int(numeric) + 1)

    def get(self, checkpoint_id: str) -> ContextCheckpoint | None:
        for checkpoint in self._entries:
            if checkpoint.checkpoint_id == checkpoint_id:
                return deepcopy(checkpoint)
        return None

    def latest(self) -> ContextCheckpoint | None:
        return deepcopy(self._entries[-1]) if self._entries else None

    def entries(self) -> list[ContextCheckpoint]:
        return deepcopy(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def export_state(self) -> dict[str, Any]:
        return {
            "sequence": self._sequence,
            "entries": [_checkpoint_to_dict(entry) for entry in self._entries],
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "InMemoryCheckpointStore":
        store = cls()
        store._entries = [
            _checkpoint_from_dict(row) for row in state.get("entries", [])
        ]
        store._sequence = int(state.get("sequence", len(store._entries)))
        return store


def _coerce_outcome(value: BranchOutcome | dict[str, Any]) -> BranchOutcome:
    if isinstance(value, BranchOutcome):
        return value
    return BranchOutcome(
        quality_score=float(value.get("quality_score", 0.0)),
        token_cost=int(value.get("token_cost", 0)),
        success=value.get("success"),
        metadata=dict(value.get("metadata") or {}),
    )


def _checkpoint_to_dict(checkpoint: ContextCheckpoint) -> dict[str, Any]:
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "turn": checkpoint.turn,
        "phase": checkpoint.phase.value,
        "task_state": checkpoint.task_state,
        "baseline_chunks": [_item_to_dict(item) for item in checkpoint.baseline_chunks],
        "treated_chunks": [_item_to_dict(item) for item in checkpoint.treated_chunks],
        "archived_chunks": [_item_to_dict(item) for item in checkpoint.archived_chunks],
        "actions": checkpoint.actions,
        "baseline_tokens": checkpoint.baseline_tokens,
        "treated_tokens": checkpoint.treated_tokens,
        "reason": checkpoint.reason,
    }


def _checkpoint_from_dict(data: dict[str, Any]) -> ContextCheckpoint:
    return ContextCheckpoint(
        checkpoint_id=str(data["checkpoint_id"]),
        turn=int(data.get("turn", 0)),
        phase=TaskPhase(data.get("phase", TaskPhase.EXPLORATION.value)),
        task_state=str(data.get("task_state", "")),
        baseline_chunks=tuple(_item_from_dict(row) for row in data.get("baseline_chunks", [])),
        treated_chunks=tuple(_item_from_dict(row) for row in data.get("treated_chunks", [])),
        archived_chunks=tuple(_item_from_dict(row) for row in data.get("archived_chunks", [])),
        actions=dict(data.get("actions") or {}),
        baseline_tokens=int(data.get("baseline_tokens", 0)),
        treated_tokens=int(data.get("treated_tokens", 0)),
        reason=str(data.get("reason", "before_model")),
    )


def _item_to_dict(item: ContextItem) -> dict[str, Any]:
    return {
        "chunk_id": item.chunk_id,
        "ctype": item.ctype.value,
        "text": item.text,
        "source": item.source,
        "role": item.role,
        "source_turn": item.source_turn,
        "source_event_ids": list(item.source_event_ids),
        "status": item.status.value,
        "parent_chunk_id": item.parent_chunk_id,
        "importance": item.importance,
        "action": item.action.value,
        "token_count": item.token_count,
        "metadata": item.metadata,
    }


def _item_from_dict(data: dict[str, Any]) -> ContextItem:
    return ContextItem(
        chunk_id=str(data["chunk_id"]),
        ctype=ContextType(data["ctype"]),
        text=str(data.get("text", "")),
        source=str(data.get("source", "")),
        role=str(data.get("role", "user")),
        source_turn=int(data.get("source_turn", 0)),
        source_event_ids=tuple(data.get("source_event_ids", [])),
        status=ContextStatus(data.get("status", ContextStatus.ACTIVE.value)),
        parent_chunk_id=data.get("parent_chunk_id"),
        importance=data.get("importance"),
        action=CompressionAction(data.get("action", CompressionAction.KEEP.value)),
        token_count=int(data.get("token_count", 0)),
        metadata=dict(data.get("metadata") or {}),
    )
