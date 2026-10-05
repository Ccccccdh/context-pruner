# CrewAI r10 新任务确认批：同一机制（v9）在未参与开发的四任务上的可复现性检验

本协议冻结在付费运行**之前**。冻结文件 `integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R10_FRESH_MULTITASK_01.json`（付费）与 `..._MOCK_01.json`（零 API 栅格）。

## 1. 本批要回答的预注册问题

r9（`runs/stage5-crewai/crewai-two-role-r9-multitask-01`）在 3 任务 × 3 重复 × 3 臂上给出插件配对完整总 token **+8.01%**（8/9 正），但同一批的逐任务配对均值是

| 任务 | r9 插件配对均值 |
|---|---:|
| `queue_backlog_replay` | **+22.25%** |
| `region_failover` | −0.26% |
| `schema_migration` | +2.04% |

任务间离散度 **22.50 个百分点**，且三个任务在 r3–r9 里都被用来调参，3 任务 × 3 重复的重复数不足以估计任务层方差。

因此 r10 预注册的问题是：

> **r9 的 +22% 级配对收益在从未参与开发的新任务上能否复现，还是任务特异的？**

r10 **只改任务集合与重复数**，不改机制、不改判据、不改臂、不改预算（见 §2 冻结表）。本批不做机制消融，也不重打分任何旧批。

## 2. 冻结表：与 r9 的唯一变量是任务集合与重复数

| 项目 | r9（冻结） | r10（本批） |
|---|---|---|
| 任务文件 | `tasks/stage5_autogen/natural_tasks_r8.json`（哈希 `61ccfafc…`） | **`tasks/stage5_autogen/natural_tasks_r10.json`**（哈希见冻结文件） |
| 任务数 × 重复 | 3 × 3 = 27 样本 | **4 × 4 = 48 样本** |
| 机制 | `crewai_pinned_evidence_v9.PinnedEvidenceMiddleware` | **同一个模块，逐字不变**（冻结哈希相同） |
| 判据规则 | `crewai_semantic_equivalence_v8.py`（哈希 `26aea58b…`） | **同一个模块，逐字不变** |
| 判据路由 | 无（直接用 v8 的 `TASK_FILE`） | `crewai_semantic_equivalence_v10.py`：只把任务路径指到 r10 文件，不新增判定逻辑 |
| 三臂 / 预算 / 上限 | `none, pruner_v1, native_summary`；1200/900/3000；输出 ≤512；摘要 ≤1024×≤4 | 同 r9；全局请求上限由 200 改为 **440**（§4 算术） |
| 首轮事实齐全 | 一级冻结门指标 | 同一冻结定义，逐样本继续记录 |
| 补救调用 | 保留并计费（上限 1 次） | 同 r9，且首轮指标在任何补救之前判定 |

r9 的目录、冻结、`RESULTS.md`、逐样本答案与评分**一律不改、不重跑、不重打分**。r8/r9 的对比只在配对均值与门指标层面进行，且必须注明是两次独立付费运行。

## 3. 新任务集合：四条双角色任务

`tasks/stage5_autogen/natural_tasks_r10.json`，每条任务保持与 r5–r9 完全相同的**交接合同形状**：固定的 `HANDOFF` 标记、必需的工具证据、单行 450 字符内的决策合同、`answer_facts`、`forbidden_facts`、`region_fact` / `version_fact`。因此冻结判据可原样应用。

| 任务 id | 决策合同 | `handle_facts` | 关键新标识 | 工具（新名字） |
|---|---|---|---|---|
| `credential_rotation` | `RENEW` | `svc-ledger-key`、`ca-central-1`、`failed 0` | 区域 `ca-central-1`、序列号 `cert-v7` | `get_credential_status`、`check_trust_chain`、`verify_failover_region` |
| `shard_split` | `FORK` | `shard-88`、`tenant-lumen`、`target_shards 3` | 分片 `shard-88`、租户 `tenant-lumen` | `inspect_shard_pressure`、`check_replica_lag`、`verify_split_plan` |
| `batch_replay` | `RETRY` | `run-8842`、`step-transform`、`failed 0` | 作业 `job-2214`、运行 `run-8842` | `inspect_failed_step`、`check_downstream_deps`、`verify_replay_safety` |
| `window_gate` | `CLEAR` | `window-9930`、`620`、`failed 0` | 窗口 `window-9930`、时段 `03:40-05:10 UTC` | `check_window_lock`、`inspect_change_guard`、`verify_risk_review` |

**新意如何被证明（零 API、可复核）**：`.tooling/probe_crewai_r10_tasks.py` 把 r10 文件与 `natural_tasks.json`（r3/r4）及 `natural_tasks_r5..r8.json` 逐字段比较，断言

1. 四个任务 id、十二个工具名与 r3–r9 **无交集**；
2. 任何 r10 事实字面量都与 r3–r9 的值**逐字不同**；唯一的例外是 `failed 0`——它是冻结决策合同句子（“把每个失败检查的有效结果写成 `0 failed`”）的一部分，属于**合同形状**而不是被复用的任务事实（r6–r8 承载它的是 `schema-42`，r10 承载它的是三条新工具链），该例外在探测脚本与测试里以常量 `FROZEN_SHARED_LITERALS` 显式声明；
3. 任何 r3–r9 的标识符**值**（带数字的 id/区域/版本/计数）都不作为子串出现在 r10 的任务 id、事实、工具输入或工具结果里；
4. 任何 r3–r9 用过的**数值事实**都不被 r10 复用（例如 `12400`、`45%`、`312`、`240` 之外的 `88/1100/620/3/0` 全部核对）；决策标签一律不复用（r3–r9 用 `ROLLBACK/NO-GO/PROCEED/REPLAY/FAILOVER/GO`，r10 用 `RENEW/FORK/RETRY/CLEAR`）。
5. 冻结语义判据在本地构造的首轮 HANDOFF 与最终答案上对四条任务**逐条通过**，且在截断 HANDOFF、错误决策、`forbidden_facts` 三个反例上**逐条拒绝**。

同一断言在 `tests/test_crewai_handoff_v10.py::CrewAIR10FreshTaskSetTest` 中以零 API 复现。

## 4. 采样矩阵、预算与请求上限算术

- 矩阵：**4 任务 × 4 重复 × 3 臂 = 48 样本**；臂顺序按 r4 的 `balanced_plan` 轮换，重复之间载荷完全一致（`fixed_history(task, repeat)` 只改历史条数，不改事实与工具数据）。
- 模型 `deepseek-v4-flash`、温度 0、禁用 thinking；provider soft/target/hard = 1200/900/3000（估计等价 3085/2314/7712）；`fixed_reserved_tokens=300`；Agent 输出 ≤512 token；摘要 ≤1024 token / 角色 ≤4 次。
- 全局请求上限 **440**，算术：
  - 必需的 agent 请求 = 2 × 48 = **96**（每样本两个角色各至少一次模型调用）；
  - `native_summary` 臂的摘要请求上限 = 2 角色 × 4 次 × 48 样本 = 384（病态最坏值）；r9 实测每样本 2–3 次，r10 零 API 栅格同样不产生任何真实请求；
  - r9 实测全批每样本 6.85 次请求（185/27），按此外推 48 样本约 **329** 次；可手动触发的补救调用每样本至多 1 次（上限之上再加 48 的余量）；
  - 取 440 = 96 + 344，即“最少 96 次 + 病态摘要最坏值 384 的约 90%”，比 r9 实测外推值 329 高约 34%（余量 111 次）。若真触顶，runner 会停止并在日志中留下最后一行；此时**保留部分目录、按新 id + `--resume` 续跑**，不把部分栅格当完整栅格。
- 每样本落盘、失败保留并继续；连接类错误（`APIConnectionError` 等）会中止本批而不重试掩盖。

## 5. 成本口径与质量门

- 主成本为**完整总 token** = agent 输入 + agent 输出 + 摘要输入 + 摘要输出，含每一次辅助（摘要）请求与每一次失败尝试；失败与触顶样本全部进入配对均值。
- 逐任务报告：插件配对均值、正收益对数、逐重复配对值、输入/输出侧差、**任务间离散度**（最大 − 最小逐任务配对均值）。
- 三套质量门并列报告：**严格**（r4 型合同）、**语义**（前瞻性等价判据）、**首轮事实齐全**（在任何补救调用之前首轮 HANDOFF 含全部冻结 `handle_facts` 字面量且单行 ≤450 字符）。
- 只有同时满足“严格与语义质量不劣、首轮事实齐全已记录、完整总 token 为正”时，才允许出现“质量等价节省”的表述；`region_failover` 式的近零配对值必须如实标注为“不能称为压缩收益”。

## 6. 零 API 先行的门

1. `tests.test_crewai_handoff_v10`：48 样本 mock 完整栅格 + 新任务新意断言 + 冻结篡改检测 + 首轮事实齐全不依赖补救 + tier 3 回退计数 + 补救调用计费路径。
2. 既有 CrewAI 零 API 套件（v4/v5/v6/v7/v8/v9 运行器、质量、语义判据）必须全绿（当前 115 tests OK）；本批不修改这些文件。
3. `--plan` 先跑并核对任务×重复×三臂、模型、预算、全局上限与请求算术；`--plan` 不发 API。
4. `.tooling/probe_crewai_r10_tasks.py` 的新意与自洽探测必须通过。

## 7. 边界（必须随数字一起引用）

- 全部为**合成运维任务**：固定工具返回、模拟双角色流程；不含真实代码修复、真实仓库或生产工作流。
- **4 任务 × 4 重复仍只能给出极有限的跨任务推断**：它把任务层样本从 3 提到 4、每任务重复从 3 提到 4，任务间离散度估计仍只有 4 个点，不能给出置信区间意义上的“跨任务稳定节省”。本批的结论强度上限是“在 4 条新任务上是否复现方向与量级”。
- 温度 0 仍有独立轨迹差异；r8/r9/r10 是**三次独立付费运行**，跨批差值不是自动因果证据。
- 严格判据与语义判据的差异必须同时报告：r9 的严格 6/9 已被定位为冻结判据的 `=` 折叠与末尾词边界不一致（`region_failover` 的 `45%` 紧接下一字段）。r10 的新任务里 `handle_facts` 最后一个字面量分别为 `failed 0` / `target_shards 3` / `620`，且都不与下一个证据字段粘连，因此**不预期**复现该判据伪影；若仍出现严格与语义分歧，按同一定位流程如实记录，不修改冻结判据、不重打分旧批。
- 三宿主数据不得合并；r5–r9 的目录、冻结与评分保持原样。
