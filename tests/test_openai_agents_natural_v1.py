"""Offline gate for fixed-input operational tasks and real SDK tool dispatch."""

import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from context_pruner import ContextBudget
from experiments.runners import run_openai_agents_natural_v1 as natural


class NaturalCaseTest(unittest.TestCase):
    def test_repeats_have_identical_input(self):
        for name in natural.TASKS:
            first = natural.build_case(name, 0)
            for repeat in (1, 2):
                later = natural.build_case(name, repeat)
                self.assertEqual(first.history, later.history)
                self.assertEqual(first.expected_tool_names, later.expected_tool_names)
                self.assertEqual(first.final_contract, later.final_contract)
                self.assertEqual(first.answer_pattern, later.answer_pattern)

    def test_expected_answers_are_not_in_task_request_or_fixture(self):
        for name in natural.TASKS:
            case = natural.build_case(name, 0)
            latest_request = case.history[-1]["content"]
            self.assertNotIn("RESULT ", latest_request)
            self.assertTrue(case.answer_pattern)
            self.assertEqual(3, len(case.expected_tool_names))

    def test_three_arms_use_real_runner_and_tools_without_api(self):
        from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
        gate_path = Path(__file__).resolve().parents[1] / ".tooling" / "gate_openai_agents_3arm.py"
        spec = importlib.util.spec_from_file_location("openai_natural_gate_helpers", gate_path)
        assert spec and spec.loader
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)

        class Stub(helpers.StubApiModel):
            ANSWERS = {
                "incident_triage": "RESULT incident=INC-204 cause=missing PAYMENT_TOKEN action=rollback release",
                "invoice_reconcile": "RESULT invoice=INV-17 outstanding_usd=120 action=request balance",
                "release_gate": "RESULT release=REL-9 decision=hold reason=Windows tests failed",
            }

            def _tool_call(self, name, index):
                return ResponseFunctionToolCall(
                    arguments="{}", call_id=f"call_{self.case.scenario}_{index}_{name}",
                    name=name, type="function_call",
                )

            def _final_message(self):
                return ResponseOutputMessage(
                    id=f"msg_{self.case.scenario}",
                    content=[ResponseOutputText(
                        annotations=[], text=self.ANSWERS[self.case.scenario], type="output_text"
                    )],
                    role="assistant", status="completed", type="message",
                )

        async def run_all():
            rows = []
            with patch.object(helpers, "StubApiModel", Stub):
                for name in natural.TASKS:
                    case = natural.build_case(name, 0)
                    for method in ("none", "native_summary", "pruner_v1"):
                        rows.append(await helpers._run_arm_case(
                            name, 0, method, budget=ContextBudget(3261, 8152, 2446), case=case
                        ))
            return rows

        rows = asyncio.run(run_all())
        self.assertEqual(9, len(rows))
        for row in rows:
            self.assertTrue(row["success"], (row["scenario"], row["method"], row["error_message"]))
            self.assertTrue(row["structure_safe"])
            self.assertEqual(3, row["tool_calls"])
        self.assertTrue(any(row["compressed_calls"] for row in rows if row["method"] == "pruner_v1"))


if __name__ == "__main__":
    unittest.main()
