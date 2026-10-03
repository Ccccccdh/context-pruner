"""Zero-API gates for the two-role fixed-input CrewAI experiment."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


_STORAGE = Path(tempfile.gettempdir()) / "context-pruner-crewai-handoff-tests"
_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("LOCALAPPDATA", str(_STORAGE))
os.environ.setdefault("CREWAI_STORAGE_DIR", str(_STORAGE))
INSTALLED = importlib.util.find_spec("crewai") is not None

if INSTALLED:
    from experiments.runners import run_crewai_handoff as pilot


@unittest.skipUnless(INSTALLED, "CrewAI optional dependency is not installed")
class CrewAIHandoffTest(unittest.TestCase):
    def test_balanced_arm_order_without_changing_repeat_input(self) -> None:
        tasks = pilot.base.load_tasks(pilot.TASK_FILE)[:3]
        methods = ("none", "native_summary", "pruner_v1")
        plan = pilot.balanced_plan(tasks, methods, 3)
        self.assertEqual(27, len(plan))
        for task in tasks:
            first_arms = [next(method for item, repeat, method in plan
                               if item["task_id"] == task["task_id"] and repeat == r)
                          for r in range(3)]
            self.assertEqual(set(methods), set(first_arms))
            self.assertEqual(pilot.fixed_history(task), pilot.fixed_history(task))

    def test_fixed_repeat_history_and_distinct_role_adapters(self) -> None:
        task = pilot.base.load_tasks(pilot.TASK_FILE)[0]
        self.assertEqual(pilot.fixed_history(task), pilot.fixed_history(task))
        args = SimpleNamespace(
            model="mock", base_url="", max_output_tokens=512,
            fixed_reserved_tokens=300, max_summary_tokens=1024,
            max_summary_calls=4,
        )
        budget = pilot.ContextBudget(3084, 7712, 2313)
        rows = [pilot.run_case(task, "pruner_v1", repeat, "mock", None,
                               pilot.base.RequestBudget(24), args, budget)
                for repeat in (0, 1)]
        self.assertTrue(all(row["success"] for row in rows))
        self.assertEqual(rows[0]["model_input_traces"], rows[1]["model_input_traces"])
        self.assertEqual([[task["tools"][0]], [task["tools"][1]]],
                         rows[0]["role_tool_traces"])
        self.assertEqual(2, len(rows[0]["role_metrics"]))
        self.assertTrue(rows[0]["role_safe"])

    def test_plan_has_no_output_and_no_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pilot.main(["--mode", "api", "--plan", "--out", directory,
                        "--experiment-id", "no-output"])
            self.assertFalse((Path(directory) / "no-output").exists())

    def test_api_requires_explicit_synthetic_data_flag(self) -> None:
        with self.assertRaisesRegex(SystemExit, "confirm-send-synthetic-data"):
            pilot.main(["--mode", "api", "--task-ids", "incident_triage"])


if __name__ == "__main__":
    unittest.main()
