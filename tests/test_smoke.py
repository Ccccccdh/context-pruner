"""冒烟测试：不依赖 LLM API，验证全流程可跑通。"""

import unittest

from context_pruner import ContextPruner


class PipelineSmokeTest(unittest.TestCase):
    def test_smoke(self) -> None:
        turns = [
            {"role": "system", "content": "你是一个科研助理，需按步骤完成任务并给出结论。"},
            {"role": "user", "content": "请调研上下文压缩相关工作并给出方案。"},
            {"role": "assistant", "content": "我计划先检索文献，再总结三类方法，最后给出建议。"},
            {"role": "tool", "content": "search(结果): LLMLingua, LongLLMLingua, 语义摘要法。"},
            {"role": "user", "content": "结论：采用熵减+语义相关性方案，进入实验阶段。"},
        ]
        result = ContextPruner().compress(
            turns,
            task_state="调研上下文压缩方法并给出方案",
        )

        self.assertLessEqual(result.tokens_after, result.tokens_before)
        self.assertGreaterEqual(result.compression_ratio, 0)
        self.assertTrue(all(c.importance is not None for c in result.chunks))


if __name__ == "__main__":
    unittest.main()
