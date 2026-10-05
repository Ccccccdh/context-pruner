"""Zero-API gates for the CrewAI r11 tool-sequence batch.

What must hold before the paid r11 batch may run:

  1. the v11 guarantees fire deterministically: the tool directive names the
     *frozen* next tool and disappears only when every frozen tool has been
     observed, and the decision-contract directive reproduces the frozen contract
     verbatim -- template included -- when the served view lost it;
  2. the frozen 48-execution mock grid (r10 tasks x 4 repeats x 3 arms) passes
     with all four gates and a consistent tool sequence in every arm, and the
     directives are a no-op outside the plugin arm;
  3. the paired-baseline tool-sequence verdict is computed against the actual
     uncompressed trace, not an assumption;
  4. the independent audit treats ``tool_sequence_consistent`` and
     ``first_pass_facts`` as hard fields, rejects tampering, refuses a grid whose
     plugin tool sequence is ragged, and says in words that such a batch cannot be
     cited as an effective saving.
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

from experiments.audits import audit_crewai_handoff_v11 as independent
from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v11 as judge
from experiments.runners import crewai_tool_sequence_v11 as mechanism
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v11 as r11


ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "runs/stage5-crewai/crewai-two-role-r11-toolseq-multitask-mock-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R11_TOOLSEQ_MULTITASK_01.json"
MOCK_FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R11_TOOLSEQ_MULTITASK_MOCK_01.json"
PAID_BATCH = ROOT / "runs/stage5-crewai/crewai-two-role-r11-toolseq-multitask-01"


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


def _observation(name: str) -> dict:
    """One CrewAI text-ReAct step, exactly as the host records it."""
    return {
        "role": "assistant",
        "content": (
            "Thought: verify the next required tool.\n"
            f"Action: {name}\n"
            'Action Input: {"x": 1}\n'
            'Observation: {"ok": true}'
        ),
    }


class ToolDirectiveTest(unittest.TestCase):
    """Guarantee A: the frozen tool order survives compression, deterministically."""

    def test_observed_tool_calls_reads_the_host_markers_only(self) -> None:
        messages = [
            {"role": "user", "content": "Action: not_a_tool — this line has no marker"},
            _observation("check_window_lock"),
            {
                "role": "assistant",
                "content": "Thought: I should call verify_risk_review at some point.",
            },
            _observation("inspect_change_guard"),
        ]
        self.assertEqual(
            ["check_window_lock", "inspect_change_guard"],
            mechanism.observed_tool_calls(messages),
        )

    def test_directive_names_the_frozen_next_tool_and_retires_when_done(self) -> None:
        task = _task("window_gate")
        rule = pinned.pinned_rule(task)
        middleware = mechanism.build_tool_sequence_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1"),
            rule=rule,
            budget=None,
            tool_names=[str(tool["name"]) for tool in task["tools"]],
            enable_tool_sequence=True,
        )
        frozen = [str(tool["name"]) for tool in task["tools"]]

        first = middleware.tool_directive([])
        self.assertEqual(1, len(first))
        self.assertIn(", ".join(frozen), first[0]["content"])
        self.assertIn(f"Call {frozen[0]} now", first[0]["content"])

        # The frozen order, not the order actually used, decides the next tool:
        # a role that called the third tool first must still be told the first.
        scrambled = middleware.tool_directive([frozen[2]])
        self.assertIn(f"Call {frozen[0]} now", scrambled[0]["content"])
        self.assertIn(frozen[2], scrambled[0]["content"])

        self.assertEqual([], middleware.tool_directive(frozen))

    def test_compressed_view_gets_the_directive_and_the_next_tool(self) -> None:
        """The r10 lossy shape: long history whose tool list is compressed away."""
        task = _task("window_gate")
        rule = pinned.pinned_rule(task)
        budget, _ = base.resolve_budget(_args())
        frozen = [str(tool["name"]) for tool in task["tools"]]
        history: list[dict[str, str]] = []
        for index in range(20):
            history.extend(
                [
                    {"role": "user", "content": f"第 {index + 1} 次历史交接涉及{task['history_topic']}。"},
                    {"role": "assistant", "content": f"历史记录 {index + 1} 已整理。"},
                ]
            )
        history.append(
            {
                "role": "user",
                "content": (
                    f"调查阶段：{task['title']}。按顺序对每个可用工具各调用一次"
                    f"（{'、'.join(frozen)}），记录工具返回的当前事实。最终只输出一行以 "
                    "HANDOFF 开头的交接内容，包含固定标识和工具关键数值；不要给出最终 decision。"
                ),
            }
        )
        history.append(_observation(frozen[0]))
        middleware = mechanism.build_tool_sequence_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=rule,
            budget=budget,
            tool_names=frozen,
            enable_tool_sequence=True,
        )
        result = middleware.before_model(
            [dict(message) for message in history], reserved_tokens=300
        )
        served = "\n".join(mechanism.v9._text_of(m) for m in result.messages)
        self.assertIn(mechanism._TOOL_DIRECTIVE_PREFIX, served)
        self.assertIn(f"Call {frozen[1]} now", served)
        self.assertIn(frozen[2], served)
        self.assertEqual(1, middleware.tool_directive_events)
        self.assertEqual(0, middleware.required_fact_whole_prefix_fallbacks)

    def test_directive_is_disabled_for_the_deciding_role(self) -> None:
        task = _task("shard_split")
        middleware = mechanism.build_tool_sequence_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1"),
            rule=pinned.pinned_rule(task),
            budget=None,
            tool_names=[],
            enable_tool_sequence=False,
        )
        self.assertEqual([], middleware.tool_directive([]))


class ContractDirectiveTest(unittest.TestCase):
    """Guarantee B: the frozen output contract cannot degrade to a placeholder."""

    def _middleware(self, task: dict, prompt: str) -> mechanism.VerbatimToolSequenceMiddleware:
        return mechanism.build_tool_sequence_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1"),
            rule=pinned.pinned_rule(task),
            budget=None,
            role_prompt=prompt,
            decision_placeholder=str(task["decision"]),
            enable_decision_contract=True,
        )

    def test_contract_is_restated_verbatim_with_the_template(self) -> None:
        task = _task("batch_replay")
        prompt = r11.latest_task_prompt(task)
        middleware = self._middleware(task, prompt)
        pins = middleware.contract_directive([{"role": "user", "content": "compressed view"}])
        self.assertEqual(1, len(pins))
        self.assertIn(prompt, pins[0]["content"])
        self.assertIn(f"decision=<{task['decision']}>", pins[0]["content"])
        self.assertIn("Never output angle brackets", pins[0]["content"])

    def test_contract_is_not_restated_when_the_view_still_has_it(self) -> None:
        task = _task("window_gate")
        prompt = r11.latest_task_prompt(task)
        middleware = self._middleware(task, prompt)
        served = [{"role": "user", "content": "…" + prompt + "…"}]
        self.assertEqual([], middleware.contract_directive(served))

    def test_contract_directive_is_disabled_for_the_first_role(self) -> None:
        task = _task("window_gate")
        middleware = mechanism.build_tool_sequence_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1"),
            rule=pinned.pinned_rule(task),
            budget=None,
            role_prompt=r11.latest_task_prompt(task),
            enable_decision_contract=False,
        )
        self.assertEqual([], middleware.contract_directive([]))

    def test_served_decider_view_always_carries_the_contract(self) -> None:
        """Guarantee B, end to end: the last served view spells out the contract.

        Checks the two cheap ways to lose it -- the visible view keeps only the
        ``RESULT`` line, or keeps only the ``evidence=`` half -- and then checks the
        real decider history for every frozen task at two repeats with the real
        runner payloads.  Compression may keep the prompt verbatim (it is the newest
        message) or drop it; either way the served view must end up carrying the
        labelled decision field *and* the template, because that is what stops the
        model autocompleting ``decision=<...>``.
        """
        task = _task("batch_replay")
        prompt = r11.latest_task_prompt(task)
        middleware = self._middleware(task, prompt)
        anchor = f"RESULT task={task['task_id']}"
        template = f"decision=<{task['decision']}>"
        lossy_views = [
            [{"role": "user", "content": f"{anchor} decision={task['decision']}"}],
            [
                {
                    "role": "user",
                    "content": f"{anchor} decision={task['decision']} evidence=<the key facts>",
                }
            ],
        ]
        for view in lossy_views:
            with self.subTest(view=view[0]["content"][:40]):
                pins = middleware.contract_directive(view)
                self.assertEqual(1, len(pins))
                self.assertIn(prompt, pins[0]["content"])
                self.assertIn(anchor, pins[0]["content"])
                self.assertIn(template, pins[0]["content"])

        budget = base.resolve_budget(_args())[0]
        for fixture in r11.load_tasks():
            for repeat in (0, 3):
                with self.subTest(task=fixture["task_id"], repeat=repeat):
                    fixture_middleware = mechanism.build_tool_sequence_middleware(
                        base.ContextPluginConfig(
                            enabled=True, method="pruner_v1", budget=budget
                        ),
                        rule=pinned.pinned_rule(fixture),
                        budget=budget,
                        role_prompt=r11.latest_task_prompt(fixture),
                        decision_placeholder=str(fixture["decision"]),
                        enable_decision_contract=True,
                    )
                    history = [
                        {"role": "user", "content": str(fixture["history_constraint"])},
                        {
                            "role": "user",
                            "content": (
                                "Investigator handoff: HANDOFF "
                                + " ".join(str(f) for f in fixture["handle_facts"])
                            ),
                        },
                        {"role": "user", "content": r11.latest_task_prompt(fixture)},
                    ]
                    served_text = "\n".join(
                        mechanism.v9._text_of(m)
                        for m in fixture_middleware.before_model(
                            history, reserved_tokens=300
                        ).messages
                    )
                    self.assertIn(f"RESULT task={fixture['task_id']}", served_text)
                    self.assertIn(f"decision=<{fixture['decision']}>", served_text)


class PairedToolSequenceTest(unittest.TestCase):
    """The paired verdict is read from the uncompressed arm's recorded trace."""

    def _row(self, method: str, trace: list[str]) -> dict:
        return {
            "task_id": "window_gate", "repeat": 0, "method": method,
            "first_role_trace": trace,
            "tool_sequence_consistent": trace == ["check_window_lock", "inspect_change_guard",
                                                  "verify_risk_review"],
        }

    def test_matching_trace_is_recorded_as_matching(self) -> None:
        frozen = ["check_window_lock", "inspect_change_guard", "verify_risk_review"]
        rows = [self._row("none", frozen), self._row("pruner_v1", frozen)]
        summary = r11._add_paired_tool_sequence(rows)
        self.assertTrue(rows[1]["tool_sequence_matches_baseline"])
        self.assertEqual(1, summary["pruner_v1"]["tool_sequence_matches_baseline"])

    def test_shortened_plugin_trace_is_recorded_as_not_matching(self) -> None:
        frozen = ["check_window_lock", "inspect_change_guard", "verify_risk_review"]
        rows = [self._row("none", frozen), self._row("pruner_v1", ["check_window_lock"])]
        summary = r11._add_paired_tool_sequence(rows)
        self.assertFalse(rows[1]["tool_sequence_matches_baseline"])
        self.assertFalse(rows[1]["tool_sequence_consistent"])
        self.assertEqual(0, summary["pruner_v1"]["tool_sequence_matches_baseline"])
        self.assertEqual(0, summary["pruner_v1"]["tool_sequence_consistent"])


class CrewAIRunnerV11MockGridTest(unittest.TestCase):
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
        self.assertEqual([(r11.REMEDY_PROBE[0], r11.REMEDY_PROBE[1], "pruner_v1")], remedies)
        self.assertEqual(47, sum(row["first_pass_complete"] for row in rows))

    def test_directives_only_run_in_the_plugin_arm(self) -> None:
        rows = _mock_rows()
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertTrue(all(row["tool_directive_events"] > 0 for row in plugin))
        self.assertTrue(all(row["pinned_events"] > 0 for row in plugin))
        self.assertTrue(
            all(row["required_fact_whole_prefix_fallbacks"] == 0 for row in plugin)
        )
        for row in rows:
            if row["method"] == "pruner_v1":
                continue
            self.assertEqual(0, row["tool_directive_events"])
            self.assertEqual(0, row["contract_directive_events"])
            self.assertEqual(0, row["pinned_events"])

    def test_mock_plugin_trace_equals_the_frozen_tool_order(self) -> None:
        for row in _mock_rows():
            if row["method"] != "pruner_v1":
                continue
            with self.subTest(task=row["task_id"], repeat=row["repeat"]):
                self.assertEqual(
                    list(row["expected_first_role_trace"]), list(row["first_role_trace"])
                )

    def test_repeated_grid_runs_are_record_identical(self) -> None:
        task = _task("credential_rotation")
        budget = base.resolve_budget(_args())[0]

        def run() -> dict:
            with contextlib.redirect_stdout(io.StringIO()):
                row = r11.run_case(task, "pruner_v1", 0, "mock", None,
                                   base.RequestBudget(440), _args(), budget)
            for record in row["agent_attempt_records"]:
                record["latency_seconds"] = 0.0
            return row

        first, second = run(), run()
        self.assertEqual(first, second)


class CrewAIAuditV11Test(unittest.TestCase):
    """Hard gates, tamper rejection, and an explicit not-citable verdict."""

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
        self.assertEqual(
            {"plugin": 16, "baseline": 16},
            {
                "plugin": result["tool_sequence_gate"]["plugin_tool_sequence_consistent"],
                "baseline": result["tool_sequence_gate"]["baseline_tool_sequence_consistent"],
            },
        )
        self.assertEqual(16, result["tool_sequence_gate"]["plugin_matches_baseline"])
        self.assertEqual(15, result["quality"]["pruner_v1"]["first_pass_facts"])
        self.assertEqual(1, result["quality"]["pruner_v1"]["recovered"])
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
        """The exact r10 failure shape must be reported as invalid, not as a saving."""
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
            self.assertIn("本批数字不得当作有效节省", result["verdict_reason"])
            self.assertIn("non-empty errors", result["errors_meaning"])

    def test_audit_rejects_tool_sequence_flag_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            batch = self._copy_grid(Path(directory))
            rows = self._rows(batch)
            row = next(entry for entry in rows if entry["method"] == "pruner_v1")
            row["tool_sequence_consistent"] = not row["tool_sequence_consistent"]
            self._write_rows(batch, rows)
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{row['task_id']}/{row['repeat']}/{row['method']}: "
                "tool-sequence flag mismatch",
                errors,
            )

    def test_audit_rejects_first_pass_and_paired_baseline_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            batch = self._copy_grid(Path(directory))
            rows = self._rows(batch)
            first = next(entry for entry in rows if entry["method"] == "pruner_v1")
            first["first_pass_complete"] = not first["first_pass_complete"]
            paired = next(entry for entry in rows if entry["method"] == "none")
            paired["paired_baseline_tool_trace"] = ["nothing"]
            self._write_rows(batch, rows)
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{first['task_id']}/{first['repeat']}/{first['method']}: "
                "first-pass completeness mismatch",
                errors,
            )
            self.assertIn(
                f"{paired['task_id']}/{paired['repeat']}/{paired['method']}: "
                "paired baseline trace mismatch",
                errors,
            )

    def test_audit_rejects_directive_activity_outside_the_plugin_arm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            batch = self._copy_grid(Path(directory))
            rows = self._rows(batch)
            row = next(entry for entry in rows if entry["method"] == "none")
            row["tool_directive_events"] = 2
            self._write_rows(batch, rows)
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{row['task_id']}/{row['repeat']}/{row['method']}: "
                "v11 directive outside the plugin arm",
                errors,
            )

    def test_audit_rejects_frozen_source_and_gate_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = self._copy_grid(tmp)
            frozen = json.loads(MOCK_FREEZE.read_text(encoding="utf-8"))
            frozen["source_sha256"]["experiments/runners/crewai_tool_sequence_v11.py"] = "0" * 64
            frozen["tool_sequence_gate"] = {"name": "tool_sequence_consistent"}
            wrong = tmp / "wrong-freeze.json"
            wrong.write_text(json.dumps(frozen), encoding="utf-8")
            errors = independent.audit(batch, wrong, dry_run_mock=True)["errors"]
            self.assertIn(
                "frozen source mismatch: experiments/runners/crewai_tool_sequence_v11.py",
                errors,
            )
            self.assertIn("tool-sequence gate declaration mismatch", errors)


class CrewAIRunnerV11FreezeTest(unittest.TestCase):
    """The paid freeze must describe the paid batch and hash every source."""

    def test_paid_freeze_matches_sources_and_manifest(self) -> None:
        frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("crewai-two-role-r11-toolseq-multitask-01", frozen["batch"])
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
        self.assertEqual(independent.MECHANISM_GUARANTEES, frozen["mechanism_guarantees"])

    def test_paid_batch_directory_does_not_exist_before_the_run(self) -> None:
        if PAID_BATCH.exists():
            self.assertTrue((PAID_BATCH / "results.jsonl").is_file())
        else:
            self.assertFalse(PAID_BATCH.exists())


if __name__ == "__main__":
    unittest.main()
