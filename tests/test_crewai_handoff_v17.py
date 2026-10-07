"""Zero-API gate for the CrewAI r17 guarded controller (no paid request).

The gate proves, before any paid batch:

  1. the v17 rule refuses an **action-less** turn while the frozen tool table is unfinished
     -- including the four negative controls (prose claim, repeated tool, bare handoff,
     explicit ``Final Answer:`` marker) -- and never refuses a turn that carries an action
     or that arrives after the table is complete;
  2. the rule's falsification on the recorded r16 sequences holds (7/7 action-less early
     stops refused, 5/5 completed units untouched);
  3. the two r17 task files reuse nothing from r9-r16 as a literal, and every task passes
     the frozen r8 judge when the roles behave correctly;
  4. the r17 freeze pins the current bytes, is a self-hash fixed point, and the writer
     **refuses to overwrite** it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runners import crewai_handoff_v17_tasks as tasks_module  # noqa: E402
from experiments.runners import crewai_loop_guard_v17 as guard             # noqa: E402
from experiments.runners import crewai_semantic_equivalence_v8 as judge    # noqa: E402

FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R17_GUARDED_3ARM_01.json"
FALSIFICATION = ROOT / (
    "integrations/crewai/R17_TRIGGER_FALSIFICATION_ON_R16_20261005.json"
)
TASK_FILES = (
    ROOT / "tasks/stage5_autogen/natural_tasks_r17.json",
    ROOT / "tasks/stage5_autogen/natural_tasks_r17_confirmation.json",
)
OLD_TASK_FILES = (
    "tasks/stage5_autogen/natural_tasks.json",
    "tasks/stage5_autogen/natural_tasks_r5.json",
    "tasks/stage5_autogen/natural_tasks_r6.json",
    "tasks/stage5_autogen/natural_tasks_r7.json",
    "tasks/stage5_autogen/natural_tasks_r8.json",
    "tasks/stage5_autogen/natural_tasks_r10.json",
    "tasks/stage5_autogen/natural_tasks_r15.json",
    "tasks/stage5_autogen/natural_tasks_r16.json",
)
TOOLS = ("read_a", "read_b", "read_c", "read_d")


class TriggerRuleTest(unittest.TestCase):
    """The v17 predicate, before it is wired into the host loop."""

    def test_actionless_turn_is_refused_while_the_table_is_unfinished(self) -> None:
        bare = "HANDOFF carrier-5521 bind-lane-07 embed_ready true"
        self.assertFalse(guard.produces_action(bare))
        self.assertFalse(guard.is_final_answer(bare))
        self.assertEqual("reject_actionless", guard.classify_turn(bare, 1, len(TOOLS)))
        self.assertFalse(guard.accepts_turn(bare, 1, len(TOOLS)))

    def test_explicit_final_answer_is_a_special_case_of_the_same_rule(self) -> None:
        marked = "Thought: done.\nFinal Answer: HANDOFF carrier-5521"
        self.assertTrue(guard.is_final_answer(marked))
        self.assertFalse(guard.produces_action(marked))
        # The action-less rule already covers it: the marker is sufficient, not necessary.
        self.assertEqual("reject_actionless", guard.classify_turn(marked, 2, len(TOOLS)))

    def test_prose_claim_does_not_advance_the_count(self) -> None:
        messages = [
            {"role": "assistant", "content": "Action: read_a\nAction Input: {}"},
            {
                "role": "assistant",
                "content": "Thought: I already called read_b, read_c and read_d earlier.",
            },
        ]
        self.assertEqual(1, guard.completed_tool_rounds(messages, TOOLS))
        self.assertFalse(guard.produces_action(str(messages[1]["content"])))

    def test_repeated_tool_does_not_advance_the_count(self) -> None:
        messages = [
            {"role": "assistant", "content": "Action: read_a\nAction Input: {}"},
            {"role": "assistant", "content": "Action: read_a\nAction Input: {}"},
        ]
        self.assertEqual(1, guard.completed_tool_rounds(messages, TOOLS))
        # It still carries an action, so the v17 rule does not refuse it: the count simply
        # does not move, and the role must eventually produce an action-less turn to stop.
        self.assertTrue(guard.accepts_turn("Action: read_a", 1, len(TOOLS)))

    def test_complete_table_releases_every_turn(self) -> None:
        for response in ("Action: read_d", "HANDOFF carrier-5521", "Final Answer: x"):
            with self.subTest(response=response):
                self.assertTrue(guard.accepts_turn(response, len(TOOLS), len(TOOLS)))
                self.assertIn(
                    guard.classify_turn(response, len(TOOLS), len(TOOLS)),
                    ("complete_with_action", "complete_with_final_answer"),
                )

    def test_guard_message_is_only_defined_for_an_unfinished_table(self) -> None:
        message = guard.guard_message(TOOLS, 1)
        self.assertIn(guard.GUARD_PREFIX, message)
        self.assertIn("Still required: read_b, read_c, read_d", message)
        self.assertIn("Call read_b now", message)
        with self.assertRaises(ValueError):
            guard.guard_message(TOOLS, len(TOOLS))


class ReplayGuardTest(unittest.TestCase):
    """The mixin's loop, against a scripted provider (no host, no API)."""

    def _state(self):
        return guard.create_guard_state(TOOLS, enabled=True, max_rejections=2)

    def test_bare_handoff_is_refused_and_the_next_tool_is_taken(self) -> None:
        state = self._state()
        llm = guard.GuardedReplayCrewAILLM(
            [
                "Action: read_a\nAction Input: {}",
                "HANDOFF nothing to see",
                "Action: read_b\nAction Input: {}",
            ]
        )
        guard.register_guard_state(llm, state)
        messages: list[dict[str, str]] = [{"role": "user", "content": "task"}]
        first = llm.call(messages)
        self.assertIn("Action: read_a", first)
        messages.append({"role": "assistant", "content": first})
        second = llm.call(messages)
        self.assertIn("Action: read_b", second)
        self.assertEqual(1, state.rejections)
        self.assertEqual(1, state.actionless_rejections)
        self.assertEqual(0, state.final_answer_rejections)
        self.assertEqual("reject_actionless", state.rejection_records[0]["reason"])
        self.assertEqual(1, state.rejection_records[0]["completed_rounds"])
        self.assertFalse(state.exhausted)

    def test_releasing_a_state_removes_it_from_the_registry(self) -> None:
        # The registry is keyed by id(llm); a new object can reuse a freed address, so the
        # runner must release its state when a sample closes.  This asserts that contract.
        llm = guard.GuardedReplayCrewAILLM(["HANDOFF x"])
        state = guard.create_guard_state(TOOLS, enabled=True)
        self.assertIs(state, guard.register_guard_state(llm, state))
        self.assertIs(state, guard.guard_state_for(llm))
        guard.release_guard_state(llm)
        self.assertIsNone(guard.guard_state_for(llm))
        # With no state the guard is inert: the response passes through untouched.
        self.assertEqual("HANDOFF x", llm.call([{"role": "user", "content": "t"}]))
        self.assertEqual(0, state.rejections)

    def test_disabled_state_is_inert(self) -> None:
        state = guard.create_guard_state(TOOLS, enabled=False)
        llm = guard.GuardedReplayCrewAILLM(["HANDOFF x", "HANDOFF y"])
        guard.register_guard_state(llm, state)
        messages = [{"role": "user", "content": "task"}]
        self.assertEqual("HANDOFF x", llm.call(messages))
        self.assertEqual("HANDOFF y", llm.call(messages))
        self.assertEqual(0, state.rejections)


class FalsificationTest(unittest.TestCase):
    """The recorded r16 evidence the r17 rule was designed against."""

    def test_every_actionless_early_stop_is_refused(self) -> None:
        data = json.loads(FALSIFICATION.read_text(encoding="utf-8"))
        check = data["checks"]["a_all_actionless_early_stops_refused"]
        self.assertEqual(7, check["units"])
        self.assertEqual(7, check["refused"])
        self.assertTrue(check["all_refused"])

    def test_no_false_rejection_on_completed_units(self) -> None:
        data = json.loads(FALSIFICATION.read_text(encoding="utf-8"))
        check = data["checks"]["b_no_false_rejection_on_completed_units"]
        self.assertEqual(5, check["units"])
        self.assertTrue(check["no_false_rejection"])
        self.assertTrue(check["action_turns_accepted"])

    def test_non_plugin_arms_carry_no_guard(self) -> None:
        data = json.loads(FALSIFICATION.read_text(encoding="utf-8"))
        check = data["checks"]["c_zero_triggers_on_non_plugin_arms"]
        self.assertEqual(0, check["guard_enabled_units"])
        self.assertEqual(0, check["recorded_rejections"])
        self.assertGreater(
            check["units_whose_closing_turn_the_rule_would_refuse_if_it_were_installed"], 0
        )


class R17TaskSetTest(unittest.TestCase):
    """Both r17 task files: novel against r9-r16 and valid for the frozen judge."""

    def _old_values(self) -> tuple[set[str], set[str], set[str], set[str]]:
        tasks: list[dict] = []
        for name in OLD_TASK_FILES:
            path = ROOT / name
            if not path.is_file():
                continue
            for task in json.loads(path.read_text(encoding="utf-8")):
                if isinstance(task, dict) and "task_id" in task:
                    tasks.append(task)
        ids = {str(task["task_id"]) for task in tasks}
        tools = {
            str(tool["name"]) for task in tasks for tool in task.get("tools") or []
            if isinstance(tool, dict)
        }
        decisions = {str(task.get("decision")) for task in tasks}
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

        walk(tasks)
        return ids, tools, decisions, set(found)

    def test_both_task_files_reuse_nothing_from_r9_r16(self) -> None:
        old_ids, old_tools, old_decisions, old_values = self._old_values()
        shared = {"failed 0"}
        for path in TASK_FILES:
            tasks = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(4, len(tasks), path.name)
            for task in tasks:
                task_id = str(task["task_id"])
                with self.subTest(task=task_id):
                    self.assertNotIn(task_id, old_ids)
                    self.assertNotIn(str(task["decision"]), old_decisions)
                    self.assertEqual(4, len(task["tools"]))
                    for tool in task["tools"]:
                        self.assertNotIn(str(tool["name"]), old_tools)
                    for field in ("handle_facts", "answer_facts", "expected_terms"):
                        for literal in task[field]:
                            if str(literal) in shared:
                                continue
                            self.assertNotIn(str(literal), old_values, f"{task_id}:{literal}")
                    for field in ("region_fact", "version_fact"):
                        value = str(task.get(field) or "")
                        if value:
                            self.assertNotIn(value, old_values, f"{task_id}:{field}")

    def test_every_task_passes_the_frozen_judge(self) -> None:
        for path in TASK_FILES:
            tasks = judge.load_tasks(path)
            for task in tasks:
                task_id = str(task["task_id"])
                with self.subTest(task=task_id):
                    handoff = "HANDOFF " + " ".join(
                        str(fact) for fact in task["handle_facts"]
                    )
                    handle_verdict = judge.judge_handoff(
                        judge.handle_rule_for(tasks, task_id), handoff
                    )
                    self.assertEqual([], handle_verdict.semantic["missing_facts"])
                    self.assertTrue(handle_verdict.semantic["one_line"])
                    evidence = (
                        judge.tool_evidence(task)[-1].replace('": ', '=').replace('"', "")
                    )
                    answer_verdict = judge.judge_answer(
                        judge.answer_rule_for(tasks, task_id),
                        f"RESULT task={task_id} decision={task['decision']} "
                        f"evidence={evidence}",
                    )
                    self.assertTrue(answer_verdict.strict_pass,
                                    answer_verdict.strict.get("issues"))
                    self.assertTrue(answer_verdict.semantic_pass,
                                    answer_verdict.semantic.get("issues"))

    def test_filler_scale_is_recorded_per_task(self) -> None:
        for path in TASK_FILES:
            for task in json.loads(path.read_text(encoding="utf-8")):
                with self.subTest(task=task["task_id"]):
                    scale = tasks_module.filler_scale(task, 0)
                    self.assertEqual(tasks_module.FILLER_PAIRS, scale["filler_pairs"])
                    self.assertEqual(23, scale["messages"])
                    self.assertGreater(scale["characters"], 1500)


class ArmScopeTest(unittest.TestCase):
    """The r17 arm-scope constraint: one controller configuration for every arm."""

    ARM_SCOPE = ROOT / "integrations/crewai/R17_ARM_SCOPE_CHECK_ON_R16_20261005.json"
    WORDING = ROOT / (
        "integrations/crewai/R17_ATTRIBUTION_WORDING_TEMPLATE_20261005.json"
    )

    def test_guard_is_inert_on_every_recorded_non_plugin_unit(self) -> None:
        data = json.loads(self.ARM_SCOPE.read_text(encoding="utf-8"))
        self.assertEqual(24, data["non_plugin_units"])
        self.assertEqual(0, data["non_plugin_refusals_total"])
        self.assertEqual([], data["non_plugin_units_with_a_refusal"])
        self.assertGreater(data["plugin_refusals_total"], 0)

    def test_runner_installs_the_guard_on_every_arm_by_default(self) -> None:
        source = (
            ROOT / "experiments/runners/run_crewai_handoff_v16.py"
        ).read_text(encoding="utf-8")
        self.assertIn("GUARD_ALL_ARMS = True", source)
        self.assertIn('"--guard-scope"', source)
        self.assertIn('"guard_installed_arms"', source)
        self.assertIn('"guard_enabled_all_arms"', source)
        # The default must not depend on the arm name any more.
        self.assertIn("guard_scope_first_role or method == \"pruner_v1\"", source)

    def test_audit_treats_a_non_plugin_rejection_as_the_stop_condition(self) -> None:
        source = (
            ROOT / "experiments/audits/audit_crewai_handoff_v16.py"
        ).read_text(encoding="utf-8")
        self.assertIn("guard_installed_arms", source)
        self.assertIn("r17 stop condition", source)

    def test_report_wording_names_the_host_configuration(self) -> None:
        wording = json.loads(self.WORDING.read_text(encoding="utf-8"))
        control = wording["control_configuration_sentence"]
        allowed = wording["allowed_pass_wording"]
        boundaries = " ".join(wording["boundary_sentences"])
        stop = wording["stop_condition"]
        # The configuration sentence must name the guard AND the host configuration.
        self.assertIn("工具轮守卫的主机配置", control)
        self.assertIn("宿主侧控制器配置", control)
        self.assertIn("guard_scope", control)
        # The pass wording must be conditioned on that configuration.
        self.assertIn("工具轮守卫的主机配置", allowed)
        self.assertNotEqual(control, allowed)
        # The forbidden list must reject the two attributions this round rules out.
        forbidden = " ".join(wording["forbidden_pass_wording"])
        self.assertIn("压缩单独省了", forbidden)
        self.assertIn("四判据达成", forbidden)
        # The boundaries must carry the inertness claim and the stop condition.
        self.assertIn("空操作", boundaries)
        self.assertIn("停止", boundaries)
        self.assertIn("non-plugin arm", stop["trigger"])
        self.assertIn("do not use the batch as plugin-effect evidence", stop["action"])
        self.assertIn("stop the batch immediately", stop["action"])
        self.assertEqual(
            "integrations/crewai/R17_ARM_SCOPE_CHECK_ON_R16_20261005.json",
            wording["pre_payment_evidence"]["artifact"],
        )


class R17FreezeTest(unittest.TestCase):
    """The freeze pins the current bytes, and the writer refuses to overwrite."""

    def test_freeze_is_a_self_hash_fixed_point_over_current_sources(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        # The freeze file is never rewritten, so a source that moved after it was written
        # is carried by the amendment instead: the amendment's map is the authority.
        authority = dict(freeze["source_sha256"])
        amendment_path = FREEZE.with_name(
            FREEZE.stem + "_FREEZE_AMENDMENT_20261005.json"
        )
        if amendment_path.is_file():
            amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
            authority.update(amendment["pinned_sources_after_amendment"])
            # The freeze file's own bytes must not have changed since the amendment.
            self.assertEqual(
                amendment["freeze_file_unchanged_sha256"],
                hashlib.sha256(FREEZE.read_bytes()).hexdigest(),
            )
        for relative, expected in authority.items():
            if not expected:
                continue
            with self.subTest(source=relative):
                self.assertEqual(
                    expected, hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                )
        clone = json.loads(json.dumps(freeze))
        clone["freeze_sha256"] = ""
        clone["manifest"]["freeze_sha256"] = ""
        recomputed = hashlib.sha256(
            json.dumps(clone, ensure_ascii=False, indent=2).encode("utf-8")
        ).hexdigest()
        self.assertEqual(freeze["freeze_sha256"], recomputed)
        self.assertEqual(freeze["freeze_sha256"], freeze["manifest"]["freeze_sha256"])
        self.assertIs(False, freeze["manifest"]["citable_as_saving"])
        self.assertIs(False, freeze["manifest"]["citable_as_quality_equivalence"])
        self.assertEqual(336, freeze["manifest"]["max_api_requests"])
        self.assertEqual(["pruner_v1"], freeze["manifest"]["guard_arms"])

    def test_writer_refuses_to_overwrite_a_written_freeze(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "write_crewai_r17_freeze", ROOT / ".tooling/write_crewai_r17_freeze.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        existing = ROOT / "tasks/stage5_autogen/natural_tasks_r17.json"
        with self.assertRaises(SystemExit) as caught:
            module.refuse_existing(existing)
        message = str(caught.exception)
        self.assertIn("refusing to overwrite an existing freeze", message)
        self.assertIn("FREEZE_AMENDMENT", message)
        # A path that does not exist is allowed to be written.
        self.assertIsNone(module.refuse_existing(ROOT / ".tooling/tmp/nonexistent-freeze.json"))


if __name__ == "__main__":
    unittest.main()
