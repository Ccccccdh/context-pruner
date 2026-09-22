"""事件驱动的上下文生命周期控制器。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from .archive import ArchiveStore, InMemoryArchiveStore
from .checkpoint import InMemoryCheckpointStore
from .evaluator import ImportanceScorer, evaluate
from .parser import parse_context
from .pipeline import ContextPruner
from .scheduler import detect_phase
from .types import (
    CompressionAction,
    BudgetPressure,
    ContextBudget,
    ContextEvent,
    ContextEventKind,
    ContextItem,
    ContextSnapshot,
    ContextStatus,
    LifecycleEvent,
    LifecycleRecord,
    TaskPhase,
)


class ContextLifecycleManager:
    """管理原始事件、工作上下文、旁路归档和选择性恢复。

    原始 ContextEvent 不会因压缩而修改；模型实际看到的是 snapshot()
    中的工作副本。被压缩或合并的原文进入 archive_store，恢复只把检索
    命中的原始项临时注入工作上下文。
    """

    def __init__(
        self,
        task_state: str = "",
        scorer: ImportanceScorer | None = None,
        summarizer=None,
        pruner=None,
        archive_store: ArchiveStore | None = None,
        recovery_limit: int = 3,
        recovery_ttl_events: int = 4,
        checkpoint_store: InMemoryCheckpointStore | None = None,
        capture_checkpoints: bool = True,
    ):
        self.task_state = task_state
        self.scorer = scorer
        self.summarizer = summarizer
        self.pruner = pruner or ContextPruner(scorer=scorer, summarizer=summarizer)
        self.archive_store = archive_store if archive_store is not None else InMemoryArchiveStore()
        self.recovery_limit = max(1, recovery_limit)
        self.recovery_ttl_events = max(1, recovery_ttl_events)
        self.checkpoint_store = (
            checkpoint_store
            if checkpoint_store is not None
            else InMemoryCheckpointStore()
        )
        self.capture_checkpoints = capture_checkpoints

        self.events: list[ContextEvent] = []
        self.context_events: list[ContextEvent] = []
        self.chunks: list[ContextItem] = []
        self.records: list[LifecycleRecord] = []
        self.phase = TaskPhase.EXPLORATION
        self.turn_count = 0
        self.recovery_count = 0
        self.recovered_tokens = 0
        self.recovery_events: list[dict[str, Any]] = []
        self._last_budget_pressure = BudgetPressure.NORMAL
        self._last_budget_limit: int | None = None
        self._last_budget_exceeded = False

        self._event_sequence = 0
        self._status_by_id: dict[str, ContextStatus] = {}
        self._recovery_overlay: dict[str, tuple[ContextItem, int]] = {}
        self._forced_archive_ids: set[str] = set()

    @property
    def turns(self) -> list[dict[str, Any]]:
        return [event.to_turn() for event in self.context_events]

    @property
    def archive_count(self) -> int:
        return len(self.archive_store)

    @property
    def checkpoint_count(self) -> int:
        return len(self.checkpoint_store)

    def record_boundary(
        self,
        kind: ContextEventKind,
        metadata: dict[str, Any] | None = None,
    ) -> ContextEvent:
        """记录不进入 prompt 的运行边界，例如模型调用前和任务结束。"""
        event = self._new_event(kind, "", "", metadata)
        self.events.append(event)
        return event

    def observe_turn(
        self,
        turn: dict,
        task_state: str | None = None,
        kind: ContextEventKind | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextSnapshot:
        """记录一条会进入上下文的模型或工具事件。"""
        if task_state is not None:
            self.task_state = task_state
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", ""))
        resolved_kind = kind or self._infer_kind(role, content)
        combined_metadata = {
            **dict(turn.get("_metadata") or {}),
            **dict(metadata or {}),
        }
        event = self._new_event(resolved_kind, role, content, combined_metadata)
        self.events.append(event)
        self.context_events.append(event)
        self._set_status(event.event_id, ContextStatus.ACTIVE)
        self.turn_count += 1
        self._expire_recovery_overlay()

        chunks = evaluate(parse_context(self.turns), self.task_state, self.scorer)
        for chunk in chunks:
            chunk.status = self._status_by_id.get(chunk.chunk_id, ContextStatus.ACTIVE)
        chunks = [chunk for chunk in chunks if chunk.status != ContextStatus.DISCARDED]

        new_phase = detect_phase(self.turns)
        if new_phase != self.phase:
            self.records.append(
                LifecycleRecord(LifecycleEvent.PHASE_CHANGED, self.turn_count, new_phase)
            )
        self.phase = new_phase
        self.chunks = chunks
        newest = chunks[-1] if chunks else None
        self.records.append(
            LifecycleRecord(
                LifecycleEvent.GENERATED,
                self.turn_count,
                self.phase,
                chunk_id=newest.chunk_id if newest else None,
                source_event_ids=newest.source_event_ids if newest else (),
                token_count=newest.token_count if newest else 0,
                metadata={"kind": resolved_kind.value},
            )
        )
        self.records.append(
            LifecycleRecord(LifecycleEvent.EVALUATED, self.turn_count, self.phase)
        )
        return self.snapshot()

    def compress(
        self,
        reason: str = "before_model",
        budget: ContextBudget | None = None,
    ) -> ContextSnapshot:
        """压缩工作副本，并把所有发生有损变化的原始项归档。"""
        if not self.context_events:
            return self.snapshot()
        self._expire_recovery_overlay()

        originals = evaluate(parse_context(self.turns), self.task_state, self.scorer)
        original_by_id = {
            source_id: item
            for item in originals
            for source_id in item.source_event_ids
        }
        try:
            result = self.pruner.compress(
                self.turns,
                task_state=self.task_state,
                budget=budget,
            )
        except TypeError as error:
            # v0 或第三方兼容压缩器可能尚未声明 budget 参数。
            if "budget" not in str(error):
                raise
            result = self.pruner.compress(self.turns, task_state=self.task_state)
        action_counts: Counter[str] = Counter()
        working: list[ContextItem] = []
        archived_ids: set[str] = set()

        previous_chunks = list(self.chunks)
        for item in result.chunks:
            if any(
                self._status_by_id.get(source_id) == ContextStatus.DISCARDED
                for source_id in item.source_event_ids
            ):
                continue
            if self._forced_archive_ids.intersection(item.source_event_ids):
                # 故障注入评测中被主动移出的证据只能通过 recovery overlay 返回。
                continue
            previously_archived = any(
                self._status_by_id.get(source_id) == ContextStatus.ARCHIVED
                for source_id in item.source_event_ids
            )
            if previously_archived and item.action == CompressionAction.KEEP:
                prior = next(
                    (
                        candidate
                        for candidate in previous_chunks
                        if set(candidate.source_event_ids).intersection(item.source_event_ids)
                        and candidate.action != CompressionAction.KEEP
                    ),
                    None,
                )
                item = prior if prior is not None else replace(
                    item,
                    text=f"[已归档:{','.join(item.source_event_ids)}]",
                    token_count=max(4, len(item.source_event_ids) * 4),
                    action=CompressionAction.ARCHIVE,
                    status=ContextStatus.COMPRESSED,
                    metadata={**item.metadata, "archive_reference": True},
                )
            action_counts[item.action.value] += 1
            if item.action == CompressionAction.KEEP:
                working.append(replace(item, status=ContextStatus.ACTIVE))
                continue

            working.append(replace(item, status=ContextStatus.COMPRESSED))
            for source_id in item.source_event_ids:
                original = original_by_id.get(source_id)
                if original is None:
                    continue
                archive_size_before = self.archive_count
                entry = self.archive_store.put(original, self.turn_count, item.action.value)
                archived_ids.add(source_id)
                self._set_status(source_id, ContextStatus.ARCHIVED)
                if self.archive_count > archive_size_before:
                    self.records.append(
                        LifecycleRecord(
                            LifecycleEvent.ARCHIVED,
                            self.turn_count,
                            self.phase,
                            chunk_id=entry.archive_id,
                            detail=item.action.value,
                            source_event_ids=(source_id,),
                            token_count=original.token_count,
                        )
                    )

        for item in result.archived_chunks:
            for source_id in item.source_event_ids:
                original = original_by_id.get(source_id)
                if original is None:
                    continue
                archive_size_before = self.archive_count
                entry = self.archive_store.put(original, self.turn_count, CompressionAction.ARCHIVE.value)
                archived_ids.add(source_id)
                self._set_status(source_id, ContextStatus.ARCHIVED)
                if self.archive_count > archive_size_before:
                    self.records.append(
                        LifecycleRecord(
                            LifecycleEvent.ARCHIVED,
                            self.turn_count,
                            self.phase,
                            chunk_id=entry.archive_id,
                            detail=CompressionAction.ARCHIVE.value,
                            source_event_ids=(source_id,),
                            token_count=original.token_count,
                        )
                    )

        self.chunks = working
        if self.capture_checkpoints:
            baseline = [
                item
                for item in originals
                if not any(
                    self._status_by_id.get(source_id) == ContextStatus.DISCARDED
                    for source_id in item.source_event_ids
                )
                and not self._forced_archive_ids.intersection(item.source_event_ids)
            ]
            treated = self.snapshot().chunks
            actions = {
                source_id: item.action.value
                for item in [*treated, *result.archived_chunks]
                for source_id in item.source_event_ids
            }
            checkpoint = self.checkpoint_store.capture(
                turn=self.turn_count,
                phase=result.phase,
                task_state=self.task_state,
                baseline_chunks=baseline,
                treated_chunks=treated,
                archived_chunks=result.archived_chunks,
                actions=actions,
                baseline_tokens=sum(item.token_count for item in baseline),
                treated_tokens=sum(item.token_count for item in treated),
                reason=reason,
            )
            self.records.append(
                LifecycleRecord(
                    LifecycleEvent.CHECKPOINTED,
                    self.turn_count,
                    result.phase,
                    chunk_id=checkpoint.checkpoint_id,
                    detail=reason,
                    source_event_ids=tuple(actions),
                    token_count=checkpoint.token_savings,
                    metadata={
                        "baseline_tokens": checkpoint.baseline_tokens,
                        "treated_tokens": checkpoint.treated_tokens,
                    },
                )
            )
        self.records.append(
            LifecycleRecord(
                LifecycleEvent.COMPRESSED,
                self.turn_count,
                self.phase,
                detail=reason,
                source_event_ids=tuple(sorted(archived_ids)),
                token_count=max(0, result.tokens_before - result.tokens_after),
                metadata={
                    "tokens_before": result.tokens_before,
                    "tokens_after": result.tokens_after,
                    "actions": dict(action_counts),
                },
            )
        )
        if result.budget_pressure != BudgetPressure.NORMAL:
            self.records.append(
                LifecycleRecord(
                    LifecycleEvent.BUDGET_PRESSURE,
                    self.turn_count,
                    self.phase,
                    detail=result.budget_pressure.value,
                    token_count=result.tokens_after,
                    metadata={
                        "target_tokens": result.budget_target_tokens,
                        "hard_budget_exceeded": result.hard_budget_exceeded,
                    },
                )
            )
        if result.hard_budget_exceeded:
            self.records.append(
                LifecycleRecord(
                    LifecycleEvent.BUDGET_VIOLATION,
                    self.turn_count,
                    self.phase,
                    token_count=result.tokens_after,
                    metadata={"hard_limit_tokens": budget.hard_limit_tokens if budget else None},
                )
            )
        self._last_budget_pressure = result.budget_pressure
        self._last_budget_limit = budget.hard_limit_tokens if budget else None
        self._last_budget_exceeded = result.hard_budget_exceeded
        return self.snapshot()

    def force_archive_matching(
        self,
        terms: list[str],
        reason: str = "fault_injection",
    ) -> ContextSnapshot:
        """确定性地移出匹配证据，用于验证恢复模块，不在正常运行路径调用。"""
        lowered = [str(term).strip().lower() for term in terms if str(term).strip()]
        if not lowered:
            return self.snapshot()
        retained: list[ContextItem] = []
        for item in self.chunks:
            # 只模拟工具证据丢失，不移出携带同一关键词的故障提示本身。
            matches_evidence = item.role in {"user", "tool", "function"} and any(
                term in item.text.lower() for term in lowered
            )
            if not matches_evidence:
                retained.append(item)
                continue
            entry = self.archive_store.put(item, self.turn_count, reason)
            for source_id in item.source_event_ids:
                self._forced_archive_ids.add(source_id)
                self._set_status(source_id, ContextStatus.ARCHIVED)
            self.records.append(
                LifecycleRecord(
                    LifecycleEvent.ARCHIVED,
                    self.turn_count,
                    self.phase,
                    chunk_id=entry.archive_id,
                    detail=reason,
                    source_event_ids=item.source_event_ids,
                    token_count=item.token_count,
                    metadata={"terms": lowered},
                )
            )
        self.chunks = retained
        return self.snapshot()

    def recover(
        self,
        reason: str = "",
        query: str = "",
        limit: int | None = None,
    ) -> ContextSnapshot:
        """从归档中检索相关原文，临时替换其压缩表示。"""
        if not self.archive_count:
            return self.snapshot()
        # 故障检测器给出的查询通常比完整任务描述更具体。把两者直接拼接会让
        # 长任务中的高频词淹没缺失实体，因此仅在没有明确查询时回退到 task_state。
        search_query = query.strip() or self.task_state.strip()
        entries = self.archive_store.search(search_query, limit or self.recovery_limit)
        if not entries:
            return self.snapshot()

        source_ids: list[str] = []
        recovered_tokens = 0
        expires_at = self.turn_count + self.recovery_ttl_events
        for entry in entries:
            recovered = replace(
                entry.item,
                status=ContextStatus.RECOVERED,
                action=CompressionAction.RESTORE,
                metadata={
                    **entry.item.metadata,
                    "archive_id": entry.archive_id,
                    "recovery_reason": reason,
                },
            )
            for source_id in recovered.source_event_ids:
                self._recovery_overlay[source_id] = (recovered, expires_at)
                self._set_status(source_id, ContextStatus.RECOVERED)
                source_ids.append(source_id)
            recovered_tokens += recovered.token_count

        self.recovery_count += 1
        self.recovered_tokens += recovered_tokens
        recovery_event = {
            "turn": self.turn_count,
            "reason": reason,
            "query": query,
            "source_event_ids": list(dict.fromkeys(source_ids)),
            "tokens": recovered_tokens,
            "recovered_texts": [entry.item.text for entry in entries],
        }
        self.recovery_events.append(recovery_event)
        self.records.append(
            LifecycleRecord(
                LifecycleEvent.RECOVERED,
                self.turn_count,
                self.phase,
                detail=reason,
                source_event_ids=tuple(dict.fromkeys(source_ids)),
                token_count=recovered_tokens,
                metadata={"query": query, "expires_at": expires_at},
            )
        )
        return self.snapshot()

    def discard(
        self,
        source_event_ids: list[str] | tuple[str, ...],
        *,
        purge_archive: bool = False,
        reason: str = "retention_policy",
    ) -> ContextSnapshot:
        """淘汰指定来源；可选同时从旁路归档物理删除。"""
        targets = {str(value) for value in source_event_ids if str(value)}
        if not targets:
            return self.snapshot()
        self.chunks = [
            item for item in self.chunks
            if not targets.intersection(item.source_event_ids)
        ]
        for source_id in targets:
            self._recovery_overlay.pop(source_id, None)
            self._forced_archive_ids.discard(source_id)
            self._set_status(source_id, ContextStatus.DISCARDED)
        purged = 0
        if purge_archive:
            archive_ids = [
                entry.archive_id
                for entry in self.archive_store.entries()
                if targets.intersection(entry.item.source_event_ids)
            ]
            purged = self.archive_store.delete(archive_ids)
        self.records.append(
            LifecycleRecord(
                LifecycleEvent.DISCARDED,
                self.turn_count,
                self.phase,
                detail=reason,
                source_event_ids=tuple(sorted(targets)),
                metadata={"purge_archive": purge_archive, "purged_entries": purged},
            )
        )
        return self.snapshot()

    def export_state(self, include_archive: bool = True) -> dict[str, Any]:
        """导出可 JSON 序列化的完整生命周期状态。"""
        state = {
            "schema_version": 1,
            "task_state": self.task_state,
            "phase": self.phase.value,
            "turn_count": self.turn_count,
            "recovery_count": self.recovery_count,
            "recovered_tokens": self.recovered_tokens,
            "recovery_events": list(self.recovery_events),
            "event_sequence": self._event_sequence,
            "events": [_event_to_dict(event) for event in self.events],
            "context_event_ids": [event.event_id for event in self.context_events],
            "chunks": [_item_to_dict(item) for item in self.chunks],
            "records": [_record_to_dict(record) for record in self.records],
            "status_by_id": {
                key: value.value for key, value in self._status_by_id.items()
            },
            "recovery_overlay": [
                {
                    "source_id": source_id,
                    "item": _item_to_dict(item),
                    "expires_at": expires_at,
                }
                for source_id, (item, expires_at) in self._recovery_overlay.items()
            ],
            "forced_archive_ids": sorted(self._forced_archive_ids),
            "last_budget_pressure": self._last_budget_pressure.value,
            "last_budget_limit": self._last_budget_limit,
            "last_budget_exceeded": self._last_budget_exceeded,
            "capture_checkpoints": self.capture_checkpoints,
            "checkpoint_store": self.checkpoint_store.export_state(),
        }
        if include_archive:
            state["archive_entries"] = [
                {
                    "item": _item_to_dict(entry.item),
                    "archived_at_turn": entry.archived_at_turn,
                    "reason": entry.reason,
                }
                for entry in self.archive_store.entries()
            ]
        return state

    def save_state(self, path: str | Path, include_archive: bool = True) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.export_state(include_archive), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        *,
        scorer: ImportanceScorer | None = None,
        summarizer=None,
        pruner=None,
        archive_store: ArchiveStore | None = None,
        recovery_limit: int = 3,
        recovery_ttl_events: int = 4,
        checkpoint_store: InMemoryCheckpointStore | None = None,
    ) -> "ContextLifecycleManager":
        if int(state.get("schema_version", 0)) != 1:
            raise ValueError("不支持的生命周期状态版本")
        manager = cls(
            task_state=str(state.get("task_state", "")),
            scorer=scorer,
            summarizer=summarizer,
            pruner=pruner,
            archive_store=archive_store,
            recovery_limit=recovery_limit,
            recovery_ttl_events=recovery_ttl_events,
            checkpoint_store=checkpoint_store,
            capture_checkpoints=bool(state.get("capture_checkpoints", True)),
        )
        manager.events = [_event_from_dict(row) for row in state.get("events", [])]
        event_by_id = {event.event_id: event for event in manager.events}
        manager.context_events = [
            event_by_id[event_id]
            for event_id in state.get("context_event_ids", [])
            if event_id in event_by_id
        ]
        manager.chunks = [_item_from_dict(row) for row in state.get("chunks", [])]
        manager.records = [_record_from_dict(row) for row in state.get("records", [])]
        manager.phase = TaskPhase(state.get("phase", TaskPhase.EXPLORATION.value))
        manager.turn_count = int(state.get("turn_count", 0))
        manager.recovery_count = int(state.get("recovery_count", 0))
        manager.recovered_tokens = int(state.get("recovered_tokens", 0))
        manager.recovery_events = list(state.get("recovery_events", []))
        manager._event_sequence = int(state.get("event_sequence", len(manager.events)))
        manager._status_by_id = {
            key: ContextStatus(value)
            for key, value in dict(state.get("status_by_id") or {}).items()
        }
        manager._recovery_overlay = {
            str(row["source_id"]): (
                _item_from_dict(row["item"]),
                int(row["expires_at"]),
            )
            for row in state.get("recovery_overlay", [])
        }
        manager._forced_archive_ids = set(state.get("forced_archive_ids", []))
        manager._last_budget_pressure = BudgetPressure(
            state.get("last_budget_pressure", BudgetPressure.NORMAL.value)
        )
        manager._last_budget_limit = state.get("last_budget_limit")
        manager._last_budget_exceeded = bool(state.get("last_budget_exceeded", False))
        if checkpoint_store is None:
            manager.checkpoint_store = InMemoryCheckpointStore.from_state(
                dict(state.get("checkpoint_store") or {})
            )
        else:
            restored = InMemoryCheckpointStore.from_state(
                dict(state.get("checkpoint_store") or {})
            )
            for checkpoint in restored.entries():
                checkpoint_store.put(checkpoint)
        for row in state.get("archive_entries", []):
            manager.archive_store.put(
                _item_from_dict(row["item"]),
                int(row.get("archived_at_turn", 0)),
                str(row.get("reason", "restored_state")),
            )
        return manager

    @classmethod
    def load_state(cls, path: str | Path, **kwargs) -> "ContextLifecycleManager":
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_state(state, **kwargs)

    def snapshot(self) -> ContextSnapshot:
        active_recovered = {
            source_id: item
            for source_id, (item, expires_at) in self._recovery_overlay.items()
            if expires_at >= self.turn_count
        }
        recovered_ids = set(active_recovered)
        visible = [
            item
            for item in self.chunks
            if item.status != ContextStatus.DISCARDED
            # 普通压缩块在恢复原文时应由原文临时替换；但结构化任务记忆是由
            # 多个证据来源组成的事实全集。恢复其中一个来源不能隐藏整块记忆，
            # 否则其余来源会同时从模型上下文中消失。
            and (
                item.metadata.get("structured_memory")
                or not recovered_ids.intersection(item.source_event_ids)
            )
        ]
        seen_chunks: set[str] = set()
        for item in active_recovered.values():
            if item.chunk_id not in seen_chunks:
                visible.append(item)
                seen_chunks.add(item.chunk_id)
        visible.sort(key=lambda item: (item.source_turn, item.chunk_id))
        return ContextSnapshot(
            turn=self.turn_count,
            phase=self.phase,
            chunks=visible,
            tokens=sum(item.token_count for item in visible),
            archived_items=self.archive_count,
            recovered_items=len(seen_chunks),
            budget_pressure=self._last_budget_pressure,
            budget_limit_tokens=self._last_budget_limit,
            budget_exceeded=self._last_budget_exceeded,
            checkpoint_count=self.checkpoint_count,
        )

    def validate_invariants(self, raise_on_error: bool = True) -> list[str]:
        """检查事件、来源映射、状态与恢复覆盖层的一致性。"""
        errors: list[str] = []
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            errors.append("事件 ID 不唯一")
        known_context_ids = {event.event_id for event in self.context_events}
        for chunk in self.chunks:
            missing = set(chunk.source_event_ids) - known_context_ids
            if missing:
                errors.append(f"chunk {chunk.chunk_id} 引用了未知来源：{sorted(missing)}")
            if any(
                self._status_by_id.get(source_id) == ContextStatus.DISCARDED
                for source_id in chunk.source_event_ids
            ):
                errors.append(f"已淘汰来源仍出现在工作上下文：{chunk.chunk_id}")
        for source_id in self._recovery_overlay:
            if source_id not in known_context_ids:
                errors.append(f"恢复覆盖层引用未知来源：{source_id}")
        if raise_on_error and errors:
            raise RuntimeError("；".join(errors))
        return errors

    def _expire_recovery_overlay(self) -> None:
        expired = [
            source_id
            for source_id, (_, expires_at) in self._recovery_overlay.items()
            if expires_at < self.turn_count
        ]
        for source_id in expired:
            del self._recovery_overlay[source_id]
            self._set_status(source_id, ContextStatus.ARCHIVED)

    def _set_status(self, source_id: str, new_status: ContextStatus) -> None:
        current = self._status_by_id.get(source_id, ContextStatus.CREATED)
        if current == new_status:
            return
        allowed = {
            ContextStatus.CREATED: {ContextStatus.ACTIVE, ContextStatus.DISCARDED},
            ContextStatus.ACTIVE: {
                ContextStatus.COMPRESSED,
                ContextStatus.ARCHIVED,
                ContextStatus.DISCARDED,
            },
            ContextStatus.COMPRESSED: {
                ContextStatus.ACTIVE,
                ContextStatus.ARCHIVED,
                ContextStatus.DISCARDED,
            },
            ContextStatus.ARCHIVED: {
                ContextStatus.RECOVERED,
                ContextStatus.DISCARDED,
            },
            ContextStatus.RECOVERED: {
                ContextStatus.ACTIVE,
                ContextStatus.ARCHIVED,
                ContextStatus.DISCARDED,
            },
            ContextStatus.DISCARDED: set(),
        }
        if new_status not in allowed[current]:
            raise RuntimeError(f"非法生命周期转换：{current.value} -> {new_status.value}")
        self._status_by_id[source_id] = new_status

    def _new_event(
        self,
        kind: ContextEventKind,
        role: str,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> ContextEvent:
        event = ContextEvent(
            event_id=f"evt_{self._event_sequence:06d}",
            kind=kind,
            role=role,
            content=content,
            turn=self.turn_count,
            metadata=dict(metadata or {}),
        )
        self._event_sequence += 1
        return event

    @staticmethod
    def _infer_kind(role: str, content: str) -> ContextEventKind:
        if content.startswith("观察：错误") or content.startswith("错误"):
            return ContextEventKind.TOOL_ERROR
        if content.startswith("观察：") or role in {"tool", "function"}:
            return ContextEventKind.TOOL_RESULT
        return ContextEventKind.MODEL_OUTPUT


def _event_to_dict(event: ContextEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "kind": event.kind.value,
        "role": event.role,
        "content": event.content,
        "turn": event.turn,
        "metadata": event.metadata,
    }


def _event_from_dict(data: dict[str, Any]) -> ContextEvent:
    return ContextEvent(
        event_id=str(data["event_id"]),
        kind=ContextEventKind(data["kind"]),
        role=str(data.get("role", "")),
        content=str(data.get("content", "")),
        turn=int(data.get("turn", 0)),
        metadata=dict(data.get("metadata") or {}),
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
    from .types import ContextType

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


def _record_to_dict(record: LifecycleRecord) -> dict[str, Any]:
    return {
        "event": record.event.value,
        "turn": record.turn,
        "phase": record.phase.value,
        "chunk_id": record.chunk_id,
        "detail": record.detail,
        "source_event_ids": list(record.source_event_ids),
        "token_count": record.token_count,
        "metadata": record.metadata,
    }


def _record_from_dict(data: dict[str, Any]) -> LifecycleRecord:
    return LifecycleRecord(
        event=LifecycleEvent(data["event"]),
        turn=int(data.get("turn", 0)),
        phase=TaskPhase(data.get("phase", TaskPhase.EXPLORATION.value)),
        chunk_id=data.get("chunk_id"),
        detail=str(data.get("detail", "")),
        source_event_ids=tuple(data.get("source_event_ids", [])),
        token_count=int(data.get("token_count", 0)),
        metadata=dict(data.get("metadata") or {}),
    )
