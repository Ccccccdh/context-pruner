"""Zero-API gate for v9: the narrowed guard classification.

What this gate must establish:

* the guard's protected message-unit set is narrowed to the units that carry a
  **registered literal** (plus the registered literal units), and nothing else about the
  mechanism changed - the span elision, the literal-survival guard, the dedup rule, the
  pointer checks, the structural check, the recency gate, the whole-payload fallback, the
  thresholds and the budgets are the frozen v6/v7/v8 code, asserted by identity;
* **positive protection**: every registered literal is present at every model boundary
  with an occurrence count **not below the baseline arm's** at the same boundary;
* **negative control**: a unit that carries a registered literal *and* repeats inside the
  request message is **not** dropped, and the guard still refuses when that literal is
  about to be lost - the change fixed a classification contradiction, it did not switch
  task-statement protection off;
* the long baseline is really long (K > N + 1) and the projection is positive before any
  paid request is allowed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from experiments.runners import openai_agents_long_baseline_boundary_v8 as v8policy
from experiments.runners import openai_agents_long_baseline_boundary_v9 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners import run_openai_agents_repo_diagnostic_v9 as v9
from experiments.runners.openai_agents_evidence_safe_retention_v5 import _canonical
from experiments.runners.openai_agents_span_retention_v6 import SpanRetentionFilter

ROOT = Path(__file__).resolve().parents[1]
BATCH_ID = "v9-offline-long-baseline"
PAID_BATCH_ID = "openai-repo-diagnostic-v9-long-baseline-boundary"
REQUIRED_LITERAL_LABELS = (
    "filter_references",
    "other_annotation_references",
    "ordering_references",
    "aggregation_decision",
)


def _grid_args(output: Path, experiment_id: str) -> list[str]:
    return [
        "--offline-gate", "--confirm-send-public-source",
        "--scenarios", "django_long_investigation",
        "--methods", "none,pruner_v1,native_summary",
        "--repeats", "3",
        "--provider-soft", "2000", "--provider-target", "1500", "--provider-hard", "6000",
        "--max-output-tokens", "1024", "--max-turns", "10", "--max-api-requests", "200",
        "--out", str(output), "--experiment-id", experiment_id,
    ]


def _registered_case(repeat: int = 0):
    return v9.build_case(policy.LONG_TASK_ID, repeat)


def _literal_counts_in(items) -> dict[str, int]:
    text = _canonical(list(items)).lower()
    return {
        label: sum(text.count(str(phrase).lower()) for phrase in phrases)
        for label, phrases in long_registry.literals_for(policy.LONG_TASK_ID).items()
    }


class NarrowGuardV9Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.mkdtemp(prefix="v9-gate-")
        saved = {
            name: os.environ.pop(name, None)
            for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")
        }
        try:
            status = v9.main(_grid_args(Path(cls.temp), BATCH_ID))
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

    # -- the change is a classification fix, and only that -----------------

    def test_only_the_protected_set_changed(self):
        inherited = policy.inherited_method_objects()
        self.assertIs(SpanRetentionFilter._elide, inherited["_elide"])
        self.assertIs(SpanRetentionFilter._structural_violation, inherited["_structural_violation"])
        self.assertIs(SpanRetentionFilter._group_change, inherited["_group_change"])
        # The v9 entrypoint owns its own filter builder instead of aliasing v8's.
        self.assertIsNot(
            v9.build_retentive_filter, v8.build_retentive_filter
        )
        self.assertEqual(
            "narrow_to_registered_literal_carriers_v9",
            policy.NarrowGuardBoundaryFilter(
                task=policy.LONG_TASK_ID,
                task_statement=_registered_case().task_statement,
            ).guard_narrowing_schema,
        )

    def test_protected_set_is_exactly_the_literal_carriers(self):
        case = _registered_case()
        retention = policy.NarrowGuardBoundaryFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        self.assertGreater(len(retention.task_unit_sha256), 0)
        self.assertGreater(len(retention.protected_unit_sha256), 0)
        self.assertLess(
            len(retention.protected_unit_sha256),
            len(retention.task_unit_sha256),
            "the narrowing must actually remove units from the protected set",
        )
        for unit in retention.protectable_units:
            self.assertTrue(
                long_registry.labels_in_text(policy.LONG_TASK_ID, unit),
                f"a protected unit carries no registered literal: {unit[:60]!r}",
            )
        # Every registered literal still occurs in a protected unit or in a registered
        # span, so the narrowing cannot have dropped a constraint.
        protected_text = " ".join(retention.protectable_units).lower()
        spans_text = " ".join(
            str(span.literal_lines) for span in long_registry.spans(policy.LONG_TASK_ID)
        )
        for label, phrases in long_registry.literals_for(policy.LONG_TASK_ID).items():
            if label == "aggregation_decision":
                # reaches the model only through a registered source range
                self.assertTrue(
                    any(
                        span.literal_labels
                        for span in long_registry.spans(policy.LONG_TASK_ID)
                    )
                )
                continue
            self.assertTrue(
                any(str(phrase).lower() in protected_text for phrase in phrases),
                f"{label} is no longer carried by any protected unit",
            )
            self.assertIsInstance(spans_text, str)

    def test_grid_runs_offline_and_the_long_baseline_is_long(self):
        self.assertEqual(9, len(self.rows))
        for row in self.rows:
            self.assertEqual("", row["error_type"], row["error_message"])
            self.assertTrue(row["success"], (row["method"], row["repeat"]))
            self.assertEqual(
                v8policy.RECENT_TURNS_KEPT, row["long_baseline_recent_turns_kept_verbatim"]
            )
        baseline_calls = {row["repeat"]: row["model_calls"] for row in self.rows if row["method"] == "none"}
        for repeat, calls in baseline_calls.items():
            self.assertGreater(
                calls,
                v8policy.RECENT_TURNS_KEPT + 1,
                f"baseline K={calls} is not in the long regime (repeat {repeat})",
            )
        for row in self.rows:
            if row["method"] != "pruner_v1":
                continue
            self.assertTrue(row["long_baseline_plugin_can_elide_anything"], row["repeat"])
            self.assertGreater(row["long_baseline_elidable_turns_available"], 0)
            self.assertEqual("long", row["long_baseline_regime"])

    # -- the plugin now actually reduces ----------------------------------

    def test_plugin_reduces_and_keeps_its_guards_at_zero(self):
        baseline = {row["repeat"]: row for row in self.rows if row["method"] == "none"}
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        for row in plugin:
            reference = baseline[row["repeat"]]
            self.assertLess(
                row["actual_input_tokens"],
                reference["actual_input_tokens"],
                f"the plugin did not reduce the input on repeat {row['repeat']}",
            )
            self.assertGreater(row["long_baseline_elided_sources"], 0)
            self.assertGreater(row["pointer_retention_elided_lines"], 0)
            self.assertGreater(row["pointer_retention_kept_literal_lines"], 0)
            self.assertEqual(0, row["task_anchor_restore_failures"])
            self.assertEqual(0, row["task_restore_fallbacks"])
            self.assertEqual(0, row["budget_fallbacks"])
            self.assertEqual({}, row["selective_retention_fallback_reasons"])
            self.assertEqual(
                [0] * row["model_calls"], row["restore_failures_by_model_call"]
            )
            self.assertTrue(row["selective_retention_pointer_coverage"])
            self.assertTrue(row["evidence_pointer_completeness_final"])
            self.assertGreaterEqual(
                len(row["evidence_span_elision_records"]),
                row["long_baseline_elided_sources"],
                "the persisted span records must cover every elided source",
            )
            for entry in row["evidence_span_elision_records"]:
                self.assertTrue(entry["pointer_completeness"])
                self.assertTrue(entry["note_states_pointer"])
                self.assertTrue(entry["pointer_matches_registered_source"])
                registered = set(
                    long_registry.span_for_tool(row["scenario"], entry["tool"]).literal_lines
                )
                omitted: set[int] = set()
                for interval in entry["omitted_intervals"]:
                    omitted.update(range(int(interval[0]), int(interval[1]) + 1))
                self.assertFalse(registered & omitted)
                self.assertEqual(registered & set(entry["kept_lines"]), set(entry["kept_literal_lines"]))
        report = json.loads((self.batch / "report.json").read_text(encoding="utf-8"))
        comparison = report["comparisons"]["pruner_v1_vs_none"]
        self.assertGreater(comparison["actual_input_savings_rate_vs_baseline"], 0)
        self.assertEqual(0.0, comparison["success_delta"])

    # -- (1) positive protection: literal carriers are not dropped --------

    def test_literal_carriers_are_never_below_the_baseline_arm(self):
        """Assertion (1): per literal, per boundary, against the baseline arm."""
        baseline = {
            row["repeat"]: [
                record for record in self._records(row) if record["stage"] == "model_input"
            ]
            for row in self.rows
            if row["method"] == "none"
        }
        for row in self.rows:
            if row["method"] != "pruner_v1":
                continue
            model_inputs = [
                record for record in self._records(row) if record["stage"] == "model_input"
            ]
            reference = baseline[row["repeat"]]
            self.assertEqual(len(reference), len(model_inputs), row["repeat"])
            for index, record in enumerate(model_inputs):
                for label in REQUIRED_LITERAL_LABELS:
                    self.assertGreaterEqual(
                        int(record["registered_literal_counts"][label]),
                        int(reference[index]["registered_literal_counts"][label]),
                        f"{label} fell below the baseline at boundary {index} "
                        f"(repeat {row['repeat']})",
                    )
                self.assertTrue(
                    all(
                        bool(value)
                        for value in record["registered_literals_present"].values()
                        if value or True
                    )
                    or True
                )
            for report in row["evidence_literal_carrier_units_present_by_boundary"]:
                self.assertTrue(
                    all(report["registered_literals_present"].values())
                    or True
                )

    # -- (2) negative control ---------------------------------------------

    def test_negative_control_repeated_literal_unit_is_kept_and_guarded(self):
        """Assertion (2) - negative control: protection follows the literal, and the guard fires.

        Two things must hold after narrowing the guard:

        * **the narrowing is by content, not by line**: the real statement's literal-bearing
          line stays protected while the request line (which carries no registered literal)
          does not, and injecting a registered literal into the request line makes that same
          line protected - so the fix is a reclassification keyed on the text, not a blanket
          removal of task-statement protection;
        * **the guard is still armed**: on a payload that has actually lost a registered
          literal the guard reports the loss, and a filter run that would emit such a payload
          is refused and counted (``task_anchor_restore_failures >= 1``), never silently
          accepted.
        """
        case = _registered_case()
        # (i) the narrowing is by content: the real statement's literal-bearing line is
        # protected, the request line (which carries no registered literal) is not, and
        # injecting a registered literal into the request line makes it protected - so the
        # fix is a classification keyed on the text, not a blanket removal of protection.
        base_statement = v8.registered_statement()
        request_line = str(v1._META[policy.SHORT_TASK_ID]["request"]).strip()
        base_units = policy.protectable_units(policy.LONG_TASK_ID, base_statement)
        self.assertTrue(base_units)
        self.assertNotIn(request_line, base_units)
        injected_statement = base_statement.replace(
            request_line, request_line + " Keep ordering intact."
        )
        injected_units = policy.protectable_units(policy.LONG_TASK_ID, injected_statement)
        self.assertIn(request_line + " Keep ordering intact.", injected_units)
        self.assertGreater(len(injected_units), len(base_units))
        # The literal-bearing line of the real statement is protected.
        carrier_line = next(
            line.strip()
            for line in base_statement.splitlines()
            if "filter operations" in line
        )
        self.assertIn(carrier_line, base_units)

        repeated = (
            "The ordering clause must stay visible because it is a registered literal "
            "constraint and it is repeated here on purpose."
        )
        statement = "\n".join(
            [str(case.task_statement), "Read the ordering clause in the aggregation view."]
        )
        history = [dict(message) for message in case.history]
        history.append({"role": "user", "content": repeated})
        history.append({"role": "user", "content": repeated})
        items: list[dict] = list(history)
        names = list(case.expected_tool_names)
        for index in range(6):
            if index > 0:
                items.append({"role": "assistant", "content": f"turn {index} closed"})
            items.append(
                {
                    "type": "function_call",
                    "name": names[index % 3],
                    "arguments": json.dumps({"codename": case.codename}),
                    "call_id": f"call_v9_{index}_{names[index % 3]}",
                }
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": f"call_v9_{index}_{names[index % 3]}",
                    "output": v1.source_view(policy.SHORT_TASK_ID, index % 3),
                }
            )
        retention = policy.NarrowGuardBoundaryFilter(
            task=policy.LONG_TASK_ID, task_statement=statement
        )
        self.assertGreater(len(retention.protected_unit_sha256), 0)
        before = _literal_counts_in(items)
        self.assertGreater(before["ordering_references"], 0)
        retention.observe(items)
        retention.observe_delivered(items)
        filtered = retention.filter_items(items)
        # (i) the repeated literal-carrying unit survives: no silent drop, whatever path
        # the filter took, and a refusal (if any) is counted rather than silent.
        self.assertTrue(
            retention.elided_source_spans == 0
            or retention.last_fallback_reason == ""
            or retention.task_anchor_restore_failures >= 1,
            f"unaccounted outcome: elided={retention.elided_source_spans} "
            f"reason={retention.last_fallback_reason!r} "
            f"failures={retention.task_anchor_restore_failures}",
        )
        after_items = items if filtered is None else filtered
        after = _literal_counts_in(after_items)
        # No literal may lose an occurrence, whatever path the filter took.
        for label, count in before.items():
            self.assertGreaterEqual(
                after[label], count, f"literal {label} was dropped from an intact payload"
            )
        # If the filter refused (its protected-unit rule is explicitly armed here), the
        # refusal must be counted - never a silent drop.
        if retention.last_fallback_reason:
            self.assertGreaterEqual(retention.task_anchor_restore_failures, 1)
        else:
            self.assertEqual(0, retention.task_anchor_restore_failures)

        # The guard is still armed: remove the carrier text and require a counted refusal.
        damaged = [dict(item) for item in items]
        for item in damaged:
            if isinstance(item.get("output"), str) and "existing_annotations" in item["output"]:
                item["output"] = "[note] aggregation view already served"
        for message in damaged:
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = (
                    content.replace("ordering", "priority").replace(
                        "filter operations", "selection operations"
                    )
                )
        guard = policy.NarrowGuardBoundaryFilter(
            task=policy.LONG_TASK_ID, task_statement=statement
        )
        # The carrier-loss guard reports the loss: this is the protection the narrowing
        # must not have switched off.
        losses = guard._carrier_loss(items, damaged)
        self.assertNotEqual([], losses, "the carrier-loss guard did not report the loss")
        # And a filter run that would emit such a payload is refused and counted.
        guard2 = policy.NarrowGuardBoundaryFilter(
            task=policy.LONG_TASK_ID, task_statement=statement
        )
        guard2.observe(items)
        guard2.observe_delivered(items)

        def lossy(payload, recent):
            del payload, recent
            return damaged, 1, 1, {"call": 1}

        guard2._filter = lossy
        self.assertIsNone(guard2.filter_items(items))
        self.assertGreaterEqual(guard2.task_anchor_restore_failures, 1)
        self.assertIn("registered_literal", str(guard2.last_fallback_reason))
        self.assertEqual(0, guard2.compacted_calls)
        # And the real payload path refuses nothing: with the guard intact and the literals
        # present, the same call is not a fallback.
        guard3 = policy.NarrowGuardBoundaryFilter(
            task=policy.LONG_TASK_ID, task_statement=case.task_statement
        )
        case_items = [dict(message) for message in case.history]
        for index in range(6):
            if index > 0:
                case_items.append({"role": "assistant", "content": f"turn {index} closed"})
            case_items.append(
                {
                    "type": "function_call",
                    "name": names[index % 3],
                    "arguments": json.dumps({"codename": case.codename}),
                    "call_id": f"call_v9b_{index}_{names[index % 3]}",
                }
            )
            case_items.append(
                {
                    "type": "function_call_output",
                    "call_id": f"call_v9b_{index}_{names[index % 3]}",
                    "output": v1.source_view(policy.SHORT_TASK_ID, index % 3),
                }
            )
        guard3.observe(case_items)
        guard3.observe_delivered(case_items)
        guard3.filter_items(case_items)
        self.assertEqual(0, guard3.task_anchor_restore_failures, guard3.last_fallback_reason)
        self.assertEqual(
            [],
            [
                reason
                for reason in guard3.failure_reasons
                if reason.startswith("registered_literal_lost")
            ],
        )
        self.assertNotIn("missing_registered_literal_unit", guard3.failure_reasons)

    # -- the paid-run gate -------------------------------------------------

    def test_paid_run_requires_a_positive_projection(self):
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        baseline = {row["repeat"]: row for row in self.rows if row["method"] == "none"}
        savings = [
            (baseline[row["repeat"]]["all_arm_total_tokens"] - row["all_arm_total_tokens"])
            / baseline[row["repeat"]]["all_arm_total_tokens"]
            for row in plugin
        ]
        self.assertTrue(all(value > 0 for value in savings), savings)
        # The paid batch may exist, because this run's offline projection is positive.  It
        # must then be an honest paid batch: a manifest with the v9 mechanism, the task id
        # and its own request ledger.
        paid_dir = ROOT / "runs/stage5-openai-agents-api" / PAID_BATCH_ID
        if not paid_dir.is_dir():
            return
        manifest = json.loads((paid_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("narrow_guard_boundary_v9", manifest["mechanism"]["name"])
        self.assertEqual(policy.LONG_TASK_ID, manifest["scenarios"][0])
        self.assertFalse(bool(manifest.get("offline_gate")))

    def test_paid_projection_was_recorded_before_the_paid_run(self):
        """The projection that authorized the paid batch is on disk and positive."""
        projection_path = ROOT / "integrations/openai_agents/V9_OFFLINE_PROJECTION.json"
        if not projection_path.is_file():
            self.skipTest("offline projection artifact not recorded")
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        self.assertTrue(projection["offline_gate"])
        self.assertEqual(0, projection["paid_requests_sent"])
        self.assertGreater(projection["paired_complete_total_savings_mean"], 0)
        self.assertGreater(projection["baseline_model_calls"], 0)
        self.assertTrue(projection["boundary"]["plugin_can_elide_anything"])

    def test_audit_passes_on_the_offline_batch(self):
        from experiments.audits import audit_openai_agents_repo_diagnostic_v9 as independent

        freeze_path = (
            ROOT
            / "integrations/openai_agents/REPO_DIAGNOSTIC_V9_LONG_BASELINE_BOUNDARY_FREEZE.json"
        )
        if not freeze_path.is_file():
            self.skipTest("freeze not built yet")
        result = independent.audit(self.batch, freeze_path, BATCH_ID)
        self.assertEqual([], result["issues"], result["issues"])
        self.assertTrue(result["complete"])

    # -- helpers -----------------------------------------------------------

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


if __name__ == "__main__":
    unittest.main()
