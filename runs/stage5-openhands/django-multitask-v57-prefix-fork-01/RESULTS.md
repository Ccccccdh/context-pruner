# v57：前缀纠错扩展（触发成功但吃满共享额度，分叉被正确拒绝）

状态：**前缀筛选成功并已冻结；分叉阶段按冻结规则拒绝开工**（`Prefix already consumed every
agent request`）。这是 v57 设计缺陷的实测证据，也是 v58 两处修订的依据。

- 冻结协议见 `integrations/openhands/PILOT_PROTOCOL_V57.md`
- 冻结清单 `manifest.json` 自身 SHA256
  `28FAEC779A15245EEFF065986CAC62CC3D9B47A1460324F74F0E42D7A1D212FF`
- 本批**有** `prefix-freeze.json` 与 `prefix-snapshot`（付费产出、已冻结），
  但**没有**任何分叉报告；`audit_validation_v57.py` 对本批不适用（没有分叉可审计）

## 1. 预注册筛选（2 次尝试，全部保留）

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | condenser 视图 token | 真实 condenser 重放 | 完整总 token |
|---:|---|---:|---|---:|---:|---|---:|
| 1 | `django_referenced_window_wrapping` | 0（24） | **通过** | 25 | — | 未预测（无需纠错） | 348,277 |
| 2 | `django_lookup_allowed_foreign_primary` | 0（24） | **未通过** | **36** | **34,474** | **Condensation** | **754,204** |

- 第 2 次尝试：phase-1 用 26 次请求；宿主未通过 → 按 v57 规则用**同一会话、同一语料臂**继续跑
  宿主反馈纠错轮（窗口 `min(36, 26+16) = 36`）；第 1 轮用掉请求 27–36（10 次），轮末宿主仍未
  通过、预测触发（视图 34,474 > 28,000）→ **选中并冻结前缀**（事件 87 个，估算输入 883,166）。
- 前缀筛选总计 **1,102,481** 完整总 token（各次尝试各计一次）。

## 2. 分叉阶段为什么拒绝（这是正确的行为）

冻结前缀已消耗 **36/36** 次共享额度。分叉策略在构造时按冻结规则
（`budget_policy_v54.PrefixCarryingBudgetPolicy`）抛出：

```
ValueError: Prefix already consumed every agent request; a branch cannot receive a
fresh allowance in the same experiment
```

“任何分支都不获得新额度”是 v53 起的冻结规则，因此**拒绝是正确的**；错的是 v57 的前缀窗口
`min(36, phase-1 请求 + 16)` 没有给分支留任何额度。本批因此**没有**分叉请求、**没有**
分叉后成本或质量数据。

## 3. 本批证明了什么

1. **前缀可以做到既“需要纠错”又“越过触发阈值”**：34,474 view token（比 28,000 高 16,474），
   且真实 `ContextPrunerCondenserV51` 在同一 view 上返回 Condensation。这结束了 v55/v56
   “触发 ∧ 需要纠错”从未同时出现的局面。
2. **触发所需的请求数与该任务集合的共享上限直接冲突**：要长到 28,000 token，前缀需要约
   30 次请求，而每样本共享上限只有 36 次。这正是 v58 需要“触发即停 + 给分支预留额度”
   的原因（`branch_request_reserve = 4`），也是本工作流目前最硬的结构性约束。

## 4. 边界声明

- 本批有 1 个冻结前缀、0 条分叉，**没有**压缩效果结论、**没有**质量结论；
  `mechanism_triggered_plugin` 对本批不适用（没有插件分叉）。
- 前缀的 754,204 token 是一次真实支出，按“各次尝试各计一次”计入全程口径。
- v57 批次保持原样，不重跑、不重打分；v58 是不复用本批任何字节的独立付费批次。
