"""任务阶段识别 + 自适应压缩调度。"""

from __future__ import annotations

from .types import CompressionAction, ContextChunk, ContextType, TaskPhase


def detect_phase(turns: list[dict], horizon: int = 12) -> TaskPhase:
    """结合轨迹长度、决策词、错误和目标变化识别当前任务阶段。"""
    n = len(turns)
    if n <= 3:
        return TaskPhase.EXPLORATION
    recent = turns[-6:]
    texts = " ".join(str(t.get("content", "")) for t in recent)
    event_kinds = {
        str(t.get("_event_kind", ""))
        for t in recent
    }
    error_signals = sum(
        marker in texts.lower()
        for marker in ("错误", "失败", "无法解析", "重试", "error", "failed", "retry")
    )
    goal_changes = sum(
        marker in texts
        for marker in ("改为", "更正", "撤销", "重新规划", "目标变化", "替代原方案")
    )
    if event_kinds.intersection({"parse_error", "tool_error"}) or error_signals >= 2 or goal_changes:
        return TaskPhase.EXPLORATION
    decisive = sum(1 for kw in ("结论", "决定", "方案", "最终", "确定", "采纳") if kw in texts)
    if decisive >= 2:
        return TaskPhase.KEY_DECISION
    stable_actions = _stable_recent_actions(recent)
    if n >= horizon and stable_actions:
        return TaskPhase.CONVERGENCE
    return TaskPhase.EXPLORATION


def _stable_recent_actions(turns: list[dict]) -> bool:
    """最近轨迹没有连续重复动作时，才允许因长度进入收敛阶段。"""
    actions: list[str] = []
    for turn in turns:
        content = str(turn.get("content", ""))
        if '"action"' in content:
            actions.append(" ".join(content.split()))
    return len(actions) < 2 or actions[-1] != actions[-2]


# 不同阶段、不同上下文类型的压缩动作偏好（初步规则）
_POLICY: dict[TaskPhase, dict[ContextType, CompressionAction]] = {
    TaskPhase.EXPLORATION: {
        ContextType.SYSTEM: CompressionAction.KEEP,
        ContextType.TASK_STATE: CompressionAction.KEEP,
        ContextType.DIALOGUE: CompressionAction.SUMMARIZE,
        ContextType.TOOL_INTERACTION: CompressionAction.KEEP,
        ContextType.LONG_TERM_MEMORY: CompressionAction.SUMMARIZE,
    },
    TaskPhase.KEY_DECISION: {
        ContextType.SYSTEM: CompressionAction.KEEP,
        ContextType.TASK_STATE: CompressionAction.KEEP,
        ContextType.DIALOGUE: CompressionAction.KEEP,
        ContextType.TOOL_INTERACTION: CompressionAction.KEEP,
        ContextType.LONG_TERM_MEMORY: CompressionAction.SUMMARIZE,
    },
    TaskPhase.CONVERGENCE: {
        ContextType.SYSTEM: CompressionAction.KEEP,
        ContextType.TASK_STATE: CompressionAction.KEEP,
        ContextType.DIALOGUE: CompressionAction.TRUNCATE,
        ContextType.TOOL_INTERACTION: CompressionAction.TRUNCATE,
        ContextType.LONG_TERM_MEMORY: CompressionAction.SUMMARIZE,
    },
}


def schedule(chunks: list[ContextChunk], phase: TaskPhase) -> list[ContextChunk]:
    """根据任务阶段为每个块指定压缩动作。

    TODO(实现): 结合 importance 阈值动态调整动作——即“自适应熵减”的核心：
    高重要性块即使处于收敛阶段也保留原文，低重要性块在探索阶段也可先摘要。
    """
    policy = _POLICY[phase]
    for chunk in chunks:
        chunk.action = policy.get(chunk.ctype, CompressionAction.KEEP)
    return chunks
