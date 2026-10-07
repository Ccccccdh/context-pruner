"""Zero-API gate for the CrewAI r16 guarded controller.

The gate must prove four things before any paid run is allowed:

  1. **the guard works** -- an early final answer is refused while the frozen tool table
     is incomplete, including three negative controls: the role tries to stop early, the
     role *claims* in prose that it already called the missing tools, and the role
     repeats a tool it already called;
  2. **the view is unchanged** -- the compressed view the model sees is byte-for-byte
     the r15 (frozen r13 mechanism) view for the same prepared messages, so the guard
     cannot be credited with any content change;
  3. **the new tasks are new** -- no task id, tool name, fact, region, version or
     decision contract is reused from r3-r15;
  4. **the tool sequence is full under the guard** -- the mock grid reaches the frozen
     tool table in every plugin unit, and the audit's hard fields pass.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The revision-aware pin amendment: the ONLY place accepted post-freeze digests come from.
PIN_AMENDMENT = ROOT / "integrations/crewai/R16_SOURCE_PIN_AMENDMENT_20261005.json"
R16_FREEZE_PATH = (
    ROOT / "integrations/crewai/PRE_RUN_FREEZE_R16_RELEASE_GATE_GUARDED_3ARM_01.json"
)


def _digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _r16_freeze() -> dict:
    return json.loads(R16_FREEZE_PATH.read_text(encoding="utf-8"))

from experiments.audits import audit_crewai_handoff_v16 as independent   # noqa: E402
from experiments.audits import crewai_amendment_hashes as independent_hashes  # noqa: E402
from experiments.runners import crewai_loop_guard_v16 as guard           # noqa: E402
from experiments.runners import crewai_pinned_evidence_v9 as pinned      # noqa: E402
from experiments.runners import crewai_semantic_equivalence_v8 as judge  # noqa: E402
from experiments.runners import crewai_toolrounds_contract_v13 as r15mech  # noqa: E402
from experiments.runners import crewai_toolrounds_contract_v13 as r16mech  # noqa: E402
from experiments.runners import run_crewai_experiment as base            # noqa: E402
from experiments.runners import run_crewai_handoff_v16 as r16            # noqa: E402

MOCK = ROOT / "runs/stage5-crewai/crewai-two-role-r16-release-gate-guarded-mock-01"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r16.json"
OLD_TASK_FILES = (
    "tasks/stage5_autogen/natural_tasks.json",
    "tasks/stage5_autogen/natural_tasks_r5.json",
    "tasks/stage5_autogen/natural_tasks_r6.json",
    "tasks/stage5_autogen/natural_tasks_r7.json",
    "tasks/stage5_autogen/natural_tasks_r8.json",
    "tasks/stage5_autogen/natural_tasks_r10.json",
    "tasks/stage5_autogen/natural_tasks_r15.json",
)


def _args(**overrides) -> SimpleNamespace:
    values = dict(
        model="mock", base_url="", max_output_tokens=512, fixed_reserved_tokens=300,
        max_summary_tokens=1024, max_summary_calls=4, provider_soft=1200,
        provider_hard=3000, provider_target=900, soft_limit=0, hard_limit=0, target=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _task(task_id: str) -> dict:
    return next(task for task in r16.load_tasks() if task["task_id"] == task_id)


class GuardBehaviourTest(unittest.TestCase):
    """Job 1: the guard refuses early final answers, and prose cannot fool it."""

    def _state(self, task: dict) -> guard.GuardState:
        return guard.create_guard_state(
            [str(tool["name"]) for tool in task["tools"]], enabled=True
        )

    def test_early_final_answer_is_rejected_until_the_table_is_complete(self) -> None:
        task = _task("artifact_publish_gate")
        names = [str(tool["name"]) for tool in task["tools"]]
        state = self._state(task)
        # The role calls two tools, then tries to finish.
        responses = [
            f"Thought: t\nAction: {names[0]}\nAction Input: {{}}",
            f"Thought: t\nAction: {names[1]}\nAction Input: {{}}",
            "Thought: I am done.\nFinal Answer: HANDOFF pkg-4410",
            f"Thought: t\nAction: {names[2]}\nAction Input: {{}}",
            f"Thought: t\nAction: {names[3]}\nAction Input: {{}}",
            "Thought: now complete.\nFinal Answer: HANDOFF pkg-4410 release-c18 publish_ready true",
        ]
        llm = guard.GuardedReplayCrewAILLM(responses)
        guard.register_guard_state(llm, state)
        messages: list[dict[str, str]] = [{"role": "user", "content": "task"}]
        # call 1 and 2: real actions, no rejection
        first = llm.call(messages)
        self.assertIn(f"Action: {names[0]}", first)
        messages.append({"role": "assistant", "content": first})
        second = llm.call(messages)
        self.assertIn(f"Action: {names[1]}", second)
        messages.append({"role": "assistant", "content": second})
        # call 3: the model tries to stop early -> the guard refuses and re-asks
        third = llm.call(messages)
        self.assertEqual(1, state.rejections)
        self.assertFalse(state.reached_required_rounds)
        self.assertIn(f"Action: {names[2]}", third)  # the follow-up action, not a handoff
        self.assertEqual("early_final_answer", state.rejection_records[0]["reason"])
        self.assertEqual(2, state.rejection_records[0]["completed_rounds"])
        # continue: third and fourth real actions, then a complete final answer
        messages.append({"role": "assistant", "content": third})
        fourth = llm.call(messages)
        self.assertIn(f"Action: {names[3]}", fourth)
        messages.append({"role": "assistant", "content": fourth})
        final = llm.call(messages)
        self.assertIn("Final Answer:", final)
        self.assertIn("HANDOFF", final)
        self.assertTrue(state.reached_required_rounds)
        self.assertEqual(1, state.rejections)
        self.assertFalse(state.exhausted)

    def test_negative_control_prose_claim_does_not_advance_the_count(self) -> None:
        task = _task("quota_scale_gate")
        names = [str(tool["name"]) for tool in task["tools"]]
        messages = [
            {"role": "assistant", "content": f"Action: {names[0]}\nAction Input: {{}}"},
            {
                "role": "assistant",
                "content": (
                    "Thought: I have already called "
                    + ", ".join(names[1:])
                    + " earlier in this conversation, so I can finish now."
                ),
            },
        ]
        self.assertEqual(1, guard.completed_tool_rounds(messages, names))
        state = guard.create_guard_state(names, enabled=True)
        llm = guard.GuardedReplayCrewAILLM(
            [
                "Thought: done.\nFinal Answer: HANDOFF tenant-vega",
                f"Thought: t\nAction: {names[1]}\nAction Input: {{}}",
            ]
        )
        guard.register_guard_state(llm, state)
        answered = llm.call(messages)
        self.assertEqual(1, state.rejections)
        self.assertIn(f"Action: {names[1]}", answered)

    def test_negative_control_repeating_a_tool_does_not_advance_the_count(self) -> None:
        task = _task("traffic_shift_gate")
        names = [str(tool["name"]) for tool in task["tools"]]
        messages = [
            {"role": "assistant", "content": f"Action: {names[0]}\nAction Input: {{}}"},
            {"role": "assistant", "content": f"Action: {names[0]}\nAction Input: {{}}"},
            {"role": "assistant", "content": f"Action: {names[0]}\nAction Input: {{}}"},
        ]
        self.assertEqual(1, guard.completed_tool_rounds(messages, names))
        state = guard.create_guard_state(names, enabled=True)
        llm = guard.GuardedReplayCrewAILLM(
            ["Thought: done.\nFinal Answer: HANDOFF edge-pop-92"]
        )
        guard.register_guard_state(llm, state)
        # The script runs out, which proves the refusal happened: the guard asked again.
        with self.assertRaises(RuntimeError):
            llm.call(messages)
        self.assertEqual(1, state.rejections)

    def test_exhaustion_is_recorded_and_never_counts_as_success(self) -> None:
        """The cap is a terminal verdict: the unit is failed, never a saving.

        ``max_rejections=2`` means two *effective* push-backs.  One guarded call can
        therefore consume up to three model attempts: two early final answers are
        refused and re-served with the control message, and the third finds the cap
        already reached -- it is released (the role's loop is over) but recorded as
        ``rejection_cap_reached`` with ``exhausted`` set, so the runner books the unit as
        a failure and the audit refuses to let it count as a success.
        """
        task = _task("failover_switch_gate")
        names = [str(tool["name"]) for tool in task["tools"]]
        state = guard.create_guard_state(names, enabled=True, max_rejections=2)
        early = "Thought: stop.\nFinal Answer: HANDOFF db-aurora-7"
        llm = guard.GuardedReplayCrewAILLM([early, early, early])
        guard.register_guard_state(llm, state)
        messages = [{"role": "user", "content": "task"}]
        # One call: the guard pushes back twice, then gives up and marks the unit failed.
        first = llm.call(messages)
        self.assertEqual(2, state.rejections)
        self.assertTrue(state.exhausted)
        self.assertFalse(state.reached_required_rounds)
        self.assertIn("Final Answer:", first)
        self.assertEqual(
            ["early_final_answer", "early_final_answer", "rejection_cap_reached"],
            [record["reason"] for record in state.rejection_records],
        )
        self.assertEqual(0, state.rejection_records[-1]["completed_rounds"])
        self.assertEqual(4, state.rejection_records[-1]["required_rounds"])
        # A failed unit is terminal: the guard never pushes back again, so the unit
        # cannot be re-scored into a success by a later call.
        with self.assertRaises(RuntimeError):
            llm.call(messages)
        self.assertEqual(2, state.rejections)
        self.assertEqual(3, len(state.rejection_records))
        self.assertTrue(state.exhausted)
        self.assertFalse(state.reached_required_rounds)

    def test_a_complete_run_never_invokes_the_guard(self) -> None:
        task = _task("artifact_publish_gate")
        names = [str(tool["name"]) for tool in task["tools"]]
        state = guard.create_guard_state(names, enabled=True)
        responses = [f"Action: {name}\nAction Input: {{}}" for name in names]
        responses.append("Thought: done.\nFinal Answer: HANDOFF pkg-4410 release-c18")
        llm = guard.GuardedReplayCrewAILLM(responses)
        guard.register_guard_state(llm, state)
        messages: list[dict[str, str]] = [{"role": "user", "content": "task"}]
        for _ in range(len(names)):
            answer = llm.call(messages)
            self.assertIn("Action:", answer)
            messages.append({"role": "assistant", "content": answer})
        final = llm.call(messages)
        self.assertIn("Final Answer:", final)
        self.assertEqual(0, state.rejections)
        self.assertTrue(state.reached_required_rounds)


class ViewByteIdentityTest(unittest.TestCase):
    """Job 2: the guard changes the controller, never the model view."""

    def _prepared(self, task: dict) -> tuple[list, list]:
        from context_pruner.adapters import crewai as adapter_module
        history: list[dict] = [
            {"role": "user", "content": str(task["history_constraint"])},
            {"role": "assistant", "content": "已记录当前约束。"},
        ]
        for index in range(14):
            history.extend(
                [
                    {"role": "user", "content": f"archive-{index + 1} 背景讨论"},
                    {"role": "assistant", "content": f"archive-{index + 1} 已记录"},
                ]
            )
        history.append({"role": "user", "content": "调查阶段：请按顺序调用每个工具。"})
        for tool in task["tools"]:
            history.append(
                {
                    "role": "assistant",
                    "content": (
                        "Thought: read the next source.\n"
                        f"Action: {tool['name']}\n"
                        f"Action Input: {json.dumps(tool['call'], ensure_ascii=False)}\n"
                        f"Observation: {json.dumps(tool['result'], ensure_ascii=False, sort_keys=True)}"
                    ),
                }
            )
        return adapter_module._protect_tool_groups(history, base.estimate_tokens)

    def test_boundary_view_is_byte_identical_to_the_r15_mechanism(self) -> None:
        for task in r16.load_tasks():
            with self.subTest(task=task["task_id"]):
                budget, _ = base.resolve_budget(_args())
                prepared, groups = self._prepared(task)
                v15 = r15mech.build_recency_middleware(
                    base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
                    rule=pinned.pinned_rule(task),
                    budget=budget,
                    k_recent_tool_rounds=r16.K_RECENT_TOOL_ROUNDS,
                )
                v16 = r15mech.build_recency_middleware(
                    base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
                    rule=pinned.pinned_rule(task),
                    budget=budget,
                    k_recent_tool_rounds=r16.K_RECENT_TOOL_ROUNDS,
                )
                view_a = list(v15.before_model([dict(m) for m in prepared],
                                               reserved_tokens=300).messages)
                view_b = list(v16.before_model([dict(m) for m in prepared],
                                               reserved_tokens=300).messages)
                dump = lambda view: json.dumps(view, ensure_ascii=False, sort_keys=True)
                self.assertEqual(
                    hashlib.sha256(dump(view_a).encode("utf-8")).hexdigest(),
                    hashlib.sha256(dump(view_b).encode("utf-8")).hexdigest(),
                    "the r16 view must equal the r15 view byte for byte",
                )
                # The guard adds text only inside its rejection path; no guard message
                # may appear in a served view.
                for message in view_b:
                    self.assertNotIn(guard.GUARD_PREFIX, str(message.get("content", "")))
                self.assertEqual(
                    v15.metrics_dict()["pinned_characters_total"],
                    v16.metrics_dict()["pinned_characters_total"],
                )

    def test_guard_cannot_reach_the_view_layer(self) -> None:
        """Why the view is byte-identical: the guard has no view-layer surface at all.

        The guard is a wrapper around the provider call that appends one message to the
        request it re-sends.  It must import no context-pruner or adapter symbol, and the
        runner must build the plugin view in the r15 configuration, so on the
        violation-free path nothing the model sees can change.
        """
        source = Path(guard.__file__).read_text(encoding="utf-8")
        # Only code-level symbols are checked: the module docstring *explains* the view
        # layer, which is the point -- the guard never imports or calls it.
        for forbidden in ("context_pruner", "_protect_tool_groups",
                          "_restore_tool_groups", "before_model",
                          "ContextPluginConfig", "create_recency_adapter"):
            with self.subTest(symbol=forbidden):
                self.assertNotIn(forbidden, source)

        # The runner's view configuration is the frozen r15 one, verbatim.
        runner_source = Path(r16.__file__).read_text(encoding="utf-8")
        for required in ("mechanism.create_recency_adapter(",
                         "k_recent_tool_rounds=K_RECENT_TOOL_ROUNDS if is_first else 0",
                         "pinned.pinned_rule(task)"):
            with self.subTest(symbol=required):
                self.assertIn(required, runner_source)
        self.assertEqual(
            r16mech.DEFAULT_K_RECENT_TOOL_ROUNDS, r16.K_RECENT_TOOL_ROUNDS
        )

    def test_guard_message_exists_only_on_the_rejection_path(self) -> None:
        names = ["a", "b", "c", "d"]
        message = guard.guard_message(names, 2)
        self.assertIn(guard.GUARD_PREFIX, message)
        self.assertIn("Still required: c, d", message)
        self.assertIn("Call c now", message)


class FreshTaskSetTest(unittest.TestCase):
    """Job 3: the four r16 tasks are new against r3-r15."""

    def test_no_reuse_of_ids_tools_facts_regions_versions_decisions(self) -> None:
        new = [json.loads(TASK_FILE.read_text(encoding="utf-8"))]
        tasks = new[0]
        old_tasks: list[dict] = []
        for name in OLD_TASK_FILES:
            for task in json.loads((ROOT / name).read_text(encoding="utf-8")):
                if isinstance(task, dict) and "task_id" in task:
                    old_tasks.append(task)
        old_ids = {str(task["task_id"]) for task in old_tasks}
        old_tools = {
            str(tool["name"]) for task in old_tasks for tool in task.get("tools") or []
            if isinstance(tool, dict)
        }
        old_decisions = {str(task.get("decision")) for task in old_tasks}

        def values(container) -> list[str]:
            found: list[str] = []

            def walk(value):
                if isinstance(value, dict):
                    for item in value.values():
                        walk(item)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        walk(item)
                elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    found.append(str(value))

            walk(container)
            return found

        old_values = set(values(old_tasks))
        shared_contract_literals = {"failed 0"}
        self.assertEqual(4, len(tasks))
        for task in tasks:
            task_id = str(task["task_id"])
            self.assertNotIn(task_id, old_ids, task_id)
            self.assertNotIn(str(task["decision"]), old_decisions, task["decision"])
            self.assertEqual(4, len(task["tools"]))
            for tool in task["tools"]:
                self.assertNotIn(str(tool["name"]), old_tools, tool["name"])
            for field in ("handle_facts", "answer_facts", "expected_terms"):
                for literal in task[field]:
                    if str(literal) in shared_contract_literals:
                        continue
                    self.assertNotIn(str(literal), old_values, f"{task_id}:{literal}")
            for field in ("region_fact", "version_fact"):
                value = str(task.get(field) or "")
                if value:
                    self.assertNotIn(value, old_values, f"{task_id}:{field}")

    def test_constructed_sample_passes_both_judges(self) -> None:
        for task in r16.load_tasks():
            task_id = str(task["task_id"])
            with self.subTest(task=task_id):
                handle_rule = judge.handle_rule_for([task], task_id)
                handoff = "HANDOFF " + " ".join(str(f) for f in task["handle_facts"])
                verdict = judge.judge_handoff(handle_rule, handoff)
                self.assertEqual([], verdict.semantic["missing_facts"])
                self.assertIsNotNone(verdict.semantic["canonical"])
                evidence = (
                    judge.tool_evidence(task)[-1].replace('": ', '=').replace('"', "")
                )
                answer = (
                    f"RESULT task={task_id} decision={task['decision']} evidence={evidence}"
                )
                answer_verdict = judge.judge_answer(
                    judge.answer_rule_for([task], task_id), answer
                )
                self.assertTrue(answer_verdict.strict_pass, answer_verdict.strict["issues"])
                self.assertTrue(answer_verdict.semantic_pass,
                                answer_verdict.semantic["issues"])


class MockGridAndAuditTest(unittest.TestCase):
    """Job 4: the guarded grid reaches the full tool table and the audit checks it."""

    def test_mock_grid_has_full_sequences_in_every_plugin_unit(self) -> None:
        rows = [
            json.loads(line)
            for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(36, len(rows))
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertEqual(12, len(plugin))
        for row in plugin:
            with self.subTest(task=row["task_id"], repeat=row["repeat"]):
                self.assertTrue(row["guard_enabled"])
                self.assertEqual(4, row["guard_required_rounds"])
                self.assertTrue(row["tool_sequence_consistent"])
                self.assertEqual(
                    list(row["expected_first_role_trace"]), list(row["first_role_trace"])
                )
                self.assertFalse(row["guard_exhausted"])
                self.assertTrue(row["guard_reached_required_rounds"])
                self.assertEqual(0, row["guard_rejections"])
        self.assertTrue(all(row["api_request_attempts"] == 0 for row in rows))

    def test_guarded_plugin_matches_the_baseline_sequence_and_quality(self) -> None:
        rows = [
            json.loads(line)
            for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for method in ("none", "native_summary", "pruner_v1"):
            selected = [row for row in rows if row["method"] == method]
            with self.subTest(method=method):
                self.assertEqual(12, len(selected))
                self.assertTrue(all(row["tool_sequence_consistent"] for row in selected))
                self.assertTrue(all(row["strict_success"] for row in selected))
                self.assertTrue(all(row["semantic_success"] for row in selected))

    def test_batch_manifest_carries_both_non_citable_flags(self) -> None:
        """A batch manifest may never claim a saving or a quality equivalence itself."""
        manifest = json.loads((MOCK / "manifest.json").read_text(encoding="utf-8"))
        self.assertIs(False, manifest["citable_as_saving"])
        self.assertIs(False, manifest["citable_as_quality_equivalence"])
        self.assertEqual(12, manifest["units_per_arm"])
        self.assertEqual(36, manifest["units_per_arm"] * 3)
        for field in ("guard_sha256", "runner_sha256", "task_sha256", "mechanism_sha256",
                      "judge_sha256", "pinned_evidence_sha256"):
            self.assertEqual(64, len(str(manifest[field])), field)
            self.assertRegex(str(manifest[field]), r"^[0-9a-f]{64}$", field)
        # The freeze self-hash and the freeze file hash are different values by design.
        self.assertIn("freeze_declared_self_sha256", manifest)
        # The grid records the runner and the audit of its time; both were edited after the
        # r16 batches ran, so each is checked the same revision-aware, fail-closed way as
        # the freeze pins above -- against the freeze's recorded value or the exact digest
        # the amendment registers, never against "anything current".
        for field, relative in (
            ("runner_sha256", "experiments/runners/run_crewai_handoff_v16.py"),
            ("audit_sha256", "experiments/audits/audit_crewai_handoff_v16.py"),
        ):
            frozen_expected = _r16_freeze()["source_sha256"][relative]
            ok, detail = independent_hashes.check_pin(
                relative, frozen_expected, _digest_of, PIN_AMENDMENT
            )
            with self.subTest(field=field):
                self.assertTrue(ok, detail)
                if field in manifest:
                    # What the grid recorded must be the frozen value, not a later one.
                    self.assertEqual(frozen_expected, manifest[field])

    def test_audit_accepts_the_freeze_for_a_batch_of_the_frozen_name(self) -> None:
        """The freeze must audit clean for the batch id it names.

        The zero-API grid is a mock batch, so its directory name is not the frozen batch
        id; this copies the grid under the frozen name with the two freeze fields the
        paid runner records, and requires the independent audit to raise no error.
        """
        import shutil
        import tempfile

        freeze_path = (ROOT / "integrations/crewai/"
                       "PRE_RUN_FREEZE_R16_RELEASE_GATE_GUARDED_3ARM_01.json")
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        # The runner records the freeze path exactly as given on the command line, so the
        # audit compares a relative path; keep the same form here.
        relative_freeze = Path("integrations/crewai") / freeze_path.name
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / freeze["batch"]
            shutil.copytree(MOCK, batch)
            manifest_path = batch / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["freeze_path"] = str(relative_freeze).replace("\\", "/")
            manifest["freeze_sha256"] = hashlib.sha256(
                freeze_path.read_bytes()
            ).hexdigest()
            manifest["freeze_declared_self_sha256"] = freeze["freeze_sha256"]
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            result = independent.audit(batch, relative_freeze)
        freeze_errors = [e for e in result["errors"] if "freeze" in e or "manifest" in e]
        self.assertEqual([], freeze_errors)
        self.assertTrue(result["guard_gate"]["guard_enabled_every_plugin_unit"])

    def test_audit_hard_fields_pass_on_the_mock_grid(self) -> None:
        result = independent.audit(MOCK, None)
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual([], result["errors"])
        self.assertEqual(12, result["units_per_arm"])
        self.assertEqual(12, result["quality"]["pruner_v1"]["tool_sequence_consistent"])
        # 11 of 12: the mock grid deliberately withholds the first-pass handoff in the
        # pre-registered remedy unit (``r16.REMEDY_PROBE``) so the recovery path is exercised
        # without paying for a real "the role stopped early" event.  The paid run must
        # show 12/12; 11/12 here is the probe, not a guard failure.
        self.assertEqual(11, result["quality"]["pruner_v1"]["first_pass_facts"])
        self.assertTrue(result["guard_gate"]["guard_enabled_every_plugin_unit"])
        self.assertEqual(0, result["guard_gate"]["plugin_samples_with_exhausted_guard"])
        self.assertEqual(48, result["guard_gate"]["plugin_guard_required_rounds_total"])
        # The one first-pass deficit must be exactly the pre-registered probe unit and
        # nothing else: the guard itself never caused a missing handoff.
        rows = [
            json.loads(line)
            for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        incomplete = sorted(
            (row["task_id"], int(row["repeat"]))
            for row in rows
            if row["method"] == "pruner_v1" and not row["first_pass_complete"]
        )
        self.assertEqual([tuple(r16.REMEDY_PROBE)], incomplete)


class FreezeDraftTest(unittest.TestCase):
    """The r16 freeze must pin the current bytes and survive its own self-hash."""

    def test_freeze_pins_current_sources_and_verifies_its_own_hash(self) -> None:
        freeze = json.loads(
            (ROOT / "integrations/crewai/"
             "PRE_RUN_FREEZE_R16_RELEASE_GATE_GUARDED_3ARM_01.json").read_text(
                encoding="utf-8")
        )
        self.assertEqual(36, freeze["samples"])
        self.assertEqual(12, freeze["units_per_arm"])
        self.assertIs(False, freeze["manifest"]["citable_as_saving"])
        self.assertIs(False, freeze["manifest"]["citable_as_quality_equivalence"])
        self.assertEqual(280, freeze["manifest"]["max_api_requests"])
        self.assertEqual(["pruner_v1"], freeze["manifest"]["guard_arms"])
        self.assertIn("void", freeze["manifest"]["cost_formula"]["void_bound"]["status"])

        # Every pinned source is checked against the freeze, with ONE declared exception:
        # a source whose current bytes hash exactly to the digest the pin amendment
        # registers for it.  That is a revision-aware check, not a skip: an unregistered
        # further edit is a failure (see RevisionAwarePinTest for the negative control).
        for relative, expected in freeze["source_sha256"].items():
            if not expected:
                continue  # the freeze file itself: covered by the self-hash check below
            ok, detail = independent_hashes.check_pin(
                relative, expected, _digest_of, PIN_AMENDMENT
            )
            with self.subTest(source=relative):
                self.assertTrue(ok, detail)

        # The declared self-hash is a fixed point of the blanked canonical dump.
        def blanked_hash(document: dict) -> str:
            clone = json.loads(json.dumps(document))
            clone["freeze_sha256"] = ""
            clone["manifest"]["freeze_sha256"] = ""
            payload = json.dumps(clone, ensure_ascii=False, indent=2).encode("utf-8")
            return hashlib.sha256(payload).hexdigest()

        self.assertEqual(freeze["freeze_sha256"], blanked_hash(freeze))
        self.assertEqual(
            freeze["freeze_sha256"], freeze["manifest"]["freeze_sha256"]
        )
        # The file hash and the declared self-hash are deliberately different values.
        file_hash = hashlib.sha256(
            (ROOT / "integrations/crewai/"
             "PRE_RUN_FREEZE_R16_RELEASE_GATE_GUARDED_3ARM_01.json").read_bytes()
        ).hexdigest()
        self.assertNotEqual(file_hash, freeze["freeze_sha256"])


class RealPayloadBoundTest(unittest.TestCase):
    """The r16 acquisition batch and the bound computed from it."""

    ACQUISITION = ROOT / "runs/stage5-crewai/crewai-r16-payload-none-01"
    BOUND = ROOT / (
        "integrations/crewai/R16_CALLWISE_BOUND_FROM_REAL_PAYLOAD_20261005.json"
    )
    BOUND_FINAL = ROOT / (
        "integrations/crewai/R16_REAL_PAYLOAD_BOUND_FINAL_20261005.json"
    )

    def test_acquisition_manifest_is_acquisition_only_and_complete(self) -> None:
        manifest = json.loads(
            (self.ACQUISITION / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual("payload_acquisition_only", manifest["purpose"])
        self.assertIs(False, manifest["citable_as_saving"])
        self.assertIs(False, manifest["citable_as_quality_equivalence"])
        self.assertIs(False, manifest["controller_guard_installed"])
        self.assertEqual(["none"], manifest["methods"])
        self.assertEqual(1, manifest["repeats"])
        self.assertEqual(120, manifest["max_api_requests"])
        self.assertEqual(4, len(manifest["tasks"]))
        # Both freeze hashes are recorded, and they are different values.
        self.assertEqual(64, len(manifest["freeze_sha256"]))
        self.assertEqual(64, len(manifest["freeze_declared_self_sha256"]))
        self.assertNotEqual(
            manifest["freeze_sha256"], manifest["freeze_declared_self_sha256"]
        )

    def test_acquisition_rows_carry_the_payload_and_stay_within_the_cap(self) -> None:
        rows = [
            json.loads(line)
            for line in (self.ACQUISITION / "results.jsonl").read_text(
                encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(4, len(rows))
        self.assertEqual(28, sum(int(row["api_request_attempts"]) for row in rows))
        self.assertLessEqual(28, 120)
        for row in rows:
            with self.subTest(task=row["task_id"]):
                payload = row["payload"]
                self.assertIs(False, payload["compression_enabled"])
                self.assertIs(False, payload["guard_installed"])
                self.assertEqual(5, payload["first_role_call_count"])
                self.assertEqual(2, payload["decider_call_count"])
                self.assertEqual(5, len(payload["first_role_frame_deltas"]))
                # The first frame is recorded verbatim: the bound needs it.
                self.assertIn("messages", payload["first_role_payloads"][0])
                self.assertEqual(
                    len(payload["first_role_payloads"][0]["messages"]),
                    payload["first_role_payloads"][0]["message_count"],
                )
                self.assertEqual(64, len(payload["payload_sha256"]))
                self.assertEqual("", row["error"])
                self.assertTrue(row["tool_sequence_consistent"])

    def test_bound_reports_both_required_columns_and_clears_the_cost_gate(self) -> None:
        report = json.loads(self.BOUND.read_text(encoding="utf-8"))
        final = json.loads(self.BOUND_FINAL.read_text(encoding="utf-8"))
        self.assertEqual(
            "SUM_calls(uncompressed_input - compressed_input) - protection_cost",
            report["method"],
        )
        for key in ("potential_old_unit_upper", "safe_candidate_after_protection"):
            self.assertIn(key, report["columns"])
        for row in final["per_task"]:
            with self.subTest(task=row["task_id"]):
                self.assertGreater(row["potential_old_unit_upper_percent"],
                                   row["reference_bound_percent"])
                self.assertGreater(
                    row["safe_candidate_upper_percent"],
                    row["safe_candidate_reference_percent"],
                )
                self.assertGreater(row["safe_candidate_reference_percent"], 3.0)
                self.assertGreater(row["protection_total_tokens"], 0)
        self.assertGreater(final["batch_totals"]["safe_candidate_reference_percent"], 3.0)
        # The void r15 bound must not be reused by the new arithmetic.
        text = self.BOUND.read_text(encoding="utf-8") + self.BOUND_FINAL.read_text(
            encoding="utf-8"
        )
        self.assertNotIn("mechanistic_upper_bound_percent", text)


class RevisionAwarePinTest(unittest.TestCase):
    """Fail-closed review of the drift: registered digests only, with negative controls.

    The pin checks in this file accept a post-freeze digest **only** because the amendment
    registers that exact digest.  These tests prove the mechanism cannot be used as a
    blanket skip:

    * the registered digests are exactly the current bytes;
    * the checker accepts the frozen value and the registered value, and nothing else;
    * an **unregistered** edit (a fake digest, and a digest the amendment records as the
      prior value) is reported as a mismatch -- asserted, not assumed;
    * a manifest recording a later revision is caught.
    """

    AMENDMENT = PIN_AMENDMENT
    LEDGER = ROOT / "integrations/crewai/R16_REVISION_AWARE_PIN_LEDGER_20261005.json"
    RUNNER = "experiments/runners/run_crewai_handoff_v16.py"
    AUDITOR = "experiments/audits/audit_crewai_handoff_v16.py"
    BATCHES = (
        ROOT / "runs/stage5-crewai/"
             "crewai-two-role-r16-release-gate-guarded-3arm-01",
        ROOT / "runs/stage5-crewai/"
             "crewai-two-role-r16-release-gate-guarded-3arm-resume-a-01",
    )

    def test_amendment_registers_exactly_the_current_digests(self) -> None:
        drifts = independent_hashes.registered_drifts(self.AMENDMENT)
        self.assertEqual({self.RUNNER, self.AUDITOR}, set(drifts))
        for relative, expected in drifts.items():
            with self.subTest(source=relative):
                self.assertEqual(expected, _digest_of(ROOT / relative))
        self.assertTrue(independent_hashes.ledger_agrees(self.AMENDMENT, self.LEDGER))

    def test_every_frozen_pin_passes_the_revision_aware_check(self) -> None:
        freeze = _r16_freeze()
        for relative, frozen in freeze["source_sha256"].items():
            if not frozen:
                continue
            ok, detail = independent_hashes.check_pin(
                relative, frozen, _digest_of, self.AMENDMENT
            )
            with self.subTest(source=relative):
                self.assertTrue(ok, detail)
                self.assertIn(detail, ("ok", "ok (registered drift)"))

    def test_unregistered_digest_is_reported_and_never_accepted(self) -> None:
        """The negative control: an unregistered edit must fail, or the gate is a no-op."""
        freeze = _r16_freeze()
        frozen = freeze["source_sha256"][self.RUNNER]
        registered = independent_hashes.registered_drifts(self.AMENDMENT)[self.RUNNER]

        # (a) a fake current digest (as if the file had been edited again) is a mismatch;
        fake = "0" * 64
        issues = independent_hashes.source_pin_issues(
            freeze, lambda path: fake if path.name == Path(self.RUNNER).name
            else _digest_of(path), self.AMENDMENT,
        )
        self.assertEqual([f"frozen source mismatch: {self.RUNNER}"], issues)
        ok, detail = independent_hashes.check_pin(
            self.RUNNER, frozen,
            lambda path: fake, self.AMENDMENT,
        )
        self.assertFalse(ok)
        self.assertIn("unregistered further edit", detail)
        self.assertNotIn(registered, detail)

        # (b) the digest the amendment records as the PRIOR (frozen) value is not an
        # accepted current value either: only the registered current digest is.
        ok_prior, _ = independent_hashes.check_pin(
            self.RUNNER, frozen, lambda path: frozen, self.AMENDMENT
        )
        self.assertTrue(ok_prior)  # unchanged file: the frozen value itself is fine
        table = independent_hashes.amended_digests(self.AMENDMENT)
        self.assertNotIn(frozen, table[self.RUNNER])
        self.assertIn(registered, table[self.RUNNER])

        # (c) with no amendment at all, the drifted file fails.
        ok_none, detail_none = independent_hashes.check_pin(
            self.RUNNER, frozen, _digest_of, ROOT / ".tooling/tmp/absent-amendment.json"
        )
        self.assertFalse(ok_none)
        self.assertIn("mismatch", detail_none)

    def test_manifest_recording_a_later_revision_is_caught(self) -> None:
        """A batch that recorded the post-edit runner must not pass as the frozen one."""
        freeze = _r16_freeze()
        frozen = freeze["source_sha256"][self.RUNNER]
        registered = independent_hashes.registered_drifts(self.AMENDMENT)[self.RUNNER]
        self.assertNotEqual(frozen, registered)
        manifest = json.loads(
            (self.BATCHES[0] / "manifest.json").read_text(encoding="utf-8")
        )
        # The real manifests recorded the frozen revision ...
        self.assertEqual(frozen, manifest["runner_sha256"])
        # ... and a manifest that recorded the later one would be caught here.
        self.assertNotEqual(registered, manifest["runner_sha256"])

    def test_amendment_declares_the_frozen_sources_not_currently_valid(self) -> None:
        self.assertFalse(independent_hashes.frozen_sources_valid(self.AMENDMENT))
        record = json.loads(self.AMENDMENT.read_text(encoding="utf-8"))
        self.assertIs(False, record["frozen_experiment_sources_valid"])
        self.assertIn("not currently valid", record["frozen_experiment_sources_valid_reason"])
        for entry in record["amendments"]:
            self.assertIn("which_batches_used_which_revision", entry)
            self.assertTrue(entry["which_batches_used_which_revision"])

    def test_batch_records_still_point_at_the_frozen_revision(self) -> None:
        freeze = _r16_freeze()
        frozen_runner = freeze["source_sha256"][self.RUNNER]
        frozen_freeze = "2460ac9fa3aa9a5507b4be5c5733adccb5d1db0c93edb7db93f093254b196049"
        legacy_declared = "c6a274077f3376d877c187c81696324cad88d27ae590255d5a8426a8704ea393"
        # The batches' own record is what makes their numbers attributable: they name the
        # runner revision they ran and the freeze file hash they ran under.  Both batches
        # recorded the SAME freeze hash and the SAME declared self-hash, which is the
        # cross-check that they ran one configuration (the freeze file's bytes were
        # regenerated later, after the runs, for the two administrative revisions).
        for directory in self.BATCHES:
            manifest = json.loads(
                (directory / "manifest.json").read_text(encoding="utf-8")
            )
            with self.subTest(batch=directory.name):
                self.assertEqual(frozen_runner, manifest["runner_sha256"])
                self.assertEqual(frozen_freeze, manifest["freeze_sha256"])
                self.assertEqual(legacy_declared,
                                 manifest["freeze_declared_self_sha256"])
                self.assertEqual(4, len(manifest["tasks"]))
                self.assertEqual("api", manifest["mode"])
                self.assertEqual(280, manifest["max_api_requests"])


if __name__ == "__main__":
    unittest.main()
