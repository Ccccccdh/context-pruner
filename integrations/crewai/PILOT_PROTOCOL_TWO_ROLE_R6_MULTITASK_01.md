# CrewAI r6 双角色多任务语义等价质量批（r5 缺陷修正版）

本批是 r5（`crewai-two-role-r5-multitask-01`）的**修正重跑**，使用新的实验 ID、新的任务文件、新的判据与审计冻结，r5 的目录、冻结与结果一律不改写、不重打分。

## 为什么需要 r6（r5 付费批暴露的两个合同缺陷）

r5 付费批 27/27 完成、177 次请求，但审计与逐样本复核显示两个**合同设计缺陷**，与压缩机制无关：

1. **补救路径拿不到早期事实**：r5 的补救提示只带最后一个工具观测，而交接要求 3 个事实（含第一个工具的事实），因此 9 个插件样本全部出现 `HandoffRecoveryError: recovery still lacks required facts`，插件臂 9/9 失败。
2. **答案合同不可满足**：r5 要求决策者一行内重复整段证据 JSON，而冻结行长上限是 300 字符；自然答案约 350–400 字符，导致 `none`/`native_summary` 也普遍 `answer_length` 失败（15/27）。此外 r5 要求把工具返回的 `"failed": 0` 自己改写成 `0 failed`，严格判据再据此判定，形成自相矛盾的合同。

r6 的修正（**只改合同与补救输入，不改臂、预算、门与判据语义**）：

- 补救提示携带**该角色记录到的全部工具观测**（按调用顺序），不再只带最后一个；
- 决策者最后调用的工具改为**答案事实校验工具**，其返回即答案事实（`verify_queue_replay` / `verify_capacity_headroom` / `verify_schema_target`），答案长度上限 450 字符；
- 不再要求模型改写 `"failed": 0`；`0 failed` ↔ `fail 0` 的语义等价仍由判据用例覆盖。

## 任务（与 r3/r4/r5 均不同的新事实集合）

| task_id | 角色 1 工具（按序各一次） | 角色 2 工具 | 决策 | 答案必须事实 | 禁用事实 |
|---|---|---|---|---|---|
| `queue_backlog_replay` | `check_pipeline_status`、`inspect_dead_letter_queue`、`verify_queue_replay` | `verify_queue_replay` | `REPLAY` | `us-west-2`、`pending_records 12400` | `us-east-1` |
| `region_failover` | `get_cluster_health`、`check_region_drain`、`verify_capacity_headroom` | `verify_capacity_headroom` | `FAILOVER` | `ap-south-1`、`45%` | `eu-central-1` |
| `schema_migration` | `inspect_schema_drift`、`run_contract_tests`、`verify_schema_target` | `verify_schema_target` | `GO` | `schema-42`、`"failed": 0` | `schema-41` |

全部为合成运维数据；不含真实仓库、生产系统或个人信息。

## 判据（两套，同时报告）

1. **严格判据**（`crewai_semantic_equivalence_v6.answer_rule`）：一行、行长、前缀、决策字段、事实字面量、禁用事实、交接事实齐全、两角色各一次指定工具、适配器结构安全字段全 0。
2. **前瞻性语义等价判据**：同事实、同决策、同工具证据下允许纯格式差异（`0 failed` ↔ `fail 0`、`45%` ↔ `45 percent`、千位分隔符、全角标点）；缺事实、错版本、错区域、错决策**永不**被规范化补救（四类反例先跑通再冻结）。

r5 与 r3/r4 一律**不**用 r6 判据重打分。

## 矩阵与预算

3 任务 × 3 重复 × 3 臂 = 27 样本；臂顺序按 `(task_index + repeat) mod 3` 轮换，重复间载荷完全一致。模型 `deepseek-v4-flash`、endpoint `https://api.deepseek.com`、温度 0、禁用 hidden thinking；provider soft/target/hard = 1200/900/3000（估算等价 3085/2314/7712）。Agent 输出 ≤512 token，摘要 ≤1024 token、每角色 ≤4 次，全局请求上限 200，失败请求同样计数。每样本落盘，失败保留并继续。

## 成本口径与边界

主成本为每臂完整总 token（agent 输入+输出+摘要输入+输出），失败与触顶样本全部进入配对；逐任务报告配对均值、正收益对数、输入侧差与输出侧差、任务间离散度。只有严格与语义两套判据都不下降时，才允许把节省称为"质量不劣的节省"。单批仍为模拟运维任务，不含真实代码修复或生产工作流；三宿主数据不得合并。
