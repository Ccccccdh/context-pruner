"""Tests for the optional OpenAI Agents SDK input filter."""

import importlib.util
import unittest

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.adapters import OpenAIAgentsContextFilter


def _history():
    messages = [{"role": "user", "content": "Remember Aurora-17."}]
    for index in range(12):
        messages.append(
            {"role": "assistant", "content": f"analysis {index} " + "background " * 35}
        )
        messages.append(
            {"role": "user", "content": f"observation {index} " + "noise " * 25}
        )
    return messages


class OpenAIAgentsContextFilterTest(unittest.TestCase):
    def adapter(self):
        return OpenAIAgentsContextFilter(
            ContextPluginConfig(budget=ContextBudget(280, 400, 230)),
            task_state="Remember Aurora-17",
            fixed_reserved_tokens=60,
        )

    def test_message_only_input_is_compressed(self):
        context_filter = self.adapter()

        result = context_filter.filter_items(_history(), "Answer concisely.")

        self.assertTrue(result.compressed)
        self.assertLess(len(result.input_items), len(_history()))
        self.assertEqual(1, result.metrics["openai_agents_compressed_calls"])
        self.assertGreater(result.metrics["reserved_tokens_total"], 60)

    def test_responses_tool_items_are_grouped_and_restored_losslessly(self):
        protected = [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "function_call",
                "name": "search",
                "call_id": "call_1",
                "arguments": '{"query":"Aurora-17"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "found Aurora-17",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": "The result is Aurora-17.",
            },
        ]
        items = _history() + protected
        context_filter = self.adapter()

        result = context_filter.filter_items(items, "Use tools.")

        self.assertTrue(result.compressed)
        self.assertIsNone(result.skip_reason)
        self.assertEqual(protected, result.input_items[-len(protected) :])
        self.assertEqual(1, result.protected_group_count)
        self.assertEqual(4, result.protected_item_count)
        self.assertEqual(0, result.unmatched_call_count)
        self.assertEqual(1, result.metrics["openai_agents_protected_group_count"])
        self.assertEqual(0, result.metrics["openai_agents_group_restore_failure_count"])
        self.assertGreater(
            result.metrics["openai_agents_opaque_reserved_tokens_total"],
            0,
        )

    def test_parallel_calls_and_outputs_remain_in_one_ordered_group(self):
        protected = [
            {"type": "reasoning", "id": "rs_parallel", "summary": []},
            {"type": "function_call", "name": "a", "call_id": "call_a"},
            {"type": "function_call", "name": "b", "call_id": "call_b"},
            {"type": "function_call_output", "call_id": "call_a", "output": "A"},
            {"type": "function_call_output", "call_id": "call_b", "output": "B"},
        ]
        items = _history() + protected + [
            {"role": "user", "content": "Now summarize the verified results."}
        ]
        context_filter = self.adapter()

        result = context_filter.filter_items(items, "Use tools.")

        start = result.input_items.index(protected[0])
        self.assertEqual(protected, result.input_items[start : start + len(protected)])
        self.assertEqual(1, result.protected_group_count)
        self.assertEqual(0, result.unmatched_call_count)

    def test_unmatched_call_is_preserved_and_reported(self):
        orphan = {
            "type": "function_call_output",
            "call_id": "missing_call",
            "output": "orphaned",
        }
        context_filter = self.adapter()

        result = context_filter.filter_items(_history() + [orphan], "Use tools.")

        self.assertTrue(result.compressed)
        self.assertIs(orphan, result.input_items[-1])
        self.assertEqual(1, result.unmatched_call_count)
        self.assertEqual(1, result.metrics["openai_agents_unmatched_call_count"])

    def test_growing_tool_group_keeps_stable_state_without_resync(self):
        first_group = [
            {"type": "reasoning", "id": "rs_growing", "summary": []},
            {"type": "function_call", "name": "first", "call_id": "grow_1"},
            {"type": "function_call_output", "call_id": "grow_1", "output": "one"},
        ]
        second_group_items = [
            {"type": "function_call", "name": "second", "call_id": "grow_2"},
            {"type": "function_call_output", "call_id": "grow_2", "output": "two"},
        ]
        context_filter = self.adapter()

        first = context_filter.filter_items(_history() + first_group, "Use tools.")
        second = context_filter.filter_items(
            _history() + first_group + second_group_items,
            "Use tools.",
        )

        self.assertEqual(0, first.metrics["resync_count"])
        self.assertEqual(0, second.metrics["resync_count"])
        self.assertEqual(
            first_group + second_group_items,
            second.input_items[-len(first_group + second_group_items) :],
        )

    @unittest.skipUnless(
        importlib.util.find_spec("agents") is not None,
        "openai-agents optional dependency is not installed",
    )
    def test_callable_returns_real_sdk_model_input_data(self):
        from agents import Agent
        from agents.run import CallModelData, ModelInputData

        protected = [
            {"type": "reasoning", "id": "rs_sdk", "summary": []},
            {"type": "function_call", "name": "lookup", "call_id": "sdk_call"},
            {
                "type": "function_call_output",
                "call_id": "sdk_call",
                "output": "Aurora-17",
            },
        ]
        data = CallModelData(
            model_data=ModelInputData(
                input=_history() + protected,
                instructions="Use tools safely.",
            ),
            agent=Agent(name="Offline contract test"),
            context=None,
        )
        context_filter = self.adapter()

        result = context_filter(data)

        self.assertIsInstance(result, ModelInputData)
        self.assertEqual(protected, result.input[-len(protected) :])
        self.assertEqual("Use tools safely.", result.instructions)


if __name__ == "__main__":
    unittest.main()
