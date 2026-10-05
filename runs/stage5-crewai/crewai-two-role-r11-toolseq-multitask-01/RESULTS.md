# CrewAI r11 工具序列批：修「压缩打断工具调用序列」的尝试**失败**（not-yet-valid）

状态：2026-10-04 完成 **48/48 样本**、**362 次 API 请求**（全局上限 440，未触顶；无触顶样本，48 行全部产生记录）。独立冻结审计 `experiments/audits/audit_crewai_handoff_v11.py` 返回 `complete: true`、`rows: 48/48`、`freeze_checked: true`，但 **`errors` 非空（20 条）**、**`verdict: not_yet_valid`**、审计进程 exit 1。冻结：`integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R11_TOOLSEQ_MULTITASK_01.json`；协议：`integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R11_TOOLSEQ_MULTITASK_01.md`；逐任务复算：批次目录内 `R10_CONFIRMATION_METRICS.json`。

> **审计明文判定：本批数字不得当作有效节省。** 本文件只报告失败与成因，不提出任何节省结论。按预注册的迭代上限（只允许一次版本升级），**不再开 v12/v13**。

## 0. 结论：v11 没有修好工具序列，反而使其从 10/16 掉到 0/16 并破坏了交接

| 预注册门控指标 | 无压缩 `none` | 插件 `pruner_v1`（v11） | 原生摘要 `native_summary` | 验收线 | 结果 |
|---|---:|---:|---:|---|---|
| `tool_sequence_consistent` | **16/16** | **0/16** | 16/16 | ≥ 基线 | **失败** |
| `tool_sequence_matches_baseline` | 16/16 | **0/16** | 16/16 | — | **失败** |
| 严格质量 | 13/16 | **0/16** | 13/16 | ≥ 基线 | **失败** |
| 语义质量 | 13/16 | **0/16** | 13/16 | ≥ 基线 | **失败** |
| `first_pass_facts`（补救前） | 16/16 | **4/16** | 16/16 | 记录 | **失败** |
| 补救调用 | 0 | **12** | 0 | — | — |
| 全前缀回退 | 0 | 0 | 0 | — | — |
| 完整总 token | 135,561 | **228,075** | 166,521 | 配对为正 | **失败** |

**配对完整总 token 均值 −68.28%（0/16 正）**，任务间离散度 11.11 pp；四条任务逐任务配对均值 −68.58% / −64.71% / −75.47% / −64.36%，**没有任何一条为正**。审计 `verdict_reason` 四条全部命中：工具序列低于基线（0 vs 16）、质量低于基线（严格 0 vs 13、语义 0 vs 13）、配对节省不为正（−68.28%）、`errors` 非空（20 条）。

## 1. 失败的直接机制：指令把第一角色推入"重复同一个工具"的循环

12/16 个插件样本以同一个错误结束：

```
HandoffRecoveryError: recovery still lacks required facts   （12 次）
```

这 12 个样本的记录显示：**第一角色只调用了一个工具，却把同一个工具反复调用了 6 次**。以 `credential_rotation/0` 为例（首个工具是 `get_credential_status`）：

- 记录到的 8 次模型调用（1 次角色 + 7 次后续）里，工具观测 JSON **逐字重复 6 次**（恢复提示里 `Recorded current observations:` 下是 6 条完全相同的 `{"credential": "svc-ledger-key", "days_to_expiry": 21, ...}`）；
- 第一角色最终输出：`HANDOFF credential=svc-ledger-key region=ca-central-1 get_credential_status={...}; check_trust_chain=not_called; verify_failover_region=not_called; ...` —— 它**自己在文本里写明另两个工具没被调用**；
- 因此首轮缺 `failed 0` 这一个必需字面量（`handoff_missing_fact`），触发 1 次补救；补救输出同样缺 `failed 0`（`recovery still lacks required facts`），样本以错误结束，`role_outputs` 只有 1 条 → 审计记 `wrong number of role outputs`。

逐任务第一角色工具调用数（4 个重复）：

| 任务 | 无压缩臂调用数 | 插件臂调用数 |
|---|---|---|
| `credential_rotation` | 3 / 3 / 3 / 3 | **1 / 1 / 1 / 1** |
| `shard_split` | 3 / 3 / 3 / 3 | **1 / 1 / 1 / 1** |
| `batch_replay` | 3 / 3 / 3 / 3 | 5 / 3 / 3 / 3（顺序不符） |
| `window_gate` | 3 / 3 / 3 / 3 | **1 / 1 / 1 / 1** |

也就是说：**插件臂在全批 16 个样本里一次也没有跑出冻结工具序列**，而基线 16/16 全部正确。v11 声称要修的 6 个 r10 样本，在 r11 变成了 16 个样本全错。

## 2. 本批唯一改变的东西（保证 B 未生效）

v11 = v9 三层（逐字证据钉住、任务合同钉住、计数型全前缀回退）**逐字保留** + 两条新的 host 指令：

| 保证 | r11 实测 |
|---|---|
| A 工具序列指令（`tool_directive_events`） | 24 次（每个插件样本的每次模型调用都命中，说明"还有没调用的冻结工具"这一条件在整段会话里**始终为真**——这正是循环的证据） |
| B 决策合同指令（`contract_directive_events`） | **0 次**（未生效，本批无法评价其对占位符的作用） |

v9 各层仍在工作：钉住事件在插件臂逐样本发生，`required_fact_whole_prefix_fallbacks` 全批 0，`crewai_group_restore_failure_count` / `crewai_unmatched_call_count` / `crewai_active_pending_view_count` 全 0（`role_safe` 无违规），说明**受保护工具组本身没有损坏**；坏掉的是"下一个该调哪个工具"的行为，而 v11 用追加指令去影响它，结果是模型在同一工具上打转。

## 3. r10 ↔ r11 直答（同任务集、机制唯一变量）

| 指标 | r10（v9/v10 机制） | r11（v11 机制） | 变化 |
|---|---:|---:|---|
| 插件 `tool_sequence_consistent` | 10/16 | **0/16** | **−10 样本（更差）** |
| 基线 `tool_sequence_consistent` | 16/16 | 16/16 | 持平 |
| 插件严格 / 语义质量 | 6/16 / 6/16 | **0/16 / 0/16** | **各 −6** |
| 基线严格 / 语义质量 | 16/16 / 16/16 | **13/16 / 13/16** | −3（见 §4 边界） |
| 插件首轮事实齐全 | 16/16 | **4/16** | −12 |
| 插件补救调用 | 0 | **12** | +12 |
| 插件完整总 token | 119,667 | **228,075** | +108,408 |
| 全批配对均值 | +11.81%（质量不劣不成立，故无效） | **−68.28%** | −80.09 pp |
| 请求数 | 319 | 362 | +43 |

**r10↔r11 直答**：v11 没有解决 r10 的工具序列缺陷。它把"少调工具/顺序不符"从 6/16 扩大为 16/16，把首轮事实齐全从 16/16 压到 4/16，把插件成本从 +11.81%（无效正收益）推成 −68.28%，并让 12 个样本以 `HandoffRecoveryError` 硬失败。**唯一达标的仍是 v9 层：全前缀回退 0、工具组恢复失败 0。**

## 4. 边界（必须随数字一起引用）

- 基线在本批从 r10 的 16/16 掉到 **13/16**（严格与语义同步），说明这四条合成任务在温度 0 下仍存在独立的轨迹波动；因此 r11 的插件退化量级不能用"基线不变"来对冲，但**插件 0/16 与基线 13/16 的差距远超该波动**。
- 本批审计 `errors` 非空（20 条 = 12 条 `wrong number of role outputs` + 4 条 `plugin tool sequence inconsistent with the frozen task` + 4 条 `tool evidence mismatch`；12 个硬失败样本在 `wrong number of role outputs` 分支被计数后不再进入逐字段核对，因此工具序列错误只出现在 4 个未硬失败的插件样本上），**本批任何数字都不得当作有效节省引用**。
- 只允许一次版本升级（v11）；本次已用尽。**不再开 v12/v13**。
- 本文件不改写 r5–r10 的目录、冻结与评分；r10 的 +11.81%/−68% 之外的结论原样保留。
- 全部为**合成运维任务**（固定工具返回、模拟双角色流程）；不含真实代码修复或生产工作流；4 任务 × 4 重复只有 4 个任务层观测点，不能给出置信区间。
- 三宿主数据不得合并。

## 5. 复现命令

```powershell
# 零 API 门（23 tests：两条指令的确定性、配对基线判定、审计对残缺工具序列必须判 not_yet_valid）
& .\.venv-crewai\Scripts\python.exe -m unittest tests.test_crewai_handoff_v11 -v
# 逐任务复算（只读）
& .\.venv-crewai\Scripts\python.exe .tooling\report_crewai_r10.py runs/stage5-crewai/crewai-two-role-r11-toolseq-multitask-01
# 独立审计（exit 1 = errors 非空，且明文写出不得当作有效节省）
& .\.venv-crewai\Scripts\python.exe -m experiments.audits.audit_crewai_handoff_v11 runs/stage5-crewai/crewai-two-role-r11-toolseq-multitask-01 --freeze integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R11_TOOLSEQ_MULTITASK_01.json
```

付费运行经密钥启动器执行（`DSH_MODULE=experiments.runners.run_crewai_handoff_v11`、`DSH_PYTHON=.venv-crewai\Scripts\python.exe`，先 `--plan` 再 `--mode api --confirm-send-synthetic-data`，日志 `runs/r11-paid-launch.log`）；密钥值不打印、不落盘。
