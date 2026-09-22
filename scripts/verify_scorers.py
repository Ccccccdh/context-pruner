"""离线验证评分器：在带标注的小样本上比较 v0 与 v1 的关键块命中率。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context_pruner import ContextChunk, ContextType
from context_pruner.evaluator import RuleBasedScorer
from context_pruner.evaluator_v1 import AdaptiveScorer, HybridScorer


TASK = "调研上下文压缩方法并给出方案"

CHUNKS = [
    ContextChunk("c00", ContextType.DIALOGUE, "今天先看一下相关资料。"),
    ContextChunk("c01", ContextType.TOOL_INTERACTION, "观察：LLMLingua 压缩率可达 20 倍。"),
    ContextChunk("c02", ContextType.DIALOGUE, "我觉得这个方向很有意思。"),
    ContextChunk("c03", ContextType.TOOL_INTERACTION, "观察：LongLLMLingua 在长上下文任务中效果更好。"),
    ContextChunk("c04", ContextType.DIALOGUE, "暂时没有更多信息。"),
    ContextChunk("c05", ContextType.TOOL_INTERACTION, "观察：语义摘要会丢失不可恢复信息。"),
    ContextChunk("c06", ContextType.DIALOGUE, "我们继续下一步。"),
    ContextChunk("c07", ContextType.TOOL_INTERACTION, "观察：工具返回成功。"),
    ContextChunk("c08", ContextType.DIALOGUE, "最终结论：采用分层压缩方案。"),
    ContextChunk("c09", ContextType.DIALOGUE, "还有一些闲聊内容。"),
]

GOLDEN_IDS = {"c01", "c03", "c05", "c08"}


def top_k_hit_rate(scorer, k: int) -> float:
    scores = scorer.score(CHUNKS, TASK)
    ranked = sorted(
        range(len(CHUNKS)),
        key=lambda i: scores[i],
        reverse=True,
    )[:k]
    selected = {CHUNKS[i].chunk_id for i in ranked}
    return len(selected & GOLDEN_IDS) / len(GOLDEN_IDS)


def main() -> None:
    print(f"golden ids: {sorted(GOLDEN_IDS)}")
    for name, scorer in [
        ("v0 RuleBasedScorer", RuleBasedScorer()),
        ("v1 HybridScorer", HybridScorer()),
        ("v1 AdaptiveScorer", AdaptiveScorer()),
    ]:
        print(f"\n{name}")
        for k in (3, 4, 5):
            print(f"  top-{k} hit rate: {top_k_hit_rate(scorer, k):.2%}")


if __name__ == "__main__":
    main()
