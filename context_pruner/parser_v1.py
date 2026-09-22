"""v1 分层解析：规则兜底 + 可插拔语义分类器。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from .parser import parse_context
from .types import ContextChunk, ContextType


class ContextClassifier(ABC):
    @abstractmethod
    def classify(self, role: str, content: str) -> ContextType:
        """返回该上下文块的类型。"""


class RuleContextClassifier(ContextClassifier):
    """v0 的规则分类，作为 v1 的兜底。"""

    def classify(self, role: str, content: str) -> ContextType:
        role = role.lower()
        if content.startswith("观察："):
            return ContextType.TOOL_INTERACTION
        if role == "assistant" and _structured_action(content):
            return ContextType.TOOL_INTERACTION
        if role in {"system", "task", "state", "memory", "tool", "function"}:
            return {
                "system": ContextType.SYSTEM,
                "task": ContextType.TASK_STATE,
                "state": ContextType.TASK_STATE,
                "memory": ContextType.LONG_TERM_MEMORY,
                "tool": ContextType.TOOL_INTERACTION,
                "function": ContextType.TOOL_INTERACTION,
            }[role]
        return ContextType.DIALOGUE


class EmbeddingContextClassifier(ContextClassifier):
    """用 embedding 与各类别原型做相似度分类；失败时回退规则。"""

    def __init__(
        self,
        embed_fn,
        prototypes: dict[ContextType, str] | None = None,
        threshold: float = 0.15,
    ):
        self.embed = embed_fn
        self.threshold = threshold
        self.prototypes = prototypes or _DEFAULT_PROTOTYPES
        self._prototype_vec = None
        self._fallback = RuleContextClassifier()

    def classify(self, role: str, content: str) -> ContextType:
        structural = self._fallback.classify(role, content)
        if structural != ContextType.DIALOGUE:
            return structural
        if self.embed is None:
            return structural
        try:
            if self._prototype_vec is None:
                self._prototype_vec = {
                    ctype: self.embed(text) for ctype, text in self.prototypes.items()
                }
            vec = self.embed(content)
            scores = {
                ctype: _cosine(vec, proto)
                for ctype, proto in self._prototype_vec.items()
            }
            best_type, best_score = max(scores.items(), key=lambda kv: kv[1])
            if best_score >= self.threshold:
                return best_type
        except Exception:
            pass
        return structural


_DEFAULT_PROTOTYPES: dict[ContextType, str] = {
    ContextType.SYSTEM: "system instruction role constraint rule 系统指令安全规则硬约束",
    ContextType.TASK_STATE: "current task goal progress subgoal status 当前任务目标进度待办状态",
    ContextType.DIALOGUE: "reasoning thought plan discussion 推理思考计划讨论方案",
    ContextType.TOOL_INTERACTION: "tool call result observation search read 工具调用结果观察检索读取",
    ContextType.LONG_TERM_MEMORY: "background knowledge fact reference 背景知识长期记忆事实参考",
}


def parse_context_semantic(
    turns,
    classifier: ContextClassifier | None = None,
    embed_fn=None,
) -> list[ContextChunk]:
    """先按规则切块，再用语义分类器覆盖 ctype。"""
    chunks = parse_context(turns)
    clf = classifier or (
        EmbeddingContextClassifier(embed_fn) if embed_fn else RuleContextClassifier()
    )
    for chunk in chunks:
        chunk.ctype = clf.classify(chunk.role, chunk.text)
    return chunks


def _cosine(a, b):
    import numpy as np

    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _structured_action(content: str) -> bool:
    try:
        data = json.loads(content.strip())
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("action"), dict)
