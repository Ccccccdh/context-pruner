"""Actual StateGraph integration test; skipped when optional dependency is absent."""

import importlib.util
import unittest

from agent_demo import KnowledgeBase, MockLLM, Task
from agent_demo.langgraph_agent import LangGraphReActAgent
from context_pruner import ContextBudget


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "langgraph is optional")
class LangGraphRuntimeTest(unittest.TestCase):
    def test_real_state_graph_runs_with_plugin_on_and_off(self):
        task = Task(
            task_id="langgraph_001",
            scenario="tool_chain",
            question="找出代号和负责人",
            docs={
                "d1": "项目代号是 Aurora-17。" + "背景" * 80,
                "d2": "负责人是 Lin。" + "说明" * 80,
                "d3": "无关部署记录。" + "噪声" * 80,
            },
            golden_facts=["Aurora-17", "Lin"],
            expected_tools=["search", "read"],
        )
        results = []
        for method in ("none", "pruner_v1"):
            env = KnowledgeBase(task.docs)
            result = LangGraphReActAgent(
                MockLLM(env),
                env,
                max_turns=8,
                method=method,
                context_budget=ContextBudget(400, 550, 350),
            ).run(task)
            results.append(result)

        self.assertTrue(all(result.success for result in results))
        self.assertFalse(results[0].plugin_metrics["enabled"])
        self.assertTrue(results[1].plugin_metrics["enabled"])
        self.assertEqual(0, results[1].plugin_metrics["resync_count"])
        self.assertGreater(results[1].checkpoint_count, 0)
        self.assertEqual("langgraph", results[1].summary("exp", "r00")["framework"])

    def test_pruned_graph_keeps_unread_document_ids_in_protected_ledger(self):
        task = Task(
            task_id="ledger_001",
            scenario="tool_chain",
            question="找出代号和负责人",
            docs={"d1": "项目代号 Aurora-17", "d2": "项目负责人 Lin"},
            golden_facts=["Aurora-17", "Lin"],
            expected_tools=["search", "read"],
        )
        env = KnowledgeBase(task.docs)
        client = _LedgerAwareClient()

        result = LangGraphReActAgent(
            client,
            env,
            max_turns=6,
            method="pruner_v1",
            context_budget=ContextBudget(220, 300, 180),
        ).run(task)

        self.assertTrue(result.success)
        self.assertTrue(client.saw_d2_in_ledger)
        self.assertEqual(["search", "read", "read"], [row["name"] for row in result.actions])
        summary = result.summary("exp", "r00")
        self.assertGreater(summary["tool_ledger_tokens_total"], 0)
        self.assertGreater(summary["ephemeral_context_tokens_total"], 0)
        self.assertIn("model_input_budget_violation_count", summary)

    def test_exhaustive_search_can_be_refined_to_strict_known_subset(self):
        task = Task(
            task_id="refine_001",
            scenario="long_context",
            question="找出 Lotus 保存年限、存储和审批角色",
            docs={
                "d1": "Lotus 审计数据保存 7 年",
                "d2": "存储使用 WORM",
                "d3": "到期数据由合规总监审批",
            },
            golden_facts=["7 年", "WORM", "合规总监"],
            expected_tools=["search", "read"],
        )
        env = KnowledgeBase(task.docs)
        result = LangGraphReActAgent(
            _RefinementClient(),
            env,
            max_turns=8,
            method="pruner_v1",
            context_budget=ContextBudget(260, 360, 220),
        ).run(task)

        searches = [row for row in result.actions if row["name"] == "search"]
        self.assertTrue(result.success)
        self.assertEqual(2, len(searches))
        self.assertIn(
            "d3",
            [row["args"]["doc_id"] for row in result.actions if row["name"] == "read"],
        )
        self.assertEqual(0, result.repeated_action_count)

    def test_incomplete_final_answer_is_rejected_using_visible_question_only(self):
        task = Task(
            task_id="completion_001",
            scenario="tool_chain",
            question="说明 RPO 和 RTO 两项恢复目标",
            docs={"d1": "RPO 为 6 小时", "d2": "RTO 为 2 小时"},
            golden_facts=["RPO", "6 小时", "RTO", "2 小时"],
            expected_tools=["search", "read"],
        )
        env = KnowledgeBase(task.docs)
        client = _IncompleteFinalClient()
        result = LangGraphReActAgent(
            client,
            env,
            max_turns=8,
            method="pruner_v1",
            context_budget=ContextBudget(220, 300, 180),
        ).run(task)

        self.assertTrue(result.success)
        self.assertEqual(1, result.completion_rejection_count)
        self.assertTrue(client.saw_completion_feedback)

    def test_visible_question_terms_are_supplied_as_completion_contract(self):
        task = Task(
            task_id="contract_001",
            scenario="tool_chain",
            question="说明 RPO 和 RTO",
            docs={"d1": "RPO 6 小时", "d2": "RTO 2 小时"},
            golden_facts=["RPO", "RTO"],
            expected_tools=[],
        )
        env = KnowledgeBase(task.docs)
        client = _ContractAwareClient()
        result = LangGraphReActAgent(client, env, method="none").run(task)

        self.assertTrue(result.success)
        self.assertTrue(client.saw_contract)

    def test_parse_format_error_does_not_trigger_evidence_recovery(self):
        task = Task(
            task_id="format_retry_001",
            scenario="tool_chain",
            question="给出完成状态",
            docs={},
            golden_facts=["完成"],
            expected_tools=[],
        )
        result = LangGraphReActAgent(
            _MalformedOnceClient(),
            KnowledgeBase({}),
            max_turns=4,
            method="pruner_v1",
            context_budget=ContextBudget(220, 300, 180),
        ).run(task)

        self.assertTrue(result.success)
        self.assertEqual(1, result.parse_error_count)
        self.assertEqual(0, result.recovery_count)


class _LedgerAwareClient:
    def __init__(self):
        self.calls = 0
        self.saw_d2_in_ledger = False

    def reset(self):
        self.calls = 0
        self.saw_d2_in_ledger = False

    def complete(self, messages, temperature=0.0):
        self.calls += 1
        ledger = "\n".join(
            row["content"] for row in messages if "工具状态" in row.get("content", "")
        )
        if self.calls == 1:
            return '{"action":{"name":"search","args":{"keyword":"项目"}}}'
        if self.calls == 2:
            self.saw_d2_in_ledger = "d2" in ledger
            return '{"action":{"name":"read","args":{"doc_id":"d1"}}}'
        if self.calls == 3:
            self.saw_d2_in_ledger = self.saw_d2_in_ledger and "d2" in ledger
            return '{"action":{"name":"read","args":{"doc_id":"d2"}}}'
        return '{"final_answer":"项目代号 Aurora-17，负责人 Lin"}'


class _MalformedOnceClient:
    def __init__(self):
        self.calls = 0

    def reset(self):
        self.calls = 0

    def complete(self, messages, temperature=0.0):
        self.calls += 1
        if self.calls == 1:
            return "not-json"
        return '{"final_answer":"状态：完成"}'


class _RefinementClient:
    def __init__(self):
        self.calls = 0

    def reset(self):
        self.calls = 0

    def complete(self, messages, temperature=0.0):
        self.calls += 1
        replies = (
            '{"action":{"name":"search","args":{"keyword":"未知宽泛查询"}}}',
            '{"action":{"name":"read","args":{"doc_id":"d1"}}}',
            '{"action":{"name":"search","args":{"keyword":"审批"}}}',
            '{"action":{"name":"read","args":{"doc_id":"d3"}}}',
            '{"action":{"name":"read","args":{"doc_id":"d2"}}}',
            '{"final_answer":"保存 7 年，使用 WORM，由合规总监审批"}',
        )
        return replies[min(self.calls - 1, len(replies) - 1)]


class _IncompleteFinalClient:
    def __init__(self):
        self.calls = 0
        self.saw_completion_feedback = False

    def reset(self):
        self.calls = 0
        self.saw_completion_feedback = False

    def complete(self, messages, temperature=0.0):
        self.calls += 1
        if self.calls == 1:
            return '{"action":{"name":"search","args":{"keyword":"RPO RTO"}}}'
        if self.calls == 2:
            return '{"action":{"name":"read","args":{"doc_id":"d1"}}}'
        if self.calls == 3:
            return '{"final_answer":"RPO 为 6 小时"}'
        self.saw_completion_feedback = self.saw_completion_feedback or any(
            "缺少用户问题中明确要求的项目：RTO" in row.get("content", "")
            for row in messages
        )
        if self.calls == 4:
            return '{"action":{"name":"read","args":{"doc_id":"d2"}}}'
        return '{"final_answer":"RPO 为 6 小时，RTO 为 2 小时"}'


class _ContractAwareClient:
    def __init__(self):
        self.saw_contract = False

    def reset(self):
        self.saw_contract = False

    def complete(self, messages, temperature=0.0):
        self.saw_contract = any(
            "回答完整性清单" in row.get("content", "")
            and "RPO、RTO" in row.get("content", "")
            for row in messages
        )
        return '{"final_answer":"RPO 6 小时，RTO 2 小时"}'


if __name__ == "__main__":
    unittest.main()
