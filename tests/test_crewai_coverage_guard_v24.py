"""Zero-API controls against the actual r23 failure boundary and legal repeats."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from experiments.runners.crewai_coverage_guard_v24 import (
    CoverageGuardState, GuardExhausted, GuardedCapturedCrewAILLM,
    GuardedCapturedNativeSummaryCrewAILLM,
    classify, register, release,
)
from experiments.runners.run_crewai_experiment import RequestBudget

ROOT = Path(__file__).resolve().parents[1]
RESULTS = (ROOT / "runs/stage5-crewai/crewai-r23-orderfree-3arm-cumulative-01"
           / "results.jsonl")


def completed(*names: str) -> list[dict]:
    return [{"role": "assistant", "content":
             f"Action: {name}\nObservation: {{\"ok\":true}}"} for name in names]


class CoverageGuardTests(unittest.TestCase):
    def test_real_r23_plugin_handoffs_split_exactly_at_missing_source(self):
        rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines()]
        counts = {"reject_actionless": 0, "complete": 0}
        for row in rows:
            if row["method"] != "pruner_v1":
                continue
            handoff = next(entry for entry in row["full_attempt_capture"]
                           if entry["host_parsed_action"] is None
                           and "HANDOFF" in str(entry["raw_model_output"]))
            verdict, missing = classify(handoff["full_input_messages"],
                                        handoff["normalized_model_output"],
                                        row["expected_first_role_trace"])
            counts[verdict] += 1
            self.assertEqual(verdict == "complete", len(row["first_role_trace"]) == 4)
            self.assertEqual(bool(missing), verdict != "complete")
        self.assertEqual(counts, {"reject_actionless": 6, "complete": 6})

    def test_order_free_repeats_are_legal_but_claims_do_not_count(self):
        required = ("a", "b", "c")
        messages = completed("b", "a", "b")
        self.assertEqual(classify(messages, "Action: a", required), ("advance", ("c",)))
        verdict, missing = classify(messages, "HANDOFF a,b,c all done", required)
        self.assertEqual((verdict, missing), ("reject_actionless", ("c",)))
        allowed, control = CoverageGuardState(required).apply(messages, "HANDOFF all done")
        self.assertFalse(allowed)
        self.assertIn("Call c now", control)
        self.assertEqual(classify(completed("c", "b", "a"), "HANDOFF", required),
                         ("complete", ()))

    def test_bounded_rejection_and_unknown_host_tool_fail_closed(self):
        state = CoverageGuardState(("a", "b"), max_rejections=1)
        self.assertFalse(state.apply(completed("a"), "HANDOFF")[0])
        with self.assertRaises(GuardExhausted):
            state.apply(completed("a"), "HANDOFF")
        self.assertTrue(state.exhausted)
        self.assertEqual(classify(completed("alien"), "HANDOFF", ("a", "b"))[0],
                         "invalid_executed_history")

    def test_real_provider_adapter_charges_rejected_and_accepted_calls(self):
        outputs = iter(("HANDOFF before b", "Action: b\nAction Input: {}"))
        sent = []

        def create(**request):
            sent.append(request)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(outputs)),
                                         finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        budget = RequestBudget(2)
        llm = GuardedCapturedCrewAILLM(
            client=client, model="fake", request_budget=budget,
            max_output_tokens=64, thinking_mode="disabled",
            executed_tool_trace=[], frozen_tool_names=("a", "b"),
        )
        state = CoverageGuardState(("a", "b"))
        register(llm, state)
        try:
            response = llm.call(completed("a"))
        finally:
            release(llm)
        self.assertIn("Action: b", response)
        self.assertEqual((budget.used, len(sent), len(llm.capture), state.rejections),
                         (2, 2, 2, 1))
        self.assertIn("Host control: required evidence sources",
                      sent[1]["messages"][-1]["content"])

    def test_native_summary_arm_captures_each_agent_attempt(self):
        outputs = iter(("HANDOFF too early", "Action: b\nAction Input: {}"))
        sent = []

        def create(**request):
            sent.append(request)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(outputs)),
                                         finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        budget = RequestBudget(2)
        llm = GuardedCapturedNativeSummaryCrewAILLM(
            client=client, model="fake", request_budget=budget,
            max_output_tokens=64, thinking_mode="disabled",
            summary_client=None, summary_model="fake",
            soft_limit_tokens=100_000, hard_limit_tokens=120_000,
            target_tokens=80_000, executed_tool_trace=[],
            frozen_tool_names=("a", "b"), summary_records=[],
        )
        register(llm, CoverageGuardState(("a", "b")))
        try:
            response = llm.call(completed("a"))
        finally:
            release(llm)
            llm.close_summary_transport()
        self.assertIn("Action: b", response)
        self.assertEqual((budget.used, len(sent), len(llm.capture)), (2, 2, 2))
        self.assertEqual(llm.summary_attempts, 0)


if __name__ == "__main__":
    unittest.main()
