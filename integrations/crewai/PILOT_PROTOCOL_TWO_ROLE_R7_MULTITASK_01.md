# CrewAI r7 双角色多任务语义等价质量批（r5/r6 合同缺陷的最终修正版）

本批是 r5 → r6 → r7 三次修正后的付费确认批，使用新实验 ID、新任务文件、新判据与新审计冻结；r5、r6 的目录、冻结与结果一律不改写、不重打分。

## 修正链

| 批次 | 状态 | 发现的问题 |
|---|---|---|
| r5 `crewai-two-role-r5-multitask-01` | 27/27 样本、177 请求、审计 `complete: true`、**9 条错误** | ① 补救提示只带最后一个工具观测 → 9/9 插件样本 `HandoffRecoveryError`；② 答案合同要求逐字重复整段证据 JSON，超过冻结 300 字符行长 → `none`/`native_summary` 也大量 `answer_length` 失败 |
| r6 `crewai-two-role-r6-multitask-01` | 9/27 样本、61 请求、因 `APIConnectionError` 中止（`complete: false`） | 修复①（补救携带全部观测）与行长（450 字符）后，仍暴露出③：答案事实以 JSON 原文（`"pending_records": 12400`）写进合同，模型自然写成 `pending_records=12400`，**严格判据因此误判"缺事实"**（9/9 语义通过、0/9 严格通过） |
| r7（本批） | 见 `RESULTS.md` | 合同事实改为模型自然写法（`pending_records 12400`、`headroom 45%`、`failed 0`）；严格判据改为"分词有序、词边界完整、分隔符任意"的匹配；语义判据在其上再放宽（`45 percent`、千位分隔、全角标点、词序反转）；补救携带该角色全部工具观测；新增 `--resume` 以便网络中断续跑 |

## 任务与事实（未参与 r3/r4/r5 开发）

| task_id | 角色 1 工具（按序各一次） | 角色 2 工具 | 决策 | 答案必须事实 | 禁用事实 |
|---|---|---|---|---|---|
| `queue_backlog_replay` | `check_pipeline_status`、`inspect_dead_letter_queue`、`verify_queue_replay` | `verify_queue_replay` | `REPLAY` | `pending_records 12400`、`us-west-2` | `us-east-1` |
| `region_failover` | `get_cluster_health`、`check_region_drain`、`verify_capacity_headroom` | `verify_capacity_headroom` | `FAILOVER` | `ap-south-1`、`headroom 45%` | `eu-central-1` |
| `schema_migration` | `inspect_schema_drift`、`run_contract_tests`、`verify_schema_target` | `verify_schema_target` | `GO` | `schema-42`、`failed 0` | `schema-41` |

合成运维数据，不含真实仓库、生产系统或个人信息。

## 判据

1. **严格判据**：一行、行长 ≤450、前缀、决策字段、事实分词有序出现、禁用事实不出现、交接事实齐全、两角色各一次指定工具、适配器结构安全字段全 0。
2. **前瞻性语义等价判据**：在严格判据之上允许纯格式差异（`failed 0` ↔ `fail 0`、`45%` ↔ `45 percent`、千位分隔、全角标点、引号/分隔符差异）；**缺事实、错版本、错区域、错决策永不被规范化补救**（四类反例在 `tests/test_crewai_semantic_equivalence_v7.py` 中先行通过）。

## 矩阵与预算

3 任务 × 3 重复 × 3 臂 = 27 样本；臂顺序 `(task_index + repeat) mod 3` 轮换，重复间载荷一致。模型 `deepseek-v4-flash`、`https://api.deepseek.com`、温度 0、禁用 thinking；provider soft/target/hard = 1200/900/3000（估算等价 3085/2314/7712）；Agent 输出 ≤512 token，摘要 ≤1024 token、每角色 ≤4 次；全局请求上限 320（含 r6 已用 61 的前提下也留有覆盖 27 样本的余量）；失败请求同样计数；每样本落盘。

## 成本与边界

主成本为每臂完整总 token（agent 输入 + 输出 + 摘要输入 + 输出），失败与触顶样本全部进入配对；逐任务报告配对均值、正收益对数、输入侧差、输出侧差与任务间离散度。只有严格与语义两套判据都不下降时，才允许把节省称为"质量不劣的节省"。单批仍为模拟运维任务，不含真实代码修复或生产工作流；三宿主数据不得合并。
