# v59：预注册共享请求上限（前缀预算 + 分支保留额度）+ 实测逐分支宿主判定 + 付费前冻结审计器

状态：**五批付费实测全部完成；本批（03）是唯一跑完“前缀冻结 + 三分叉”的批次**。
结论必须分两层读：**机制侧成功**（前缀真实越过触发并压缩，三条分叉各拿 10 次请求、
各自独立持久化、文件边界 0 违规），**质量侧未取得判定**（`branch_host_verdicts_measured:
false`，见 §4）。按任务约定，**本批不主张任何质量结论**。

- 冻结协议见 `integrations/openhands/PILOT_PROTOCOL_V59.md`（含 §2b/§2c/§2d 三次预注册修订）
- 冻结清单 `manifest.json`（111 个源文件 SHA256，本批 §6 给出自身哈希）
- 独立审计：`integrations/openhands/audit_validation_v59.py`（**本批未完成审计**，原因见 §7，
  如实记录，不以运行记录冒充审计结论）

## 1. 五批全景（全部保留，绝不覆盖）

| 批次 | `--check` | 付费段结果 | 前缀 | 三条分叉 | 候选尝试 token（各计一次） |
|---|---|---|---|---|---|
| `…v59-prefix-fork-01` | 通过 | **整批中止**：第 1 次尝试第 15 次请求抛未捕获异常 | 无 | 无 | 44,896 |
| `…v59-prefix-fork-02` | 通过 | 阶梯走完，**无合格前缀** | 无 | 无 | 849,267 |
| `…v59-prefix-fork-03` | 通过 | **前缀冻结 + 三条分叉全部完成** | **有** | **3/3** | 1,038,300 |
| `…v59-prefix-fork-04` | 通过 | 阶梯走完，**无合格前缀** | 无 | 无 | 285,709 |
| `…v59-prefix-fork-05` | 通过 | 阶梯走完，**无合格前缀** | 无 | 无 | 870,186 |

固定任务集合（冻结顺序，与 v55–v58 相同）：`django_referenced_window_wrapping`
（`django__django-17084`）、`django_lookup_allowed_foreign_primary`（`django__django-16661`）、
`django_list_editable_atomicity`（`django__django-16100`）。

## 2. 预注册的上限算术（本批唯一的行为性改动，付费前冻结）

测得的结构性张力：本任务上前缀要长到越过冻结的 28,000 token 触发阈值，需要约 30 次 Agent
请求（v55：26 次 → 28,775；v58：32 次 → 29,490；**本批 03：42 次 → 36,917**），
而 v54–v58 的每样本共享上限只有 36，v58 因此只给分叉剩 4 次，三条分叉全部无宿主判定。
v59 不改任何压缩机制阈值，只预注册新的共享上限与保留额度：

```
共享上限 84 = 前缀预算上限 42 + 每臂保留额度 14 × 3 臂
prefix_correction_window = min(84 − 14, 42, work_used + prefix_correction_call_limit)
```

- `prefix_budget_cap = 42`：前缀最多花 42 次；runner 与审计都用恒等式断言 `42 + 14×3 = 84`，
  前缀一旦越过 42 次即 `raise`（**拒绝运行，不静默收缩**）。
- `branch_request_reserve = 14`：分叉在 fork 点至少保有 14 次。分支自己的纠错窗口在 fork 点
  固定为 `min(84, 前缀请求 + 10)`，即分支纠错阶段最多发 **10** 次请求；`14 ≥ 10` 由
  `ensure_prefix_reserve` 断言，否则拒绝 fork。
- `max_total_estimated_input_per_sample` 2,000,000 → 4,670,000（请求上限乘以 84/36 = 2.333 的
  **同一倍数**），否则新增请求会被估算输入上限而不是请求上限挡住。单次估算输入上限 80,000 未改。
- `minimum_prefix_requests_to_trigger = 30`：把实测到的“越过触发所需前缀长度”写进协议，
  使 42 的预算上限可被事后对照实测复核。

**实测证明这套算术成立**（本批 03）：前缀花满 **42** 次（= 预算上限），三条分叉各继承
**42** 次携带量并各消耗 **10** 次，共享上限 84 全程未被违反，文件边界 0 违规。

## 3. 本批（03）前缀：第一次真正越过触发阈值并冻结

| 项 | 值 |
|---|---|
| 任务 / 档 | `django_lookup_allowed_foreign_primary` / 第 0 档（工作请求上限 24） |
| 宿主目标测试（fork 点） | **未通过**（因此需要纠错） |
| Agent 请求 | **42**（恰好用满预注册的前缀预算上限） |
| condenser 视图 token | **36,917**（触发阈值 28,000，**余量 +8,917**） |
| 真实 condenser 重放 | **`Condensation`**（36,917 → 14,777，忘掉 80 个事件，`hard_budget_exceeded: false`） |
| 账本末次估算输入 | 45,606（下界 28,000 + 2,000 满足） |
| 前缀完整总 token | **933,719** |
| 事件数 / 摘要请求 | 89 / 0 |

选择规则按序复算一致，规则指纹 `447fd51c37e76161…`（与 v58 **逐位相同**，阶梯与闸门未变）。
被拒绝的尝试（`django_referenced_window_wrapping`，11 次请求，宿主**通过** → 不再作为候选）
按其自身结果计入全程成本。

## 4. 本批（03）分叉：三条全部完成，机制触发，但**宿主判定仍非实测**

| 分叉 | 臂 | 分叉后完整总 token | 全程（各尝试计一次） | 分叉后请求 | 携带前缀请求 | 宿主判定实测 | 压缩次数 |
|---|---|---|---:|---:|---:|---:|---|---:|
| `branch-0-none` | none | **442,486** | 1,480,786 | 10 | 42 | **否** | 0 |
| `branch-1-native_summary` | native_summary | **288,610** | 1,326,910 | 10 | 42 | **否** | **1** |
| `branch-2-pruner_v1` | pruner_v1 | **212,833** | **1,251,133** | 10 | 42 | **否** | **1** |

- 三条分叉的 `restore_hash_equal`、`source_prefix_unchanged`、
  `branch_events_persisted_match_observation`、`trigger_prediction_matches_freeze`
  **全部为真**；文件边界违规 **0**；分叉事件后缀互不重叠；
  各自持久化 `final-files/`、`final-hashes.json`、`events.json`、`ledger.json`、
  `report.json`、`artifacts.json`，批次根 `branch-artifact-index.json` 钉住这些文件自身哈希。
- **机制真实触发**：`mechanism_triggered_plugin: true`，插件臂与原生臂各压缩 **1** 次。
  分叉后（成本侧）相对 `none`：`pruner_v1` **+51.90%**、`native_summary` **+34.78%**；
  全程口径：**+15.51%** / **+10.39%**。
- **但宿主判定没有实测**：三条分叉把整个 10 次纠错窗口**一次性用在第一轮 burst 里**，
  burst 以 `RuntimeError: Frozen correction request limit`（`budget_policy_v42` 第 35 行）结束，
  runner 当时把该异常当作分叉失败处理，**跳过了紧随其后的宿主目标测试**，因此
  `host_rounds` 为空、`host_verdict_measured: false`。批次级因此报
  **`branch_host_verdicts_measured: false`（0/3）**、`quality_verdict_obtained: false`。
- **这是本批最重要的限制**：上表的成本差是**成本侧证据**（发生在同一前缀、同样 10 次请求下，
  且插件臂实测压缩 1 次），但本批**没有**分叉后的宿主判定，因此**不能说**“三臂质量等价”、
  “插件质量不劣”，也不能把 +51.90% 当成质量等价的节省。

## 5. 五批实测把“上限算术”这条路的边界量出来了（本批 04/05 的关键发现）

批 04 与批 05 都走完阶梯且**没有**合格前缀，原因与批 02 不同，也与“预算不够”相反：

| 批次 | 任务 | 请求 | 宿主目标测试 | 视图 token |
|---|---|---:|---|---:|
| 05 | 17084 | 10 | **通过** | — |
| 05 | 16661 | **41** | **通过** | — |
| 05 | 16100 | 6 | **通过** | — |
| 04 | 17084 / 16661 / 16100 | 9 / 12 / 6 | **全部通过** | — |

也就是说：**把共享上限从 36 抬到 84、并把前缀纠错窗口放到 42 之后，模型在这 41–42 次
请求里真的把这个特性写对了，宿主目标测试随之通过**，于是冻结规则要求的
“fork 点必须**未通过**”不再成立 —— 前缀越长，越容易修好；修好了就按规则不再是候选。
批 02（窗口 22）留下了“未通过但只到 21,881 < 28,000”，批 03（窗口 42）留下了
“未通过且 36,917 > 28,000”，批 04/05 则“未通过”本身消失。

**这条算术的失败点因此要写清楚**：它不是“前缀不够长”，也不是“上限不够大”，而是
**同一份 42 次请求预算既要把前缀推到 28,000 token 以上、又要求模型在推的过程中不要修好**。
在冻结阈值与冻结阶梯下这两个条件互相拉扯：跨过触发所需的请求数（约 30–42）恰好也是
模型把该任务修对所需的请求数。要同时满足它们，需要的是**更长的前缀在“仍然失败”的状态下**
（例如不同的任务选择、或不同的提示词），那属于**新协议、新任务集合与提示词**，
本批按约定**不修改任何机制阈值、阶梯、提示词字节**，因此报告 “mechanism still not triggered”。

## 6. 冻结与清单（付费前固定，运行中零漂移）

| 批次 | `manifest.json` SHA256 | 源文件数 | `--check` 后源漂移（见 §7） |
|---|---:|---:|---|
| 01 | `6AC74E06BDC6A214F2E9A22B948796541D4D82979A9189F7FE0B8050E1B7CB5A` | 111 | runner / 审计器 / 协议 / 门控（后续修订） |
| 02 | `5B892202F14D06CCBAEF77CF3EC91FFE6E5E2218E4431AD2A391F260F723959D` | 111 | runner / 审计器 / 协议 / 门控（后续修订） |
| **03** | **`D147F3907EA4FF2EA0DD51AB070F240276BFAFD6FF609120F4251A0BE9BE6277`** | 111 | runner（§2d 分支守卫）/ 审计器（版本断言）/ 协议 |
| 04 | `2B19D21B952DD4230974C220DF8146760B79CEA4B1CB0CF3E30DB7888DCD1ACB` | 111 | 审计器（一次修订） |
| 05 | `F83286C724DC49CFCC340DE07B2367CAF767D026166DDA7AB1C4B0E921B45800` | 111 | **0（当前工具即冻结版本）** |

即：**只有批 05 与最终工具逐字节一致**，但批 05 恰好没有选中前缀；
**有分叉结果的批 03 冻结的是 b03 版 runner**，其漂移已被逐字节复现证明确实只是
§2d 的分支守卫（`.tooling/v59_b03_provenance.py`：
撤销该守卫与版本串后 SHA256 精确复现冻结值 `9c01ccd01a294e52…`）。

本批（03）`manifest.json` 里冻结了 **审计器本身**
（`integrations/openhands/audit_validation_v59.py`）、正式 runner、
Windows 包装、本协议文档、v59 零 API 门控与全部复用模块；`--check` 之后与付费运行之前、
之中、之后都实测**零漂移**。冻结字段（截自本批 `manifest.json`）：

```
max_agent_calls_per_sample       : 84
branch_request_reserve           : 14
prefix_budget_cap                : 42
prefix_correction_call_limit     : 36
max_total_estimated_input_per_sample : 4670000
trigger_input_tokens             : 28000   （未改）
pruner_target_tokens             : 22400   （未改）
pruner_hard_tokens               : 39200   （未改）
native_max_tokens                : 33600   （未改）
phase1_work_request_ladder       : [24, 26, 28]      （未改）
trigger_prediction_margin_tokens : 0        （未改）
trigger_prediction_ledger_margin_tokens : 2000 （未改）
```

## 7. 审计：本批未能完成，原因如实记录

`integrations/openhands/audit_validation_v59.py` 被运行后立刻拒绝审计本批：

```
AssertionError: the auditor integrations/openhands/audit_validation_v59.py is not the
revision this batch's gate froze: the audit that judges a batch must itself be the frozen one
```

原因是**工具在批次冻结之后仍被继续修订**：批 03 冻结后，为修掉 §4 的分叉缺口
（§2d 的预注册修订）又改了 runner，并把审计的版本断言放宽为接受 `-bNN` 后缀、
之后的又一次修订才把审计本身定稿。按 v59 的严格规则（这也是用户要求的那条），
**审计器必须是冻结版本**，因此本批（其冻结快照早于最终审计器）无法被自证式审计，
**不能**用运行记录冒充审计结论。这条限制被保留而不是被 `--accept-post-gate-tooling` 绕过：
v59 审计**拒绝**该开关（源码内 `assert not args.accept_post_gate_tooling`）。

因此本批提交的是**运行记录 + 可复用机器 + 预注册协议 + 零 API 门控**，
其中 `comparison.json` / `branch-verdicts.json` 的每一个数字都可由
`select.json`、各分叉 `report.json` / `ledger.json` / `artifacts.json` 与
`branch-artifact-index.json` 独立复算；缺的是**独立审计器对重建字节的重跑**。

可复用的部分（对下一批直接有效）：
- 逐分叉持久化与篡改拒绝（`branch_artifacts_v55`，本批三条分叉全部落盘并通过哈希校验）；
- 上限算术与保留额度（`Engine.prefix_arithmetic` / `ensure_prefix_reserve` /
  `prefix_reserve_guard`），本批实测 42 + 14×3 = 84 成立且前缀恰好用满上限；
- 审计器已冻结才可运行的严格规则（见上一节）。

## 8. 零 API 门控（付费前）

```powershell
cd 'C:\Users\LENOVO\Desktop\AI Agent\code'
New-Item -ItemType Directory -Force -Path .tooling\tmp | Out-Null
$env:TEMP='C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp'; $env:TMP=$env:TEMP
& .\.venv-openhands\Scripts\python.exe -m pytest `
    tests/test_openhands_same_prefix_fork_v54.py `
    tests/test_openhands_v54_branch_loopback.py `
    tests/test_openhands_v54_formal_runner_gate.py `
    tests/test_openhands_v55_formal_runner_gate.py `
    tests/test_openhands_v56_formal_runner_gate.py `
    tests/test_openhands_v57_formal_runner_gate.py `
    tests/test_openhands_v58_formal_runner_gate.py `
    tests/test_openhands_v59_formal_runner_gate.py -q `
    --basetemp="C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp\pt59"
```

结果：**68 passed**（七个既有文件 51 项 + v59 门控 17 项；v59 门控全部零 API）。
v59 门控覆盖：上限恒等式、保留额度保证（`reserve ≥ 分支纠错窗口`、前缀反推预算上限）、
越界与缺额拒绝路径、三档阶梯顺序与选中规则、触发预测真实 condenser 复算、
v58/v59 规则指纹一致、逐分叉持久化与篡改拒绝、独立重建重跑、逐分支判定字段、
批次级“无实测判定即不主张质量结论”、以及**尝试级 burst 错误不让整批中止**。

## 9. 分段计时（秒，本批实测）

| 分段 | 数值 |
|---|---:|
| `prepare_reuse_verify_seconds`（复用 `--check` 工作区并重算哈希） | 94.95 |
| `prepare_copy_hash_seconds` | 1,407.40 |
| 模型等待（前缀）`prefix_model_wait_seconds` | 1,780.13 |
| 宿主测试 `host_test_seconds` | 6,526.86 |
| 触发预测 `trigger_prediction_seconds` | 11.43 |
| 前缀快照 + 校验 | 273.30 / 59.22 |
| 快照恢复（3 条分叉）`restore_workspace_seconds` | 791.56 |
| 恢复后全哈希 `restore_hash_verify_seconds` | 88.65 |
| 模型等待（分叉）`branch_model_wait_seconds` | 803.77 |
| 逐分叉持久化 `branch_persist_seconds` | 65.34 |

墙钟：选择段 138.47 秒（`--check` 后复用工作区），分叉段 1,176.84 秒；
结论与 v54 一致：**墙钟主要花在复制/哈希/宿主测试上，不是模型等待**
（模型等待合计 2,583.90 秒，宿主测试 6,526.86 秒）。

## 10. 边界声明

- 本批 **1 个真实任务 × 1 个共同前缀 × 3 臂**，只有 **1 次**分叉后压缩，
  **不能**估计插件在任务总体上的平均效果，也不能外推到其他宿主。
- **本批没有分叉后宿主判定**（§4），因此**不主张任何质量结论**；
  上表成本差只是这一条前缀上的一次压缩对成本的作用。
- 五批合计把“抬高共享请求上限”这条路的边界量出来了（§5）：
  它解决了“分叉没额度”的缺陷，暴露出“前缀变长会把任务修好、从而失去 fork 资格”的新缺陷。
- 美元金额未知；凭据扫描：五批 v59 生成物（`workspaces` / `prefix-snapshot` / `baseline` /
  `prefix-select` / `sdk-persistence` 之外的全部 JSON/MD/TXT）共扫描 **118 个文件**，
  按 `sk-[A-Za-z0-9]{24,}` 模式命中 **0**。密钥从未写入任何文件、日志或聊天。

## 11. 与 v58 的差异清单

1. 共享每样本请求上限 36 → **84**（= 前缀预算上限 42 + 14 × 3）；
2. 每臂保留额度 4 → **14**，且 `14 ≥ 分支纠错窗口 10`（runner 与审计双向断言）；
3. 前缀预算上限：无 → **42**，越界即抛错（不静默收缩）；
4. 触发即停规则：只看“预测触发” → **“预测触发 ∧ 保留额度完好”**；
5. 前缀纠错窗口依据实测从 16 → **36**（§2c，仍以 42 为上限）；
6. 每样本估算输入上限 2,000,000 → **4,670,000**（同一倍数，保住逐请求语义）；
7. 逐分支：`host_rounds_completed` / `host_verdict_measured` / `final_host_verdict`，
   批次 `branch-verdicts.json` 与 `quality_verdict_obtained`；
8. 尝试级与分叉级 burst 错误一律记入该次尝试/该轮，**不让整批中止**，且**绝不选中**
   没有宿主判定的尝试（§2b/§2d）；
9. 审计器**付费前冻结**、审计**拒绝** `--accept-post-gate-tooling`；
10. 未改：三个冻结任务、三档阶梯、选中规则、触发预测三条件与余量（0 / 2,000）、
    提示词字节、工具边界、模型/温度、预算策略其余数值、
    **压缩机制阈值 28,000 / 22,400 / 39,200 / 33,600**、逐分叉持久化与审计方式。
