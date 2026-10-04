# v55：预注册前缀筛选 + 可独立验证的分叉（1 共同前缀 × 3 臂）

状态：**按冻结规则判定 “mechanism still not triggered”，未发任何分叉请求**（runner 退出码 3）。
本批的付费支出只发生在前缀筛选阶段（1,065,205 完整总 token），**没有**任何压缩效果结论。

- 冻结协议见 `integrations/openhands/PILOT_PROTOCOL_V55.md`
- 冻结清单见本目录 `manifest.json`（103 个源文件 SHA256，其自身 SHA256
  `943E8E5443D8A748593AAA49DFEB003E653B5322D3746787111A605706D9637B`）
- 本批**没有** `prefix-freeze.json`、**没有** `prefix-snapshot`、**没有** `branch-*` 目录，
  因此 `audit_validation_v55.py` 对本批不适用（没有可审计的前缀与分叉）

## 1. 零 API `--check`（付费前）

三个冻结任务各自准备一次干净工作区并跑负/正宿主基线，全部与冻结期望一致；
每次评测的打分副本在记录后删除，未留残余（`host-workspace` 计数 0）。

| 任务 | 公开回归 | 宿主基线 | 工作区文件数 |
|---|---|---|---|
| `django_referenced_window_wrapping` | 124 通过 | 125 项 `errors=1` | 20,128 |
| `django_lookup_allowed_foreign_primary` | 162 通过 | 163 项 `failures=1` | 19,972 |
| `django_list_editable_atomicity` | 74 通过 | 75 项 `failures=1, skipped=7` | 19,939 |

分段计时：`gate_prepare_seconds` 847.79、`gate_hash_seconds` 228.81、
`host_test_seconds` 656.05。

## 2. 预注册筛选的付费结果（全部保留）

规则（付费前冻结，见协议 §2）：语料臂 `none`；第 0 档（工作请求上限 24）按冻结顺序遍历
三个任务，没有合格尝试再走第 1 档（上限 26）；已通过宿主测试的任务不在更高档重跑；
合格条件是“phase-1 结束状态未通过宿主目标测试” **且** “冻结的零 API 触发预测判定会压缩”。

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | condenser 视图 token | 相对 28,000 | 真实 condenser 重放 | 完整总 token | 墙钟秒 |
|---:|---|---:|---|---:|---:|---:|---|---:|---:|
| 1 | `django_referenced_window_wrapping` | 0（24） | **通过** | 25 | — | — | 未预测（无需纠错） | 440,529 | 82.56 |
| 2 | `django_lookup_allowed_foreign_primary` | 0（24） | 未通过 | 26 | **28,775** | **+775** | **Condensation**（忘 52 事件，28,775 → 14,640） | 418,470 | 214.44 |
| 3 | `django_list_editable_atomicity` | 0（24） | **通过** | 14 | — | — | 未预测（无需纠错） | 152,058 | 95.43 |
| 4 | `django_lookup_allowed_foreign_primary` | 1（26） | 未通过 | 6 | 16,315 | −11,685 | View（未触发） | 54,148 | 34.39 |

- 规则指纹：`8da36d8225028b718dd990e01c87f8b1a8efd3238b76a086d6aae3492ff3b133`
- 尝试顺序与冻结序列逐项一致（`select.json.plan` 六项，实际执行前四项）。
- 结果：没有任何尝试同时满足两个条件 → `select.json` 记录
  `complete: false`、`reason: no attempt in the frozen ladder both failed the host target
  test and satisfied the frozen trigger prediction: mechanism still not triggered`。
- 按协议**不修改任何机制阈值、不事后改判、不重跑挑选**，也不生成前缀快照、不发分叉请求。

## 3. 本批最重要的取证：机制在第 2 次尝试其实已经越过阈值

第 2 次尝试（`django_lookup_allowed_foreign_primary`，第 0 档，26 次 Agent 请求）：

- 宿主目标测试**未通过**（`modeladmin` 163 项，`failures=1`）——前缀确实停在需要纠错的状态；
- `View.from_events(前缀事件 + 冻结纠错用户消息)` 实测 **28,775** token，**高于**冻结触发阈值
  28,000（余量 +775）；
- 用**真实** `ContextPrunerCondenserV51` 在同一 view 上重放：返回 **Condensation**，
  `before=28,775 → after=14,640`，忘掉 52 个事件、选中 58 个证据单元
  （`hard_budget_exceeded: false`）；
- 账本规则也通过（该次最后一次调用 `estimated_input = 34,643 ≥ 28,000 + 2,000`）。

**唯一**挡住分叉的是 v55 自己预注册的 2,000 token 实测余量（`measured_ok: false`）。
也就是说：本批证明了“机制触发”这件事在真实前缀上**可达**，但 v55 的闸门比机制本身更严。
v55 的这条余量本是用来防止“擦边即触发”的不确定性；实测证据（v54 重放与 v54 记录一致、
本批第 2 次尝试真实 condenser 返回 Condensation）说明实测重放**就是** condenser 的输入，
没有需要余量吸收的测量误差。

第 4 次尝试也说明另一件事：同一任务的 phase-1 长度在两次运行间从 26 次请求掉到 **6 次**
（视图只剩 16,315 token），两档阶梯不足以在冻结预算内把前缀做长。

## 4. 本批实现并门控、但未在真实数据上演示的部分

以下机制在本批**代码与零 API 门控**中已完成并通过，但因为本批没有分叉，
**没有**真实数据样本：

1. 逐分叉持久化（`final-files/`、`final-hashes.json`、`events.json`、`ledger.json`、
   `report.json`、`artifacts.json` + 批次级 `branch-artifact-index.json`）；
2. 审计从**每条分叉自己的字节**重建工作区并重跑宿主目标测试；
3. 篡改拒绝（改字节、改索引、改哈希表、删索引文件、恢复被记录为删除的文件）；
4. 共享 36 次请求上限的携带与分叉后增量统计。

零 API 门控 `tests/test_openhands_v55_formal_runner_gate.py`（9 项）覆盖以上全部内容，
并在合成 Django 形状工作区上跑通了真实 SDK 会话/fork/工具执行器/压缩器路径，
其中包含“注入不触发预测 → 引擎拒绝分叉、不建前缀快照、不建分叉目录”的诚实中止路径。

## 5. 边界声明

- 本批只有**筛选阶段的 4 次真实尝试**，没有共同前缀、没有分叉、**没有**压缩效果结论；
  表中 token 数是各次尝试各计一次的实际支出，不是压缩收益。
- `mechanism_triggered_plugin` 在本批**不适用**（没有插件分叉）。
- 三次尝试的 `sdk_status` 分别为 `finished` / `finished` / `stuck` / `finished`；
  第 3 次触发 SDK 的 loop 检测但宿主目标测试仍判通过，记录照实保留。
- 本批不能估计插件在任务总体上的平均效果，也不能外推到其他宿主。

## 6. 由本批证据驱动的下一版（v56，独立付费批次）

`integrations/openhands/PILOT_PROTOCOL_V56.md`：

1. 实测重放闸门**去掉 2,000 余量**，改用机制自身条件（view token > 28,000），
   并在分叉时用同一函数重测、断言与冻结判定一致；
2. 冻结的 phase-1 阶梯扩到**三档（24 / 26 / 28）**，全部在共享 36 次上限内
   （第 2 档前缀最多 30 次，仍给分叉留 6 次）；
3. **压缩机制阈值（28,000 / 22,400 / 39,200 / 33,600）一字未改**。

v55 批次 01 的结论保持原样，不重写、不重打分；批次 02（v56）不复用本批任何字节。
