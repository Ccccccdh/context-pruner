"""Zero-API gate for OpenAI Agents v4 selective retention.

Two levels are exercised:

* the real SDK Runner through the v4 runner's own ``main()`` with a local stub
  provider (the full Django grid: 3 repeats x 3 arms, no network);
* the retention filter directly on the real public task payload, where the
  invariants (constraints present, tool groups unchanged, real reduction,
  counted fallback) can be checked byte-exactly.

Asserted here:
(a) the Django literal constraints are present in the final model input;
(b) protected Responses tool-group hashes are unchanged;
(c) ``task_anchor_restore_failures`` is zero on the happy path and is counted,
    with a whole-payload fallback, when a protected unit is forced to go missing
    - both at the filter level and end-to-end through the SDK boundary;
(d) at least one arm produces a real measured input reduction;
(e) the full Django grid runs without any network call.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_repo_diagnostic_v4 as v4
from experiments.runners.openai_agents_selective_retention_v4 import (
    SelectiveRetentionFilter,
    _text_sha,
    normalise_unit,
)

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / ".tooling" / "gate_openai_agents_3arm.py"
DJANGO_ANSWER = (
    "RESULT issue=django-16263 cause=existing_annotations force subquery "
    "fix=prune annotations not referenced by filters other annotations or ordering"
)


def _injected_unit_loss(retention: SelectiveRetentionFilter):
    """Return a ``_filter`` replacement that reports a reduction but loses units."""

    def damaged_filter(items):
        del items
        return [], 1, 1

    del retention
    return damaged_filter
RETENTION_FAULT_ENV = "DSH_V4_GATE_INJECT_MISSING_UNIT"
RETENTION_ROWS = (
    "task_anchor_restore_failures",
    "selective_retention_restore_failures",
    "task_restore_fallbacks",
    "restore_failures_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
)


def _load_gate_helpers():
    """The same memoised helper module the runner's offline gate instantiates."""
    return v4._gate_helpers()


def _contract_stub(helpers):
    """A stub model whose final answer satisfies the frozen Django contract."""

    class ContractStub(helpers.StubApiModel):
        def _final_message(self) -> ResponseOutputMessage:
            return ResponseOutputMessage(
                id=f"msg_{self.case.scenario}",
                content=[ResponseOutputText(annotations=[], text=DJANGO_ANSWER, type="output_text")],
                role="assistant",
                status="completed",
                type="message",
            )

    return ContractStub


def _grid_args(output: Path, experiment_id: str, fault: bool = False) -> list[str]:
    args = [
        "--offline-gate", "--confirm-send-public-source",
        "--scenarios", "django_count_annotations",
        "--methods", "none,pruner_v1,native_summary",
        "--repeats", "3",
        "--provider-soft", "2000", "--provider-target", "1500", "--provider-hard", "6000",
        "--max-output-tokens", "1024", "--max-api-requests", "100",
        "--out", str(output), "--experiment-id", experiment_id,
    ]
    if fault:
        args.append(RETENTION_FAULT_ENV)
    return args


def _registered_case(repeat: int = 0):
    case = v1.build_case("django_count_annotations", repeat)
    return v4._CaseWithTaskStatement(case, v4.task_statement(case))


class SelectiveRetentionV4Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = _load_gate_helpers()
        cls.helpers.StubApiModel = _contract_stub(cls.helpers)
        cls.temp = tempfile.mkdtemp(prefix="v4-gate-")
        status = v4.main(_grid_args(Path(cls.temp), "v4-offline-grid"))
        assert status == 0, "offline grid run failed"
        cls.batch = Path(cls.temp) / "v4-offline-grid"
        cls.rows = [
            json.loads(line)
            for line in (cls.batch / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp, ignore_errors=True)

    # -- (e) the full grid runs without network ---------------------------

    def test_grid_runs_without_network(self):
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
        manifest = json.loads((self.batch / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("public_source_diagnostic", manifest["data_class"])
        self.assertNotEqual("synthetic_only", manifest["data_class"])
        self.assertIn("public swe-bench", manifest["disclosure"].lower())
        self.assertTrue(manifest["shared_runner_disclosure_amended"])
        self.assertIn("task_anchor_restore_failures", manifest["persisted_retention_fields"])

    # -- (a) constraints survive in the final model input -----------------

    def test_literal_constraints_reach_the_model_in_every_plugin_sample(self):
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        for row in plugin:
            model_inputs = [r for r in self._records(row) if r["stage"] == "model_input"]
            self.assertTrue(model_inputs)
            final = model_inputs[-1]
            for label in (
                "filter_references",
                "other_annotation_references",
                "ordering_references",
            ):
                self.assertTrue(
                    final["constraint_present"][label],
                    f"{label} missing from the final model input of r{row['repeat']}",
                )
                self.assertTrue(
                    final["constraint_present_in_unprotected_messages"][label],
                    f"{label} missing from the unprotected messages of r{row['repeat']}",
                )

    def test_protected_task_units_are_present_verbatim_after_filtering(self):
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        items = [dict(message) for message in case.history]
        items.extend([
            {"type": "function_call", "name": "read_django_count_entry", "arguments": "{}", "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": v1.source_view(case.scenario, 0)},
        ])
        retention.observe(items)
        filtered = retention.filter_items(items)
        self.assertIsNotNone(filtered)
        self.assertEqual([], retention._missing_protected_units(filtered))
        present = {
            _text_sha(normalise_unit(unit))
            for item in filtered
            for unit in (item.get("content") or "").splitlines()
            if unit.strip()
        }
        for digest in retention.task_unit_sha256:
            self.assertIn(digest, present)

    # -- (b) protected tool groups are unchanged --------------------------

    def test_protected_tool_group_hashes_are_unchanged(self):
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        items = [dict(message) for message in case.history]
        items.extend([
            {"type": "function_call", "name": "read_django_count_entry", "arguments": "{}", "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": v1.source_view(case.scenario, 0)},
        ])
        before = retention._group_hashes(items)
        self.assertTrue(before)
        retention.observe(items)
        filtered = retention.filter_items(items)
        self.assertIsNotNone(filtered)
        after = retention._group_hashes(filtered)
        self.assertEqual(before, after, "protected Responses tool groups changed")
        self.assertEqual(0, retention.group_hash_mismatch_count)
        self.assertEqual(
            sorted(before),
            sorted(retention._group_hash_baseline),
            "a protected group disappeared from the run's baseline ledger",
        )
        # The compaction note is the only field allowed to change, and it keeps
        # the call id so the call/output pairing is intact.
        notes = [
            item for item in filtered
            if isinstance(item, dict) and str(item.get("output", "")).startswith("[selective-retention v4]")
        ]
        self.assertEqual(1, len(notes))
        self.assertEqual("c1", notes[0]["call_id"])

    def test_evidence_layer_shows_unchanged_boundaries(self):
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        for row in plugin:
            records = self._records(row)
            before = [r for r in records if r["stage"] == "filter_before"]
            after = [r for r in records if r["stage"] == "filter_after"]
            self.assertEqual(len(before), len(after))
            self.assertTrue(before, "the plugin arm installed no filter")
            for previous, following in zip(before, after):
                self.assertEqual(
                    [group["group_id"] for group in previous["protected_groups"]],
                    [group["group_id"] for group in following["protected_groups"]],
                    "tool group boundaries moved",
                )
                self.assertEqual(
                    [group["tool_output_count"] for group in previous["protected_groups"]],
                    [group["tool_output_count"] for group in following["protected_groups"]],
                    "a protected tool call/output pair was split",
                )
                # The only sanctioned change to a tool group is an already-served
                # output text becoming this module's compaction note; an output
                # that changes without a note would be an undocumented rewrite.
                notes_added = (
                    following["compaction_note_count"] - previous["compaction_note_count"]
                )
                if notes_added == 0:
                    self.assertEqual(
                        [group["tool_output_sha256"] for group in previous["protected_groups"]],
                        [group["tool_output_sha256"] for group in following["protected_groups"]],
                        "tool output changed without an auditable compaction note",
                    )
                else:
                    self.assertGreater(notes_added, 0)
                    self.assertLess(
                        following["input_bytes"], previous["input_bytes"],
                        "a compaction must shrink the payload",
                    )
            model_hashes = {r["input_sha256"] for r in records if r["stage"] == "model_input"}
            for record in after:
                self.assertIn(record["input_sha256"], model_hashes)
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertTrue(
            any(
                record["compaction_note_count"]
                for row in plugin
                for record in self._records(row)
                if record["stage"] == "model_input"
            ),
            "no compaction note reached any model input",
        )

    # -- (c) restore-failure counting and fallback ------------------------

    def test_restore_failures_are_persisted_and_zero_on_the_happy_path(self):
        for row in self.rows:
            for field in RETENTION_ROWS:
                self.assertIn(field, row)
            self.assertTrue(row["restore_failure_is_whole_prefix_fallback"])
            self.assertEqual(0, row["task_anchor_restore_failures"])
            self.assertEqual(0, row["task_restore_fallbacks"])
            self.assertEqual({}, row["selective_retention_fallback_reasons"])
            self.assertEqual(
                row["task_anchor_restore_failures"],
                row["input_evidence_task_anchor_restore_failures"],
            )
            if row["method"] == "none":
                # The baseline installs no filter; its retention ledger is all zero.
                self.assertEqual([0] * row["model_calls"], row["restore_failures_by_model_call"])
            else:
                self.assertEqual([0] * row["model_calls"], row["restore_failures_by_model_call"])

    def test_injected_missing_protected_unit_is_counted_and_falls_back(self):
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        retention._filter = _injected_unit_loss(retention)
        items = [dict(message) for message in case.history]
        self.assertIsNone(retention.filter_items(items))
        self.assertEqual(1, retention.task_anchor_restore_failures)
        self.assertEqual(1, retention.task_restore_fallbacks)
        self.assertEqual("protected_item_dropped:item_count", retention.last_fallback_reason)
        self.assertEqual(0, retention.compacted_calls)
        # The hard guarantee: no protected unit can go missing without a whole
        # payload fallback being requested and counted.
        self.assertIn(next(iter(retention.failure_reasons)), retention.metrics_dict()["fallback_reasons"])

    def test_audit_detects_restore_failure_and_quality_tampering(self):
        from experiments.audits import audit_openai_agents_repo_diagnostic_v4 as independent

        freeze_path = ROOT / "integrations/openai_agents/REPO_DIAGNOSTIC_V4_SELECTIVE_RETENTION_FREEZE.json"
        rows = [json.loads(json.dumps(row)) for row in self.rows]
        baseline = independent.audit(self.batch, freeze_path, "v4-offline-grid")
        self.assertEqual([], baseline["issues"], baseline["issues"])
        self.assertTrue(baseline["complete"])

        original = (self.batch / "samples.jsonl").read_text(encoding="utf-8")

        def write(rows_to_write):
            (self.batch / "samples.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows_to_write) + "\n", encoding="utf-8"
            )

        try:
            tampered = [json.loads(json.dumps(row)) for row in rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["task_anchor_restore_failures"] = 1
                    break
            write(tampered)
            issues = independent.audit(self.batch, freeze_path, "v4-offline-grid")["issues"]
            self.assertTrue(
                any("restore-failure" in issue for issue in issues),
                issues,
            )
            tampered = [json.loads(json.dumps(row)) for row in rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["success"] = not row["success"]
                    break
            write(tampered)
            issues = independent.audit(self.batch, freeze_path, "v4-offline-grid")["issues"]
            self.assertTrue(any("quality mismatch" in issue for issue in issues), issues)
        finally:
            (self.batch / "samples.jsonl").write_text(original, encoding="utf-8")

    def test_injected_failure_through_the_real_sdk_runner_is_counted(self):
        """The end-to-end path: a forced unit loss must appear in the result row."""
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_openai_agents_selective_retention_v4.SelectiveRetentionFaultRun", "-v"],
            cwd=str(ROOT), env={**os.environ, RETENTION_FAULT_ENV: "1"},
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = [
            line for line in result.stdout.splitlines() if line.strip().startswith("{")
        ]
        self.assertTrue(payload, result.stdout)
        summary = json.loads(payload[-1])
        self.assertGreaterEqual(summary["task_anchor_restore_failures"], 1)
        self.assertEqual(0, summary["compacted_calls"])

    # -- (d) real measured input reduction --------------------------------

    def test_at_least_one_arm_reduces_the_actual_input(self):
        baseline = {
            row["repeat"]: row["actual_input_tokens"]
            for row in self.rows if row["method"] == "none"
        }
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        reductions = [
            (baseline[row["repeat"]] - row["actual_input_tokens"]) / baseline[row["repeat"]]
            for row in plugin
        ]
        self.assertTrue(all(value > 0.05 for value in reductions), reductions)
        for row in plugin:
            self.assertGreater(row["selective_retention_compacted_tool_outputs"], 0)
            self.assertGreater(row["selective_retention_saved_bytes_total"], 0)
            self.assertGreater(row["selective_retention_task_unit_count"], 0)
            self.assertGreater(row["selective_retention_constraint_unit_count"], 0)
        report = json.loads((self.batch / "report.json").read_text(encoding="utf-8"))
        comparison = report["comparisons"]["pruner_v1_vs_none"]
        self.assertGreater(comparison["actual_input_savings_rate_vs_baseline"], 0)
        self.assertEqual(0.0, comparison["success_delta"])

    # -- helpers ----------------------------------------------------------

    def _records(self, row: dict) -> list[dict]:
        path = self.batch / row["input_evidence_file"]
        self.assertTrue(path.is_file(), f"evidence file missing for {row['scenario']} {row['method']}")
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]


class SelectiveRetentionFaultRun(unittest.TestCase):
    """Child-process gate: the same grid with a forced protected-unit loss.

    The fault is the smallest possible one - the filter is asked to drop a
    protected unit - so the run must (i) count every failure, (ii) fall back for
    the whole payload of that call, and (iii) never claim a compaction.
    """

    def test_injected_loss_is_reported_in_the_result_row(self):
        if os.getenv(RETENTION_FAULT_ENV) != "1":  # pragma: no cover - parent gate
            self.skipTest("only runs as the injected child of the v4 gate")
        helpers = _load_gate_helpers()
        helpers.StubApiModel = _contract_stub(helpers)
        temp = tempfile.mkdtemp(prefix="v4-fault-")
        try:
            original_filter = SelectiveRetentionFilter._filter

            def damaged_filter(self, items):
                del items
                # Same fault as the unit-level gate: a reported reduction whose
                # payload has lost the protected units.
                return [], 1, 1

            SelectiveRetentionFilter._filter = damaged_filter
            del original_filter
            status = v4.main(_grid_args(Path(temp), "v4-offline-fault"))
            self.assertEqual(0, status)
            rows = [
                json.loads(line)
                for line in (Path(temp) / "v4-offline-fault" / "samples.jsonl")
                .read_text(encoding="utf-8").splitlines()
                if line
            ]
            plugin = [row for row in rows if row["method"] == "pruner_v1"]
            self.assertEqual(3, len(plugin))
            for row in plugin:
                self.assertGreaterEqual(row["task_anchor_restore_failures"], 1)
                self.assertEqual(
                    row["task_anchor_restore_failures"],
                    row["input_evidence_task_anchor_restore_failures"],
                )
                self.assertTrue(row["restore_failure_is_whole_prefix_fallback"])
                self.assertEqual(0, row["selective_retention_compacted_tool_outputs"])
                self.assertEqual(0, row["selective_retention_saved_bytes_total"])
            summary = {
                "task_anchor_restore_failures": sum(
                    row["task_anchor_restore_failures"] for row in plugin
                ),
                "compacted_calls": sum(
                    row["selective_retention_compacted_calls"] for row in plugin
                ),
            }
            print(json.dumps(summary))
        finally:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
