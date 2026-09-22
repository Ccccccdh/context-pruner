"""压缩动作：裁剪、摘要、合并去重。"""

from __future__ import annotations

import json
from dataclasses import replace

from .types import CompressionAction, ContextChunk, estimate_tokens
from .semantic import extract_provenance_metadata


def truncate(chunk: ContextChunk, keep_ratio: float = 0.5) -> ContextChunk:
    """启发式裁剪：保留开头与结尾（对长轨迹信息分布更合理）。"""
    compact = _compact_agent_control(chunk.text)
    if compact is not None:
        return replace(
            chunk,
            text=compact,
            token_count=estimate_tokens(compact),
            action=CompressionAction.TRUNCATE,
            parent_chunk_id=chunk.parent_chunk_id or chunk.chunk_id,
            metadata={**chunk.metadata, "structured_compaction": True},
        )
    if keep_ratio >= 1.0 or len(chunk.text) <= 32:
        return replace(chunk, metadata=dict(chunk.metadata))
    n = max(8, int(len(chunk.text) * keep_ratio))
    head, tail = chunk.text[: n // 2], chunk.text[-(n - n // 2):]
    text = head + "\n…[已裁剪]…\n" + tail
    return replace(
        chunk,
        text=text,
        token_count=estimate_tokens(text),
        action=CompressionAction.TRUNCATE,
        parent_chunk_id=chunk.parent_chunk_id or chunk.chunk_id,
        metadata=dict(chunk.metadata),
    )


def summarize(chunk: ContextChunk, summarizer=None) -> ContextChunk:
    """用 LLM 生成语义摘要；summarizer: (text) -> str，为 None 时退化为裁剪。"""
    compact = _compact_agent_control(chunk.text)
    if compact is not None:
        return replace(
            chunk,
            text=compact,
            token_count=estimate_tokens(compact),
            action=CompressionAction.SUMMARIZE,
            parent_chunk_id=chunk.parent_chunk_id or chunk.chunk_id,
            metadata={**chunk.metadata, "structured_compaction": True},
        )
    if summarizer is None:
        return truncate(chunk, keep_ratio=0.4)
    summary = summarizer(chunk.text)
    if summary:
        text = f"[摘要] {summary.strip()}"
        extracted = extract_provenance_metadata(chunk.text)
        return replace(
            chunk,
            text=text,
            token_count=estimate_tokens(text),
            action=CompressionAction.SUMMARIZE,
            parent_chunk_id=chunk.parent_chunk_id or chunk.chunk_id,
            metadata={**chunk.metadata, **extracted},
        )
    return replace(chunk, metadata=dict(chunk.metadata))


def archive_reference(chunk: ContextChunk) -> ContextChunk:
    """用可追溯的小型引用替换正文；原文由生命周期管理器写入归档。"""
    source_ids = ",".join(chunk.source_event_ids)
    text = f"[已归档:{source_ids}]"
    return replace(
        chunk,
        text=text,
        token_count=estimate_tokens(text),
        action=CompressionAction.ARCHIVE,
        parent_chunk_id=chunk.parent_chunk_id or chunk.chunk_id,
        metadata={
            **chunk.metadata,
            **extract_provenance_metadata(chunk.text),
            "archive_reference": True,
        },
    )


def merge(
    chunks: list[ContextChunk],
    sim_fn=None,
    threshold: float = 0.9,
) -> list[ContextChunk]:
    """合并高度相似 / 重复的块。

    sim_fn: (a_text, b_text) -> float；默认基于字符集合的简单相似度。
    TODO(优化): 换 embedding 余弦相似度，并做跨块去重。
    """
    merged: list[ContextChunk] = []
    for chunk in chunks:
        candidate = replace(chunk, metadata=dict(chunk.metadata))
        for index, m in enumerate(merged):
            if getattr(m, "role", "") != getattr(chunk, "role", ""):
                continue
            if _structured_payload(m.text) or _structured_payload(chunk.text):
                sim = 1.0 if _normalize(m.text) == _normalize(chunk.text) else 0.0
            else:
                sim = _text_sim(m.text, chunk.text) if sim_fn is None else sim_fn(m.text, chunk.text)
            if sim >= threshold:
                text = _merge_unique_text(m.text, chunk.text)
                source_ids = tuple(dict.fromkeys(m.source_event_ids + chunk.source_event_ids))
                merged[index] = replace(
                    m,
                    text=text,
                    token_count=estimate_tokens(text),
                    action=CompressionAction.MERGE,
                    source_event_ids=source_ids,
                    metadata={**m.metadata, "merged_items": len(source_ids)},
                )
                break
        else:
            merged.append(candidate)
    return merged


def _merge_unique_text(first: str, second: str) -> str:
    """精确重复只保留一份；近似文本仅补充尚未出现的行。"""
    if _normalize(first) == _normalize(second):
        return first
    lines = list(dict.fromkeys(
        line.strip()
        for line in (first + "\n" + second).splitlines()
        if line.strip()
    ))
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _compact_agent_control(text: str) -> str | None:
    """压缩 ReAct 控制消息，同时保证结果仍是完整、可解析的 JSON。"""
    candidate = text.strip()
    if candidate.startswith("```"):
        parts = candidate.split("```")
        if len(parts) >= 3:
            candidate = parts[1].removeprefix("json").strip()
    try:
        data = json.loads(candidate)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("action"), dict):
        return None
    return json.dumps(
        {"action": data["action"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _structured_payload(text: str) -> bool:
    try:
        return isinstance(json.loads(text.strip()), dict)
    except (TypeError, json.JSONDecodeError):
        return False


def _text_sim(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
