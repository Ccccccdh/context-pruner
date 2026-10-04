# v58：预注册前缀筛选（纠错扩展 + 触发即停 + 分支保留额度）+ 三分叉

状态：**前缀筛选成功、三条分叉全部完成；插件臂在分叉后真实压缩 1 次**。
分叉后成本差异因此**可以**归因到压缩，但**质量对比不可用**——见 §5（这是本批最重要的限制）。

- 冻结协议见 `integrations/openhands/PILOT_PROTOCOL_V58.md`
- 冻结清单 `manifest.json`（96 个源文件 SHA256，自身 SHA256
  `4463AEFB52316CAEA67E3857BB6FB492336CD3F6D165B8E458BD8DE2ED1601CD`）
- 独立审计：`integrations/openhands/audit_validation_v58.py`（结果见 `audit.json` / `AUDIT_REPORT.md`）

## 1. 零 API `--check`（付费前）

| 任务 | 公开回归 | 宿主基线 | 工作区文件数 |
|---|---|---|---|
| `django_referenced_window_wrapping` | 124 通过 | 125 项 `errors=1` | 20,128 |
| `django_lookup_allowed_foreign_primary` | 162 通过 | 163 项 `failures=1` | 19,972 |
| `django_list_editable_atomicity` | 74 通过 | 75 项 `failures=1, skipped=7` | 19,939 |

分段：`gate_prepare_seconds` 854.04、`gate_hash_seconds` 67.98、`host_test_seconds` 781.40。

## 2. 预注册筛选（2 次尝试，全部保留）

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | condenser 视图 token | 真实 condenser 重放 | 完整总 token |
|---:|---|---:|---|---:|---:|---|---:|
| 1 | `django_referenced_window_wrapping` | 0（24） | **通过** | 19 | — | 未预测（无需纠错） | 255,774 |
| 2 | `django_lookup_allowed_foreign_primary` | 0（24） | **未通过** | 32 | **29,490** | **Condensation** | 595,245 |

- 尝试 2：phase-1 用 26 次请求；宿主目标测试未通过，于是按 v58 规则延长**一轮**宿主反馈纠错
  （请求 27–32，窗口 `min(36−4, 26+16) = 32`）；轮末宿主仍未通过、预测触发（视图 29,490 >
  28,000，账本末次估算 36,254 ≥ 30,000，真实 condenser 返回 Condensation）→ **选中**，
  前缀立即冻结（不再多花一次请求）。
- 前缀筛选总计 **851,019** 完整总 token（各次尝试各计一次），其中选中前缀 595,245。
- 冻结前缀：事件 71 个，32 次 Agent 请求，0 次摘要请求。

## 3. 分叉结果（同一绝对路径顺序续跑，前缀计一次）

| 分叉 | 宿主目标测试 | 宿主判定是否实测 | 分叉后请求 | 携带前缀请求 | 分叉后完整总 token | 全程（筛选各尝试计一次） | 相对 none（分叉后） | 压缩次数 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `branch-0-none` | 未实测（见 §5） | 否 | 4 | 32 | **133,071** | 984,090 | 基准 | 0 |
| `branch-1-native_summary` | 未实测（见 §5） | 否 | 4 | 32 | **133,087** | 984,106 | −0.01% | 0 |
| `branch-2-pruner_v1` | 未实测（见 §5） | 否 | 4 | 32 | **71,469** | **922,488** | **+46.29%** | **1** |

- 三条分叉的 `restore_hash_equal`、`source_prefix_unchanged`、事件持久化一致、
  `trigger_prediction_matches_freeze`（分叉时实测视图 29,490，与冻结判定一致）全部为真；
  文件边界违规 0；没有一条分叉改动任何文件（`changed_files = []`），三条最终字节与前缀逐字节相同。
- **插件臂在分叉第一步真实压缩**：`before = 29,490 → after = 15,124` view token
  （忘掉 52 个事件，`hard_budget_exceeded: false`，见 `branch-2-pruner_v1/pruner-state.json`）。
  这是 v54/v55/v56 都没有出现过的 `mechanism_triggered_plugin = true`。
- `native_summary` 未触发（其阈值 33,600，前缀 29,490 未到），与设计一致。
- 分叉后 token 差 **+46.29%** 只描述这一条前缀上的一次压缩效果；本批只有 1 个前缀、1 个任务，
  **不能**外推。

## 4. 预算与请求（无分支获得新额度）

- 前缀消耗 32 次请求，三条分叉各携带 **32** 次（`carried_over_agent_requests = 32`），
  共享上限 36；纠错边界在 fork 点固定为 `min(36, 32+10) = 36`，因此每条分叉实际可用
  **4** 次请求（前缀剩下的部分，不是新额度）。
- 三条分叉各发 4 次请求（work, work, verify, finish），全部 `returned`，失败请求 0。
- 第 4 次请求之后 SDK 还要再走一步时，预算策略按冻结规则抛出
  `Frozen experiment call/token limit`（累计 32+4 = 36 到达共享上限）；runner 记录为
  `error_type = ConversationRunError`，并**没有**把它当作宿主判定。详见 §5。

## 5. 本批最重要的限制：分叉预算太薄，宿主判定没有实测

前缀占用了 36 次共享额度中的 32 次，分叉只剩 4 次请求。三条分叉都在这 4 次里
（读文件 → 再读 → 公开验证 → Finish）**没有落到任何编辑**，随后在第一个宿主轮内
撞上共享上限并中止：`host_rounds` 为空，因此 `final_host_passed = false` 是**报告默认值，
不是实测值**。改动：

- **不能**说“三臂质量等价”或“插件质量不劣”——本批根本没有分叉后的宿主判定；
- 分叉后成本差（+46.29%）发生在**同样的 4 次请求预算**下、从**同一份前缀**分叉，
  且插件臂实测压缩了一次（29,490 → 15,124），所以它是**压缩对成本的作用**；
  但它同时意味着三臂的轨迹不同（压缩改变了模型看到的内容），本批只有一次前缀，
  不能据此讨论质量影响、离散度或置信区间。
- 根因是**冻结阈值与共享上限之间的张力**：该任务上前缀要长到 28,000 token 需要约
  30 次请求，而共享上限只有 36 次。v55/v56 的第 2 个失败模式（provider 常在 3–6 次请求后
  关掉首阶段）与这个张力叠加，使“触发 ∧ 需要纠错 ∧ 分支还有意义可做”很难同时成立。
  本批证明了**机制可以触发**，同时量出了它需要的预算；扩大预算或调整阈值属于新协议，
  本批不做。

## 6. 分段计时（秒，实测）

| 分段 | 数值 |
|---|---:|
| `prepare_reuse_verify_seconds`（复用 `--check` 工作区并重算哈希） | 119.54 |
| `prepare_copy_hash_seconds`（第二个任务重新复制 + 哈希） | 312.01 |
| 模型等待（前缀）`prefix_model_wait_seconds` | 202.80 |
| 宿主测试（含选择与分叉，`host_test_seconds`） | 878.28 |
| 触发预测 `trigger_prediction_seconds` | 2.49 |
| 前缀快照 + 校验（`prefix_snapshot_seconds` / `snapshot_verify_seconds`） | 81.66 / 14.63 |
| 快照恢复（3 条分叉，`restore_workspace_seconds`） | 342.13 |
| 恢复后全哈希 `restore_hash_verify_seconds` | 44.39 |
| 模型等待（分叉）`branch_model_wait_seconds` | 147.77 |
| 逐分叉持久化 `branch_persist_seconds` | 60.82 |

结论与 v54 一致：**墙钟主要花在复制/哈希/宿主测试上，而不是模型等待**（模型等待合计 350.57 秒，
宿主测试 878.28 秒，快照恢复与哈希 520 秒）。打分副本在每次评测后删除，磁盘峰值约 6.5 GB 空闲。

## 7. 逐分叉持久化与独立验证（本批相对 v54 的核心交付）

每条分叉目录下都有 `final-files/`（与前缀不同的文件字节；允许编辑的文件无论是否改动都持久化）、
`final-hashes.json`（19,972 项全文件无缓存哈希）、`events.json`（该分叉自己的事件流）、
`ledger.json`、`report.json`（内含同一份 `branch_artifacts`）与 `artifacts.json`
（每个持久化文件的 SHA256 + 改动集合 + 边界标志）；批次根还有
`branch-artifact-index.json` 把上述文件自身的 SHA256 钉住。

审计据此**从冻结快照 + 每条分叉自己的字节**重建其最终工作区、核对重建后的完整文件哈希表，
再在重建字节上重跑宿主目标测试（本批三条分叉最终都等于冻结前缀，所以重跑结果与
“前缀未通过”一致，审计把“该分叉自己没有实测宿主轮”如实记录为
`host_verdict_measured: false`）。

## 8. 边界声明

- 本批 1 个真实任务 × 1 个共同前缀 × 3 臂；只有 **1 次**分叉后压缩，
  **不能**估计插件在任务总体上的平均效果，也不能外推。
- 分叉后质量判定不适用（§5）；`native_summary` 未触发；美元金额未知。
- 全程成本口径：前缀筛选的每一次尝试各计一次，共同前缀只在前缀处计一次，没有任何一臂
  被重复计入前缀费用。
