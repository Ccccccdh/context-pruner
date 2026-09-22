"""零外部服务的语义特征、抽取式摘要与 provenance 元数据。"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter


class LocalHashEmbedding:
    """基于词和中文二元片段的确定性特征哈希向量。

    它不是神经 embedding，但能在无模型、无网络时提供稳定的局部语义相关性；
    生产环境仍可通过 ``embed_fn`` 注入真实 embedding 模型。
    """

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 16:
            raise ValueError("dimensions 至少为 16")
        self.dimensions = dimensions

    def __call__(self, text: str) -> list[float]:
        features = Counter(_features(text))
        vector = [0.0] * self.dimensions
        for feature, count in features.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            sign = -1.0 if value & 1 else 1.0
            vector[index] += sign * (1.0 + math.log(max(1, count)))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class ExtractiveSummarizer:
    """优先保留约束、数字、错误和实体的本地抽取式摘要器。"""

    def __init__(self, max_chars: int = 240) -> None:
        if max_chars < 48:
            raise ValueError("max_chars 至少为 48")
        self.max_chars = max_chars

    def __call__(self, text: str) -> str:
        source = str(text).strip()
        if len(source) <= self.max_chars:
            return source
        sentences = [part.strip() for part in _SENTENCE_SPLIT.split(source) if part.strip()]
        if not sentences:
            return source[: self.max_chars].rstrip()

        ranked = sorted(
            enumerate(sentences),
            key=lambda pair: (_sentence_score(pair[1], pair[0], len(sentences)), -pair[0]),
            reverse=True,
        )
        selected: list[tuple[int, str]] = []
        used = 0
        for index, sentence in ranked:
            remaining = self.max_chars - used
            if remaining <= 0:
                break
            candidate = sentence if len(sentence) <= remaining else sentence[:remaining].rstrip()
            if not candidate:
                continue
            selected.append((index, candidate))
            used += len(candidate) + 1
        selected.sort(key=lambda pair: pair[0])
        return " ".join(sentence for _, sentence in selected).strip()[: self.max_chars]


def extract_provenance_metadata(text: str, max_facts: int = 6, max_entities: int = 12) -> dict:
    """抽取可审计的事实句与实体索引，不把抽取结果当作原始真相。"""
    source = str(text)
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(source) if part.strip()]
    facts = [
        sentence
        for sentence in sentences
        if _IMPORTANT.search(sentence) or re.search(r"\d", sentence)
    ][:max_facts]
    entities: list[str] = []
    patterns = (
        r"\b\d{4}[-年/]\d{1,2}(?:[-月/]\d{1,2})?(?:\s+\d{1,2}:\d{2})?\b",
        r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b",
        r"\b\d+(?:\.\d+)?%\b",
        r"\b\d+(?:\.\d+)?\s*(?:GB|MB|KB|万元|天|小时|分钟)\b",
    )
    for pattern in patterns:
        for match in re.findall(pattern, source, flags=re.IGNORECASE):
            value = str(match).strip()
            if value and value not in entities:
                entities.append(value)
            if len(entities) >= max_entities:
                break
        if len(entities) >= max_entities:
            break
    return {"facts": facts, "entities": entities}


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_IMPORTANT = re.compile(
    r"必须|不得|禁止|只能|约束|最终|决定|结论|错误|失败|目标|截止|预算|端口|协议|密钥|负责人",
    re.IGNORECASE,
)


def _features(text: str) -> list[str]:
    lowered = str(text).lower()
    words = re.findall(r"[a-z0-9_][a-z0-9_.-]*", lowered)
    chinese_runs = re.findall(r"[一-鿿]+", lowered)
    grams = [
        run[index : index + 2]
        for run in chinese_runs
        for index in range(max(1, len(run) - 1))
        if run[index : index + 2]
    ]
    return words + grams


def _sentence_score(sentence: str, index: int, total: int) -> float:
    score = 0.0
    if _IMPORTANT.search(sentence):
        score += 4.0
    if re.search(r"\d", sentence):
        score += 2.0
    if re.search(r"[A-Za-z][A-Za-z0-9_.-]{2,}", sentence):
        score += 1.0
    if index == 0 or index == total - 1:
        score += 0.5
    score += min(1.0, len(set(_features(sentence))) / 12.0)
    return score
