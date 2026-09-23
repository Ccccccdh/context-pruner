import json
import unittest

from context_pruner import (
    AdaptiveScorer,
    CompressionAction,
    ContextChunk,
    ContextType,
    ContextBudget,
    ContextPrunerV1,
    EntropyScorer,
    ExtractiveSummarizer,
    HybridScorer,
    LocalHashEmbedding,
    TaskPhase,
    parse_context_semantic,
    schedule_adaptive,
)
from context_pruner.compressors import summarize
from context_pruner.compressors import merge
from context_pruner.evaluator import _cosine
from context_pruner.parser_v1 import EmbeddingContextClassifier
from context_pruner.scheduler import detect_phase


def fake_embed(text: str):
    """确定性假 embedding，按类别关键词给不同向量。"""
    text = text.lower()
    if any(k in text for k in ["观察", "search", "read", "tool", "call", "result"]):
        return [0.1, 0.9, 0.0]
    if any(k in text for k in ["任务", "目标", "状态", "task", "goal"]):
        return [0.9, 0.0, 0.0]
    return [0.0, 0.0, 0.9]


class ParserV1Test(unittest.TestCase):
    def test_rule_fallback_marks_observation_as_tool(self):
        chunks = parse_context_semantic(
            [{"role": "user", "content": "观察：search 结果：LLMLingua"}]
        )
        self.assertEqual(chunks[0].ctype, ContextType.TOOL_INTERACTION)

    def test_rule_fallback_marks_generic_verified_result_as_tool(self):
        chunks = parse_context_semantic(
            [{"role": "user", "content": "VERIFIED TOOL RESULT [gate]: status=passed"}]
        )
        self.assertEqual(chunks[0].ctype, ContextType.TOOL_INTERACTION)

    def test_embedding_classifier_uses_embedding(self):
        clf = EmbeddingContextClassifier(fake_embed)
        self.assertEqual(
            clf.classify("user", "观察：search 结果"),
            ContextType.TOOL_INTERACTION,
        )

    def test_rule_classifier_marks_structured_action_as_tool(self):
        content = json.dumps(
            {"thought": "读取资料", "action": {"name": "read", "args": {"doc_id": "d1"}}},
            ensure_ascii=False,
        )
        chunks = parse_context_semantic([{"role": "assistant", "content": content}])
        self.assertEqual(ContextType.TOOL_INTERACTION, chunks[0].ctype)


class StructureSafetyTest(unittest.TestCase):
    def test_structured_action_is_compacted_without_breaking_json(self):
        original = ContextChunk(
            "action",
            ContextType.TOOL_INTERACTION,
            json.dumps(
                {
                    "thought": "一段很长的思考，不应该通过字符切片破坏 JSON。" * 8,
                    "action": {"name": "read", "args": {"doc_id": "d2"}},
                },
                ensure_ascii=False,
            ),
            role="assistant",
        )

        compacted = summarize(original)
        data = json.loads(compacted.text)

        self.assertEqual("read", data["action"]["name"])
        self.assertEqual("d2", data["action"]["args"]["doc_id"])
        self.assertNotIn("thought", data)
        self.assertTrue(compacted.metadata["structured_compaction"])

    def test_read_evidence_is_kept_during_convergence(self):
        evidence = ContextChunk(
            "evidence",
            ContextType.TOOL_INTERACTION,
            "观察：read 结果：LongLLMLingua 面向长文档。",
            importance=0.0,
        )

        scheduled = schedule_adaptive([evidence], TaskPhase.CONVERGENCE)

        self.assertEqual(CompressionAction.KEEP, scheduled[0].action)

    def test_semantic_merge_never_combines_distinct_tool_actions(self):
        first = ContextChunk(
            "a1",
            ContextType.TOOL_INTERACTION,
            '{"action":{"name":"read","args":{"doc_id":"d1"}}}',
            role="assistant",
        )
        second = ContextChunk(
            "a2",
            ContextType.TOOL_INTERACTION,
            '{"action":{"name":"read","args":{"doc_id":"d2"}}}',
            role="assistant",
        )

        result = merge([first, second], sim_fn=lambda _a, _b: 1.0)

        self.assertEqual(2, len(result))


class PhaseDetectionTest(unittest.TestCase):
    def test_recent_error_returns_to_exploration(self):
        turns = [
            {"role": "assistant", "content": f"普通分析 {index}"}
            for index in range(12)
        ]
        turns.append(
            {
                "role": "user",
                "content": "观察：错误：工具失败，请重试",
                "_event_kind": "tool_error",
            }
        )

        self.assertEqual(TaskPhase.EXPLORATION, detect_phase(turns))

    def test_stable_long_trace_enters_convergence(self):
        turns = [
            {"role": "assistant", "content": f"普通分析 {index}"}
            for index in range(12)
        ]

        self.assertEqual(TaskPhase.CONVERGENCE, detect_phase(turns))


class EvaluatorV1Test(unittest.TestCase):
    def _chunks(self):
        return [
            ContextChunk("c0", ContextType.DIALOGUE, "我先做计划并分析任务目标。"),
            ContextChunk(
                "c1",
                ContextType.TOOL_INTERACTION,
                "观察：read 结果：LLMLingua 压缩率可达 20 倍。",
            ),
            ContextChunk("c2", ContextType.DIALOGUE, "这个结果很重要，结论是采用该方法。"),
        ]

    def test_entropy_scorer_returns_bounded_scores(self):
        chunks = self._chunks()
        scores = EntropyScorer().score(chunks)
        self.assertEqual(len(scores), len(chunks))
        self.assertTrue(all(0.0 <= s <= 1.0 for s in scores))

    def test_hybrid_scorer_runs_without_embedding(self):
        chunks = self._chunks()
        scores = HybridScorer().score(chunks, task_state="调研上下文压缩方法")
        self.assertEqual(len(scores), len(chunks))
        self.assertTrue(all(0.0 <= s <= 1.0 for s in scores))

    def test_adaptive_scorer_is_percentile_normalized(self):
        chunks = self._chunks()
        scores = AdaptiveScorer().score(chunks, task_state="调研上下文压缩方法")
        self.assertEqual(len(scores), len(chunks))
        self.assertTrue(min(scores) >= 0.0)
        self.assertTrue(max(scores) <= 1.0)


class LocalSemanticTest(unittest.TestCase):
    def test_hash_embedding_ranks_related_text_higher(self):
        embed = LocalHashEmbedding(dimensions=128)
        query = embed("Phoenix 目标集群 cluster-zeta")
        related = embed("Phoenix 最终迁移到 cluster-zeta")
        unrelated = embed("界面颜色和字体尚未确定")

        self.assertGreater(_cosine(query, related), _cosine(query, unrelated))

    def test_extractive_summary_preserves_constraints_and_entities(self):
        text = (
            "普通背景资料4。" * 20
            + "硬约束：只能读取本地文件，禁止访问网络。"
            + "项目代号 Aurora-17，预算为 42 万元。"
            + "其他无关讨论。" * 20
        )
        summary = ExtractiveSummarizer(max_chars=120)(text)

        self.assertLessEqual(len(summary), 120)
        self.assertIn("禁止访问网络", summary)
        self.assertIn("Aurora-17", summary)

    def test_summary_records_extracted_provenance_metadata(self):
        chunk = ContextChunk(
            "fact",
            ContextType.DIALOGUE,
            "最终项目代号是 Aurora-17。" + "普通说明。" * 80,
        )
        compacted = summarize(chunk, ExtractiveSummarizer(max_chars=96))

        self.assertIn("Aurora-17", compacted.metadata["entities"])
        self.assertTrue(compacted.metadata["facts"])

    def test_hard_budget_builds_traceable_task_memory_from_evidence(self):
        turns = [
            {
                "role": "user",
                "content": f"观察：read 结果：项目代号 Aurora-17，预算为 42 万元。补充说明 {index}。" * 4,
            }
            for index in range(4)
        ]

        result = ContextPrunerV1().compress(
            turns,
            task_state="汇总项目代号和预算",
            budget=ContextBudget(120, 180, 140),
        )
        memories = [
            chunk for chunk in result.chunks if chunk.metadata.get("structured_memory")
        ]

        self.assertEqual(1, len(memories))
        self.assertIn("已验证工具证据", memories[0].text)
        self.assertIn("Aurora-17", memories[0].text)
        self.assertEqual(4, len(memories[0].source_event_ids))

    def test_task_memory_marks_verified_doc_provenance(self):
        turns = [
            {
                "role": "user",
                "content": "观察：read 结果：项目代号是 Aurora-17。",
                "_event_id": "evt_read_1",
                "_metadata": {
                    "action": {"name": "read", "args": {"doc_id": "design-v2"}}
                },
            }
        ]

        result = ContextPrunerV1().compress(turns, task_state="给出项目代号")
        memory = next(
            chunk for chunk in result.chunks if chunk.metadata.get("structured_memory")
        )

        self.assertEqual("user", memory.role)
        self.assertTrue(memory.metadata["trusted_tool_evidence"])
        self.assertIn("doc_id=design-v2", memory.text)
        self.assertIn("可直接据此作答", memory.text)

    def test_task_memory_accumulates_every_read_evidence_source(self):
        facts = [
            "协议为 gRPC。",
            "端口为 7443。",
            "加密方式为 TLS 1.3。",
            "旧端口 8080 已废弃。",
        ]
        turns = [
            {
                "role": "user",
                "content": f"观察：read 结果：{fact}",
                "_event_id": f"evt_{index:06d}",
            }
            for index, fact in enumerate(facts)
        ]

        result = ContextPrunerV1().compress(
            turns,
            task_state="给出协议、端口和加密方式",
            budget=ContextBudget(240, 320, 220),
        )
        memory = next(
            chunk for chunk in result.chunks if chunk.metadata.get("structured_memory")
        )

        self.assertTrue(all(fact in memory.text for fact in facts))
        self.assertEqual(4, len(memory.source_event_ids))

    def test_generic_verified_tool_results_become_protected_task_memory(self):
        turns = [
            {
                "role": "user",
                "content": "VERIFIED TOOL RESULT [incident_lookup]: severity=critical",
                "_event_id": "evt_tool_1",
            },
            {
                "role": "user",
                "content": "VERIFIED TOOL RESULT [change_gate]: rollback_ready=yes",
                "_event_id": "evt_tool_2",
            },
        ]

        result = ContextPrunerV1().compress(
            turns,
            task_state="choose ROLLBACK only when severity is critical and rollback_ready is yes",
            budget=ContextBudget(80, 120, 70),
        )
        memory = next(
            chunk for chunk in result.chunks if chunk.metadata.get("structured_memory")
        )

        self.assertIn("severity=critical", memory.text)
        self.assertIn("rollback_ready=yes", memory.text)
        self.assertEqual(("evt_tool_1", "evt_tool_2"), memory.source_event_ids)


if __name__ == "__main__":
    unittest.main()
