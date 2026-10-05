"""Zero-API gate for OpenAI Agents v7 applicability boundary.

What this gate has to establish, without any provider request:

* the v7 policy is a **recency restriction on the already-audited v6 reduction**, so
  the frozen Django grid must produce **no compression at all**: the plugin arm's
  payload is byte-identical to the baseline arm's at every shared model boundary, the
  elided-source counter is 0, and the projectable saving is exactly 0;
* the newest turn is never touched - no output in it may be replaced - and the guard
  and restore ledgers stay at zero on the happy path;
* the policy did **not** disable the reduction itself: on a longer payload (five tool
  turns, which the frozen grid does not produce) the older turns are reduced, the
  newest turn is untouched, pointer coverage and the line-interval partition hold, and
  every registered literal line is retained;
* the boundary proposition is falsifiable and behaves as stated:
  ``plugin_wins_possible`` is False exactly when the baseline finishes within the
  frozen recency window;
* the acceptance line is evaluated, and the batch is only ever paid for when the
  offline projection is positive - which on this task it is not.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.runners import openai_agents_literal_registry_v6 as registry
from experiments.runners import openai_agents_recency_boundary_v7 as policy
from experiments.runners import openai_agents_span_retention_v6 as v6
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_repo_diagnostic_v5 as v5
from experiments.runners import run_openai_agents_repo_diagnostic_v7 as v7
from experiments.runners.openai_agents_recency_boundary_v7 import (
    RECENT_TURNS_KEPT,
    RecencyBoundaryFilter,
    boundary_proposition,
    elidable_indices,
    protected_turn_indices,
    turn_count,
)

ROOT = Path(__file__).resolve().parents[1]
BATCH_ID = "v7-offline-boundary"
PAID_BATCH_ID = "openai-repo-diagnostic-v7-applicability-boundary"
REQUIRED_LITERAL_LABELS = (
    "filter_references",
    "other_annotation_references",
    "ordering_references",
    "aggregation_decision",
)
BOUNDARY_ROWS = (
    "recency_boundary_recent_turns_kept_verbatim",
    "recency_boundary_elidable_indices",
    "recency_boundary_protected_turn_items",
    "recency_boundary_elided_sources",
    "recency_boundary_baseline_payload_sha256_by_call",
    "recency_boundary_plugin_payload_sha256_by_call",
    "recency_boundary_payload_identical_to_baseline",
    "recency_boundary_shared_boundary_count",
    "recency_boundary_boundary_proposition",
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
)


def _grid_args(output: Path, experiment_id: str) -> list[str]:
    return [
        "--offline-gate", "--confirm-send-public-source",
        "--scenarios", "django_count_annotations",
        "--methods", "none,pruner_v1,native_summary",
        "--repeats", "3",
        "--provider-soft", "2000", "--provider-target", "1500", "--provider-hard", "6000",
        "--max-output-tokens", "1024", "--max-api-requests", "100",
        "--out", str(output), "--experiment-id", experiment_id,
    ]


def _registered_case(repeat: int = 0):
    case = v1.build_case("django_count_annotations", repeat)
    return v5._CaseWithTaskStatement(case, v7.task_statement(case))


def _payload_with_turns(case, turns: int) -> list[dict]:
    """The frozen history plus ``turns`` tool turns, separated by assistant messages.

    The real SDK trajectory keeps every tool call in *one* accumulated group (which is
    why the frozen grid reports a single turn at every boundary); to exercise the
    recency policy's "older turns" path a payload must therefore contain several
    separated runs of tool items, which is what the assistant messages here produce.
    """
    items: list[dict] = [dict(message) for message in case.history]
    names = list(case.expected_tool_names)
    for index in range(turns):
        name = names[index % len(names)]
        call_id = f"call_{case.scenario}_{index}_{name}"
        items.append(
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps({"codename": case.codename}),
                "call_id": call_id,
            }
        )
        items.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": v1.source_view(case.scenario, index % 3),
            }
        )
        if index + 1 < turns:
            items.append({"role": "assistant", "content": f"turn {index} observed."})
    return items


class ApplicabilityBoundaryV7Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.mkdtemp(prefix="v7-gate-")
        saved = {
            name: os.environ.pop(name, None)
            for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")
        }
        try:
            status = v7.main(_grid_args(Path(cls.temp), BATCH_ID))
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value
        assert status == 0, "offline grid run failed"
        cls.batch = Path(cls.temp) / BATCH_ID
        cls.rows = [
            json.loads(line)
            for line in (cls.batch / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp, ignore_errors=True)

    # -- the policy is a restriction, not a new mechanism ------------------

    def test_policy_constants_are_frozen_and_reported(self):
        self.assertEqual(1, RECENT_TURNS_KEPT)
        self.assertEqual(RECENT_TURNS_KEPT, policy.policy_dict()["recent_turns_kept_verbatim"])
        self.assertEqual(
            "current_call_index - turn_index >= recent_turns_kept_verbatim",
            policy.policy_dict()["elidable_rule"],
        )
        # The reduction itself is v6's, unchanged.
        self.assertTrue(issubclass(RecencyBoundaryFilter, v6.SpanRetentionFilter))
        self.assertIs(RecencyBoundaryFilter._elide, v6.SpanRetentionFilter._elide)
        self.assertIs(
            RecencyBoundaryFilter._structural_violation,
            v6.SpanRetentionFilter._structural_violation,
        )
        self.assertIs(
            RecencyBoundaryFilter._carrier_loss, v6.SpanRetentionFilter._carrier_loss
        )
        manifest = json.loads((self.batch / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("recency_boundary_v7", manifest["mechanism"]["name"])
        self.assertEqual(
            RECENT_TURNS_KEPT, int(manifest["mechanism"]["recent_turns_kept_verbatim"])
        )
        self.assertEqual("public_source_diagnostic", manifest["data_class"])
        self.assertEqual([], manifest["literal_registry"]["problems"])

    def test_turn_accounting_is_exact(self):
        case = _registered_case()
        one = _payload_with_turns(case, 1)
        self.assertEqual(1, turn_count(one))
        self.assertEqual(2, len(protected_turn_indices(one)))
        self.assertEqual(set(), elidable_indices(one))
        five = _payload_with_turns(case, 5)
        self.assertEqual(5, turn_count(five))
        protected = protected_turn_indices(five)
        elidable = elidable_indices(five)
        # The newest turn holds the last call/output pair; the other four turns are
        # older than it and therefore elidable.
        self.assertEqual(2, len(protected))
        self.assertEqual(8, len(elidable))
        self.assertEqual(set(), protected & elidable)
        tool_indices = {index for index, value in policy.turn_indices(five).items() if value >= 0}
        self.assertEqual(tool_indices, protected | elidable)
        message_indices = set(range(len(five))) - tool_indices
        self.assertEqual(len(five) - 2, len(elidable) + len(message_indices))
        self.assertEqual({14, 15}, protected)
        self.assertEqual({2, 3, 5, 6, 8, 9, 11, 12}, elidable)

    # -- the offline grid: no compression, identical payload ---------------

    def test_grid_runs_without_network_and_reports_no_compression(self):
        keys = [(row["scenario"], row["repeat"], row["method"]) for row in self.rows]
        expected = [
            ("django_count_annotations", repeat, method)
            for repeat in range(3)
            for method in ("none", "pruner_v1", "native_summary")
        ]
        self.assertEqual(sorted(expected), sorted(keys))
        self.assertEqual(9, len(self.rows))
        for row in self.rows:
            self.assertEqual("", row["error_type"], row["error_message"])
            self.assertTrue(row["success"], (row["method"], row["repeat"]))
            for field in BOUNDARY_ROWS:
                self.assertIn(field, row)
            self.assertEqual(
                RECENT_TURNS_KEPT, row["recency_boundary_recent_turns_kept_verbatim"]
            )
        baseline = {row["repeat"]: row for row in self.rows if row["method"] == "none"}
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        for row in plugin:
            reference = baseline[row["repeat"]]
            self.assertEqual(
                reference["actual_input_tokens"],
                row["actual_input_tokens"],
                f"the plugin changed the payload on repeat {row['repeat']}",
            )
            self.assertEqual(0, row["recency_boundary_elided_sources"])
            self.assertEqual(0, row["pointer_retention_elided_sources"])
            self.assertEqual(0, row["selective_retention_elided_outputs"])
            self.assertTrue(row["recency_boundary_payload_identical_to_baseline"])
            self.assertEqual(
                reference["recency_boundary_payload_sha256_by_call"],
                row["recency_boundary_plugin_payload_sha256_by_call"],
                "a payload hash differs from the baseline arm",
            )
            self.assertGreaterEqual(row["recency_boundary_protected_outputs"], 1)
            self.assertEqual(
                0.0, float(row["recency_boundary_projectable_saving_rate"] or 0.0)
            )
        report = json.loads((self.batch / "report.json").read_text(encoding="utf-8"))
        comparison = report["comparisons"]["pruner_v1_vs_none"]
        self.assertEqual(0.0, comparison["actual_input_savings_rate_vs_baseline"])
        self.assertEqual(0.0, comparison["all_arm_total_token_savings_rate_vs_baseline"])
        self.assertEqual(0.0, comparison["success_delta"])

    def test_the_newest_turn_is_never_touched(self):
        for row in self.rows:
            if row["method"] != "pruner_v1":
                continue
            for record in self._records(row):
                if record["stage"] != "model_input":
                    continue
                manifest = record["output_manifest"]
                if not manifest:
                    continue
                # The last output in payload order belongs to the newest turn.
                newest = manifest[-1]
                self.assertFalse(
                    newest["output_elided"],
                    f"the newest turn's output was replaced ({row['repeat']})",
                )
                self.assertFalse(newest["note_has_source_pointer"])
                for entry in manifest:
                    self.assertFalse(entry["output_elided"])

    def test_is_the_newest_turn_the_only_protected_one(self):
        """On the real trajectory the SDK keeps every tool call in one group.

        The frozen grid therefore reports an empty elidable set at every boundary, and
        the protected item count equals the number of tool outputs on the boundary -
        which is the structural reason the plugin cannot compress anything here.
        """
        for row in self.rows:
            if row["method"] != "pruner_v1":
                continue
            for record in self._records(row):
                if record["stage"] != "filter_before":
                    continue
                self.assertEqual([], list(record["recency_elidable_indices"]))
                self.assertEqual(0, int(record["recency_elidable_turn_items"]))
                if int(record["recency_output_items"]):
                    self.assertGreaterEqual(int(record["recency_turn_count"]), 1)
                    self.assertGreaterEqual(
                        int(record["recency_protected_turn_items"]),
                        int(record["recency_output_items"]),
                        "on this trajectory the newest turn holds every tool item",
                    )

    def test_guards_and_restore_ledgers_are_zero(self):
        for row in self.rows:
            self.assertEqual(0, row["task_anchor_restore_failures"], row["method"])
            self.assertEqual(0, row["task_restore_fallbacks"])
            self.assertEqual(0, row["budget_fallbacks"])
            self.assertTrue(row["restore_failure_is_whole_prefix_fallback"])
            self.assertEqual(
                [0] * row["model_calls"], row["restore_failures_by_model_call"]
            )
            if row["method"] == "pruner_v1":
                self.assertEqual(0, row["selective_retention_literal_guard_fallbacks"])
                self.assertEqual({}, row["selective_retention_fallback_reasons"])

    def test_every_registered_literal_is_present_at_every_boundary(self):
        for row in self.rows:
            for record in self._records(row):
                if record["stage"] != "model_input":
                    continue
                for label in REQUIRED_LITERAL_LABELS:
                    if label == "aggregation_decision":
                        carries = any(
                            entry.get("registered_literals")
                            for entry in record["output_manifest"]
                        )
                        if not carries:
                            continue
                    self.assertTrue(
                        record["registered_literals_present"][label],
                        f"{label} missing for {row['method']} r{row['repeat']}",
                    )

    # -- the reduction is not disabled, only restricted --------------------

    def test_older_turns_are_still_reduced_on_a_longer_payload(self):
        case = _registered_case()
        retention = RecencyBoundaryFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        sent = _payload_with_turns(case, 5)
        retention.observe(sent)
        retention.observe_delivered(sent)
        filtered = retention.filter_items(sent)
        self.assertIsNotNone(filtered, retention.last_fallback_reason)
        self.assertEqual("", retention.last_fallback_reason)
        self.assertGreater(retention.elided_source_spans, 0)
        self.assertGreater(retention.kept_literal_lines, 0)
        self.assertEqual(0, retention.task_anchor_restore_failures)
        self.assertEqual(0, retention.budget_fallbacks)
        self.assertEqual(0, retention.group_hash_mismatch_count)
        # The newest turn's items are byte-identical; an older turn's output is not.
        records = {
            str(policy.jsonable(item).get("call_id")): policy.jsonable(item)
            for item in filtered
            if isinstance(policy.jsonable(item), dict)
            and str(policy.jsonable(item).get("type") or "").endswith("_output")
        }
        newest_call = str(policy.jsonable(sent[-1]).get("call_id"))
        self.assertEqual(
            str(policy.jsonable(sent[-1]).get("output")), records[newest_call].get("output")
        )
        oldest_call = str(policy.jsonable(sent[2]).get("call_id"))
        self.assertNotEqual(
            str(policy.jsonable(sent[2]).get("output")), records[oldest_call].get("output")
        )
        self.assertIn(v6.NOTE_PREFIX, str(records[oldest_call].get("output")))
        for entry in retention.last_call_elisions:
            first, last = entry["source_range"]
            expected = list(range(int(first), int(last) + 1))
            kept = sorted(int(value) for value in entry["kept_lines"])
            omitted: list[int] = []
            for interval in entry["omitted_intervals"]:
                omitted.extend(range(int(interval[0]), int(interval[1]) + 1))
            self.assertEqual(expected, sorted(kept + omitted))
            self.assertTrue(entry["pointer_completeness"])
            self.assertTrue(entry["pointer_matches_registered_source"])
            registered = set(
                registry.span_for_tool(case.scenario, entry["tool"]).literal_lines
            )
            self.assertFalse(registered & set(omitted))
        self.assertEqual([], retention._carrier_loss(sent, filtered))

    def test_recency_window_larger_than_the_baseline_disables_the_reduction(self):
        """The boundary control: raising N cannot help, it can only reduce options."""
        case = _registered_case()
        sent = _payload_with_turns(case, 5)
        self.assertGreater(len(elidable_indices(sent, keep=1)), 0)
        for keep in (2, 3, 4):
            self.assertLessEqual(
                len(elidable_indices(sent, keep=keep)),
                len(elidable_indices(sent, keep=1)),
                f"a larger recency window made more turns elidable at keep={keep}",
            )
        self.assertEqual(set(), elidable_indices(sent, keep=5))
        self.assertEqual(set(), elidable_indices(sent, keep=6))

    # -- the boundary proposition ------------------------------------------

    def test_boundary_proposition_is_falsifiable(self):
        for calls in (1, 2, 3, 4, 6):
            proposition = boundary_proposition(calls, 2874)
            expected_wins = (calls - 1 - RECENT_TURNS_KEPT) > 0
            self.assertEqual(
                expected_wins,
                proposition["plugin_wins_possible"],
                f"calls={calls} should give plugin_wins_possible={expected_wins}",
            )
            self.assertEqual(
                max(0, calls - 1 - RECENT_TURNS_KEPT),
                proposition["elidable_turns_available_to_the_plugin"],
                f"calls={calls}",
            )
            self.assertEqual(
                expected_wins, proposition["plugin_can_elide_anything"], f"calls={calls}"
            )
        short = boundary_proposition(2, 2874)
        self.assertFalse(short["plugin_can_elide_anything"])
        arithmetic = short["short_regime_arithmetic"]
        self.assertEqual(2335.8, arithmetic["v5_plugin_extra_round_input_tokens"])
        self.assertEqual(0.9985, arithmetic["v5_extra_round_share_of_input_regression"])
        self.assertEqual(-136.7, arithmetic["v5_pinned_text_effect_input_tokens"])
        self.assertLess(
            arithmetic["v5_pinned_text_effect_input_tokens"],
            0,
            "the pinned-text term is a benefit (negative cost), not a cost",
        )
        self.assertGreater(
            arithmetic["v5_plugin_extra_round_input_tokens"],
            0.75 * arithmetic["baseline_complete_total_tokens"],
            "the extra round must dominate the baseline for the claim to hold",
        )
        self.assertLess(
            abs(arithmetic["v5_pinned_text_effect_input_tokens"]),
            arithmetic["v5_plugin_extra_round_input_tokens"] / 10,
            "the pinned-text term must be an order of magnitude smaller than the round",
        )
        # The baseline arm of the frozen Django batch is the short regime.
        baseline_calls = [
            row["model_calls"] for row in self.rows if row["method"] == "none"
        ]
        self.assertLessEqual(max(baseline_calls), 4)

    def test_no_paid_run_is_justified_by_this_offline_projection(self):
        """The rule the batch followed: no request when the projection is not positive."""
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        projectable = [
            float(row.get("recency_boundary_projectable_saving_rate") or 0.0)
            for row in plugin
        ]
        self.assertEqual([0.0, 0.0, 0.0], projectable)
        self.assertFalse(
            any(value > 0 for value in projectable),
            "the offline projection is positive; a paid run would then be required",
        )
        # A batch directory may exist (the offline grid writes one); what must not exist
        # is a *paid* run, which the runner marks with its own manifest flag and which
        # this test identifies by that flag rather than by the directory name.
        paid_dir = ROOT / "runs/stage5-openai-agents-api" / PAID_BATCH_ID
        if paid_dir.is_dir():
            manifest = json.loads((paid_dir / "manifest.json").read_text(encoding="utf-8"))
            documents = json.dumps(manifest, ensure_ascii=False).lower()
            self.assertIn(
                "offline",
                documents,
                "a v7 batch exists without any offline marker; check whether it was paid for",
            )

    # -- audit -------------------------------------------------------------

    def test_audit_passes_and_detects_tampering(self):
        from experiments.audits import audit_openai_agents_repo_diagnostic_v7 as independent

        freeze_path = (
            ROOT
            / "integrations/openai_agents/REPO_DIAGNOSTIC_V7_APPLICABILITY_BOUNDARY_FREEZE.json"
        )
        result = independent.audit(self.batch, freeze_path, BATCH_ID)
        self.assertEqual([], result["issues"], result["issues"])
        self.assertTrue(result["complete"])
        self.assertEqual(0, result["plugin_elided_sources_total"])
        self.assertEqual(3, result["plugin_samples_with_payload_identical_to_baseline"])
        self.assertEqual(0, result["task_anchor_restore_failures"]["pruner_v1"])
        self.assertEqual(0, result["budget_fallbacks"]["pruner_v1"])
        self.assertIn("boundary", result)
        self.assertTrue(
            all(
                entry["plugin_wins_possible"] == (entry["baseline_model_calls"] > 2)
                for entry in result["boundary"].values()
            ),
            result["boundary"],
        )
        self.assertIn(result["verdict"], {"valid-saving", "not-yet-valid"})

        original = (self.batch / "samples.jsonl").read_text(encoding="utf-8")
        try:
            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["recency_boundary_payload_identical_to_baseline"] = False
                    break
            self._write(tampered)
            issues = independent.audit(self.batch, freeze_path, BATCH_ID)["issues"]
            self.assertTrue(
                any("not identical to the baseline" in issue for issue in issues), issues
            )

            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["recency_boundary_shared_boundary_count"] = 99
                    break
            self._write(tampered)
            issues = independent.audit(self.batch, freeze_path, BATCH_ID)["issues"]
            self.assertTrue(any("shared boundary count" in issue for issue in issues), issues)

            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["recency_boundary_recent_turns_kept_verbatim"] = 7
                    break
            self._write(tampered)
            issues = independent.audit(self.batch, freeze_path, BATCH_ID)["issues"]
            self.assertTrue(any("recency" in issue for issue in issues), issues)

            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "none":
                    row["success"] = not row["success"]
                    break
            self._write(tampered)
            issues = independent.audit(self.batch, freeze_path, BATCH_ID)["issues"]
            self.assertTrue(any("quality mismatch" in issue for issue in issues), issues)

            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["task_anchor_restore_failures"] = 1
                    break
            self._write(tampered)
            issues = independent.audit(self.batch, freeze_path, BATCH_ID)["issues"]
            self.assertTrue(any("restore-failure" in issue for issue in issues), issues)
        finally:
            (self.batch / "samples.jsonl").write_text(original, encoding="utf-8")

    def test_audit_rejects_a_batch_that_claims_compression_it_did_not_do(self):
        from experiments.audits import audit_openai_agents_repo_diagnostic_v7 as independent

        freeze_path = (
            ROOT
            / "integrations/openai_agents/REPO_DIAGNOSTIC_V7_APPLICABILITY_BOUNDARY_FREEZE.json"
        )
        original = (self.batch / "samples.jsonl").read_text(encoding="utf-8")
        try:
            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["recency_boundary_projectable_saving_rate"] = 0.5
                    break
            self._write(tampered)
            issues = independent.audit(self.batch, freeze_path, BATCH_ID)["issues"]
            self.assertTrue(
                any("projectable saving" in issue for issue in issues), issues
            )
        finally:
            (self.batch / "samples.jsonl").write_text(original, encoding="utf-8")

    # -- helpers -----------------------------------------------------------

    def _write(self, rows) -> None:
        (self.batch / "samples.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )

    def _records(self, row: dict) -> list[dict]:
        relative = row.get("input_evidence_file")
        if not relative:
            return []
        path = self.batch / relative
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]


def _load_gate_helpers():
    return v7._gate_helpers()


if __name__ == "__main__":
    unittest.main()
