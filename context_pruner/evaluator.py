"""上下文重要性评估：信息熵/困惑度 + 语义相关性（可插拔实现）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ContextChunk


class ImportanceScorer(ABC):
    """评估器接口。

    后续可替换为：embedding 语义相关性、轻量 LLM 打分、困惑度/信息熵筛选。
    """

    @abstractmethod
    def score(self, chunks: list[ContextChunk], task_state: str) -> list[float]:
        """返回与 chunks 等长的 0~1 重要性分数。"""


class RuleBasedScorer(ImportanceScorer):
    """兜底实现：关键词 / 长度 / 位置启发式，保证无 API 时系统可跑通。"""

    _KEYWORDS = ("目标", "结论", "约束", "决定", "必须", "错误", "失败", "关键", "最终")

    def score(self, chunks: list[ContextChunk], task_state: str) -> list[float]:
        scores = []
        for chunk in chunks:
            s = 0.0
            text = chunk.text
            if any(kw in text for kw in self._KEYWORDS):
                s += 0.4
            # 短块信息密度通常更高（启发式）
            s += max(0.0, 1.0 - len(text) / 500) * 0.2
            if chunk.ctype.value == "system":
                s += 0.3
            scores.append(min(1.0, s))
        return scores


class EmbeddingScorer(ImportanceScorer):
    """基于 embedding 余弦相似度的语义相关性打分。

    embed_fn: callable(text) -> list[float]，如 OpenAI / 本地模型的 embedding 接口。
    """

    def __init__(self, embed_fn, threshold: float = 0.3):
        self._embed = embed_fn
        self._threshold = threshold

    def score(self, chunks: list[ContextChunk], task_state: str) -> list[float]:
        if not task_state:
            return [0.5] * len(chunks)
        q = self._embed(task_state)
        scores = []
        for chunk in chunks:
            v = self._embed(chunk.text)
            sim = _cosine(q, v)
            # 把 (threshold, 1] 映射到 (0, 1]
            scores.append(max(0.0, min(1.0, (sim - self._threshold) / (1 - self._threshold))))
        return scores


def _cosine(a, b) -> float:
    import numpy as np

    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def evaluate(
    chunks: list[ContextChunk],
    task_state: str,
    scorer: ImportanceScorer | None = None,
) -> list[ContextChunk]:
    """为每个上下文块写入 importance 分数。"""
    scorer = scorer or RuleBasedScorer()
    scores = scorer.score(chunks, task_state)
    for chunk, s in zip(chunks, scores):
        chunk.importance = round(float(s), 4)
    return chunks
