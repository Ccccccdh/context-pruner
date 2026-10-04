"""Counter-example-first gate for the prospective CrewAI r5 semantic judge.

Every test in this file runs without API requests.  The first four tests are the
required counter-examples: the judge must reject a missing fact, a wrong version,
a wrong region and a wrong decision, and normalisation must not rescue any of
them.  Only after those pass may the r5 batch be frozen and paid for.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from experiments.runners import crewai_semantic_equivalence_v7 as judge


ROOT = Path(__file__).resolve().parents[1]
TASKS = {task["task_id"]: task for task in judge.load_tasks(judge.TASK_FILE)}


def valid_answer(task_id: str) -> str:
    """The exact answer shape the r7 contract asks for.

    The evidence is the decider's own tool result, written in the natural
    ``key=value`` form the live model produces (the r5/r6 batches showed the
    model never echoes the raw JSON spelling).
    """
    task = TASKS[task_id]
    evidence = judge.tool_evidence(task)[-1].replace('": ', '=').replace('"', "")
    return f"RESULT task={task_id} decision={task['decision']} evidence={evidence}"


class CrewAISemanticCounterExampleTest(unittest.TestCase):
    """The four failure kinds must be rejected under both gates."""

    def _verdict(self, task_id: str, answer: str):
        rule = judge.answer_rule_for(TASKS.values(), task_id)
        return judge.judge_answer(rule, answer)

    def test_counter_example_missing_fact_is_never_rescued(self) -> None:
        answer = valid_answer("queue_backlog_replay").replace("pending_records=12400", "")
        verdict = self._verdict("queue_backlog_replay", answer)
        self.assertFalse(verdict.strict_pass)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("answer_missing_fact", verdict.semantic["issues"])
        self.assertIn("pending records 12400", verdict.semantic["missing_facts"])

    def test_counter_example_wrong_version_is_never_rescued(self) -> None:
        answer = valid_answer("schema_migration").replace("schema-42", "schema-41")
        verdict = self._verdict("schema_migration", answer)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("answer_wrong_version", verdict.semantic["issues"])
        self.assertEqual("schema-41", verdict.semantic["conflicting_version"])
        self.assertIn("schema-41", verdict.semantic["forbidden_hits"])

    def test_counter_example_wrong_region_is_never_rescued(self) -> None:
        answer = valid_answer("region_failover").replace("ap-south-1", "ap-south-2")
        verdict = self._verdict("region_failover", answer)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("answer_wrong_region", verdict.semantic["issues"])
        self.assertEqual("ap-south-2", verdict.semantic["conflicting_region"])
        forbidden = valid_answer("region_failover").replace("ap-south-1", "eu-central-1")
        forbidden_verdict = self._verdict("region_failover", forbidden)
        self.assertFalse(forbidden_verdict.semantic_pass)
        self.assertIn("eu-central-1", forbidden_verdict.semantic["forbidden_hits"])
        self.assertIn("ap-south-1", forbidden_verdict.semantic["missing_facts"])

    def test_counter_example_wrong_decision_is_never_rescued(self) -> None:
        answer = valid_answer("queue_backlog_replay").replace("decision=REPLAY", "decision=DEFER")
        verdict = self._verdict("queue_backlog_replay", answer)
        self.assertFalse(verdict.strict_pass)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("answer_wrong_decision", verdict.semantic["issues"])
        self.assertEqual("defer", verdict.semantic["decision_seen"])

    def test_counter_example_wrong_decision_hidden_in_evidence(self) -> None:
        """A decision word inside the evidence field must not satisfy the decision."""
        answer = valid_answer("queue_backlog_replay").replace(
            "decision=REPLAY", "decision=<miss"
        ).replace("evidence=", "evidence=REPLAY ")
        verdict = self._verdict("queue_backlog_replay", answer)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("answer_wrong_decision", verdict.semantic["issues"])


class CrewAISemanticEquivalenceTest(unittest.TestCase):
    """Formatting-only differences are equivalent; factual ones are not."""

    def _verdict(self, task_id: str, answer: str):
        return judge.judge_answer(judge.answer_rule_for(TASKS.values(), task_id), answer)

    def test_every_valid_answer_passes_both_gates(self) -> None:
        for task_id in TASKS:
            with self.subTest(task_id=task_id):
                verdict = self._verdict(task_id, valid_answer(task_id))
                self.assertTrue(verdict.semantic_pass, verdict.semantic["issues"])
                self.assertTrue(verdict.strict_pass, verdict.strict["issues"])

    def test_the_two_gates_differ_only_on_accepting_spelling(self) -> None:
        """Both gates read the same facts; the semantic gate reads more forms."""
        strict_answer = valid_answer("region_failover")
        self.assertTrue(self._verdict("region_failover", strict_answer).strict_pass)
        marker = valid_answer("schema_migration")
        both = self._verdict("schema_migration", marker)
        self.assertTrue(both.strict_pass, both.strict["issues"])
        self.assertTrue(both.semantic_pass, both.semantic["issues"])

    def test_percent_spelling_and_zero_failed_are_formatting_only(self) -> None:
        strict_answer = valid_answer("region_failover")
        self.assertTrue(self._verdict("region_failover", strict_answer).strict_pass)
        percent = strict_answer.replace("headroom=45%", "headroom 45 percent")
        self.assertIn("45 percent", percent)
        percent_verdict = self._verdict("region_failover", percent)
        self.assertTrue(percent_verdict.semantic_pass, percent_verdict.semantic["issues"])
        self.assertFalse(percent_verdict.strict_pass)
        self.assertIn("answer_missing_fact", percent_verdict.strict["issues"])
        failed = valid_answer("schema_migration").replace("failed=0", "fail 0")
        semantic = self._verdict("schema_migration", failed)
        self.assertTrue(semantic.semantic_pass, semantic.semantic["issues"])
        self.assertFalse(semantic.strict_pass)

    def test_thousands_separator_and_fullwidth_marker_are_formatting_only(self) -> None:
        separated = valid_answer("queue_backlog_replay").replace("12400", "12,400")
        self.assertTrue(self._verdict("queue_backlog_replay", separated).semantic_pass)
        fullwidth = valid_answer("region_failover").replace(
            "decision=FAILOVER", "decision：FAILOVER"
        ).replace("evidence=", "evidence：")
        self.assertTrue(self._verdict("region_failover", fullwidth).semantic_pass)

    def test_a_fact_absent_from_the_norm_text_stays_absent(self) -> None:
        """Deleting a fact must not be repaired by any normalisation rule."""
        answer = valid_answer("queue_backlog_replay").replace(
            "pending_records=12400", "pending_records=0"
        )
        verdict = self._verdict("queue_backlog_replay", answer)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("pending records 12400", verdict.semantic["missing_facts"])

    def test_version_literal_inside_a_longer_token_is_not_a_match(self) -> None:
        answer = valid_answer("schema_migration").replace("schema-42", "schema-42x")
        verdict = self._verdict("schema_migration", answer)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("schema-42", verdict.semantic["missing_facts"])

    def test_multiline_answer_fails_both_gates(self) -> None:
        answer = valid_answer("queue_backlog_replay") + "\nsecond line"
        verdict = self._verdict("queue_backlog_replay", answer)
        self.assertFalse(verdict.strict_pass)
        self.assertFalse(verdict.semantic_pass)
        self.assertIn("answer_multiline", verdict.semantic["issues"])


class CrewAIHandoffJudgeTest(unittest.TestCase):
    """The handoff gate keeps fact and format separation, with no decision leak."""

    def _rule(self, task_id: str):
        return judge.handle_rule_for(TASKS.values(), task_id)

    def test_fact_complete_format_variant_is_canonicalised(self) -> None:
        verdict = judge.judge_handoff(
            self._rule("schema_migration"),
            "HANDOFF: schema-42 contract failed 0, drift 0",
        )
        self.assertTrue(verdict.semantic_pass, verdict.semantic["issues"])
        self.assertFalse(verdict.strict_pass)
        self.assertTrue(verdict.strict["marker_variant"])
        self.assertEqual(
            "HANDOFF schema-42 contract failed 0, drift 0",
            verdict.semantic["canonical"],
        )

    def test_missing_handoff_fact_requires_recovery(self) -> None:
        verdict = judge.judge_handoff(self._rule("queue_backlog_replay"), "HANDOFF PIPE-3391")
        self.assertFalse(verdict.semantic_pass)
        self.assertTrue(verdict.semantic["needs_recovery"])
        self.assertIsNone(verdict.semantic["canonical"])
        self.assertIn("12400", verdict.semantic["missing_facts"])

    def test_decision_leak_in_handoff_is_flagged(self) -> None:
        verdict = judge.judge_handoff(
            self._rule("region_failover"),
            "HANDOFF apollo-cache-9 ap-south-1 45% FAILOVER",
        )
        self.assertTrue(verdict.semantic["decision_leak"])
        self.assertIn("handoff_decision_leak", verdict.semantic["issues"])

    def test_short_decision_label_does_not_trigger_false_leak(self) -> None:
        verdict = judge.judge_handoff(
            self._rule("schema_migration"),
            "HANDOFF schema-42 contract failed 0, drift 0",
        )
        self.assertFalse(verdict.semantic["decision_leak"])


class CrewAISemanticFreezeTest(unittest.TestCase):
    """The judge is frozen before the paid batch; hashes must round-trip."""

    def test_every_answer_fact_has_tool_evidence_in_the_frozen_task_file(self) -> None:
        for task_id, task in TASKS.items():
            with self.subTest(task_id=task_id):
                evidence = judge.tool_evidence(task)[-1]
                rule = judge.answer_rule_for(TASKS.values(), task_id)
                verdict = judge.judge_answer(
                    rule,
                    f"RESULT task={task_id} decision={task['decision']} evidence={evidence}",
                )
                self.assertTrue(verdict.semantic_pass, verdict.semantic["issues"])
                self.assertTrue(verdict.strict_pass, verdict.strict["issues"])

    def test_task_file_and_judge_hash_match_the_freeze(self) -> None:
        import hashlib

        freeze = json.loads(
            (ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R7_MULTITASK_01.json")
            .read_text(encoding="utf-8")
        )
        for source in (
            "tasks/stage5_autogen/natural_tasks_r7.json",
            "experiments/runners/crewai_semantic_equivalence_v7.py",
        ):
            with self.subTest(source=source):
                digest = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
                self.assertEqual(digest, freeze["source_sha256"][source])


if __name__ == "__main__":
    unittest.main()
