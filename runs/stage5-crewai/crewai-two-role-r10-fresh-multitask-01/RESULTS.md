# CrewAI r10 新任务确认批：v9 钉住机制在未参与开发的四任务、四重复上的可复现性检验

状态：2026-10-04 完成 **48/48 样本**、**319 次 API 请求**（全局上限 440，未触顶；失败/触顶样本 0）。独立冻结审计 `experiments/audits/audit_crewai_handoff_v10.py` 返回 `complete: true`、`rows: 48/48`、`freeze_checked: true`，但 **`errors` 非空（6 条 `tool evidence mismatch`）**，审计进程 exit 1。冻结：`integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R10_FRESH_MULTITASK_01.json`（SHA256 `20796ee2adb4a635512bef675ac94a1be474676fa25932c77584e6d26a133cb8`，9 个冻结源全部在运行后零漂移）；任务文件 `tasks/stage5_autogen/natural_tasks_r10.json` SHA256 `873a306b109ee438e6513870bbbd5f25410a98865f8aff0fcd44d5279793aa0f`；协议：`integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R10_FRESH_MULTITASK_01.md`；诊断：`integrations/crewai/R10_FRESH_TASK_CONFIRMATION_DIAGNOSIS.md`；逐任务复算：批次目录内 `R10_CONFIRMATION_METRICS.json`。

## 0. 预注册问题的直接答案

> **r9 的 +22% 级配对收益在从未参与开发的新任务上能否复现？**

**不能复现，而且它不是"任务特异"这么简单，是"任务特异 + 质量违规"的组合。** 三条工具序列合规的新任务上，插件配对均值只有 **+0.98% / +1.83% / +3.11%**；唯一量级接近 +22% 的 `window_gate`（+41.34%）由**第一角色只调用了 1/3 个工具**制造，且该任务插件严格/语义质量 **0/4**。因此 r10 **不构成质量等价节省**，也不支持 r9 的 +22.25% 具有跨任务普遍性。

## 1. 本批是"只换任务集合"的确认批

| 项目 | r9（冻结） | r10（本批） |
|---|---|---|
| 任务文件 | `natural_tasks_r8.json`（哈希 `61ccfafc…`） | **`natural_tasks_r10.json`**（4 条全新任务） |
| 机制 | `crewai_pinned_evidence_v9.py`（哈希 `5ccc86ab…`） | **同一模块，逐字不变（冻结哈希相同）** |
| 判据规则 | `crewai_semantic_equivalence_v8.py`（哈希 `26aea58b…`） | **同一模块，逐字不变** |
| 判据路由 | 直接用 v8 的 `TASK_FILE` | `crewai_semantic_equivalence_v10.py`：只改任务路径，不新增判定逻辑 |
| 矩阵 | 3 任务 × 3 重复 × 3 臂 = 27 | **4 任务 × 4 重复 × 3 臂 = 48** |
| 全局请求上限 | 200（实用 185） | **440**（实用 319） |

机制哈希在 r9 冻结与 r10 冻结中**完全相同**（`5ccc86ab…`），判据规则哈希也完全相同（`26aea58b…`）。r5–r9 的目录、冻结、`RESULTS.md`、逐样本答案与评分**未改动、未重跑、未重打分**。

### 新任务集合（`tasks/stage5_autogen/natural_tasks_r10.json`）

| 任务 id | 决策合同 | 冻结 `handle_facts` | 新标识 | 新工具名 |
|---|---|---|---|---|
| `credential_rotation` | `RENEW` | `svc-ledger-key`、`ca-central-1`、`failed 0` | 区域 `ca-central-1`、序列 `cert-v7` | `get_credential_status`、`check_trust_chain`、`verify_failover_region` |
| `shard_split` | `FORK` | `shard-88`、`tenant-lumen`、`target_shards 3` | 分片 `shard-88`、租户 `tenant-lumen` | `inspect_shard_pressure`、`check_replica_lag`、`verify_split_plan` |
| `batch_replay` | `RETRY` | `run-8842`、`step-transform`、`failed 0` | 作业 `job-2214`、运行 `run-8842` | `inspect_failed_step`、`check_downstream_deps`、`verify_replay_safety` |
| `window_gate` | `CLEAR` | `window-9930`、`620`、`failed 0` | 窗口 `window-9930`、时段 `03:40-05:10 UTC` | `check_window_lock`、`inspect_change_guard`、`verify_risk_review` |

**新意是零 API 可复核的**：`.tooling/probe_crewai_r10_tasks.py` 与 `tests/test_crewai_handoff_v10.py::CrewAIR10FreshTaskSetTest` 断言（a）4 个任务 id、12 个工具名与 `natural_tasks.json`（r3/r4）及 `r5..r8` 无交集；（b）任何 r10 事实字面量都与 r3–r9 的值逐字不同，唯一例外是 `failed 0`（冻结合同句"把每个失败检查的有效结果写成 `0 failed`"的一部分，属合同形状；r6–r8 承载它的是 `schema-42` 链路，r10 承载它的是三条全新工具链 `check_trust_chain`/`inspect_change_guard`/`inspect_failed_step`，该例外在脚本与测试中以常量显式声明）；（c）任何 r3–r9 的**标识符值**（带数字的 id/区域/版本/计数，如 `PIPE-3391`、`us-west-2`、`ap-south-1`、`schema-42`、`apollo-cache-9`、`12400`、`45%`、`312`、`circuit-9`）都不作为子串出现在 r10 的任务 id、事实、工具输入或工具结果里；（d）不复用任何 r3–r9 用过的数值事实（`88/1100/620/3/0` 逐一核对）与决策标签（r3–r9 用 `ROLLBACK/NO-GO/PROCEED/REPLAY/FAILOVER/GO`，r10 用 `RENEW/FORK/RETRY/CLEAR`）；（e）冻结语义判据在四条任务的构造样本上逐条通过，并在截断 HANDOFF、错误决策、`forbidden_facts` 三个反例上逐条拒绝。

**探测过程确实抓到两个真实缺陷并已修正**：`shard_split` 原定决策 `SHARD` 会被证据串 `split_ready=true target_shards=3` 触发 `handoff_decision_leak`（因为 `shard` 出现在证据里），改为 `FORK`；`batch_replay` / `window_gate` 的 `answer_facts` 原引用了不在决策者自己工具证据里的事实，判据判 `answer_missing_fact`，改为证据内事实。改的是**尚未冻结**的任务文件，冻结发生在运行之前。

## 2. 质量门（按臂）

| 臂 | 严格质量 | 语义质量 | 首轮事实齐全 | 工具序列一致 | 补救调用 | 全前缀回退 | 压缩事件 | 钉住事件 | 完整总 token |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 无压缩 `none` | **16/16** | **16/16** | 16/16 | 16/16 | 0 | 0 | 0 | 0 | 135,487 |
| 插件 `pruner_v1`（v9） | **6/16** | **6/16** | **16/16** | **10/16** | **0** | 0 | 56 | 56 次调用（92 条钉住消息、16,392 字符） | 119,667 |
| 原生摘要 `native_summary` | 13/16 | 13/16 | 16/16 | 16/16 | 0 | 0 | 0 | 0 | 169,777 |

- **插件质量不劣不成立**：严格与语义各 6/16，基线 16/16，`quality_non_inferior: false`；首轮事实齐全 16/16 与基线持平（`first_pass_non_inferior: true`）、补救 0 次、全前缀回退 0 次。
- 钉住层级命中（插件臂 16 样本 × 4 次模型调用 = 64 次调用）：`tier1_evidence_pinned` 56 次、`tier2_task_contract` 28 次。
- 插件臂 16 个样本共 **56 次压缩事件**，说明压缩真的在跑；失败**不是**"压缩没触发"造成的。
- 三个臂都没有运行时异常（每行 `error` 字段均为空字符串），也没有触顶样本；48/48 样本全部产生两个角色的输出。

### 插件臂 10 个失败的准确成因（详见诊断文件）

| 类别 | 插件样本 | 原生摘要样本 | 直接证据 |
|---|---:|---:|---|
| A. 决策者答案错误，工具序列正常 | **4** | 3 | `batch_replay` 重复 1/2/3 写 `decision=<RETRY>`（`decision_seen` 为空）；`shard_split` 重复 3 写 `decision=PROCEED`（期望 `FORK`）；原生摘要 `window_gate` 重复 0/2/3 写 `decision=<CLEAR>` |
| B. 第一角色工具序列被压断（少调工具或顺序不符） | **6** | 0 | `credential_rotation` 重复 2/3 调用顺序为 `get_credential_status` → `verify_failover_region` → `check_trust_chain`（冻结顺序要求 `check_trust_chain` 在 `verify_failover_region` 之前）；`window_gate` 重复 0/1/2/3 只调用了 `check_window_lock`（三个工具要求各一次） |

两类互不重叠：4 + 6 = 10 个插件失败。判定顺序为"先工具边界、后答案"：`window_gate` 的四个插件样本答案其实是正确的 `decision=CLEAR`（`decision_seen='clear'`），它们失败**只因为工具序列被压断**，因此计入 B 类而不是 A 类。

`<RETRY>`/`<CLEAR>` 正是冻结决策合同句里的模板写法（`decision=<{decision}>`，并附带 "Do not output angle brackets"），即模型在未真正完成决策时回抄了合同模板。**同一失败模式在原生摘要臂出现 3 次（`window_gate` 的 `<CLEAR>`），在无压缩臂 0 次**，因此不能把它归因于压缩或钉住机制：它是该模型在新任务合同上的不稳定，但插件臂暴露得最多。工具序列被压断则是**插件臂独有**（原生摘要与无压缩均 16/16 一致）。

## 3. 完整总 token：逐任务配对均值、逐任务离散度、全批均值

主口径为**完整总 token** = agent 输入 + agent 输出 + 摘要输入 + 摘要输出，含每一次辅助（摘要）请求与每一次失败。配对口径为**同一任务同一重复**的 `none` 对 `pruner_v1`；正收益＝插件更省。

| 任务 | 无压缩 | 插件 | 原生摘要 | 插件配对均值 | 逐重复配对 | 正收益对数 | 输入差/样本 | 输出差/样本 | 插件严格/语义 |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `credential_rotation` | 33,974 | 33,633 | 43,469 | **+0.98%** | −0.10 / +1.71 / +0.87 / +1.43 | 3/4 | −83 | −3 | 2/4 |
| `shard_split` | 34,180 | 33,549 | 43,379 | **+1.83%** | +0.56 / +3.06 / +1.81 / +1.88 | 4/4 | −126 | −32 | 3/4 |
| `batch_replay` | 33,967 | 32,899 | 42,846 | **+3.11%** | +2.17 / +2.58 / +3.98 / +3.70 | 4/4 | −159 | −108 | 1/4 |
| `window_gate` | 33,366 | **19,586** | 40,083 | **+41.34%（无效）** | +42.61 / +41.50 / +40.95 / +40.33 | 4/4 | −3,462 | +17 | **0/4** |
| 全批 | 135,487 | 119,667 | 169,777 | **+11.81%** | — | 15/16 | — | — | **6/16** |

- **任务间离散度 40.37 个百分点**（最大 +41.34%，最小 +0.98%），比 r9 的 22.50 pp 更大。
- **`window_gate` 的 +41.34% 是假收益**：该任务四个插件样本整个第一角色只调用一次工具（无压缩臂 6 次调用），agent 输入合计 31,973 → 18,127 token，agent 输出反而略升（1,393 → 1,459），工具序列 `tool_evidence_consistent: false`，严格/语义 0/4。把该任务排除后，三条合规任务的配对均值为 **+1.97%**（12 对，11 对为正）。
- **原生摘要臂配对均值 −25.45%（0/16 正）**：它必须为每角色额外支付摘要请求（39 次摘要请求、35,231 摘要输入 token），完整总 token 比无压缩高 25.31%。
- 请求账本：319 次 = 无压缩 96 + 插件 88 + 原生摘要 135（含 39 次摘要）；全局上限 440，未触顶；补救调用 0（因此插件臂 88 次 = 16 样本 × 5.5 次调用，低于基线的 96 = 16 × 6）。

### 插件收益构成（诚实分解，含钉住成本）

- 钉住把必需事实与合同计入本臂输入：插件臂共 56 次钉住、92 条钉住消息、16,392 字符（约 1,025 字符/样本）。三条合规任务的插件输入只比无压缩少 83–159 token/样本，这一量级与钉住成本同阶——**"钉住换首轮正确率"的代价在 r10 更明显**：首轮事实齐全保住了 16/16，但节省空间被压到 ≈1–3%。
- `window_gate` 的输入差（−3,462 token/样本）全部来自少调用工具与少两轮会话，不来自压缩：逐调用比较显示插件臂首次调用 3,678 字符 vs 无压缩 3,428 字符（钉住后**更大**），真正的差额来自调用次数 4 vs 6。

## 4. 独立审计（`audit_crewai_handoff_v10.py`）

`complete: true`、`rows: 48/48`、`expected_rows: 48`、`request_attempts: 319`、`freeze_checked: true`；9 个冻结源 SHA256 全部匹配；逐样本严格/语义/首轮事实齐全三套判定与行内布尔值一致；补救上限与触发一致；钉住声明与冻结 `handle_facts` 逐任务一致；非插件臂零钉住活动；完整总 token 与请求账本逐样本核对；配对均值、离散度、正收益对数由审计独立重算。

**但 `errors` 非空（审计进程 exit 1）**，6 条全部是工具边界错误：

```
credential_rotation/2/pruner_v1: tool evidence mismatch
credential_rotation/3/pruner_v1: tool evidence mismatch
window_gate/0/pruner_v1: tool evidence mismatch
window_gate/1/pruner_v1: tool evidence mismatch
window_gate/2/pruner_v1: tool evidence mismatch
window_gate/3/pruner_v1: tool evidence mismatch
```

这与 §2 的 B 类失败完全一致，也说明"插件臂少调工具"是**冻结审计的机器判定**，不是事后人工解读。**本批审计未通过，因此本批的任何配对均值都不得作为有效节省引用。** 审计的其余检查（含 48/48 完整性、冻结哈希、零钉住活动越界）没有报错，故本批的失败是**质量控制失败**，不是记账或冻结失败。

## 5. 与 r8 / r9 的直接对照

同机制（v9）、同判据（v8 规则），**不同批次、不同模型轨迹、不同任务集合**；差值不是自动因果证据。

| 指标 | r8（v8 机制，r8 任务） | r9（v9 机制，r8 任务） | r10（v9 机制，**新任务**） |
|---|---:|---:|---:|
| 样本 | 27 | 27 | **48** |
| 插件首轮事实齐全 | 3/9 | **9/9** | **16/16** |
| 插件补救调用 | 6 | 0 | 0 |
| 插件严格质量 | 6/9 | 6/9 | **6/16** |
| 插件语义质量 | 9/9 | 9/9 | **6/16** |
| 无压缩严格 / 语义 | 9/9 / 9/9 | 6/9 / 9/9 | **16/16 / 16/16** |
| 插件工具序列一致 | 9/9 | 9/9（全批 27/27） | **10/16** |
| 最大任务配对均值 | +29.78% | +22.25% | +41.34%（**质量违规，无效**） |
| 次高任务配对均值 | +2.65% | +2.04% | +3.11% |
| 全批插件配对均值 | +10.99%（7/9 正） | +8.01%（8/9 正） | +11.81%（15/16 正，**不有效**） |
| 任务间离散度 | 29.24 pp | 22.50 pp | **40.37 pp** |
| 插件完整总 token | 66,625 | 67,487 | 119,667 |
| 请求数 | 190 | 185 | 319 |

**读法**：

1. **+22% 不可复现。** r9 的 +22.25% 与 r10 的 +41.34% 都属于"某个任务特别省"，但 r10 的这个特殊任务是**没做完全部工具调用**造成的，而 r9 的 `queue_backlog_replay` 工具序列 9/9 一致。也就是说，r10 连"另一个同样 +20% 以上的合规任务"都没有出现。
2. **三条合规新任务的收益只有 +0.98% / +1.83% / +3.11%**，与 r9 的 `schema_migration`（+2.04%）、`region_failover`（−0.26%）同量级。r9 的 +8.01% 平均值里有相当部分来自单个任务，而 r10 的同类任务量级**没有超过 3.2%**。
3. **R9 的"三臂质量齐平"没有迁移。** r10 的基线 16/16、插件 6/16、原生摘要 13/16。默认情况下基线在新任务上明显更稳。
4. **首轮事实齐全门本身是可迁移的**：插件臂 16/16、补救 0、全前缀回退 0。v9 机制在**事实呈现**这一层上确实在四条约新任务上守住了，失败发生在事实之后（决策与工具序列）。

## 6. 判定

1. **质量**：严格 6/16、语义 6/16（基线 16/16）、首轮事实齐全 16/16（基线 16/16）。`quality_non_inferior: false`、`first_pass_non_inferior: true`。
2. **成本**：全批插件配对均值 **+11.81%（15/16 正）**，但**不成立为节省**——质量不劣的前提被打破，且其中 +41.34% 来自工具边界违规。
3. **有效收益（三条工具序列合规的任务）**：**+0.98% / +1.83% / +3.11%**，均值 **+1.97%**，全部低于 r9 的最高任务值与 r9 批次均值。
4. **因此本批支持的最强表述是**：*在四条未参与开发的新双角色任务、每任务四重复上，v9 必需事实钉住机制仍然把首轮事实齐全维持在 16/16 且补救 0 次、全前缀回退 0 次，但插件臂质量从基线的 16/16 掉到 6/16（严格与语义一致）：6 个样本的第一角色工具序列被压断（`window_gate` 整条任务只调用了 1/3 个工具），4 个样本的决策者回抄了合同占位符或给出错误决策；三条工具合规任务的配对收益只有 +0.98% ~ +3.11%。**r9 的 +22% 级配对收益未能在新任务上复现，r10 不构成质量等价节省。***
5. **对 r9 的表述不作修订**：r9 的 +8.01% 与首轮 9/9 仍按其自身冻结口径成立；r10 只表明该量级**不可外推到新任务**。

## 7. 边界（必须随数字一起引用）

- 全部为**合成运维任务**：固定工具返回、模拟双角色流程；不含真实代码修复、真实仓库或生产工作流。
- **4 任务 × 4 重复仍只有 4 个任务层观测点**，不能给出置信区间；本批的推断上限是"方向与量级是否复现"，不是"跨任务稳定节省已建立/已否证"。
- **本批审计 exit 1（6 条工具边界错误）**：引用本批任何数字时必须同时引用这一点。
- 温度 0 仍有独立轨迹差异；r8/r9/r10 是**三次独立付费运行**，跨批差值不是自动因果证据。
- `<占位符>` 回抄在原生摘要臂也出现 3 次，不能归因于压缩或钉住机制单一原因。
- `window_gate` 的 +41.34% **绝不能单独引用**；它必须与"第一角色只调用 1/3 工具、严格与语义 0/4、`tool_evidence_consistent: false`"同时出现。
- 三宿主数据不得合并；r5–r9 的目录、冻结与评分保持原样。

## 8. 复现命令（零 API、审计与复算）

```powershell
# 新意 + 自洽探测（零 API）
& .\.venv-crewai\Scripts\python.exe .tooling\probe_crewai_r10_tasks.py
# 零 API 完整栅格（48 执行）
& .\.venv-crewai\Scripts\python.exe -u -m experiments.runners.run_crewai_handoff_v10 --mode mock --task-ids credential_rotation,shard_split,batch_replay,window_gate --methods none,pruner_v1,native_summary --repeats 4 --max-api-requests 440 --experiment-id crewai-two-role-r10-fresh-multitask-mock-01
# 零 API 门（新意、栅格、冻结篡改、tier 3 回退、补救计费）
& .\.venv-crewai\Scripts\python.exe -m unittest tests.test_crewai_handoff_v10 -v
# 独立审计（付费批，exit 1 表示 errors 非空）
& .\.venv-crewai\Scripts\python.exe -m experiments.audits.audit_crewai_handoff_v10 runs/stage5-crewai/crewai-two-role-r10-fresh-multitask-01 --freeze integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R10_FRESH_MULTITASK_01.json
# 逐任务复算（只读，写 R10_CONFIRMATION_METRICS.json）
& .\.venv-crewai\Scripts\python.exe .tooling\report_crewai_r10.py runs/stage5-crewai/crewai-two-role-r10-fresh-multitask-01
```

付费运行通过密钥启动器执行（密钥不在 shell 环境中、不打印、不落盘）：

```powershell
$env:DSH_MODULE='experiments.runners.run_crewai_handoff_v10'; $env:DSH_RUNNER=''
$env:DSH_RUNNER_ARGS='--mode api --confirm-send-synthetic-data --task-ids credential_rotation,shard_split,batch_replay,window_gate --methods none,pruner_v1,native_summary --repeats 4 --max-api-requests 440 --experiment-id crewai-two-role-r10-fresh-multitask-01'
$env:DSH_PYTHON='.venv-crewai\Scripts\python.exe'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1
```
