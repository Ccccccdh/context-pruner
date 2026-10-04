# OpenHands v57：预注册前缀筛选（含前缀纠错扩展）+ 可独立验证的分叉（冻结）

> v57 在 v56 的基础上再做一件**协议内**的事：把前缀用**同一语料臂的宿主纠错轮**延长
> （见 §3、§14）。压缩机制阈值、工具边界、提示词与预算数值一字未改。依据是 v55 与 v56
> 两个付费批次的实测：provider 经常在 3–6 次请求后就关掉首阶段，14k–16k 的前缀永远够不到
> 冻结的 28,000 触发阈值；而唯一一次 26 次请求的前缀实测 28,775，真实 condenser 已返回
> Condensation。两个旧批次的结论保持原样，不重写、不重打分。

状态：**已冻结**。冻结内容 = `integrations/openhands/run_validation_v55.py` 里的 `PROTOCOL`
字典 + 本文件描述的规则 + `manifest.json` 中全部源文件 SHA256。协议字段一旦与
`manifest.json` 不一致，runner 直接拒绝运行（`Frozen protocol changed`）。机制阈值
（触发/目标/硬上限）与 v53/v54 逐字相同，**本批没有改动任何压缩机制阈值**。

批次目录：`runs/stage5-openhands/django-multitask-v57-prefix-fork-01`

## 0. 本批新增/冻结的文件

| 文件 | 作用 |
|---|---|
| `integrations/openhands/run_validation_v57.py` | 正式 runner（预注册筛选 + 分叉 + 逐分叉持久化） |
| `integrations/openhands/run_validation_windows_v57.py` | Windows 兼容包装（TEMP 重定向、原子写重试，0 次 API 重试） |
| `integrations/openhands/prefix_selection_v55.py` | 预注册阶梯、选择规则、零 API 触发预测 |
| `integrations/openhands/branch_artifacts_v55.py` | 逐分叉持久化、索引、从自身字节重建 |
| `integrations/openhands/audit_validation_v57.py` | 独立审计（逐分叉重建 + 重跑宿主测试） |
| `tests/test_openhands_v57_formal_runner_gate.py` | 零 API 门控（持久化 / 篡改拒绝 / 筛选规则 / 触发预测） |
| v54 / v55 的 runner、协议与审计 | **只复用，不修改**（v54 与 v55 的冻结文件逐字节不变；`prefix_selection_v55.py`、`branch_artifacts_v55.py`、`same_prefix_fork_v54.py`、`budget_policy_v54.py`、`fork_tools_v54.py` 被 v56 直接复用） |

## 1. 这一批修的两个缺陷

v54 的取证结论是：分叉机制本身工作正常（3/3 分叉、冻结源有效、边界 0 违规、共享请求上限
被尊重），但

1. **前缀结束时宿主目标测试已经通过**，三臂分叉后无错可纠，插件臂 `condensation_events = 0`，
   `mechanism_triggered_plugin: false`，因此 −43.29% / −45.85% 只是轨迹噪声；
2. **三个分叉跑在同一绝对路径上**，批次结束时该路径只剩最后一条分叉的字节，审计只能对
   “恰好与前缀逐字节相同”的分叉重跑宿主测试，插件臂的宿主结论只能是运行记录值。

v55 针对这两点各做一件冻结的改动，并新增一个更硬的触发预测。

## 2. 预注册前缀筛选（付费请求发出前冻结）

固定任务集合（`validation_tasks_v49` 的冻结顺序）：

| 顺序 | 任务 | 实例 | 目标测试模块 |
|---:|---|---|---|
| 1 | `django_referenced_window_wrapping` | `django__django-17084` | `aggregation` |
| 2 | `django_lookup_allowed_foreign_primary` | `django__django-16661` | `modeladmin` |
| 3 | `django_list_editable_atomicity` | `django__django-16100` | `admin_changelist` |

语料臂固定为 `none`（无压缩）。**预注册尝试序列** = 第 0 档（工作请求上限 24 = 冻结的
`initial_work_requests`）遍历三个任务（按上表顺序）；若没有任何尝试被选中，第 1 档
（上限 26 = 冻结的 `first_phase_max_requests`）同样遍历；仍没有就第 2 档（上限 28，
phase-1 记录上限 30）；没有第 3 档。三档都在共享 36 次上限之内（28 + 2 收尾 = 30 ≤ 36），
第 2 档前缀最多消耗 30 次，仍给分叉留 6 次。

- **已通过宿主目标测试的任务不会在更高档重跑**：筛选规则要求“结束状态需要纠错”，
  重跑一个已经修好的任务不可能改变判定，只会多花钱。每次跳过都会写入
  `prefix-select/selection.json`（含原因），不是静默省略。
- **选中条件**（两者同时满足，且按上述顺序取**第一个**满足者）：
  1. 该次尝试的 phase-1 结束状态**未通过宿主目标测试**（`host_passed = false`）；
  2. 冻结的零 API 触发预测在该前缀状态上判定**插件 condenser 会压缩**（见 §3）。
- 选择只依赖该次尝试自身的结果，**与三条分叉的结果无关**（分叉此时还没跑），不做任何
  事后挑选；被拒绝的尝试全部保留在 `prefix-select/attempts.json` 与
  `select.json` 里，并按“各计一次”计入全程成本。
- 若整条阶梯跑完仍无尝试被选中：本批**直接报告“机制仍未触发”**，不生成
  `prefix-freeze.json`，不发任何分叉请求，退出码 3，且**不修改任何机制阈值**。
- 只有选中的那一次尝试会成为共同前缀：工作区快照、完整哈希、事件 ID 摘要、宿主反馈
  逐字字节、请求账本与预算状态一起冻结。

## 3. 零 API 触发预测（v54 预测失效的原因与修法）

v54 用“账本里最后一次调用的 `estimated_input`（28,275）> 冻结阈值（28,000）”判定会触发。
**该量不是 condenser 实际度量的量**：插件 condenser 度量的是 SDK view 的事件 token
（`get_total_token_count(view.events, agent_llm)`，包含工具 schema，用模型自己的分词器）。
对冻结的 v54 前缀事件日志做离线重放：前缀本身 **22,213** view token，加上冻结的纠错用户消息
（即分叉第一条请求真正看到的状态）**22,303** view token，都比 28,000 低约 6,000，
与 v54 记录的“0 次压缩”完全一致。也就是说 v54 的预测恰好错在方向相反的一侧。

v55 因此冻结**双条件**预测，两者都在付费请求之前计算，且都不发模型请求：

| 条件 | 量 | 冻结阈值 |
|---|---|---|
| 实测重放 | `View.from_events(前缀事件 + 冻结纠错用户消息)` 的 token 数 | **> 触发阈值 28,000（余量 0）** |
| 真实压缩器重放 | 用**同一个** `ContextPrunerCondenserV51`（同样阈值/契约/当前状态）在该 view 上跑 `condense` | 返回值必须是 `Condensation` |
| 账本余量 | 前缀最后一次 agent 调用的 `estimated_input` | ≥ 触发阈值 28,000 + **2,000** |

- v55 批次 01 把实测重放的余量定为 2,000 token，结果**闸门比机制本身更严**：批次 01 的
  第 2 次尝试实测 28,775 token（比 28,000 高 775），真实 condenser 返回的是
  **Condensation**（忘掉 52 个事件，28,775 → 14,640），只是被那 2,000 余量挡下。v56 因此把
  实测重放改为**机制自身的条件（余量 0）**：实测的量就是 condenser 的输入，不含测量误差，
  不需要余量去吸收；分叉时还会用同一函数再测一次并断言与冻结判定一致。账本规则保留
  2,000 余量，仍作为算术下界（批次 01 该次尝试账本估算 34,643，实测 28,775）。
- 分叉阶段每个分叉在发出第一条请求前，还会在 fork 后的事件上用同一函数**再算一次**
  （`trigger_prediction_at_fork`），并断言与冻结预测一致（`trigger_prediction_matches_freeze`）；
  审计再独立重放一次。
- 若预测不成立：按 §2 继续阶梯；阶梯走完仍不成立就如实报告“机制仍未触发”。

## 4. 逐分叉持久化与独立验证（关掉 v54 的审计缺口）

每条分叉结束时，在**它自己的目录** `branch-<position>-<arm>/` 下落盘：

| 文件 | 内容 |
|---|---|
| `final-files/` | 与冻结前缀**逐字节不同**的每个文件的字节；允许编辑的文件**无论是否改动**都持久化，以便审计验证“没改动”这句话 |
| `final-hashes.json` | 该分叉结束时的**全文件**无缓存哈希表（20,128 项） |
| `events.json` | 该分叉自己的事件流（类型、事件 ID、摘要、会话 ID） |
| `ledger.json` | 该分叉自己的请求账本（只记自身请求；携带量单列） |
| `report.json` | 该分叉自己的报告（含 `branch_artifacts` 的同一份记录） |
| `artifacts.json` | 每个持久化文件的 SHA256 索引 + 改动集合 + 边界标志 |

批次根还写 `branch-artifact-index.json`，把每条分叉上述文件自身的 SHA256 钉住，因此篡改
一个字节需要在三处保持一致才能通过。

**审计不再依赖“恢复共享路径再重跑”**：对每一条分叉，审计从冻结快照恢复 → 覆盖**该分叉
自己持久化的字节** → 核对重建后的全文件哈希表等于该分叉记录的 `final-hashes.json` →
在这份重建字节上重跑宿主目标测试，并与该分叉记录的结果逐项比较。三条分叉都这样做，
包括改动过文件的插件臂。

## 5. 分叉顺序与资源约束

- 顺序冻结为 `none → native_summary → pruner_v1`；`--rotate` 整体前移一位。
- **同一绝对路径顺序续跑**：三个分叉共用 `<batch>/workspaces/prefix`，每条开始前从
  `<batch>/prefix-snapshot` 恢复并比较全文件 SHA256；分支之间绝不并发。
- 前缀账本的**已消耗请求数与估算输入**通过 `PrefixCarryingBudgetPolicy` 带入每条分叉：
  纠错边界在 fork 点固定为 `min(36, 前缀请求 + 10)`，累计达到共享上限 36 时引擎直接抛
  `Frozen experiment call/token limit`。**没有任何分叉获得新的额度**；前缀越长，留给分叉的
  额度越少，这一关系在报告与审计里逐条列出。
- 分支账本只记分支自己的请求（不复制前缀行，否则与携带计数重复计费）。
- 前缀预算状态只作为 fork 点证据冻结，不套回分支策略。

## 6. 工具允许范围与文件边界（三臂完全相同）

| 工具 | 允许范围 |
|---|---|
| `scoped_editor` | 只能**编辑**该任务数据集 `reference.patch` 触及的单一文件（`--check` 逐任务核对）；越界或工作区外路径在 executor 层直接拒绝 |
| `scoped_symbols` | 只读，只能在冻结的研究文件集合内按 AST 查询定义 |
| `scoped_tests` | 只能跑固定的公开回归模块，最多 3 次、代码未变时返回缓存；不接受命令、路径或测试 ID |
| `finish` | 由预算策略在 `finish` 阶段单独放行 |

没有注册任何 shell。编辑范围与读范围对前缀和三臂逐字相同，工具名与
`budget_policy_v54.TOOL_NAMES` 由零 API 门控断言一致。

## 7. 预算与请求计量（与 v53/v54 相同，未改）

模型 `openai/deepseek-v4-flash`、温度 0、禁用 thinking、`num_retries=0`、单次最大输出 3072、
单次估算输入上限 80,000、每样本估算输入上限 2,000,000、每样本 Agent 请求上限 36、
原生摘要请求上限 16、纠错保留 10、收尾保留 2；触发上限 28,000 / 目标 22,400 / 硬上限
39,200 / 原生摘要 33,600。改动**只有**前缀挑选长度：phase-1 工作请求上限的阶梯
24 → 26（不超出共享 36）。每个请求（含失败请求与摘要请求）落盘到该臂自己的 `ledger.json`。

## 8. 主统计量与判读

- **主统计量**：分叉后分支自身的完整总 token（provider 输入+输出，含所有摘要请求）；
  失败与触顶分支全部保留。
- **次要统计量**：把前缀筛选的**每一次尝试各计一次**、再加强该分支的分叉后增量
  （`whole_run_tokens_all_selection_counted_once`），这样筛选成本不被隐藏，
  共同前缀也不会被重复计入任何一臂。
- 插件臂分叉后若一次都没有压缩，本批只报告“**机制未触发**”，不声称任何压缩效果。
- 质量以宿主目标测试 + 未改动上游回归模块 + 文件边界为准。
- 本批只有 **1 个前缀**，**不能**估计插件在任务总体上的平均效果。

## 9. 门控（付费前，零 API）

```powershell
cd 'C:\Users\LENOVO\Desktop\AI Agent\code'
New-Item -ItemType Directory -Force -Path .tooling\tmp | Out-Null
$env:TEMP='C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp'; $env:TMP=$env:TEMP
& .\.venv-openhands\Scripts\python.exe -m pytest `
    tests/test_openhands_same_prefix_fork_v54.py `
    tests/test_openhands_v54_branch_loopback.py `
    tests/test_openhands_v54_formal_runner_gate.py `
    tests/test_openhands_v55_formal_runner_gate.py -q `
    --basetemp="C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp\pt55"

# 真实 Django 树门控（三个冻结任务各准备一次工作区 + 负/正宿主基线，20-40 分钟）
DSH_RUNNER='integrations/openhands/run_validation_windows_v55.py'; $env:DSH_MODULE=''
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v55-prefix-fork-01 --check'
$env:DSH_PYTHON='.venv-openhands\Scripts\python.exe'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1
```

`--check` 的冻结期望（宿主补丁在每个任务里**新增**一个目标测试，所以公开模块正好少一个用例
且在未改动基线上全绿）：

| 任务 | 公开回归 | 宿主基线 |
|---|---|---|
| `django_referenced_window_wrapping` | 124 通过 | 125 项 `errors=1` |
| `django_lookup_allowed_foreign_primary` | 162 通过 | 163 项 `failures=1` |
| `django_list_editable_atomicity` | 74 通过 | 75 项 `failures=1, skipped=7` |

## 10. 运行与审计

```powershell
$env:DSH_RUNNER='integrations/openhands/run_validation_windows_v57.py'; $env:DSH_MODULE=''
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v57-prefix-fork-01 --check'
$env:DSH_PYTHON='.venv-openhands\Scripts\python.exe'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1
# 然后
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v57-prefix-fork-01 --run --resume'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1

& .\.venv-openhands\Scripts\python.exe integrations/openhands/audit_validation_v57.py `
    --out runs/stage5-openhands/django-multitask-v57-prefix-fork-01
```

## 11. 磁盘与计时

- `min_free_disk_gb = 1.5`：每次 prepare、每次分叉前都实测空闲空间，不足即中止并记录。
- 每次宿主评测产生的打分副本（`host-workspace`）在记录文件数与判定后**立即删除**，
  批次不再为每次评测保留一整份 Django 树（v54 的 5 份副本是磁盘主要开销）。
- 分段计时（v54 已有，v55 追加）：`prepare` 复制/哈希、快照创建与校验、快照恢复、
  恢复后全哈希、模型等待、宿主测试、**触发预测**、**分叉持久化**、打分副本清理、独立审计。

## 12. 与 v54 的差异清单

1. 前缀来源：固定单任务一次运行 → 预注册的两档三任务阶梯 + 双条件触发预测；
2. 触发预测：账本估算输入 vs 阈值（错 6,062 token）→ 真实 condenser 在真实 view 上的
   实测重放（并保留账本余量作为下界）；
3. 分叉持久化：只留共享路径最后一条 → 每条分叉落盘自己的字节/哈希/事件/账本/报告/索引；
4. 审计：只能重跑与前缀逐字节相同的分叉 → **每条**分叉都从自己的字节重建并重跑宿主测试；
5. 磁盘：每次评测保留打分副本 → 记录后删除；
6. 全程成本口径：前缀计一次 → 前缀筛选每一次尝试各计一次。
其余（任务集合、模型、endpoint、工具边界、导航、提示词除首阶段请求数外的文字、预算策略
数值、压缩器实现、触发/目标/硬阈值、成功判定）与 v54 逐项相同。

## 13. v56 修订的直接依据（v55 批次 01 付费实测）

v55 批次 01（`runs/stage5-openhands/django-multitask-v55-prefix-fork-01`，1,065,205 token）
按**预先冻结的规则**走完阶梯后判定 “mechanism still not triggered”，没有发任何分叉请求。
四次尝试的原始数据如下（全部保留在该批次 `prefix-select/` 与 `select.json`）：

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | condenser 视图 token | 相对 28,000 触发阈值 | 真实 condenser 重放 | 该次完整总 token |
|---|---|---:|---|---:|---:|---:|---|---:|
| 1 | `django_referenced_window_wrapping` | 0（上限 24） | **通过** | 25 | — | — | 未预测（无需纠错） | 440,529 |
| 2 | `django_lookup_allowed_foreign_primary` | 0（上限 24） | 未通过 | 26 | **28,775** | **+775** | **Condensation**（忘 52 事件，28,775 → 14,640） | 418,470 |
| 3 | `django_list_editable_atomicity` | 0（上限 24） | **通过** | 14 | — | — | 未预测（无需纠错） | 152,058 |
| 4 | `django_lookup_allowed_foreign_primary` | 1（上限 26） | 未通过 | 6 | 16,315 | −11,685 | View（未触发） | 54,148 |

结论与 v56 的两处修订一一对应：

1. **机制本身在第 2 次尝试就已经越过触发阈值**（28,775 > 28,000，且真实 condenser 返回
   Condensation），被挡下的只是 v55 预注册的 2,000 token 余量。因此 v56 把实测重放的闸门
   改回机制自身的条件（余量 0）——**压缩机制阈值未改**，改的是筛选闸门的保守程度。
2. **同一任务的 phase-1 长度在两次运行间从 26 次请求掉到 6 次**，两档阶梯不够用；v56 因此把
   阶梯扩到三档（24/26/28，仍全部在共享 36 次上限内），让“提高前缀长度”这条既定补救
   在冻结预算内真正可用。

v55 批次 01 的结论**不重写、不重打分**；它的 `select.json`、四次尝试报告、账本与宿主评测
输出保持原样，作为 v56 闸门修订的证据。批次 01 未产生前缀快照，也未运行任何分叉，
因此本批（v56）是从零开始的独立付费批次，不复用批次 01 的任何字节。

## 14. v57 的前缀纠错扩展（本批相对 v56 的唯一行为改动）

两个已完成的预注册批次把失败模式定死了（数值见 §13）：

| 批次 | 任务 | 档 | 宿主目标测试 | 请求 | condenser 视图 token | 重放 |
|---|---|---:|---|---:|---:|---|
| v55 批次 01 | 16661 | 0 | 未通过 | 26 | **28,775** | **Condensation** |
| v55 批次 01 | 16661 | 1 | 未通过 | 6 | 16,315 | View |
| v56 批次 01 | 16661 | 0 | 未通过 | 6 | 16,543 | View |
| v56 批次 01 | 16661 | 1 | 未通过 | 3 | 14,674 | View |
| v56 批次 01 | 16661 | 2 | 未通过 | 3 | 14,552 | View |

也就是说：**该 provider 经常在 3–6 次请求后就关掉首阶段**（视图只有 14k–16k），无论档位多高都
够不到冻结的 28,000 触发阈值；唯一一次 26 次请求的前缀实测 28,775，真实 condenser 当场返回
Condensation。因此 v57 采用冻结规则自己给出的补救办法——**在冻结预算内提高前缀长度**：

1. phase-1 结束、宿主目标测试**未通过**时，用**同一条会话、同一语料臂 `none`**继续跑
   **宿主反馈纠错轮**（`prefix_host_feedback_rounds = 3`）；每轮把逐字冻结的只读失败反馈发给
   模型，再在**分支自己的工作区**上重新跑宿主目标测试；任一轮通过就停（该次尝试随即不再
   是候选，因为“结束状态不需要纠错”）。
2. 前缀的纠错窗口在 phase-1 结束时固定为 `min(36, phase-1 请求数 + prefix_correction_call_limit)`
   （`prefix_correction_call_limit = 16`），与分支在 fork 点固定窗口的做法完全一致；
   窗口用尽或共享 36 次上限到达即停，并把原因写入报告。
3. **fork 点仍然是“宿主目标测试仍未通过的前缀”**；分支自己的纠错窗口依旧是
   `min(36, 前缀请求 + 10)`，没有任何分支获得新额度。前缀越长，分叉可用请求越少，报告与
   审计逐条列出。
4. 压缩机制阈值（28,000 / 22,400 / 39,200 / 33,600）、工具边界、提示词字节（除首阶段请求数）、
   模型、温度、预算策略数值与成功判定**一字未改**；实测重放闸门仍为 v56 的“机制自身条件 +
   真实 condenser 重放 + 账本下界”。

审计沿用 v56 的全部逐分叉重建与重跑；另外**每条分叉在发第一条请求前**都会在 fork 后的事件上
再算一次同一预测并与冻结判定比较（`trigger_prediction_matches_freeze`）。
若本批仍无法选出“宿主未通过 ∧ 实测触发”的前缀，则照实报告 “mechanism still not triggered”，
不再修改任何阈值。
