# OpenAI Agents v7 适用边界小试（未付费：离线投影为 0 %）

2026-10-04。本批**没有发起任何付费请求**：v7 的离线投影为 **0 %**（既不大于 0），按约定"离线投影为负/为零就不许付费"直接停在这里。目录里只有**零 API 离线栅格**的产物：`manifest.json`（`offline_gate: true`、`paid_requests_sent: 0`）、`samples.jsonl`、`report.json`、`input-evidence/*.jsonl`、`audit.json`。所有记录的 `api_request_attempts` 都是本地假模型调用的计数，**没有一个请求离开本机**；审计字段 `attempts: 39` 是这些本地调用次数，不是供应商请求数。

协议 `integrations/openai_agents/REPO_DIAGNOSTIC_V7_APPLICABILITY_BOUNDARY_PROTOCOL.md`；冻结 `integrations/openai_agents/REPO_DIAGNOSTIC_V7_APPLICABILITY_BOUNDARY_FREEZE.json`（29 份源码/协议/测试/公开输入哈希，含审计器自身源码）；边界结论 `integrations/openai_agents/PLUGIN_APPLICABILITY_BOUNDARY.md`。

## 0. 预注册验收线与本批判定

预注册（写入冻结与协议）：**插件严格质量 >= 基线 且 完整总 token 配对均值为正**。

- 插件严格质量 **3/3**，基线 **3/3** -> 质量门槛**通过**（离线假模型固定轨迹，两臂同轨迹）。
- 完整总 token 配对均值 **0.00 %**（正 **0/3**）-> 成本门槛**不通过**。

**判定：not-yet-valid（离线）。** 这不是"机制失败"，而是本轮的**设计结论**：在基线只走 2 次调用的 Django 任务上，v7 的可省略轮数为 0，插件与基线载荷逐字节相同，节省恒为 0。样本量保持最小（1 任务 x 3 重复 x 3 臂 = 9 样本），未扩样、未付费。

## 1. v7 做了什么

v7 **不发明第七种压缩写法**，只给 v6 已经审计过的缩减加一条新近度策略：

```
elidable(turn) := newest_turn - turn >= RECENT_TURNS_KEPT   （冻结 N = 1）
```

- 只压缩比最新工具轮**至少早一轮**的轮次；最新轮与所有模型消息逐字不动；
- 缩减本体、字面注册表、字面生存守卫、指针完整性、行区间划分、整份回退计数**全部沿用 v6，未改判定语义**（测试用 `assertIs` 断言 `_elide` / `_structural_violation` / `_carrier_loss` 仍是 v6 的函数对象）；
- `N = 1` 是**对插件最有利**的设置（N 越大可省略轮数越少），因此用它陈述边界是最严格的口径。

## 2. 主结果（零 API 离线栅格，9 样本）

| 臂 | 冻结严格成功 | 实际输入 token 均值 | 完整总 token 均值 | 模型调用均值 | 省略源数 |
|---|---:|---:|---:|---:|---:|
| 无压缩 | **3/3** | 14,715.0 | 14,811.0 | 4.00 | 0 |
| pruner_v1（v7 边界策略） | **3/3** | **14,715.0** | **14,811.0** | **4.00** | **0** |
| 原生摘要 | **3/3** | 14,633.0 | 15,887.0 | 4.00 | 0 |

配对完整总 token 变化：**0.00 % / 0.00 % / 0.00 %**（正 0/3）。

**关键断言（全部通过）**：

- 插件与基线的**每一次模型输入逐字节相同**：审计比对两臂逐边界 `input_sha256`（`plugin_samples_with_payload_identical_to_baseline = 3`，`shared_boundary_count = 4`）；
- `recency_boundary_elided_sources = 0`、`pointer_retention_elided_sources = 0`、`selective_retention_elided_outputs = 0`；
- `projectable_saving_rate = 0.0`（3/3）；
- **最新轮从未被改动**：每次调用逐条目 `output_elided` 全为 False，`most_recent_tool_group_kept_in_full = True`；
- `task_anchor_restore_failures = 0`、`task_restore_fallbacks = 0`、`budget_fallbacks = 0`（两臂），逐调用账本全零；
- 四个已注册字面在每次模型边界都在（`existing_annotations` 在其承载工具输出到达后的边界上都在）。

## 3. 为什么"无可压缩"是必然，而不是失败

结构原因（离线证据逐边界可查）：Django 轨迹每次调用的工具项构成**恰好一个轮**（最新的那一轮），且每个输出在本次运行中被完整送达过；`recency_elidable_indices` 在**每次 `filter_before` 都是空集**。于是插件的候选集为空，v6 的缩减一条也不会触发，载荷自然与基线相同。

数字链（来自 v5 的零 API 成本分解，`.tooling/diagnose_v5_cost_regression.py`，残差 ~ 9e-13）：

1. 基线只走 **2** 次调用、完整总 token **2,962**；
2. 插件多走一次调用的代价 **+2,335.8** input token = v5 输入回退的 **99.85 %**，是整条基线的 **79 %**；
3. 重传固定字节反而是**收益 -136.7 token**（量级只有那次轮次的 1/17）；
4. 因此短任务上只有两种结果：**机制不改可见文本 -> 0 %**（本批），或**改可见文本 -> 诱发额外轮次 -> 必然为负**（v5 -79.79 %、v6 -176.36 %，v6 更因模型重新取回而被 `MaxTurnsExceeded` 截断）。

## 4. 边界判据（可证伪）

> 设基线完成同任务需 **K** 次模型调用、最近 **N** 轮逐字保留，则插件可省略轮数 = `max(0, K - 1 - N)`。
> **`K <= N + 1` 时插件一个字节都省不下来**（收益恒 0）；**任何改变可见文本的机制在该区间必然为负**。
> 只有 **`K > N + 1`**（存在远早于当前轮、且当前轮无需重读的历史）插件才**可能**取胜。

按 K 排列（N = 1）：K=2 -> 0 轮可省（本批，实测 0 %）；K=3 -> 1 轮可省（v5/v6 实测仍为负）；K >> 3 -> 长会话，唯一可能取胜的区间，已知参考点是 **OpenHands 933,719 token 前缀（分叉后 +46.29 %，全程 +15.51 %）**——**属另一宿主，禁止与本宿主百分比合并**。

本仓库**未测量**、因此**不作断言**的输入：本宿主上的长会话（`K >> N + 1`）、`N >= 2`、多任务、其他宿主、真实代码修复任务、"省略多少行会诱发重新取回"的阈值、以及真实轮次的可预测性（v6 已证明零 API 假模型无法预测轮次）。详见 `PLUGIN_APPLICABILITY_BOUNDARY.md` 第 6 节。

## 5. 零 API 门控与审计

- `tests.test_openai_agents_evidence_safe_retention_v7`：**13 tests 全通过**（离线栅格无网络、插件载荷与基线逐字相同、最新轮未被改动、守卫与恢复计数全零、字面逐边界在位、**缩减未被策略废掉**（人工 5 轮载荷上更早的 4 轮被省略且最新轮逐字保留）、命题 `K <= N+1 -> False` 自检、审计篡改自检、离线为 0 时不得付费）。
- 既有 v1-v6 模块（含 v5 组基线诊断、v6 指针门控）**75 tests 一次命令全通过**（3 项父门控跳过），证明本轮未破坏任何既有判定。
- 独立审计 `experiments/audits/audit_openai_agents_repo_diagnostic_v7.py`：`complete: true`、`issues: []`、`evidence_checked: 9`；冻结集包含审计器、v7 三个模块与复用的 v6 门控脚本。

## 6. 边界与限制

- 本批是**离线**结果：假模型按固定轨迹作答，**不构成**付费行为证据；本批任何"轮次/成本"数字都来自本地假模型，真实轮次只能由付费批次测到（v6 的教训）。
- 只有 1 个任务、3 个重复、9 个样本；该任务在 v1-v4 已参与调参，不是盲测确认批。
- v1-v6 源码、协议、冻结与结果目录未被修改；v7 使用新文件、新协议、新冻结与新批次 ID；本批未做任何事后改判。
