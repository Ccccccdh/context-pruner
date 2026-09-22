"""上下文解析与分层：把 Agent 轨迹切分为五类上下文块。"""

from __future__ import annotations

from typing import Iterable

from .types import ContextChunk, ContextType

# 工具调用关键词（规则分类用，后续可换成 LLM / embedding 分类器）
_TOOL_HINTS = ("tool", "function", "call", "结果", "返回", "执行", "命令", "API", "invoke")

_ROLE_MAP = {
    "system": ContextType.SYSTEM,
    "task": ContextType.TASK_STATE,
    "state": ContextType.TASK_STATE,
    "memory": ContextType.LONG_TERM_MEMORY,
    "assistant": ContextType.DIALOGUE,
    "user": ContextType.DIALOGUE,
    "tool": ContextType.TOOL_INTERACTION,
    "function": ContextType.TOOL_INTERACTION,
}


def parse_context(turns: Iterable[dict]) -> list[ContextChunk]:
    """把多轮轨迹转成带类型标注的上下文块。

    参数 turns: [{"role": "user"|"assistant"|"tool"|"system", "content": "..."}]

    TODO(实现): 引入轻量分类器区分“对话推理轨迹”与“工具交互信息”，
    并根据任务目标进一步细分 TASK_STATE / LONG_TERM_MEMORY。
    """
    chunks: list[ContextChunk] = []
    for i, turn in enumerate(turns):
        role = str(turn.get("role", "user")).lower()
        content = str(turn.get("content", "")).strip()
        if not content:
            continue
        event_id = str(turn.get("_event_id") or f"evt_{i:06d}")
        source_turn = int(turn.get("_turn", i))
        ctype = _classify(role, content)
        chunks.append(
            ContextChunk(
                chunk_id=event_id,
                ctype=ctype,
                text=content,
                source=f"turn:{i}",
                role=role,
                source_turn=source_turn,
                source_event_ids=(event_id,),
                metadata={
                    "event_kind": turn.get("_event_kind", ""),
                    **dict(turn.get("_metadata") or {}),
                },
            )
        )
    return chunks


def _classify(role: str, content: str) -> ContextType:
    if content.startswith("观察："):
        return ContextType.TOOL_INTERACTION
    if role in _ROLE_MAP:
        return _ROLE_MAP[role]
    if any(hint.lower() in content.lower() for hint in _TOOL_HINTS):
        return ContextType.TOOL_INTERACTION
    return ContextType.DIALOGUE
