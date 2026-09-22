"""事件级检查点和反事实分支评估测试。"""

import unittest

from context_pruner import (
    BranchOutcome,
    ContextBudget,
    ContextLifecycleManager,
    ContextPrunerV1,
    LifecycleEvent,
    evaluate_checkpoint,
)


class CheckpointTest(unittest.TestCase):
    def _compressed_manager(self) -> ContextLifecycleManager:
        manager = ContextLifecycleManager(
            task_state="保留项目代号 Aurora-17",
            pruner=ContextPrunerV1(),
        )
        manager.observe_turn(
            {"role": "user", "content": "硬约束：项目代号 Aurora-17 必须保留。"}
        )
        for index in range(6):
            manager.observe_turn(
                {"role": "assistant", "content": f"普通分析 {index}。" * 30}
            )
        manager.compress(budget=ContextBudget(160, 220, 140))
        return manager

    def test_lifecycle_captures_both_branches(self):
        manager = self._compressed_manager()
        checkpoint = manager.checkpoint_store.latest()

        self.assertIsNotNone(checkpoint)
        self.assertEqual(1, manager.snapshot().checkpoint_count)
        self.assertGreaterEqual(checkpoint.baseline_tokens, checkpoint.treated_tokens)
        self.assertTrue(checkpoint.actions)
        self.assertIn(
            LifecycleEvent.CHECKPOINTED,
            [record.event for record in manager.records],
        )

    def test_evaluation_is_paired_and_does_not_mutate_checkpoint(self):
        manager = self._compressed_manager()
        checkpoint = manager.checkpoint_store.latest()
        original_text = checkpoint.treated_chunks[0].text

        def evaluator(chunks, branch):
            text = "\n".join(chunk.text for chunk in chunks)
            chunks[0].text = "评测分支内的临时修改"
            return BranchOutcome(
                quality_score=float("Aurora-17" in text),
                token_cost=sum(chunk.token_count for chunk in chunks),
                success="Aurora-17" in text,
                metadata={"branch": branch},
            )

        result = evaluate_checkpoint(checkpoint, evaluator, quality_tolerance=0.0)

        self.assertTrue(result.accepted)
        self.assertEqual(0.0, result.quality_delta)
        self.assertLessEqual(result.token_delta, 0)
        self.assertEqual(
            original_text,
            manager.checkpoint_store.latest().treated_chunks[0].text,
        )

    def test_checkpoints_survive_lifecycle_state_round_trip(self):
        manager = self._compressed_manager()
        state = manager.export_state()
        restored = ContextLifecycleManager.from_state(
            state,
            pruner=ContextPrunerV1(),
        )

        self.assertEqual(manager.checkpoint_count, restored.checkpoint_count)
        self.assertEqual(
            manager.checkpoint_store.latest().checkpoint_id,
            restored.checkpoint_store.latest().checkpoint_id,
        )
        restored.observe_turn({"role": "assistant", "content": "继续分析。"})
        restored.compress(budget=ContextBudget(160, 220, 140))
        self.assertEqual("ckpt_000001", restored.checkpoint_store.latest().checkpoint_id)


if __name__ == "__main__":
    unittest.main()
