"""Zero-API gates for the wording-effect analysis.

Pins the analysis's mechanical claims so they cannot drift: the paired median difference is
zero with a split sign pattern, the over-length answers are only in the plugin arm, no answer
token is hidden by the reduction, the pointer text is 157 characters, and the plugin arm is
the only arm whose answers vary across repeats.

The analysis is descriptive and changes nothing: the frozen verdicts are asserted as they
stand, so a later edit that quietly softened them would fail here.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from experiments.runners import openai_agents_wording_effect_analysis as analysis

REPO = Path(__file__).resolve().parents[1]
JSON_PATH = REPO / "integrations/openai_agents/WORDING_EFFECT_ANALYSIS_20261005.json"


class WordingEffectAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = analysis.write()
        cls.verdict = cls.report["verdict"]

    def test_the_analysis_is_descriptive_and_covers_the_three_batches(self):
        self.assertEqual("openai_agents_wording_effect_analysis", self.report["schema"])
        batches = {sample["batch"] for sample in self.report["samples"]}
        self.assertEqual({"v12", "v13", "v14"}, batches)
        self.assertEqual(13, self.verdict["sign_pattern"]["positive"] + self.verdict["sign_pattern"]["negative"] + self.verdict["sign_pattern"]["zero"])

    def test_there_is_no_consistent_lengthening_direction(self):
        signs = self.verdict["sign_pattern"]
        self.assertEqual(signs["positive"], signs["negative"])
        self.assertEqual(0, self.verdict["median_delta"])
        self.assertGreater(self.verdict["length_variance"]["pruner_v1"]["stdev"], self.verdict["length_variance"]["none"]["stdev"])

    def test_the_over_length_failures_are_only_in_the_plugin_arm(self):
        self.assertTrue(self.verdict["over_length_only_in_plugin_arm"])
        self.assertEqual(3, self.report["over_length"]["total_failing_samples"])
        for entry in self.report["samples"]:
            if not entry["within_160"]:
                self.assertEqual("pruner_v1", entry["method"])

    def test_no_answer_token_is_hidden_by_the_reduction(self):
        self.assertFalse(self.verdict["hidden_evidence_supported"])
        self.assertEqual(13, self.verdict["hidden_evidence_checks"])
        self.assertEqual(0, self.verdict["hidden_evidence_failures"])
        for check in self.report["token_checks"]:
            self.assertEqual([], check["tokens_missing_from_plugin_only"], check["task"])
            self.assertEqual(
                check["tokens_in_baseline_text"], check["tokens_in_plugin_text"], check["task"]
            )

    def test_the_pointer_text_length_and_the_omission_amounts_are_reported(self):
        self.assertEqual(157, self.verdict["pointer_text_chars"])
        omitted = {entry["omitted_original_chars"] for entry in self.verdict["omitted_chars_by_sample"]}
        self.assertGreaterEqual(len(omitted), 3)

    def test_the_baseline_arms_are_stable_and_the_plugin_arm_is_not(self):
        for batch, groups in self.verdict["determinism"].items():
            for key, value in groups.items():
                if key.endswith("|none"):
                    self.assertTrue(value["deterministic"], f"{batch}:{key}")
                if key.endswith("|pruner_v1") and batch == "v14":
                    self.assertFalse(value["deterministic"], f"{batch}:{key}")

    def test_the_frozen_verdicts_are_unchanged_by_this_analysis(self):
        pilot = json.loads(
            (
                REPO
                / "runs/stage5-openai-agents-api/openai-repo-diagnostic-v14-multitask-confirm-01/audit.json"
            ).read_text(encoding="utf-8")
        )
        self.assertFalse(pilot["acceptance"]["met"])
        self.assertFalse(pilot["acceptance"]["track_A_all_tasks"])
        v13 = json.loads(
            (
                REPO
                / "runs/stage5-openai-agents-api/openai-repo-diagnostic-v13-requests1766-confirm-01/audit.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(v13["acceptance"]["met"])
        for name in ("v12", "v13", "v14"):
            path = JSON_PATH
            self.assertTrue(path.is_file())
        self.assertTrue(self.report["limits"])


if __name__ == "__main__":
    unittest.main()
