"""v1 自适应调度：结合任务阶段与上下文重要性动态选择压缩动作。"""

from __future__ import annotations

import json

from .types import BudgetPressure, CompressionAction, ContextChunk, ContextType, TaskPhase


def schedule_adaptive(
    chunks: list[ContextChunk],
    phase: TaskPhase,
    high_threshold: float = 0.70,
    medium_threshold: float = 0.40,
    pressure: BudgetPressure = BudgetPressure.NORMAL,
) -> list[ContextChunk]:
    """按重要性分布动态调度，而不是只按固定阶段策略。"""
    if pressure == BudgetPressure.SOFT:
        high_threshold = min(0.9, high_threshold + 0.1)
        medium_threshold = min(0.75, medium_threshold + 0.1)
    elif pressure == BudgetPressure.HARD:
        high_threshold = min(0.95, high_threshold + 0.15)
        medium_threshold = min(0.85, medium_threshold + 0.2)
    latest_user_turn = max(
        (chunk.source_turn for chunk in chunks if chunk.role == "user"),
        default=None,
    )
    for chunk in chunks:
        importance = chunk.importance or 0.0
        # The newest user message is normally the active task/request. Its exact
        # facts must survive even when it lacks words such as "must" or "do not";
        # otherwise a summary can replace current values with policy thresholds.
        if chunk.role == "user" and chunk.source_turn == latest_user_turn:
            chunk.action = CompressionAction.KEEP
            chunk.metadata["current_user_turn"] = True
            continue
        if chunk.ctype in {
            ContextType.SYSTEM,
            ContextType.TASK_STATE,
            ContextType.LONG_TERM_MEMORY,
        }:
            chunk.action = CompressionAction.KEEP
            continue

        if chunk.ctype == ContextType.TOOL_INTERACTION:
            if _is_structured_action(chunk.text):
                # 交给 compressor 做 JSON-aware compact，绝不做字符级截断。
                chunk.action = CompressionAction.SUMMARIZE
                continue
            if _is_read_evidence(chunk.text):
                # 在没有结构化事实抽取器前，read 原始证据优先保证正确性。
                chunk.action = (
                    CompressionAction.ARCHIVE
                    if pressure == BudgetPressure.HARD and importance < high_threshold
                    else CompressionAction.KEEP
                )
                continue
            if phase == TaskPhase.KEY_DECISION or importance >= high_threshold:
                chunk.action = CompressionAction.KEEP
            elif pressure == BudgetPressure.HARD and importance < medium_threshold:
                chunk.action = CompressionAction.ARCHIVE
            elif phase == TaskPhase.CONVERGENCE and importance < medium_threshold:
                chunk.action = CompressionAction.TRUNCATE
            else:
                chunk.action = CompressionAction.KEEP
            continue

        # DIALOGUE：高重要性保留，中重要性摘要，低重要性在收敛阶段裁剪。
        if importance >= high_threshold:
            chunk.action = CompressionAction.KEEP
        elif importance >= medium_threshold:
            chunk.action = CompressionAction.SUMMARIZE
        elif pressure == BudgetPressure.HARD:
            chunk.action = CompressionAction.ARCHIVE
        elif phase == TaskPhase.CONVERGENCE:
            chunk.action = CompressionAction.TRUNCATE
        else:
            chunk.action = CompressionAction.SUMMARIZE
    return chunks


def _is_structured_action(text: str) -> bool:
    try:
        data = json.loads(text.strip())
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("action"), dict)


def _is_read_evidence(text: str) -> bool:
    normalized = text.lstrip().lower()
    return normalized.startswith("观察：read 结果：") or normalized.startswith("read 结果：")
