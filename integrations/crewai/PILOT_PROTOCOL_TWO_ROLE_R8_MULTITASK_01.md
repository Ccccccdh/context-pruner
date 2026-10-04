# CrewAI r8 双角色多任务语义等价质量批（判据修正版）

r5 → r6 → r7 → r8 的第四次也是本轮最后一次修正。r8 与 r7 使用**相同的任务、事实、工具、预算、三臂与采样矩阵**，只修正两条判据规则：r7 付费批（27/27 样本、192 请求）被这两条规则全批误判为失败。

## r7 付费批暴露的判据缺陷

1. **禁用事实规则把"正确的否定"当成违规**：任务历史约束写明"历史讨论里的 `eu-central-1` 不是本次备用区域"、`schema-41` 已下线。模型在答案里如实写出"`eu-central-1` not the standby region"、"`schema-41` retired"，判据却按"出现即违规"判失败，16 个样本因此 `answer_forbidden_fact`。
2. **事实字面量不跨 `=` 折叠**：模型写 `headroom=45%`，而合同字面量是 `headroom 45%`，严格判据因此报 `answer_missing_fact`。

r8 的两处修正：

- **禁用事实只在被"断言为当前值"时失败**：字面量必须出现在区域/版本族断言词（region、standby、target、drain、primary、version、schema、migrate）附近，且附近没有否定/退役词（not、never、retired、superseded、former、rejected…）。把历史值当当前值写入仍然失败（反例测试 `test_asserted_historical_value_is_still_a_forbidden_hit`）。
- **`=` 与标点、引号一并作为可折叠的分隔符**，`headroom=45%`、`headroom 45%`、`"headroom": "45%"` 视为同一事实。

缺事实、错版本、错区域、错决策仍然永不被规范化补救；四类反例测试在 `tests/test_crewai_semantic_equivalence_v8.py` 中先行通过后才冻结。

## 任务（与 r7 完全相同，未参与 r3/r4/r5 开发）

| task_id | 角色 1 工具 | 角色 2 工具 | 决策 | 答案必须事实 |
|---|---|---|---|---|
| `queue_backlog_replay` | `check_pipeline_status`、`inspect_dead_letter_queue`、`verify_queue_replay` | `verify_queue_replay` | `REPLAY` | `pending_records 12400`、`us-west-2` |
| `region_failover` | `get_cluster_health`、`check_region_drain`、`verify_capacity_headroom` | `verify_capacity_headroom` | `FAILOVER` | `ap-south-1`、`headroom 45%` |
| `schema_migration` | `inspect_schema_drift`、`run_contract_tests`、`verify_schema_target` | `verify_schema_target` | `GO` | `schema-42`、`failed 0` |

## 矩阵、预算与成本口径

3 任务 × 3 重复 × 3 臂 = 27 样本；臂顺序轮换、重复间载荷一致；`deepseek-v4-flash`、温度 0、禁用 thinking；provider soft/target/hard = 1200/900/3000；Agent 输出 ≤512 token、摘要 ≤1024 token/角色 ≤4 次；全局请求上限 320；失败样本保留并继续；每样本落盘。主成本为完整总 token（agent 输入+输出+摘要输入+输出），失败与触顶样本全部进入配对；逐任务报告配对均值、正收益对数、输入/输出侧差与任务间离散度。只有严格与语义两套判据都不下降时才称为"质量不劣的节省"。单批为模拟运维任务，不含真实代码修复；三宿主数据不得合并。
