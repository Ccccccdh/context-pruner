"""Zero-API controls for the prospective all-arm tool-order guard and auditor."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from argparse import Namespace
from unittest.mock import patch

from experiments.audits.audit_crewai_handoff_v18_sanity import audit
from experiments.runners.crewai_loop_guard_v18 import (
    GuardExhausted, GuardedReplayCrewAILLM, GuardedNativeSummaryCrewAILLM,
    classify, create_guard_state, register_guard_state, release_guard_state,
)
from experiments.runners import run_crewai_experiment as base
from experiments.runners.run_crewai_handoff_v18_zero_api import make_live_guarded_llm

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "integrations/crewai/R18_DEVELOPMENT_TASK_REGISTRY_20261006.json"


def executed(*tools: str) -> list[dict]:
    return [{"role": "assistant", "content": f"Thought: call\nAction: {tool}\nAction Input: {{}}\nObservation: {{}}"} for tool in tools]


def fake_rows() -> list[dict]:
    tasks = json.loads(REGISTRY.read_text(encoding="utf-8"))["tasks"]
    rows = []
    for task in tasks:
        for arm in ("none", "pruner_v1", "native_summary"):
            agent = [{"input_tokens": 100, "output_tokens": 10}]
            summary = ([{"input_tokens": 20, "output_tokens": 5}] if arm == "native_summary" else [])
            rows.append({"task_id": task["task_id"], "repeat": 0, "method": arm,
                         "expected_first_role_trace": task["tools"], "first_role_trace": task["tools"],
                         "guard_enabled": True, "guard_exhausted": False,
                         "guard_rejections": 0, "guard_rejection_records": [], "error": "",
                         "answer": f"RESULT decision={task['decision']} " + " ".join(task["required_literals"]),
                         "strict_success": True, "agent_attempt_records": agent,
                         "summary_attempt_records": summary,
                         "api_request_attempts": 1 + len(summary),
                         "all_arm_total_tokens": 110 + (25 if summary else 0)})
    return rows


class V18GuardControls(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = ["first", "second", "third", "fourth"]

    def test_all_arm_compliant_trace_zero_rejections(self) -> None:
        for arm in ("none", "pruner_v1", "native_summary"):
            state = create_guard_state(self.tools)
            for index, tool in enumerate(self.tools):
                accepted, _ = state.apply(executed(*self.tools[:index]), f"Thought: call\nAction: {tool}\nAction Input: {{}}")
                self.assertTrue(accepted, arm)
            self.assertTrue(state.apply(executed(*self.tools), "HANDOFF done")[0])
            self.assertEqual(state.rejections, 0)

    def test_bare_and_marked_handoff_rejected(self) -> None:
        for response in ("HANDOFF done", "Final Answer: HANDOFF done"):
            state = create_guard_state(self.tools)
            accepted, control = state.apply(executed("first"), response)
            self.assertFalse(accepted)
            self.assertIn("Call second", control)
            self.assertEqual(state.rejections, 1)

    def test_wrong_order_duplicate_and_multiple_actions_rejected(self) -> None:
        state = create_guard_state(self.tools)
        for response in ("Action: third\nAction Input: {}", "Action: first\nAction Input: {}", "Action: second\nAction: third"):
            accepted, _ = state.apply(executed("first"), response)
            self.assertFalse(accepted)
        self.assertEqual(state.rejections, 3)
        self.assertTrue(state.apply(executed("first"), "Action: second\nAction Input: {}")[0])

    def test_bounded_exhaustion_fails_closed(self) -> None:
        state = create_guard_state(self.tools, max_rejections=2)
        for _ in range(2):
            self.assertFalse(state.apply([], "HANDOFF")[0])
        with self.assertRaises(GuardExhausted):
            state.apply([], "Action: fourth\nAction Input: {}")
        self.assertTrue(state.exhausted)
        self.assertEqual(state.rejections, 2)

    def test_invalid_executed_history_fails_closed(self) -> None:
        self.assertEqual(classify(executed("first", "third"), "Action: second", self.tools)[0], "invalid_executed_history")
        with self.assertRaises(GuardExhausted):
            create_guard_state(self.tools).apply(executed("first", "third"), "Action: second")

    def test_replay_wrapper_retries_and_counts_every_call(self) -> None:
        llm = GuardedReplayCrewAILLM(["HANDOFF early", "Action: third\nAction Input: {}", "Action: first\nAction Input: {}"])
        state = create_guard_state(self.tools)
        register_guard_state(llm, state)
        try:
            self.assertIn("Action: first", llm.call([]))
            self.assertEqual(state.rejections, 2)
            self.assertEqual(len(llm.attempt_records), 3)
            self.assertIn("Call first", llm.inputs[-1][-1]["content"])
        finally:
            release_guard_state(llm)

    def test_summary_arm_has_guard_wrapper(self) -> None:
        self.assertTrue(issubclass(GuardedNativeSummaryCrewAILLM, base.NativeSummaryCrewAILLM))

    def test_native_summary_live_class_uses_guard_before_provider_result(self) -> None:
        args = Namespace(model="mock", max_output_tokens=512, fixed_reserved_tokens=300,
                         max_summary_tokens=1024, max_summary_calls=4,
                         provider_soft=1200, provider_hard=3000, provider_target=900,
                         soft_limit=0, hard_limit=0, target=0)
        budget, _ = base.resolve_budget(args)
        state = create_guard_state(["first"])
        llm = make_live_guarded_llm("native_summary", object(), base.RequestBudget(10),
                                    args, budget, object(), state)
        replies = iter(["HANDOFF early", "Action: first\nAction Input: {}"])

        def fake_provider(instance, messages, **kwargs):
            instance.attempt_records.append({"status": "success", "input_tokens": 10, "output_tokens": 2})
            return next(replies)

        try:
            with patch.object(base.NativeSummaryCrewAILLM, "call", fake_provider):
                self.assertIn("Action: first", llm.call([]))
            self.assertEqual(state.rejections, 1)
            self.assertEqual(len(llm.attempt_records), 2)
        finally:
            llm.close_summary_transport()
            release_guard_state(llm)

    def test_registry_auditor_positive_and_negative_controls(self) -> None:
        rows = fake_rows()
        good = audit(REGISTRY, rows, repeats=1)
        self.assertTrue(good["complete"], good["errors"])
        self.assertEqual(good["requests"], 12)
        self.assertEqual(good["complete_tokens"], 1065)
        bad = json.loads(json.dumps(rows))
        bad[0]["first_role_trace"] = bad[0]["first_role_trace"][:-1]
        bad[1]["guard_rejections"] = 1
        bad[2]["all_arm_total_tokens"] -= 1
        result = audit(REGISTRY, bad, repeats=1)
        self.assertFalse(result["complete"])
        self.assertTrue(any("tool trace mismatch" in x for x in result["errors"]))
        self.assertTrue(any("guard ledger mismatch" in x for x in result["errors"]))
        self.assertTrue(any("complete token ledger mismatch" in x for x in result["errors"]))


if __name__ == "__main__":
    unittest.main()
