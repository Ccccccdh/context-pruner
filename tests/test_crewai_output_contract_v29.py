"""Offline regression and negative controls for the proposed host output layer."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from experiments.runners.crewai_output_contract_v29 import (
    ContractedReplayCrewAILLM, OutputContractState, normalize, normalize_role_outputs,
    register, release,
)
from experiments.runners import crewai_semantic_equivalence_v8 as judge

ROOT = Path(__file__).resolve().parents[1]


def rows(batch: str) -> list[dict]:
    path = ROOT / "runs/stage5-crewai" / batch / "results.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


class OutputContractV29(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks28 = {t["task_id"]: t for t in json.loads((
            ROOT / "tasks/stage5_autogen/natural_tasks_r28_fresh.json").read_text(encoding="utf-8"))}
        cls.tasks27 = {t["task_id"]: t for t in json.loads((
            ROOT / "tasks/stage5_autogen/natural_tasks_r27_variable.json").read_text(encoding="utf-8"))}

    def test_r28_retrospective_keeps_ambiguous_field_failures(self):
        transformed = 0
        strict = 0
        for row in rows("crewai-r28-variable-source-confirm-01"):
            task = self.tasks28[row["task_id"]]
            served, edits = normalize_role_outputs(row, task)
            self.assertEqual(len(served), 2)
            self.assertTrue(judge.judge_handoff(judge.handle_rule(task), served[0]).strict_pass)
            strict += judge.judge_answer(judge.answer_rule(task), served[1]).strict_pass
            transformed += bool(edits)
        self.assertEqual(strict, 16)
        self.assertGreaterEqual(transformed, 4)

    def test_r27_decision_collision_remains_failed(self):
        row = next(r for r in rows("crewai-r27-variable-source-dev-01")
                   if r["task_id"] == "shipment_customs_dispatch")
        task = self.tasks27[row["task_id"]]
        served, _ = normalize_role_outputs(row, task)
        self.assertFalse(judge.judge_handoff(judge.handle_rule(task), served[0]).strict_pass)

    def test_wrong_value_wrong_decision_and_absent_tool_are_unchanged(self):
        task = self.tasks28["dns_cutover_wait"]
        observed = [tool["name"] for tool in task["tools"]]
        for raw in ("RESULT task=dns_cutover_wait decision=GO evidence=ttl=60s",
                    "RESULT task=dns_cutover_wait decision=WAIT evidence=ttl=61s"):
            served, _ = normalize(raw, task, stage=1, observed_tools=observed)
            self.assertFalse(judge.judge_answer(judge.answer_rule(task), served).strict_pass)
        raw = "RESULT task=dns_cutover_wait decision=WAIT evidence=ttl=60s"
        served, edits = normalize(raw, task, stage=1, observed_tools=["read_zone_topology"])
        self.assertEqual(served, raw)
        self.assertEqual(edits, [])

    def test_marker_normalization_does_not_add_missing_facts(self):
        task = self.tasks28["invoice_settlement_finalize"]
        served, edits = normalize("HANDOFF: invoices-q12", task, stage=0,
                                  observed_tools=["read_invoice_batch"])
        self.assertEqual(served, "HANDOFF invoices-q12")
        self.assertTrue(edits)
        self.assertFalse(judge.judge_handoff(judge.handle_rule(task), served).strict_pass)

    def test_bounded_retry_requests_field_name_without_supplying_value(self):
        task = self.tasks28["dns_cutover_wait"]
        observed = [tool["name"] for tool in task["tools"]]
        first = ("RESULT task=dns_cutover_wait decision=WAIT evidence="
                 "failed=1 ttl=60s cutover_ready=false blocking_checks=1")
        corrected = ("RESULT task=dns_cutover_wait decision=WAIT evidence="
                     "failed_probes=1 ttl_seconds=60 cutover_ready=false blocking_checks=1")
        responses = iter((first, corrected))
        prompts = []

        def call_once(messages):
            prompts.append(list(messages))
            return next(responses)

        state = OutputContractState(task, stage=1, observed_tools=observed)
        served = state.process([{"role": "user", "content": "decide"}], call_once)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(state.retry_count, 1)
        self.assertIn("failed_probes", prompts[-1][-1]["content"])
        self.assertNotIn("failed_probes 1", prompts[-1][-1]["content"])
        self.assertTrue(judge.judge_answer(judge.answer_rule(task), served).strict_pass)

    def test_retry_stops_after_one_and_wrong_decision_stays_wrong(self):
        task = self.tasks28["dns_cutover_wait"]
        raw = "RESULT task=dns_cutover_wait decision=GO evidence=failed=2 ttl=61s"
        calls = []
        state = OutputContractState(task, stage=1,
                                    observed_tools=[tool["name"] for tool in task["tools"]])
        served = state.process([], lambda messages: (calls.append(1), raw)[1])
        self.assertEqual(len(calls), 2)
        self.assertEqual(served, raw)
        self.assertFalse(judge.judge_answer(judge.answer_rule(task), served).strict_pass)

    def test_replay_llm_path_meters_a_second_call_in_host_stack(self):
        task = self.tasks28["dns_cutover_wait"]
        raw = "RESULT task=dns_cutover_wait decision=WAIT evidence=failed=1 ttl=60s cutover_ready=false blocking_checks=1"
        fixed = "RESULT task=dns_cutover_wait decision=WAIT evidence=failed_probes=1 ttl_seconds=60 cutover_ready=false blocking_checks=1"
        llm = ContractedReplayCrewAILLM([raw, fixed])
        state = OutputContractState(task, stage=1,
                                    observed_tools=[tool["name"] for tool in task["tools"]])
        register(llm, state)
        try:
            served = llm.call([{"role": "user", "content": "decide"}])
        finally:
            release(llm)
        self.assertEqual(state.retry_count, 1)
        self.assertEqual(len(state.records), 2)
        self.assertTrue(judge.judge_answer(judge.answer_rule(task), served).strict_pass)


if __name__ == "__main__":
    unittest.main()
