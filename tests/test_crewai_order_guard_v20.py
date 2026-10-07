"""Zero-API checks for the r20 order guard: predicates, replay invariants, view identity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runners import crewai_loop_guard_v18 as guard  # noqa: E402

TOOLS = ["read_a", "read_b", "read_c", "read_d"]
HISTORY_AFTER_A = [
    {"role": "assistant", "content": "Action: read_a\nAction Input: {}\nObservation: x"}
]
MEASUREMENT = ROOT / "integrations/crewai/R20_ORDER_GUARD_MEASUREMENT_20261006.json"


class OrderGuardPredicateTest(unittest.TestCase):
    """The three refusals the r20 assignment names, plus the release case."""

    def test_out_of_order_action_is_refused(self) -> None:
        verdict, completed, next_tool = guard.classify(
            HISTORY_AFTER_A, "Action: read_c\nAction Input: {}", TOOLS
        )
        self.assertEqual("reject_wrong_action_or_order", verdict)
        self.assertEqual(1, completed)
        self.assertEqual("read_b", next_tool)

    def test_repeated_action_is_refused(self) -> None:
        verdict, _, _ = guard.classify(
            HISTORY_AFTER_A, "Action: read_a\nAction Input: {}", TOOLS
        )
        self.assertEqual("reject_wrong_action_or_order", verdict)

    def test_actionless_turn_is_refused(self) -> None:
        verdict, _, next_tool = guard.classify(HISTORY_AFTER_A, "HANDOFF done", TOOLS)
        self.assertEqual("reject_actionless", verdict)
        self.assertEqual("read_b", next_tool)

    def test_skipping_ahead_is_refused(self) -> None:
        verdict, _, _ = guard.classify(
            HISTORY_AFTER_A, "Action: read_d\nAction Input: {}", TOOLS
        )
        self.assertEqual("reject_wrong_action_or_order", verdict)

    def test_correct_next_tool_is_released(self) -> None:
        verdict, completed, next_tool = guard.classify(
            HISTORY_AFTER_A, "Action: read_b\nAction Input: {}", TOOLS
        )
        self.assertEqual("advance", verdict)
        self.assertEqual(1, completed)
        self.assertEqual("read_b", next_tool)

    def test_complete_table_releases_anything(self) -> None:
        history = [
            {"role": "assistant",
             "content": f"Action: {name}\nAction Input: {{}}\nObservation: x"}
            for name in TOOLS
        ]
        verdict, completed, _ = guard.classify(history, "HANDOFF done", TOOLS)
        self.assertEqual("complete", verdict)
        self.assertEqual(4, completed)


class OrderGuardReplayTest(unittest.TestCase):
    """The recorded r17 confirmation trajectories, replayed through the predicates."""

    def setUp(self) -> None:
        self.assertTrue(MEASUREMENT.is_file(), "run the r20 measurement first")
        self.report = json.loads(MEASUREMENT.read_text(encoding="utf-8"))

    def test_compliant_arms_never_refused(self) -> None:
        for arm in ("none", "native_summary"):
            entry = self.report["arm_totals"][arm]
            with self.subTest(arm=arm):
                self.assertEqual(0, entry["order_guard_refusals"])
                self.assertEqual(0, entry["order_guard_refusal_units"])

    def test_plugin_misordered_units_are_refused_and_counted(self) -> None:
        entry = self.report["arm_totals"]["pruner_v1"]
        self.assertEqual(3, entry["misordered_units"])
        self.assertEqual(3, entry["order_guard_refusal_units"])
        self.assertEqual(11, entry["order_guard_refusals"])
        self.assertEqual(
            {"reject_wrong_action_or_order": 11}, entry["refusal_verdicts"]
        )

    def test_under_called_and_misordered_are_separately_counted(self) -> None:
        modes = self.report["failure_modes_pruner_v1"]
        self.assertEqual([], modes["under_called"])
        self.assertEqual(
            ["autoscale_policy_gate/0", "autoscale_policy_gate/1",
             "autoscale_policy_gate/2"],
            modes["misordered_but_complete"],
        )
        self.assertEqual(9, modes["none"])

    def test_ordered_prefix_distribution_is_recorded_per_arm(self) -> None:
        distribution = self.report["ordered_prefix_distribution"]
        self.assertEqual({"4/4": 12}, distribution["none"])
        self.assertEqual({"4/4": 12}, distribution["native_summary"])
        self.assertEqual({"4/4": 9, "1/4": 3}, distribution["pruner_v1"])

    def test_view_is_byte_identical_and_controller_only(self) -> None:
        invariance = self.report["view_invariance"]
        for task_id, entry in invariance["per_task"].items():
            with self.subTest(task=task_id):
                self.assertTrue(entry["byte_identical"])
                self.assertFalse(entry["control_text_in_view"])
                self.assertEqual(
                    entry["view_sha256_without_guard"], entry["view_sha256_with_guard"]
                )
        controller = invariance["controller_only"]
        self.assertFalse(controller["imports_view_layer"])
        self.assertTrue(controller["views_byte_identical_every_task"])
        self.assertTrue(controller["control_text_never_in_view"])

    def test_cost_lower_bound_is_reported_and_negative(self) -> None:
        cost = self.report["cost"]
        self.assertLess(cost["lower_bound_all_units"], 0)
        self.assertLess(cost["lower_bound_refused_units"], 0)
        self.assertEqual(3282, cost["max_observed_call_tokens"])
        self.assertEqual(6, cost["guard_cap"])

    def test_cost_negative_value_comes_from_resends_not_compression(self) -> None:
        """Corrected reading: one task's re-sends make an otherwise positive shape negative."""
        entry = self.report["conditions"]["batch_lower_bound_positive"]
        self.assertEqual(54094.6, entry["resend_driven_overrun_tokens"])
        self.assertGreater(entry["compression_driven_difference_percent"], 0)
        correction = self.report["correction_20261006"]
        self.assertEqual(31, correction["resend_driven_overrun"]["overrun_calls"])
        self.assertEqual(
            9, correction["compression_driven_difference"]["same_work_units"]
        )
        self.assertGreater(
            correction["compression_driven_difference"]["same_work_percent"], 0
        )
        self.assertGreater(
            correction["batch_after_removing_the_overrun"]["difference_percent"], 0
        )

    def test_stop_reason_is_quality_not_cost(self) -> None:
        correction = self.report["correction_20261006"]
        self.assertEqual("quality", correction["stop_reason_that_survives"]["side"])
        statement = correction["stop_reason_that_survives"]["statement"]
        self.assertIn("autoscale_policy_gate", statement)
        self.assertIn("does not work on that task", statement)


if __name__ == "__main__":
    unittest.main()
