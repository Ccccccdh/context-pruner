"""压缩方法接入的轻量单元测试（标准库 unittest，无需 pytest）。"""

import tempfile
import unittest
from pathlib import Path

from agent_demo.agent import RunRecord
from agent_demo.agent import _evaluate, _kirr, _partial_score
from agent_demo.env import Task
from agent_demo.history import HistoryManager
from agent_demo.logger import write_run


class HistoryCompressionTest(unittest.TestCase):
    def _history(self, method: str, **kwargs) -> HistoryManager:
        return HistoryManager(
            system_prompt="sys",
            task_text="请调研上下文压缩方法并给出结论。",
            method=method,
            **kwargs,
        )

    def _fill(self, h: HistoryManager, steps: int = 4) -> None:
        for i in range(steps):
            h.add("assistant", f"第 {i} 步思考与工具调用")
            h.add("user", f"观察：第 {i} 步结果")

    def test_window_keeps_only_recent_steps(self):
        h = self._history("window", window_steps=2)
        self._fill(h, steps=5)
        before = len(h.turns)
        h.apply_compression()
        self.assertEqual(len(h.turns), 4)
        self.assertLess(len(h.turns), before)
        self.assertEqual(h.turns[0]["content"], "第 3 步思考与工具调用")

    def test_summary_fallback_collapses_history(self):
        h = self._history("summary")
        self._fill(h, steps=3)
        h.apply_compression()
        self.assertIn("[历史摘要]", h.turns[0]["content"])
        self.assertTrue(len(h.turns) > 1)
        self.assertEqual(h.turns[1]["content"], "第 1 步思考与工具调用")

    def test_pruner_v0_runs_and_rebuilds_messages(self):
        h = self._history("pruner_v0")
        self._fill(h, steps=4)
        h.apply_compression()
        self.assertTrue(h.turns)
        self.assertTrue(all("role" in t and "content" in t for t in h.turns))

    def test_unknown_method_raises(self):
        h = self._history("not_a_method")
        with self.assertRaises(NotImplementedError):
            h.apply_compression()

    def test_recover_restores_full_history(self):
        h = self._history("window", window_steps=1)
        self._fill(h, steps=4)
        h.apply_compression()
        self.assertLess(len(h.turns), len(h.full_turns))
        self.assertTrue(h.recover("test"))
        self.assertEqual(h.turns, h.full_turns)
        self.assertEqual(h.recovery_count, 1)


class ScoringHelperTest(unittest.TestCase):
    def test_partial_score(self):
        from agent_demo.env import Task

        task = Task(task_id="t", scenario="s", question="q", golden_facts=["a", "b", "c"])
        self.assertAlmostEqual(_partial_score(task, "答案是 a 和 b"), 2 / 3)

    def test_kirr(self):
        messages = [{"role": "user", "content": "这里包含 LLMLingua 和 LongLLMLingua"}]
        self.assertAlmostEqual(_kirr(messages, ["LLMLingua", "语义摘要"]), 0.5)

    def test_fact_matching_tolerates_nonsemantic_modal_word(self):
        task = Task(
            task_id="t",
            scenario="s",
            question="q",
            golden_facts=["差异率为 0%"],
        )

        self.assertTrue(_evaluate(task, "校验差异率必须为 0% 才允许切流。"))
        self.assertFalse(_evaluate(task, "校验差异率必须为 1% 才允许切流。"))

    def test_fact_matching_accepts_table_separator_for_key_value(self):
        task = Task(
            task_id="t",
            scenario="s",
            question="q",
            golden_facts=["PORT=8443"],
        )
        self.assertTrue(_evaluate(task, "| PORT | 8443 | config/service.toml |"))

    def test_pass_fact_accepts_positive_chinese_status_but_not_failure(self):
        task = Task(task_id="t", scenario="s", question="q", golden_facts=["PASS"])
        self.assertTrue(_evaluate(task, "最终 3 个测试全部通过。"))
        self.assertTrue(_evaluate(task, "verification: passed"))
        self.assertFalse(_evaluate(task, "仍有测试未通过。"))
        self.assertFalse(_evaluate(task, "verification: failed"))


class LoggerTest(unittest.TestCase):
    def test_write_run_with_run_id(self):
        task = Task(task_id="t1", scenario="s", question="q", golden_facts=["a"])
        record = RunRecord(task=task, method="none", success=True, final_answer="a")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            write_run(out, record, run_id="r00")
            self.assertTrue((out / "t1.r00.summary.json").exists())
            self.assertTrue((out / "t1.r00.turns.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
