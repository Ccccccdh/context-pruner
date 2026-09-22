"""Context-Pruner 统一入口：解析 → 评估 → 调度 → 压缩。"""

from __future__ import annotations

from .compressors import merge, summarize, truncate
from .evaluator import ImportanceScorer, evaluate
from .parser import parse_context
from .scheduler import detect_phase, schedule
from .types import CompressedResult, CompressionAction, TaskPhase


class ContextPruner:
    """长程 Agent 上下文压缩器。

    用法:
        pruner = ContextPruner(scorer=EmbeddingScorer(embed_fn), summarizer=llm_summarize)
        result = pruner.compress(turns, task_state="...")
    """

    def __init__(self, scorer: ImportanceScorer | None = None, summarizer=None):
        self.scorer = scorer
        self.summarizer = summarizer  # callable(text) -> str

    def compress(self, turns: list[dict], task_state: str = "") -> CompressedResult:
        chunks = parse_context(turns)
        phase = detect_phase(turns)
        chunks = evaluate(chunks, task_state, self.scorer)
        chunks = schedule(chunks, phase)
        return _apply(chunks, phase, self.summarizer)


def _apply(chunks, phase: TaskPhase, summarizer) -> CompressedResult:
    tokens_before = sum(c.token_count for c in chunks)
    out = []
    for c in chunks:
        if c.action == CompressionAction.TRUNCATE:
            out.append(truncate(c))
        elif c.action == CompressionAction.SUMMARIZE:
            out.append(summarize(c, summarizer))
        else:
            out.append(c)
    out = merge(out)
    tokens_after = sum(c.token_count for c in out)
    return CompressedResult(
        phase=phase,
        chunks=out,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )
