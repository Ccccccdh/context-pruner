# CrewAI r5 双角色多任务语义等价质量批

本批使用**未参与 r3/r4 开发**的新任务、新事实、新工具、新区域与新版本标识（`tasks/stage5_autogen/natural_tasks_r5.json`），在固定的三臂结构上同时报告**冻结严格质量**与**前瞻性语义等价质量**。

## 任务

| task_id | 工具 | 决策 | 必须保留的事实 | 禁用事实 |
|---|---|---|---|---|
| `queue_backlog_replay` | `check_pipeline_status`、`inspect_dead_letter_queue`、`check_log_delivery` | `REPLAY` | `12400`、`us-west-2` | `us-east-1` |
| `region_failover` | `get_cluster_health`、`check_region_drain`、`verify_capacity_headroom` | `FAILOVER` | `ap-south-1`、`45%` | `eu-central-1` |
| `schema_migration` | `inspect_schema_drift`、`run_contract_tests`、`check_migration_window` | `GO` | `schema-42`、`0 failed`、`22:00 UTC` | `schema-41` |

三个任务都是模拟运维双角色流程：`Evidence investigator` 先按顺序对每个工具各调用一次，再输出一行 `HANDOFF`；`Operations decision maker` 调用本任务的最后一个工具后输出一行 `RESULT task=... decision=... evidence=...`。工具返回、区域、版本与计数全部为冻结的合成数据，不含真实仓库、生产系统或个人信息。

## 判据（两套，同时报告）

1. **严格判据**：一行、前缀与长度、决策字段、事实字面量逐字包含、禁用事实不出现、交接事实齐全、两角色各调用指定工具、适配器结构安全字段全为 0。缺失事实最多触发**一次**无工具补救（补救请求计入本臂与全局账本）。
2. **前瞻性语义等价判据**（`crewai_semantic_equivalence_v5.py`）：同事实、同决策、同工具证据下，允许纯格式差异（`0 failed` ↔ `fail 0`、`45%` ↔ `45 percent`、千位分隔符、全角标点）。**缺事实、错版本、错区域、错决策绝不能被规范化补救**，四种反例先在 `tests/test_crewai_semantic_equivalence_v5.py` 通过后才冻结。

旧批（r3/r4）**不**用新判据重新打分；r4 保持 8/9 原样。

## 运行矩阵与预算

3 任务 × 3 次固定同输入重复 × 3 臂（`none`、`native_summary`、`pruner_v1`）= 27 样本。臂顺序按 `(task_index + repeat) mod 3` 轮换，重复之间载荷完全一致。模型 `deepseek-v4-flash`、endpoint `https://api.deepseek.com`、温度 0、**禁用隐藏 thinking**；provider soft/target/hard 为 1200/900/3000（估算等价 3085/2314/7712）。每 Agent 输出最多 512 token，摘要最多 1024 token、每角色最多 4 次；全批所有 Agent、摘要与补救请求合计上限 **200**，失败请求同样计数。每样本落盘，失败样本保留并继续（提供商不可用或触顶才中止）。

## 成本口径

主成本为每臂**完整总 token**：agent 输入 + agent 输出 + 摘要输入 + 摘要输出，失败样本与触顶样本全部进入配对。逐任务报告配对均值、正收益对数、输入侧差与输出侧差，并给出任务间离散度；只有严格与语义两个判据都不下降时，才允许把节省称为"质量不劣的节省"。

## 边界

单批仍是模拟运维任务，不含真实代码修复、生产工作流或跨宿主结论；三宿主数据与百分比不得合并。
