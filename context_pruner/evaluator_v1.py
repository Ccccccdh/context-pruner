"""v1 重要性评估：规则 + embedding 语义相关性 + 信息熵/困惑度。"""

from __future__ import annotations

import math
from collections import Counter

from .evaluator import EmbeddingScorer, RuleBasedScorer
from .types import ContextChunk


class EntropyScorer:
    """基于字符 n-gram 信息熵和数值信号的启发式信息密度评分。"""

    def __init__(self, n: int = 2, ppl_fn=None):
        self.n = n
        self.ppl_fn = ppl_fn  # 可选：callable(text) -> perplexity

    def score(self, chunks: list[ContextChunk], task_state: str = "") -> list[float]:
        scores = []
        for chunk in chunks:
            text = chunk.text
            entropy = _normalized_entropy(text, self.n)
            density = min(1.0, len(text) / 600.0)
            numeric = min(1.0, sum(ch.isdigit() for ch in text) / max(1, len(text)) * 4)
            score = 0.55 * entropy + 0.25 * density + 0.20 * numeric

            if self.ppl_fn is not None:
                try:
                    ppl = float(self.ppl_fn(text))
                    # 低困惑度通常表示模型更确定；映射到 0~1，并轻度加权。
                    confidence = max(0.0, 1.0 - ppl / 200.0)
                    score = 0.8 * score + 0.2 * confidence
                except Exception:
                    pass
            scores.append(round(min(1.0, max(0.0, score)), 4))
        return scores


class HybridScorer:
    """组合评分：规则 + embedding 相关性 + 信息熵。"""

    def __init__(
        self,
        embed_fn=None,
        ppl_fn=None,
        rule_weight: float = 0.3,
        embedding_weight: float = 0.4,
        entropy_weight: float = 0.3,
    ):
        self.embed_fn = embed_fn
        self.ppl_fn = ppl_fn
        self.weights = (rule_weight, embedding_weight, entropy_weight)
        self.rule = RuleBasedScorer()
        self.embedding = EmbeddingScorer(embed_fn) if embed_fn else None
        self.entropy = EntropyScorer(ppl_fn=ppl_fn)

    def score(self, chunks: list[ContextChunk], task_state: str) -> list[float]:
        rule_scores = self.rule.score(chunks, task_state)
        if self.embedding is not None:
            emb_scores = self.embedding.score(chunks, task_state)
        else:
            emb_scores = [0.5] * len(chunks)
        ent_scores = self.entropy.score(chunks, task_state)

        wr, we, wt = self.weights
        if self.embedding is None:
            total = wr + wt
            wr, we, wt = wr / total, 0.0, wt / total
        return [
            round(min(1.0, max(0.0, wr * r + we * e + wt * t)), 4)
            for r, e, t in zip(rule_scores, emb_scores, ent_scores)
        ]


class AdaptiveScorer:
    """在 HybridScorer 基础上加入可选 self-information，并做百分位自适应归一化。"""

    def __init__(
        self,
        embed_fn=None,
        self_info_fn=None,
        ppl_fn=None,
        rule_weight: float = 0.25,
        embedding_weight: float = 0.40,
        entropy_weight: float = 0.20,
        self_info_weight: float = 0.15,
    ):
        self.embed_fn = embed_fn
        self.self_info_fn = self_info_fn
        self.ppl_fn = ppl_fn
        self.rule = RuleBasedScorer()
        self.embedding = EmbeddingScorer(embed_fn) if embed_fn else None
        self.entropy = EntropyScorer(ppl_fn=ppl_fn)

        self._weights = (rule_weight, embedding_weight, entropy_weight, self_info_weight)

    def score(self, chunks: list[ContextChunk], task_state: str) -> list[float]:
        rule_scores = self.rule.score(chunks, task_state)
        emb_scores = self.embedding.score(chunks, task_state) if self.embedding else [0.5] * len(chunks)
        ent_scores = self.entropy.score(chunks, task_state)
        self_scores = self._self_info_scores(chunks)

        wr, we, wt, ws = self._weights
        if self.embedding is None:
            we = 0.0
        if self.self_info_fn is None:
            ws = 0.0
        total = wr + we + wt + ws
        if total <= 0:
            total = 1.0
        wr, we, wt, ws = wr / total, we / total, wt / total, ws / total

        raw = [
            wr * r + we * e + wt * t + ws * s
            for r, e, t, s in zip(rule_scores, emb_scores, ent_scores, self_scores)
        ]
        return _percentile_scale(raw)

    def _self_info_scores(self, chunks: list[ContextChunk]) -> list[float]:
        if self.self_info_fn is None:
            return [0.5] * len(chunks)
        scores = []
        for c in chunks:
            try:
                value = float(self.self_info_fn(c.text))
                scores.append(max(0.0, min(1.0, value)))
            except Exception:
                scores.append(0.5)
        return scores


def _percentile_scale(raw_scores: list[float]) -> list[float]:
    """把原始分转成该批内的百分位，使阈值随上下文重要性分布自适应。"""
    if not raw_scores:
        return []
    n = len(raw_scores)
    if n == 1:
        return [1.0]
    order = sorted(range(n), key=lambda i: raw_scores[i])
    scaled = [0.0] * n
    start = 0
    while start < n:
        end = start
        value = raw_scores[order[start]]
        while end + 1 < n and raw_scores[order[end + 1]] == value:
            end += 1
        average_rank = (start + end) / 2
        percentile = average_rank / (n - 1)
        for pos in range(start, end + 1):
            scaled[order[pos]] = percentile
        start = end + 1
    return [round(v, 4) for v in scaled]


def _normalized_entropy(text: str, n: int = 2) -> float:
    text = text.strip()
    if len(text) < n:
        return 0.0
    grams = [text[i : i + n] for i in range(len(text) - n + 1)]
    counts = Counter(grams)
    total = sum(counts.values())
    h = -sum((c / total) * math.log2(c / total) for c in counts.values())
    # 以可能 n-gram 种类数做归一化，避免过长文本天然熵更高。
    max_h = math.log2(max(1, len(counts)))
    return h / max_h if max_h > 0 else 0.0
