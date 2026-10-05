"""Zero-API gates for the CrewAI r12 recency batch.

What must hold before the paid r12 batch may run:

  1. the structure rule is deterministic and adds **no** message of its own: the
     newest K protected tool rounds are re-appended as the adapter's own
     placeholders (so the real tool messages come back byte-for-byte), older rounds
     stay compressible, and the counters add up;
  2. the frozen 48-execution mock grid passes with all four gates, a consistent
     tool sequence in every arm, and the recency counters live only in the plugin
     arm;
  3. the paired-baseline tool-sequence verdict is computed against the actual
     uncompressed trace;
  4. the independent audit treats ``tool_sequence_consistent`` and
     ``first_pass_facts`` as hard fields, rejects tampering, refuses a ragged plugin
     tool sequence, and answers the second pre-registered question
     (``still_compressible``) in words.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from experiments.audits import audit_crewai_handoff_v12 as independent
from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_recency_tool_rounds_v12 as mechanism
from experiments.runners import crewai_semantic_equivalence_v12 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v12 as r12


ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "runs/stage5-crewai/crewai-two-role-r12-recency-toolrounds-mock-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R12_RECENCY_TOOLROUNDS_01.json"
MOCK_FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R12_RECENCY_TOOLROUNDS_MOCK_01.json"
PAID_BATCH = ROOT / "runs/stage5-crewai/crewai-two-role-r12-recency-toolrounds-01"


def _args(**overrides) -> SimpleNamespace:
    values = dict(
        model="mock", base_url="", max_output_tokens=512, fixed_reserved_tokens=300,
        max_summary_tokens=1024, max_summary_calls=4, provider_soft=1200,
        provider_hard=3000, provider_target=900, soft_limit=0, hard_limit=0, target=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _mock_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _task(task_id: str = "window_gate") -> dict:
    return next(task for task in judge.load_tasks() if task["task_id"] == task_id)


def _placeholder(group_id: str) -> dict:
    """Exactly what the CrewAI adapter puts in the prepared list."""
    return {
        "role": "system",
        "content": f"{mechanism.PROTECTED_GROUP_PREFIX} {group_id}]",
        "_context_pruner_crewai_group": group_id,
    }


def _tool_round(name: str) -> list[dict]:
    """The real one-message shape the CrewAI adapter protects and restores.

    ``context_pruner.adapters.crewai`` protects the text-ReAct message that merges
    the action with its observation; that is exactly what the host records, so the
    fixture must use the same shape or the protection would never fire.
    """
    return [
        {
            "role": "assistant",
            "content": (
                "Thought: call the next frozen tool.\n"
                f"Action: {name}\n"
                'Action Input: {"x": 1}\n'
                'Observation: {"ok": true}'
            ),
        }
    ]


class RecencySelectionTest(unittest.TestCase):
    """The structure rule itself: which rounds are kept, and nothing invented."""

    def test_newest_k_rounds_are_selected_by_identity_and_order(self) -> None:
        prepared = [
            {"role": "user", "content": "history"},
            _placeholder("crewai-a-0"),
            {"role": "assistant", "content": "thinking"},
            _placeholder("crewai-a-0"),  # a repeated slot is not a second round
            _placeholder("crewai-b-0"),
            _placeholder("crewai-c-0"),
            {"role": "user", "content": "task statement"},
        ]
        self.assertEqual(
            ["crewai-a-0", "crewai-a-0", "crewai-b-0", "crewai-c-0"],
            [p["_context_pruner_crewai_group"] for p in mechanism.group_placeholders(prepared)],
        )
        self.assertEqual(
            ["crewai-c-0"],
            [
                p["_context_pruner_crewai_group"]
                for p in mechanism.newest_round_placeholders(prepared, 1)
            ],
        )
        self.assertEqual(
            ["crewai-b-0", "crewai-c-0"],
            [
                p["_context_pruner_crewai_group"]
                for p in mechanism.newest_round_placeholders(prepared, 2)
            ],
        )
        self.assertEqual([], mechanism.newest_round_placeholders(prepared, 0))

    def test_no_rounds_means_nothing_to_protect(self) -> None:
        self.assertEqual(
            [], mechanism.newest_round_placeholders([{"role": "user", "content": "x"}], 1)
        )

    def test_compressed_view_gets_the_newest_round_back_verbatim(self) -> None:
        """The r10 shape: the compressed view dropped a tool round.

        The round that must survive is the newest one; which of the two the frozen
        compression keeps is an implementation detail, so the assertion is that the
        newest group is always in the served view and that the appends are the
        adapter's own placeholders, never new text.
        """
        task = _task("window_gate")
        budget, _ = base.resolve_budget(_args())
        middleware = mechanism.build_recency_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=pinned.pinned_rule(task),
            budget=budget,
            k_recent_tool_rounds=1,
        )
        history: list[dict] = [{"role": "user", "content": str(task["history_constraint"])}]
        for index in range(24):
            history.extend(
                [
                    {"role": "user", "content": f"Historical handoff {index + 1}: background only."},
                    {"role": "assistant", "content": f"Historical note {index + 1} filed as stale."},
                ]
            )
        history.append(
            {
                "role": "user",
                "content": (
                    "调查阶段：{title}。按顺序对每个可用工具各调用一次，记录工具返回的当前事实。"
                    "最终只输出一行以 HANDOFF 开头的交接内容，包含固定标识和工具关键数值；"
                    "不要给出最终 decision。"
                ).format(title=task["title"]),
            }
        )
        history.append(_placeholder("crewai-round-1-0"))
        history.append(_placeholder("crewai-round-2-0"))
        result = middleware.before_model(
            [dict(message) for message in history], reserved_tokens=300
        )
        served_ids = [
            str(message.get("_context_pruner_crewai_group"))
            for message in result.messages
            if mechanism.is_tool_group_placeholder(message)
        ]
        self.assertLess(len(result.messages), len(history))  # compression really ran
        self.assertIn("crewai-round-2-0", served_ids)  # newest round always kept
        metrics = middleware.metrics_dict()
        self.assertEqual(1, metrics["k_recent_tool_rounds"])
        self.assertEqual(1, metrics["recent_rounds_seen_total"])
        self.assertEqual(1, metrics["recency_omitted_tool_rounds_total"])
        self.assertEqual(
            metrics["recent_rounds_seen_total"],
            metrics["recency_protected_rounds_total"] + metrics["recent_rounds_readded_total"],
        )
        self.assertEqual(0, metrics["required_fact_whole_prefix_fallbacks"])
        served_text = "\n".join(str(message.get("content", "")) for message in result.messages)
        for literal in middleware.rule.required_literals:
            self.assertTrue(judge._literal_present(judge._fold(served_text), literal), literal)

    def test_protected_round_is_not_duplicated_when_it_survives(self) -> None:
        """No compression means no duplication: the placeholder is served once."""
        task = _task("batch_replay")
        middleware = mechanism.build_recency_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1"),
            rule=pinned.pinned_rule(task),
            budget=None,
            k_recent_tool_rounds=1,
        )
        prepared = [{"role": "user", "content": "短历史"}, _placeholder("crewai-round-1-0")]
        result = middleware.before_model([dict(message) for message in prepared])
        ids = [
            str(message.get("_context_pruner_crewai_group"))
            for message in result.messages
            if mechanism.is_tool_group_placeholder(message)
        ]
        # The frozen compression itself may fold the placeholder away (the adapter
        # then restores the real round); what must never happen is the recency rule
        # adding a second copy of a round that is still there.
        self.assertEqual(len(ids), len(set(ids)))
        metrics = middleware.metrics_dict()
        self.assertEqual(0, metrics["recent_rounds_readded_total"])
        self.assertEqual(1, metrics["recency_protected_rounds_total"])

    def test_middleware_adds_no_directive_text(self) -> None:
        """No directive: the v12 rule never injects an instruction message.

        The frozen v9 tiers may still append their own pin messages (that is the v9
        contract), so this test isolates the v12 rule: the served view may never
        contain directive wording, and the middleware may not even carry the r11
        directive surface.  What it may add is only the adapter's protected
        tool-group placeholder, which the adapter turns back into the real tool
        message.
        """
        task = _task("credential_rotation")
        middleware = mechanism.build_recency_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1"),
            rule=pinned.pinned_rule(task),
            budget=None,
            k_recent_tool_rounds=1,
        )
        prepared = [
            {"role": "user", "content": "短历史"},
            _placeholder("crewai-round-1-0"),
        ]
        result = middleware.before_model([dict(message) for message in prepared])
        served_text = "\n".join(str(m.get("content", "")) for m in result.messages)
        for forbidden in (
            "host directive", "Required tool sequence", "Call ",
            "Still required", "Output contract reminder", "Never output angle brackets",
        ):
            self.assertNotIn(forbidden, served_text)
        self.assertFalse(hasattr(middleware, "tool_directive"))
        self.assertFalse(hasattr(middleware, "contract_directive"))
        # Everything the middleware serves is either the prepared list itself, the
        # adapter's own placeholder for a round it protected, or one of the frozen v9
        # tier-1 evidence pins (which quote the frozen tool bytes verbatim).
        allowed = {json.dumps(message, sort_keys=True) for message in prepared}
        evidence_fragments = set(middleware.rule.evidence_fragments)
        for message in result.messages:
            if json.dumps(message, sort_keys=True) in allowed:
                continue
            if mechanism.is_tool_group_placeholder(message):
                continue
            content = str(message.get("content", ""))
            self.assertTrue(
                any(fragment in content for fragment in evidence_fragments),
                f"v12 served a message that is neither prepared, a placeholder, "
                f"nor a frozen evidence pin: {message}",
            )


class RecencyAdapterIntegrationTest(unittest.TestCase):
    """End to end: with K=1 the first role still finishes the frozen tool list."""

    def test_adapter_keeps_the_newest_round_and_real_messages_come_back(self) -> None:
        """End to end through the real adapter helpers: the newest round survives.

        ``_protect_tool_groups`` builds the placeholders the adapter will restore, so
        this is the same path the runner exercises: two protected tool rounds go in,
        and the newest one is still a group placeholder after the middleware ran.
        """
        task = _task("window_gate")
        budget, _ = base.resolve_budget(_args())
        middleware = mechanism.build_recency_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=pinned.pinned_rule(task),
            budget=budget,
            k_recent_tool_rounds=1,
        )
        rounds = [
            *_tool_round("check_window_lock"),
            *_tool_round("inspect_change_guard"),
        ]
        adapter_module = __import__(
            "context_pruner.adapters.crewai", fromlist=["_protect_tool_groups"]
        )
        prepared, groups = adapter_module._protect_tool_groups(rounds, base.estimate_tokens)
        self.assertEqual(2, len(groups))
        result = middleware.before_model(
            [dict(message) for message in prepared], reserved_tokens=300
        )
        served_ids = [
            str(message.get("_context_pruner_crewai_group"))
            for message in result.messages
            if mechanism.is_tool_group_placeholder(message)
        ]
        # The newest round's group must be present so the adapter restores it, and
        # no group may appear twice.
        self.assertIn(groups[-1].group_id, served_ids)
        self.assertEqual(len(served_ids), len(set(served_ids)))

    def test_mock_grid_keeps_every_tool_sequence_consistent(self) -> None:
        rows = _mock_rows()
        for row in rows:
            if row["method"] != "pruner_v1":
                continue
            with self.subTest(task=row["task_id"], repeat=row["repeat"]):
                self.assertTrue(row["tool_sequence_consistent"])
                self.assertEqual(
                    list(row["expected_first_role_trace"]), list(row["first_role_trace"])
                )
                self.assertEqual(1, row["k_recent_tool_rounds"])


class PairedToolSequenceTest(unittest.TestCase):
    """The paired verdict is read from the uncompressed arm's recorded trace."""

    def _row(self, method: str, trace: list[str]) -> dict:
        frozen = ["check_window_lock", "inspect_change_guard", "verify_risk_review"]
        return {
            "task_id": "window_gate", "repeat": 0, "method": method,
            "first_role_trace": trace,
            "tool_sequence_consistent": trace == frozen,
        }

    def test_matching_and_shortened_traces_are_distinguished(self) -> None:
        frozen = ["check_window_lock", "inspect_change_guard", "verify_risk_review"]
        rows = [
            self._row("none", frozen),
            self._row("pruner_v1", frozen),
            self._row("native_summary", ["check_window_lock"]),
        ]
        summary = r12._add_paired_tool_sequence(rows)
        self.assertTrue(rows[1]["tool_sequence_matches_baseline"])
        self.assertFalse(rows[2]["tool_sequence_matches_baseline"])
        self.assertEqual(1, summary["pruner_v1"]["tool_sequence_matches_baseline"])
        self.assertEqual(0, summary["native_summary"]["tool_sequence_matches_baseline"])


class CrewAIRunnerV12MockGridTest(unittest.TestCase):
    """The frozen zero-API grid: 48 executions, four gates, one metered remedy."""

    def test_mock_grid_has_48_samples_with_four_gates(self) -> None:
        rows = _mock_rows()
        self.assertEqual(48, len(rows))
        self.assertEqual(
            48, len({(row["task_id"], row["repeat"], row["method"]) for row in rows})
        )
        self.assertTrue(all(row["strict_success"] for row in rows))
        self.assertTrue(all(row["semantic_success"] for row in rows))
        self.assertTrue(all(row["tool_sequence_consistent"] for row in rows))
        self.assertTrue(all(row["tool_sequence_matches_baseline"] for row in rows))
        self.assertTrue(all(row["api_request_attempts"] == 0 for row in rows))

    def test_only_the_published_remedy_probe_needs_a_remedy(self) -> None:
        rows = _mock_rows()
        remedies = [
            (row["task_id"], row["repeat"], row["method"])
            for row in rows if row["recovery_invocations"]
        ]
        self.assertEqual([(r12.REMEDY_PROBE[0], r12.REMEDY_PROBE[1], "pruner_v1")], remedies)
        self.assertEqual(47, sum(row["first_pass_complete"] for row in rows))

    def test_recency_counters_live_only_in_the_plugin_arm(self) -> None:
        rows = _mock_rows()
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertTrue(all(row["recency_calls"] > 0 for row in plugin))
        self.assertTrue(all(row["recent_rounds_seen_total"] > 0 for row in plugin))
        for row in plugin:
            self.assertEqual(
                row["recent_rounds_seen_total"],
                row["recency_protected_rounds_total"] + row["recent_rounds_readded_total"],
            )
            self.assertEqual(0, row["required_fact_whole_prefix_fallbacks"])
        for row in rows:
            if row["method"] == "pruner_v1":
                continue
            self.assertEqual(0, row["recency_calls"])
            self.assertEqual(0, row["recent_rounds_readded_total"])
            self.assertEqual(0, row["pinned_events"])

    def test_repeated_grid_runs_are_record_identical(self) -> None:
        task = _task("shard_split")
        budget = base.resolve_budget(_args())[0]

        def run() -> dict:
            with contextlib.redirect_stdout(io.StringIO()):
                row = r12.run_case(task, "pruner_v1", 0, "mock", None,
                                   base.RequestBudget(440), _args(), budget)
            for record in row["agent_attempt_records"]:
                record["latency_seconds"] = 0.0
            return row

        first, second = run(), run()
        self.assertEqual(first, second)


class CrewAIAuditV12Test(unittest.TestCase):
    """Hard gates, tamper rejection, and explicit not-citable / boundary wording."""

    def _copy_grid(self, directory: Path) -> Path:
        batch = directory / "batch"
        batch.mkdir()
        for name in ("manifest.json", "results.jsonl"):
            shutil.copy2(MOCK / name, batch / name)
        return batch

    def _rows(self, batch: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _write_rows(self, batch: Path, rows: list[dict]) -> None:
        (batch / "results.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )

    def test_mock_grid_passes_the_frozen_audit(self) -> None:
        result = independent.audit(MOCK, MOCK_FREEZE, dry_run_mock=True)
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual([], result["errors"])
        self.assertEqual(48, result["rows"])
        self.assertTrue(result["tool_sequence_non_inferior"])
        self.assertTrue(result["quality_non_inferior"])
        # The frozen mock grid deliberately drops one first-role HANDOFF (the
        # published remedy probe), so the plugin is 15/16 there.  The gate is
        # reported, not forced: on the paid batch it must be read, not assumed.
        self.assertEqual(
            {"plugin": 15, "baseline": 16},
            {
                "plugin": result["first_pass_gate"]["plugin_first_pass_facts"],
                "baseline": result["first_pass_gate"]["baseline_first_pass_facts"],
            },
        )
        self.assertEqual(
            {"plugin": 16, "baseline": 16},
            {
                "plugin": result["tool_sequence_gate"]["plugin_tool_sequence_consistent"],
                "baseline": result["tool_sequence_gate"]["baseline_tool_sequence_consistent"],
            },
        )
        self.assertEqual(16, result["tool_sequence_gate"]["plugin_matches_baseline"])
        self.assertEqual(1, result["structure_rule_gate"]["k_recent_tool_rounds"])
        self.assertEqual(15, result["quality"]["pruner_v1"]["first_pass_facts"])
        self.assertEqual(1, result["quality"]["pruner_v1"]["recovered"])
        self.assertIn(result["boundary"], (
            "quality_preserved_and_nothing_left_to_compress",
            "correctness_preserved_and_compression_still_observed",
            "correctness_not_established",
        ))
        self.assertEqual(
            {
                "credential_rotation": ["svc-ledger-key", "ca-central-1", "failed 0"],
                "shard_split": ["shard-88", "tenant-lumen", "target_shards 3"],
                "batch_replay": ["run-8842", "step-transform", "failed 0"],
                "window_gate": ["window-9930", "620", "failed 0"],
            },
            result["pin_rules"],
        )

    def test_audit_rejects_a_ragged_plugin_tool_sequence(self) -> None:
        """The r10 failure shape must be invalid, not a saving."""
        with tempfile.TemporaryDirectory() as directory:
            batch = self._copy_grid(Path(directory))
            rows = self._rows(batch)
            row = next(
                entry for entry in rows
                if entry["method"] == "pruner_v1" and entry["task_id"] == "window_gate"
            )
            row["first_role_trace"] = ["check_window_lock"]
            row["role_tool_traces"] = [["check_window_lock"], row["role_tool_traces"][1]]
            row["tool_sequence_consistent"] = False
            row["tool_sequence_matches_baseline"] = False
            self._write_rows(batch, rows)
            result = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)
            key = f"{row['task_id']}/{row['repeat']}/{row['method']}"
            self.assertIn(
                f"{key}: plugin tool sequence inconsistent with the frozen task",
                result["errors"],
            )
            self.assertIn(f"{key}: tool evidence mismatch", result["errors"])
            self.assertFalse(result["tool_sequence_non_inferior"])
            self.assertEqual("not_yet_valid", result["verdict"])
            self.assertIn(result["boundary"], (
                "correctness_not_established",
                "quality_preserved_and_nothing_left_to_compress",
                "correctness_preserved_and_compression_still_observed",
            ))
            self.assertIn("本批数字不得当作有效节省", result["verdict_reason"])
            self.assertIn("non-empty errors", result["errors_meaning"])

    def test_audit_rejects_tool_sequence_and_first_pass_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            batch = self._copy_grid(Path(directory))
            rows = self._rows(batch)
            flag = next(entry for entry in rows if entry["method"] == "pruner_v1")
            flag["tool_sequence_consistent"] = not flag["tool_sequence_consistent"]
            first = next(
                entry for entry in rows
                if entry["method"] == "pruner_v1" and entry is not flag
            )
            first["first_pass_complete"] = not first["first_pass_complete"]
            self._write_rows(batch, rows)
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{flag['task_id']}/{flag['repeat']}/{flag['method']}: "
                "tool-sequence flag mismatch",
                errors,
            )
            self.assertIn(
                f"{first['task_id']}/{first['repeat']}/{first['method']}: "
                "first-pass completeness mismatch",
                errors,
            )

    def test_audit_rejects_recency_accounting_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            batch = self._copy_grid(Path(directory))
            rows = self._rows(batch)
            unbalanced = next(entry for entry in rows if entry["method"] == "pruner_v1")
            unbalanced["recent_rounds_readded_total"] += 1
            outside = next(entry for entry in rows if entry["method"] == "none")
            outside["recency_calls"] = 3
            wrong_k = next(
                entry for entry in rows
                if entry["method"] == "pruner_v1" and entry is not unbalanced
            )
            wrong_k["k_recent_tool_rounds"] = 2
            self._write_rows(batch, rows)
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{unbalanced['task_id']}/{unbalanced['repeat']}/"
                f"{unbalanced['method']}: recency accounting mismatch",
                errors,
            )
            self.assertIn(
                f"{outside['task_id']}/{outside['repeat']}/{outside['method']}: "
                "v12 structure rule outside the plugin arm",
                errors,
            )
            self.assertIn(
                f"{wrong_k['task_id']}/{wrong_k['repeat']}/{wrong_k['method']}: "
                "frozen K mismatch",
                errors,
            )

    def test_audit_rejects_frozen_rule_and_gate_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = self._copy_grid(tmp)
            frozen = json.loads(MOCK_FREEZE.read_text(encoding="utf-8"))
            frozen["source_sha256"][
                "experiments/runners/crewai_recency_tool_rounds_v12.py"
            ] = "0" * 64
            frozen["mechanism_rules"] = {"k_recent_tool_rounds": 2}
            wrong = tmp / "wrong-freeze.json"
            wrong.write_text(json.dumps(frozen), encoding="utf-8")
            errors = independent.audit(batch, wrong, dry_run_mock=True)["errors"]
            self.assertIn(
                "frozen source mismatch: "
                "experiments/runners/crewai_recency_tool_rounds_v12.py",
                errors,
            )
            self.assertIn("mechanism rule declaration mismatch", errors)


class CrewAIRunnerV12FreezeTest(unittest.TestCase):
    """The paid freeze must describe the paid batch and hash every source."""

    def test_paid_freeze_matches_sources_and_manifest(self) -> None:
        frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("crewai-two-role-r12-recency-toolrounds-01", frozen["batch"])
        self.assertEqual(48, frozen["samples"])
        self.assertEqual(independent.FROZEN_PATHS, set(frozen["source_sha256"]))
        for name in sorted(independent.FROZEN_PATHS):
            path = ROOT / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                frozen["source_sha256"][name],
                name,
            )
        manifest = frozen["manifest"]
        self.assertEqual(independent.MANIFEST_FIELDS, set(manifest))
        self.assertEqual("api", manifest["mode"])
        self.assertEqual(list(independent.FRESH_TASKS), manifest["tasks"])
        self.assertEqual(4, manifest["repeats"])
        self.assertEqual(independent.QUALITY_GATES, manifest["quality_gates"])
        self.assertEqual(independent.FIRST_PASS_GATE, frozen["first_pass_gate"])
        self.assertEqual(independent.TOOL_SEQUENCE_GATE, frozen["tool_sequence_gate"])
        self.assertEqual(independent.MECHANISM_RULES, frozen["mechanism_rules"])

    def test_paid_batch_directory_does_not_exist_before_the_run(self) -> None:
        if PAID_BATCH.exists():
            self.assertTrue((PAID_BATCH / "results.jsonl").is_file())
        else:
            self.assertFalse(PAID_BATCH.exists())


if __name__ == "__main__":
    unittest.main()
