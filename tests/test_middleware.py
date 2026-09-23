"""Regression tests for the framework-neutral middleware facade."""

import json
import unittest

from context_pruner import (
    ContextBudget,
    ContextPluginConfig,
    ContextPrunerMiddleware,
)


def _history():
    messages = [{"role": "developer", "content": "Never lose project constraints."}]
    messages.append({"role": "user", "content": "Remember codename Aurora-17."})
    for index in range(10):
        messages.append(
            {"role": "assistant", "content": f"analysis {index} " + "background " * 30}
        )
        messages.append(
            {"role": "user", "content": f"observation {index} " + "noise " * 25}
        )
    return messages


class ContextPrunerMiddlewareTest(unittest.TestCase):
    def config(self):
        return ContextPluginConfig(budget=ContextBudget(260, 360, 220))

    def test_reserves_ephemeral_input_and_exports_combined_metrics(self):
        middleware = ContextPrunerMiddleware(self.config(), task_state="Aurora-17")

        result = middleware.before_model(_history(), reserved_tokens=80)

        self.assertEqual(80, result.metrics["reserved_tokens_total"])
        self.assertEqual(80, result.metrics["peak_reserved_tokens"])
        self.assertEqual(
            result.metrics["last_served_context_tokens"] + 80,
            result.metrics["peak_model_input_tokens"],
        )
        self.assertIn("model_input_budget_violation_count", result.metrics)
        self.assertEqual(
            280,
            middleware.plugin.manager.export_state()["last_budget_limit"],
        )

    def test_json_state_round_trip_does_not_duplicate_history(self):
        messages = _history()
        middleware = ContextPrunerMiddleware(self.config(), task_state="Aurora-17")
        first = middleware.before_model(messages, reserved_tokens=50)
        state = json.loads(json.dumps(first.lifecycle_state, ensure_ascii=False))

        resumed = ContextPrunerMiddleware.from_state(state, config=self.config())
        second = resumed.before_model(messages, reserved_tokens=50)

        self.assertEqual(2, second.metrics["before_model_calls"])
        self.assertEqual(100, second.metrics["reserved_tokens_total"])
        self.assertEqual(0, second.metrics["resync_count"])

    def test_developer_message_is_normalized_as_protected_system_context(self):
        middleware = ContextPrunerMiddleware(self.config())
        middleware.before_model(_history())

        first = middleware.plugin.manager.context_events[0]

        self.assertEqual("system", first.role)

    def test_explicit_english_user_constraint_remains_in_model_view(self):
        middleware = ContextPrunerMiddleware(self.config())

        result = middleware.before_model(_history())
        rendered = json.dumps(result.messages, ensure_ascii=False)

        self.assertIn("Aurora-17", rendered)
        self.assertNotIn("已归档", rendered)


if __name__ == "__main__":
    unittest.main()
