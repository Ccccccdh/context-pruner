# 阶段二任务集变更记录

## v0.3.11（2026-09-14）

来源实验：`runs/deepseek-real-v040-expanded/expanded-30x3-v040/`。

- `tool_chain_005`：问题中的“恢复目标”明确为“RPO 和 RTO 两项恢复目标”。原评分同时要求 RPO/RTO，但旧问题未明确恢复目标的数量，导致两种方法都可能在只取得 RPO 后合理地提前作答。
- `tool_chain_010`：同样明确要求 RPO 和 RTO 两项恢复目标，使问题、`golden_facts` 与 `answer_constraints.required` 对齐。
- `long_context_008` 的任务内容未修改。运行时 `search` 观察新增“已覆盖当前知识库”或“部分候选，可继续换关键词检索”覆盖语义，避免模型把部分检索结果误认为全部资料。

这些修改不改变任何文档内容或答案事实，同时作用于 `none` 和 `pruner_v1`。v0.3.10 及更早实验继续使用各自 manifest 中冻结的旧任务哈希，历史结果不覆盖、不与 v0.3.11 样本混合。
