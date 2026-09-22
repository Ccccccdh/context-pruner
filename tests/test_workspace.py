import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agent_demo import (
    LangGraphReActAgent,
    ScriptedWorkspaceLLM,
    WorkspaceEnvironment,
    load_tasks,
)
from context_pruner import ContextBudget, ContextPrunerV1
from experiments.runners.run_stage4a_experiment import main as run_stage4a


TASK_FILE = Path(__file__).parents[1] / "tasks" / "stage4a" / "tasks.json"
STAGE4B_TASK_FILE = Path(__file__).parents[1] / "tasks" / "stage4b" / "tasks.json"


class WorkspaceEnvironmentTest(unittest.TestCase):
    def task(self, task_id: str):
        return next(task for task in load_tasks(TASK_FILE) if task.task_id == task_id)

    def test_paths_cannot_escape_workspace(self):
        task = self.task("workspace_audit_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            result = env.execute("read", {"doc_id": "../tasks.json"})
            self.assertTrue(result.is_error)
            self.assertIn("非法工作区路径", result.output)

    def test_read_uses_trusted_evidence_prefix_and_keeps_source(self):
        task = self.task("workspace_audit_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            result = env.execute("read", {"doc_id": "config/service.toml"})
            self.assertTrue(result.output.startswith("read 结果："))
            self.assertIn("文件=config/service.toml", result.output)

    def test_search_snippet_becomes_provenance_aware_task_memory(self):
        task = self.task("workspace_audit_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            evidence = env.execute("search", {"keyword": "RPO", "path": "."})
            self.assertTrue(evidence.output.startswith("search 证据："))
            result = ContextPrunerV1().compress(
                [
                    {"role": "system", "content": "必须依据工具证据回答"},
                    {"role": "user", "content": task.question},
                    {"role": "user", "content": "观察：" + evidence.output},
                ],
                task_state=task.question,
                budget=ContextBudget(200, 300, 180),
            )
            memories = [
                item for item in result.chunks if item.metadata.get("structured_memory")
            ]
            self.assertEqual(1, len(memories))
            self.assertIn("RPO=15 分钟", memories[0].text)
            self.assertIn("docs/recovery.md:3", memories[0].text)

    def test_small_code_file_keeps_all_test_contract_lines_in_memory(self):
        task = self.task("workspace_code_repair_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            evidence = env.execute("read", {"doc_id": "tests/test_service.py"})
            self.assertGreater(len(evidence.output), 180)
            result = ContextPrunerV1().compress(
                [
                    {"role": "system", "content": "必须修复三个缺陷并运行测试"},
                    {"role": "user", "content": task.question},
                    {"role": "user", "content": "观察：" + evidence.output},
                ],
                task_state=task.question,
                budget=ContextBudget(800, 1_200, 700),
            )
            memory = next(
                item for item in result.chunks if item.metadata.get("structured_memory")
            )
            self.assertIn("test_total", memory.text)
            self.assertIn("test_retry_delay_is_capped", memory.text)
            self.assertIn("test_redaction", memory.text)

    def test_code_edit_and_real_test_validation(self):
        task = self.task("workspace_code_repair_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            for action in task.metadata["mock_plan"]:
                result = env.execute(action["name"], action.get("args", {}))
                self.assertFalse(result.is_error, result.output)
            validation = env.validate(task, task.metadata["mock_final_answer"])
            self.assertTrue(validation["success"])
            self.assertTrue(env.metrics()["tests_passed"])
            self.assertEqual(
                ["src/calculator.py", "src/redaction.py", "src/retry.py"],
                env.changed_files(),
            )
            ledger = env.tool_ledger(task.metadata["mock_plan"], [])
            self.assertIn("最近测试=PASS", ledger)
            self.assertIn("立即给出简洁 final_answer", ledger)
            self.assertEqual(
                "tests_already_current",
                env.redundant_action_reason(
                    {"name": "run_tests", "args": {}},
                    task.metadata["mock_plan"],
                    [],
                ),
            )

    def test_workspace_events_carry_versions_and_tests_become_stale_after_edit(self):
        task = self.task("workspace_code_repair_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            first = env.execute(
                "replace_text",
                {
                    "path": "src/calculator.py",
                    "old": "return max(values)",
                    "new": "return sum(values)",
                },
            )
            self.assertIn("file_version=1", first.output)
            self.assertIn("workspace_epoch=1", first.output)
            self.assertIn('new="return sum(values)"', first.output)
            self.assertIn("无需为确认本次替换而重新读取", first.output)
            failed_test = env.execute("run_tests", {})
            self.assertIn("verification 结果：FAIL; workspace_epoch=1", failed_test.output)
            second = env.execute(
                "replace_text",
                {
                    "path": "src/retry.py",
                    "old": "return max(base * (2 ** attempt), cap)",
                    "new": "return min(base * (2 ** attempt), cap)",
                },
            )
            self.assertIn("workspace_epoch=2", second.output)
            self.assertEqual("STALE", env.test_state())
            self.assertFalse(env.metrics()["tests_passed"])

    def test_workspace_tracks_post_mutation_reads_and_intermediate_tests(self):
        task = self.task("workspace_code_repair_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            edit = env.execute(
                "replace_text",
                {
                    "path": "src/calculator.py",
                    "old": "return max(values)",
                    "new": "return sum(values)",
                },
            )
            self.assertFalse(edit.is_error)
            ledger = env.tool_ledger(
                [
                    {
                        "name": "replace_text",
                        "args": {
                            "path": "src/calculator.py",
                            "old": "return max(values)",
                            "new": "return sum(values)",
                        },
                    }
                ],
                [],
            )
            self.assertIn("已确认修改版本", ledger)
            self.assertIn("src/calculator.py", ledger)
            self.assertIn("不要仅为确认写入而回读", ledger)
            env.execute("read", {"doc_id": "src/calculator.py"})
            env.execute("run_tests", {})
            env.execute("run_tests", {})
            metrics = env.metrics()
            self.assertEqual(1, metrics["post_mutation_read_count"])
            self.assertEqual(2, metrics["test_run_count"])
            self.assertEqual(1, metrics["intermediate_test_count"])

    def test_task_memory_invalidates_pre_mutation_reads_and_old_verification(self):
        result = ContextPrunerV1().compress(
            [
                {"role": "system", "content": "必须依据当前工作区状态回答"},
                {"role": "user", "content": "修复 calculator 并验证"},
                {
                    "role": "user",
                    "content": "观察：read 结果：文件=src/calculator.py\n0001: return max(values)",
                },
                {
                    "role": "user",
                    "content": "观察：verification 结果：PASS; workspace_epoch=0; exit=0",
                },
                {
                    "role": "user",
                    "content": "观察：mutation 结果：文件=src/calculator.py; file_version=1; workspace_epoch=1; 替换=1",
                },
                {
                    "role": "user",
                    "content": "观察：read 结果：文件=src/calculator.py\n0001: return sum(values)",
                },
                {
                    "role": "user",
                    "content": "观察：verification 结果：PASS; workspace_epoch=1; exit=0",
                },
            ],
            task_state="修复 calculator 并验证",
            budget=ContextBudget(220, 400, 220),
        )
        memory = next(item for item in result.chunks if item.metadata.get("structured_memory"))
        self.assertNotIn("return max(values)", memory.text)
        self.assertIn("return sum(values)", memory.text)
        self.assertIn("verification 结果：PASS; workspace_epoch=1", memory.text)
        self.assertEqual("PASS", memory.metadata["latest_verification_status"])
        self.assertEqual(1, memory.metadata["workspace_mutation_epoch"])
        self.assertEqual(2, memory.metadata["stale_evidence_filtered_count"])

    def test_segmented_reads_are_not_treated_as_duplicate(self):
        task = self.task("workspace_audit_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            first = {"name": "read", "args": {"doc_id": "README.md", "start_line": 1, "end_line": 1}}
            second = {"name": "read", "args": {"doc_id": "README.md", "start_line": 2, "end_line": 3}}
            self.assertIsNone(env.redundant_action_reason(first, [], []))
            self.assertIsNone(env.redundant_action_reason(second, [first], []))
            self.assertEqual(
                "already_read_range",
                env.redundant_action_reason(first, [first], []),
            )
            covering = {
                "name": "read",
                "args": {"doc_id": "README.md", "start_line": 1, "end_line": 200},
            }
            self.assertEqual(
                "already_read_range",
                env.redundant_action_reason(second, [covering], []),
            )

    def test_same_read_is_allowed_again_after_that_file_changes(self):
        task = self.task("workspace_code_repair_001")
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            read = {
                "name": "read",
                "args": {"doc_id": "src/calculator.py", "start_line": 1, "end_line": 200},
            }
            edit = {
                "name": "replace_text",
                "args": {
                    "path": "src/calculator.py",
                    "old": "return max(values)",
                    "new": "return sum(values)",
                },
            }
            self.assertIsNone(env.redundant_action_reason(read, [read, edit], []))

    def test_stage4b_expansion_uses_three_independent_workspaces(self):
        tasks = load_tasks(STAGE4B_TASK_FILE)
        self.assertEqual(3, len(tasks))
        self.assertEqual(3, len({task.scenario for task in tasks}))
        self.assertEqual(3, len({task.workspace for task in tasks}))

    def test_every_stage4b_mock_plan_reaches_expected_workspace_state(self):
        for task in load_tasks(STAGE4B_TASK_FILE):
            with self.subTest(task=task.task_id), tempfile.TemporaryDirectory() as directory:
                env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
                for action in task.metadata["mock_plan"]:
                    env.execute(action["name"], action.get("args", {}))
                validation = env.validate(task, task.metadata["mock_final_answer"])
                self.assertTrue(validation["success"], validation)
                self.assertEqual("PASS", env.test_state())


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "langgraph is optional")
class WorkspaceLangGraphTest(unittest.TestCase):
    def test_long_real_file_trace_runs_with_plugin(self):
        task = next(
            task
            for task in load_tasks(TASK_FILE)
            if task.task_id == "workspace_audit_001"
        )
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(task, Path(directory) / "workspace")
            result = LangGraphReActAgent(
                ScriptedWorkspaceLLM(task),
                env,
                max_turns=20,
                method="pruner_v1",
                context_budget=ContextBudget(700, 950, 550),
            ).run(task)
            self.assertTrue(result.success)
            self.assertEqual(16, result.num_turns)
            self.assertEqual(1.0, result.tool_correctness)
            self.assertTrue(result.environment_validation["success"])
            self.assertGreater(result.archive_count, 0)

    def test_stage4_runner_resumes_completed_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            args = [
                "--mock",
                "--tasks",
                str(TASK_FILE),
                "--task-ids",
                "workspace_audit_001",
                "--methods",
                "none,pruner_v1",
                "--max-turns",
                "20",
                "--context-soft-limit",
                "700",
                "--context-hard-limit",
                "950",
                "--context-target",
                "550",
                "--out",
                directory,
                "--experiment-id",
                "resume-check",
            ]
            root = run_stage4a(args)
            summary = root / "none" / "workspace_audit_001.r00.summary.json"
            modified = summary.stat().st_mtime_ns
            run_stage4a([*args, "--resume"])
            manifest = json.loads((root / "suite_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(modified, summary.stat().st_mtime_ns)
            self.assertEqual(1, manifest["resume_count"])
            self.assertEqual(2, manifest["sample_count"])

    def test_workspace_completion_guard_blocks_post_pass_tool_call(self):
        task = next(
            task
            for task in load_tasks(TASK_FILE)
            if task.task_id == "workspace_code_repair_001"
        )
        guarded_task = replace(
            task,
            metadata={
                **task.metadata,
                "mock_plan": [
                    *task.metadata["mock_plan"],
                    {"name": "read", "args": {"doc_id": "src/calculator.py"}},
                ],
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            env = WorkspaceEnvironment.materialize(
                guarded_task, Path(directory) / "workspace"
            )
            result = LangGraphReActAgent(
                ScriptedWorkspaceLLM(guarded_task),
                env,
                max_turns=20,
                method="pruner_v1",
                context_budget=ContextBudget(800, 1_100, 700),
            ).run(guarded_task)
            self.assertTrue(result.success)
            self.assertEqual(1, result.post_completion_tool_call_count)
            self.assertEqual(1, result.summary("test", "r00")["post_completion_tool_call_count"])
            self.assertEqual(15, len(result.actions))


if __name__ == "__main__":
    unittest.main()
