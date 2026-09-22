"""原始上下文旁路归档与轻量选择性检索。"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from .types import CompressionAction, ContextItem, ContextStatus, ContextType


@dataclass
class ArchiveEntry:
    archive_id: str
    item: ContextItem
    archived_at_turn: int
    reason: str
    retrieval_count: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ArchivePolicy:
    """归档安全与保留策略；默认脱敏，不自动删除。"""

    redact_sensitive: bool = True
    max_entries: int | None = None
    max_age_turns: int | None = None

    def __post_init__(self) -> None:
        if self.max_entries is not None and self.max_entries <= 0:
            raise ValueError("max_entries 必须大于 0")
        if self.max_age_turns is not None and self.max_age_turns < 0:
            raise ValueError("max_age_turns 不能为负数")


class ArchiveStore(Protocol):
    def put(self, item: ContextItem, turn: int, reason: str) -> ArchiveEntry: ...

    def search(self, query: str, limit: int = 3) -> list[ArchiveEntry]: ...

    def __len__(self) -> int: ...

    def entries(self) -> list[ArchiveEntry]: ...

    def delete(self, archive_ids: list[str]) -> int: ...

    def prune(self, current_turn: int) -> int: ...


class InMemoryArchiveStore:
    """会话内旁路存储，与 SQLite 实现共享检索和保留策略。"""

    def __init__(self, policy: ArchivePolicy | None = None) -> None:
        self._entries: dict[str, ArchiveEntry] = {}
        self.policy = policy or ArchivePolicy()

    def put(self, item: ContextItem, turn: int, reason: str) -> ArchiveEntry:
        source_ids = item.source_event_ids or (item.chunk_id,)
        archive_id = "arc:" + "+".join(source_ids)
        existing = self._entries.get(archive_id)
        if existing is not None:
            return existing
        archived_item = _prepare_archived_item(
            item,
            f"memory://{archive_id}",
            self.policy,
        )
        entry = ArchiveEntry(archive_id, archived_item, turn, reason)
        self._entries[archive_id] = entry
        self.prune(turn)
        return entry

    def search(self, query: str, limit: int = 3) -> list[ArchiveEntry]:
        selected = _rank_entries(list(self._entries.values()), query, limit)
        for entry in selected:
            entry.retrieval_count += 1
        return selected

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[ArchiveEntry]:
        return list(self._entries.values())

    def delete(self, archive_ids: list[str]) -> int:
        removed = 0
        for archive_id in dict.fromkeys(archive_ids):
            if self._entries.pop(str(archive_id), None) is not None:
                removed += 1
        return removed

    def prune(self, current_turn: int) -> int:
        candidates: list[str] = []
        if self.policy.max_age_turns is not None:
            oldest = int(current_turn) - self.policy.max_age_turns
            candidates.extend(
                entry.archive_id
                for entry in self._entries.values()
                if entry.archived_at_turn < oldest
            )
        remaining = [
            entry for entry in self._entries.values()
            if entry.archive_id not in set(candidates)
        ]
        if self.policy.max_entries is not None and len(remaining) > self.policy.max_entries:
            remaining.sort(key=lambda entry: (entry.archived_at_turn, entry.archive_id))
            overflow = len(remaining) - self.policy.max_entries
            candidates.extend(entry.archive_id for entry in remaining[:overflow])
        return self.delete(candidates)


class SQLiteArchiveStore:
    """带会话命名空间的本地持久化归档，使用标准库 SQLite。"""

    def __init__(
        self,
        path: str | Path,
        namespace: str = "default",
        policy: ArchivePolicy | None = None,
    ) -> None:
        if not str(namespace).strip():
            raise ValueError("archive namespace 不能为空")
        self.path = str(path)
        self.namespace = str(namespace)
        self.policy = policy or ArchivePolicy()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS context_archive (
                namespace TEXT NOT NULL,
                archive_id TEXT NOT NULL,
                item_json TEXT NOT NULL,
                archived_at_turn INTEGER NOT NULL,
                reason TEXT NOT NULL,
                retrieval_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (namespace, archive_id)
            )
            """
        )
        self._connection.commit()

    def put(self, item: ContextItem, turn: int, reason: str) -> ArchiveEntry:
        source_ids = item.source_event_ids or (item.chunk_id,)
        archive_id = "arc:" + "+".join(source_ids)
        archived_item = _prepare_archived_item(
            item,
            f"sqlite://{self.namespace}/{archive_id}",
            self.policy,
        )
        payload = json.dumps(_item_to_dict(archived_item), ensure_ascii=False)
        with self._lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO context_archive
                    (namespace, archive_id, item_json, archived_at_turn, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.namespace, archive_id, payload, int(turn), str(reason)),
            )
            self._connection.commit()
            entry = self._get(archive_id)
            self.prune(turn)
            return entry

    def search(self, query: str, limit: int = 3) -> list[ArchiveEntry]:
        with self._lock:
            selected = _rank_entries(self.entries(), query, limit)
            if selected:
                self._connection.executemany(
                    """
                    UPDATE context_archive
                    SET retrieval_count = retrieval_count + 1
                    WHERE namespace = ? AND archive_id = ?
                    """,
                    [(self.namespace, entry.archive_id) for entry in selected],
                )
                self._connection.commit()
                for entry in selected:
                    entry.retrieval_count += 1
            return selected

    def entries(self) -> list[ArchiveEntry]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT archive_id, item_json, archived_at_turn, reason, retrieval_count
                FROM context_archive WHERE namespace = ? ORDER BY archived_at_turn, archive_id
                """,
                (self.namespace,),
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    def __len__(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM context_archive WHERE namespace = ?",
                (self.namespace,),
            ).fetchone()
        return int(row[0]) if row else 0

    def delete(self, archive_ids: list[str]) -> int:
        unique = list(dict.fromkeys(str(value) for value in archive_ids))
        if not unique:
            return 0
        with self._lock:
            removed = 0
            for archive_id in unique:
                cursor = self._connection.execute(
                    "DELETE FROM context_archive WHERE namespace = ? AND archive_id = ?",
                    (self.namespace, archive_id),
                )
                removed += max(0, cursor.rowcount)
            self._connection.commit()
        return removed

    def prune(self, current_turn: int) -> int:
        entries = self.entries()
        candidates: list[str] = []
        if self.policy.max_age_turns is not None:
            oldest = int(current_turn) - self.policy.max_age_turns
            candidates.extend(
                entry.archive_id
                for entry in entries
                if entry.archived_at_turn < oldest
            )
        candidate_set = set(candidates)
        remaining = [entry for entry in entries if entry.archive_id not in candidate_set]
        if self.policy.max_entries is not None and len(remaining) > self.policy.max_entries:
            remaining.sort(key=lambda entry: (entry.archived_at_turn, entry.archive_id))
            overflow = len(remaining) - self.policy.max_entries
            candidates.extend(entry.archive_id for entry in remaining[:overflow])
        return self.delete(candidates)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteArchiveStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _get(self, archive_id: str) -> ArchiveEntry:
        row = self._connection.execute(
            """
            SELECT archive_id, item_json, archived_at_turn, reason, retrieval_count
            FROM context_archive WHERE namespace = ? AND archive_id = ?
            """,
            (self.namespace, archive_id),
        ).fetchone()
        if row is None:
            raise KeyError(archive_id)
        return _row_to_entry(row)


def _rank_entries(entries: list[ArchiveEntry], query: str, limit: int) -> list[ArchiveEntry]:
    if limit <= 0 or not entries:
        return []
    query_terms = _terms(query)
    ranked: list[tuple[float, int, str, ArchiveEntry]] = []
    for entry in entries:
        item_terms = _terms(entry.item.text)
        overlap = len(query_terms & item_terms)
        if query_terms and overlap == 0:
            continue
        coverage = overlap / max(1, len(query_terms))
        exact_bonus = 1.0 if query and query.lower() in entry.item.text.lower() else 0.0
        evidence_bonus = 0.35 if (
            entry.item.role in {"user", "tool", "function"}
            or entry.item.text.lstrip().startswith("观察：")
        ) else 0.0
        score = coverage + exact_bonus + evidence_bonus + min(
            0.2,
            (entry.item.importance or 0.0) * 0.2,
        )
        ranked.append((score, entry.archived_at_turn, entry.archive_id, entry))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [row[3] for row in ranked[:limit]]


_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(api[_-]?key|password|passwd|secret|access[_-]?token)\b\s*[:=]\s*['\"]?[^\s,'\"]{6,}"
    ),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def redact_sensitive_text(text: str) -> tuple[str, bool]:
    value = str(text)
    redacted = False
    for pattern in _SECRET_PATTERNS:
        updated, count = pattern.subn("[REDACTED]", value)
        value = updated
        redacted = redacted or count > 0
    return value, redacted


def _prepare_archived_item(
    item: ContextItem,
    archive_uri: str,
    policy: ArchivePolicy,
) -> ContextItem:
    text = item.text
    metadata = dict(item.metadata)
    redacted = False
    if policy.redact_sensitive:
        text, redacted = redact_sensitive_text(text)
        metadata = _redact_value(metadata)
    metadata.update({"archive_uri": archive_uri, "redacted": redacted})
    return replace(
        item,
        text=text,
        status=ContextStatus.ARCHIVED,
        metadata=metadata,
    )


def _redact_value(value):
    if isinstance(value, str):
        return redact_sensitive_text(value)[0]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def _item_to_dict(item: ContextItem) -> dict:
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


def _row_to_entry(row) -> ArchiveEntry:
    archive_id, payload, archived_at_turn, reason, retrieval_count = row
    data = json.loads(payload)
    item = ContextItem(
        chunk_id=data["chunk_id"],
        ctype=ContextType(data["ctype"]),
        text=data["text"],
        source=data.get("source", ""),
        role=data.get("role", "user"),
        source_turn=int(data.get("source_turn", 0)),
        source_event_ids=tuple(data.get("source_event_ids", [])),
        status=ContextStatus(data.get("status", ContextStatus.ARCHIVED.value)),
        parent_chunk_id=data.get("parent_chunk_id"),
        importance=data.get("importance"),
        action=CompressionAction(data.get("action", CompressionAction.KEEP.value)),
        token_count=int(data.get("token_count", 0)),
        metadata=dict(data.get("metadata") or {}),
    )
    return ArchiveEntry(
        archive_id=str(archive_id),
        item=item,
        archived_at_turn=int(archived_at_turn),
        reason=str(reason),
        retrieval_count=int(retrieval_count),
    )


def _terms(text: str) -> set[str]:
    """同时支持英文词、数字与中文二元片段的零依赖检索特征。"""
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9_\-.]+", lowered))
    chinese = "".join(re.findall(r"[一-鿿]", lowered))
    grams = {chinese[i : i + 2] for i in range(max(0, len(chinese) - 1))}
    if len(chinese) == 1:
        grams.add(chinese)
    return words | grams
