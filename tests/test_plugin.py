"""Phase 3 plugin and framework-adapter regression tests."""

import asyncio
import json
import unittest

from context_pruner import ContextBudget, ContextLifecyclePlugin, ContextPluginConfig
from context_pruner.adapters import LangGraphContextAdapter


def _history():
    messages = [
        {"role": "system", "content": "Always preserve constraints."},
        {"role": "user", "content": "必须记住项目代号 Aurora-17。"},
    ]
    for index in range(8):
        messages.append(
            {"role": "assistant", "content": f"普通分析 {index}：" + "背景信息" * 30}
        )
        messages.append(
            {"role": "user", "content": f"观察：普通工具输出 {index}。" + "噪声" * 20}
        )
    return messages


class ContextLifecyclePluginTest(unittest.TestCase):
    def config(self, enabled=True):
        return ContextPluginConfig(
            enabled=enabled,
            budget=ContextBudget(260, 360, 220),
        )

    def test_disabled_plugin_is_exact_pass_through(self):
        messages = _history()
        plugin = ContextLifecyclePlugin(self.config(False), task_state="记住代号")

        result = plugin.before_model(messages)

        self.assertEqual(messages, result.messages)
        self.assertEqual(0, result.metrics["gross_input_tokens_saved"])
        self.assertFalse(result.metrics["enabled"])

    def test_enabled_plugin_reduces_model_view_and_keeps_full_event_history(self):
        messages = _history()
        plugin = ContextLifecyclePlugin(self.config(), task_state="记住 Aurora-17")

        result = plugin.before_model(messages)
        visible = "\n".join(
            str(item.get("content", "")) if isinstance(item, dict) else str(item)
            for item in result.messages
        )

        self.assertGreater(result.metrics["gross_input_tokens_saved"], 0)
        self.assertLess(
            result.metrics["peak_served_context_tokens"],
            result.metrics["peak_full_context_tokens"],
        )
        self.assertIn("Aurora-17", visible)
        self.assertEqual(len(messages), len(plugin.manager.context_events))
        self.assertGreater(plugin.manager.archive_count, 0)

    def test_serialized_state_resumes_without_duplicate_events(self):
        messages = _history()
        plugin = ContextLifecyclePlugin(self.config(), task_state="记住 Aurora-17")
        plugin.before_model(messages)
        state = json.loads(json.dumps(plugin.export_state(), ensure_ascii=False))

        resumed = ContextLifecyclePlugin.from_state(state, config=self.config())
        resumed.before_model(messages)
        resumed.after_model({"role": "assistant", "content": "最终答案 Aurora-17"})

        self.assertEqual(len(messages) + 1, len(resumed.manager.context_events))
        self.assertEqual(2, resumed.metrics.before_model_calls)
        self.assertEqual(0, resumed.metrics.resync_count)

    def test_replaced_message_prefix_causes_safe_resync(self):
        plugin = ContextLifecyclePlugin(self.config(), task_state="记住代号")
        messages = _history()
        plugin.before_model(messages)
        changed = list(messages)
        changed[1] = {"role": "user", "content": "必须记住项目代号 Borealis-9。"}

        result = plugin.before_model(changed)

        self.assertEqual(1, result.metrics["resync_count"])
        self.assertEqual(len(changed), len(plugin.manager.context_events))

    def test_consecutive_identical_messages_remain_distinct_events(self):
        plugin = ContextLifecyclePlugin(self.config(), task_state="记录重试")
        messages = [
            {"role": "user", "content": "retry"},
            {"role": "user", "content": "retry"},
        ]

        plugin.before_model(messages)

        self.assertEqual(2, len(plugin.manager.context_events))
        self.assertEqual(2, len(plugin.observed_message_keys))


class LangGraphAdapterTest(unittest.TestCase):
    def config(self, enabled=True):
        return ContextPluginConfig(
            enabled=enabled,
            budget=ContextBudget(260, 360, 220),
        )

    def test_wrapped_model_receives_compressed_copy_but_keeps_full_graph_messages(self):
        seen = {}

        def model_node(state):
            seen["messages"] = list(state["messages"])
            return {"messages": [{"role": "assistant", "content": "Aurora-17"}]}

        adapter = LangGraphContextAdapter(self.config())
        wrapped = adapter.wrap_model_node(model_node)
        full = _history()
        result = wrapped({"messages": full, "task": "记住 Aurora-17"})

        self.assertLess(len(seen["messages"]), len(full))
        self.assertEqual(1, len(result["messages"]))
        self.assertIn("context_pruner_state", result)
        self.assertGreater(
            result["context_pruner_metrics"]["gross_input_tokens_saved"], 0
        )

    def test_tool_wrapper_persists_result_as_tool_event(self):
        adapter = LangGraphContextAdapter(self.config())
        before = adapter.before_model_node(
            {"messages": [{"role": "user", "content": "查找 Aurora-17"}], "task": "查找"}
        )

        def tool_node(state):
            return {"messages": [{"role": "tool", "content": "Aurora-17"}]}

        result = adapter.wrap_tool_node(tool_node)(
            {
                "messages": [{"role": "user", "content": "查找 Aurora-17"}],
                "task": "查找",
                **before,
            }
        )
        manager_state = result["context_pruner_state"]["manager"]

        self.assertEqual("tool_result", manager_state["events"][-1]["kind"])

    def test_model_wrapper_reserves_ephemeral_tokens_before_compression(self):
        adapter = LangGraphContextAdapter(
            self.config(),
            budget_resolver=lambda state: 100,
        )

        def model_node(state):
            return {"messages": [{"role": "assistant", "content": "done"}]}

        result = adapter.wrap_model_node(model_node)(
            {"messages": _history(), "task": "记住 Aurora-17"}
        )
        manager = result["context_pruner_state"]["manager"]

        self.assertEqual(260, manager["last_budget_limit"])

    def test_async_wrapper_supports_async_nodes(self):
        adapter = LangGraphContextAdapter(self.config())

        async def model_node(state):
            return {"messages": [{"role": "assistant", "content": "done"}]}

        wrapped = adapter.awrap_model_node(model_node)
        result = asyncio.run(
            wrapped({"messages": _history(), "task": "记住 Aurora-17"})
        )

        self.assertEqual("done", result["messages"][0]["content"])
        self.assertEqual(1, result["context_pruner_metrics"]["before_model_calls"])


if __name__ == "__main__":
    unittest.main()
