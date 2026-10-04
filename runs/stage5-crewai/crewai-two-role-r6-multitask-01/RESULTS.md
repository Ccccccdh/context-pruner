# CrewAI r6 双角色多任务（r5 缺陷修正版，未完成）

状态：2026-10-04 **中止**。27 个计划样本中只完成 **9 个**（全部为 `queue_backlog_replay`），共 **61 次 API 请求**，第 9 个样本（`queue_backlog_replay` repeat 2 `pruner_v1`）的决策角色遇到提供商连接错误：

```
APIConnectionError: Connection error.
```

r6 的 runner 出于安全设计在提供商连接/超时/认证/余额/限流错误时终止整批（与 r4/r5 相同），因此该实验目录 `results.jsonl` 只含 9 行、**不完整**，独立审计对该目录必然报 `complete: false`。本批不产生任何成本或质量结论，也不得与其它批次合并统计。

## 它仍然证明了什么

1. **r5 缺陷①已修**：r6 把该角色记录到的全部工具观测交给补救步骤（r5 只给最后一个）。r6 的 9 个样本中补救调用数为 0，交接事实齐全，没有出现 `HandoffRecoveryError`。
2. **暴露了第三个合同缺陷**：按 r6 合同（答案事实写成 JSON 原文，例如 `"pending_records": 12400`）判分时，**语义质量 8/9、严格质量 0/9**——模型的自然答案是 `pending_records=12400`，严格判据因此报 `answer_missing_fact`。这个缺陷属于**合同措辞**，不是压缩机制，也不是模型错误；r7/r8 已把事实字面量改成模型自然写法并让严格判据跨分隔符折叠。

## 逐样本（9 行，含失败）

| 样本 | 严格 | 语义 | 完整总 token | 压缩事件 | 错误 |
|---|---:|---:|---:|---:|---|
| `queue_backlog_replay` 0 `none` | — | ✅ | 8,331 | 0 | — |
| `queue_backlog_replay` 0 `pruner_v1` | — | ✅ | 5,729 | 4 | — |
| `queue_backlog_replay` 0 `native_summary` | — | ✅ | 10,042 | 0 | — |
| `queue_backlog_replay` 1 `pruner_v1` | — | ✅ | 5,791 | 4 | — |
| `queue_backlog_replay` 1 `native_summary` | — | ✅ | 10,609 | 0 | — |
| `queue_backlog_replay` 1 `none` | — | ✅ | 8,775 | 0 | — |
| `queue_backlog_replay` 2 `native_summary` | — | ✅ | 10,790 | 0 | — |
| `queue_backlog_replay` 2 `none` | — | ✅ | 9,199 | 0 | — |
| `queue_backlog_replay` 2 `pruner_v1` | ❌ | ❌ | 4,790 | 4 | `APIConnectionError`（决策角色未完成） |

（严格判据当时要求 JSON 字面量，故 9 行全部为 ❌；语义判据 8/9。）

## 边界

- 该目录**没有被审计**（样本不全），其 manifest 的 `mode` 为 `api`、`max_api_requests` 为 320，但只发出 61 次请求。
- 目录、冻结（`PRE_RUN_FREEZE_TWO_ROLE_R6_MULTITASK_01.json`）与结果保持原样，不重跑、不覆盖、不重打分；r7/r8 使用新实验 ID。
