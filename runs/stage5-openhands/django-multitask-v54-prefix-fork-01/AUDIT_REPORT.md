# v54 同前缀分叉小试独立审计

计划 1 个共同前缀 × 3 臂；实际保存 3/3 分叉。完整=True。

共同前缀：宿主目标测试 通过；Agent 请求 18，完整总 token 247,769。

| 分叉 | 臂 | 分叉后完整总 token | 全程（前缀计一次） | 分叉后请求 | 携带前缀请求 | 宿主测试 | 文件边界 | 压缩次数 |
|---|---|---:|---:|---:|---:|---|---|---:|
| branch-0-none | none | 73,817 | 321,586 | 3 | 18 | 通过 | 是 | 0 |
| branch-1-native_summary | native_summary | 107,660 | 355,429 | 4 | 18 | 通过 | 是 | 0 |
| branch-2-pruner_v1 | pruner_v1 | 105,773 | 353,542 | 4 | 18 | 通过 | 是 | 0 |

## 审计核查

- 冻结源哈希：85/90 一致；仅审计工具在门控后修复：['integrations/openhands/audit_validation_v54.py', 'integrations/openhands/budget_policy_v54.py', 'integrations/openhands/fork_tools_v54.py', 'integrations/openhands/run_validation_v54.py', 'tests/test_openhands_v54_formal_runner_gate.py']
- 冻结 `prefix-snapshot` 完整哈希：20128 文件一致
- 每个分叉均在**同一绝对路径**从只读快照恢复并重新做完整哈希比较；恢复后按分叉自身记录的改动重建，再独立重跑宿主目标测试（见各行 `host_verdict_source`）
- 文件边界违规：0；失败分支请求：0；共享 36 次请求上限：全部尊重
- 插件分叉压缩次数：0；机制是否触发：False
- 凭据匹配：0（扫描 317 个生成物文本文件）

## 解释边界

本批只有 1 个共同前缀、1 个真实任务，**不能**估计插件在任务总体上的平均效果，也不能外推到其他宿主。分叉后成本是分支自身的 provider 总 token（含所有摘要请求）；全程成本把共同前缀各计一次，因此没有任何一臂被重复计入前缀费用。
若插件分叉未发生压缩，本批只能证明流程可运行；若质量或成本不满足，先改机制。
