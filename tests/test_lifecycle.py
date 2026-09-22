import unittest

from context_pruner import ContextLifecycleManager, ContextStatus, TaskPhase


class ContextLifecycleManagerTest(unittest.TestCase):
    def _manager(self):
        return ContextLifecycleManager(task_state="调研上下文压缩方法并给出方案")

    def test_observe_generates_active_chunks(self):
        m = self._manager()
        snap = m.observe_turn({"role": "user", "content": "开始调研"})
        self.assertEqual(snap.turn, 1)
        self.assertTrue(snap.chunks)
        self.assertTrue(all(c.status == ContextStatus.ACTIVE for c in snap.chunks))

    def test_compress_marks_some_chunks_compressed(self):
        m = self._manager()
        turns = [
            {"role": "user", "content": "请调研上下文压缩方法并给出方案。"},
            {"role": "assistant", "content": "我先检索文献。"},
            {"role": "user", "content": "观察：search 结果：LLMLingua、语义摘要。"},
            {"role": "assistant", "content": "我计划总结三类方法。"},
        ]
        for t in turns:
            m.observe_turn(t)
        snap = m.compress()
        self.assertTrue(snap.tokens > 0)
        self.assertTrue(
            any(c.status == ContextStatus.COMPRESSED for c in snap.chunks)
        )

    def test_recover_restores_compressed_chunks(self):
        m = self._manager()
        for t in [
            {"role": "user", "content": "目标：调研压缩方法。"},
            {"role": "assistant", "content": "计划检索。"},
            {"role": "user", "content": "观察：结果包含 LLMLingua。"},
        ]:
            m.observe_turn(t)
        m.compress()
        snap = m.recover("test_reason")
        self.assertTrue(
            any(c.status == ContextStatus.RECOVERED for c in snap.chunks)
        )
        self.assertIn("test_reason", [r.detail for r in m.records])


if __name__ == "__main__":
    unittest.main()
