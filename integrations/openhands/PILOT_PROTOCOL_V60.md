# OpenHands v60：预注册“读数更细”的前缀延长 + 更宽的分叉窗口（三硬标志批）

状态：**已冻结（本文件与 `manifest.json` 中全部源文件 SHA256 在任何付费请求之前固定）**。
冻结内容 = `integrations/openhands/run_validation_v60.py` 的 `PROTOCOL` 字典 + 本文件描述的规则 +
`manifest.json` 记录的源文件哈希（**包含审计器 `audit_validation_v60.py` 与门控
`tests/test_openhands_v60_formal_runner_gate.py`**，两者与 runner 一起在 v60 门控前定稿）。

本批新增/冻结的文件：

| 文件 | 作用 |
|---|---|
| `integrations/openhands/run_validation_v60.py` | 正式 runner（v59 算术 + 更细读数 + 三标志门） |
| `integrations/openhands/run_validation_windows_v60.py` | Windows 兼容包装（TEMP 重定向、原子写重试、0 次 API 重试） |
| `integrations/openhands/audit_validation_v60.py` | 独立审计（逐分叉重建 + 重跑宿主测试；硬拒 `--accept-post-gate-tooling`） |
| `integrations/openhands/PILOT_PROTOCOL_V60.md` | 本文件（预注册） |
| `tests/test_openhands_v60_formal_runner_gate.py` | 零 API 门控（上限算术 / 保留额度 / 阶梯顺序 / 触发预测 / 三标志门 / 篡改拒绝） |
| `integrations/openhands/prefix_selection_v55.py`、`branch_artifacts_v55.py`、`same_prefix_fork_v54.py`、`budget_policy_v54.py`、`fork_tools_v54.py` | **只复用，不修改**（与 v58/v59 逐字节相同） |


批次目录：`runs/stage5-openhands/django-multitask-v60-01`

固定任务集合（`validation_tasks_v49` 冻结顺序，与 v55–v59 逐字相同）：

| 顺序 | 任务 | 实例 | 目标测试模块 |
|---:|---|---|---|
| 1 | `django_referenced_window_wrapping` | `django__django-17084` | `aggregation` |
| 2 | `django_lookup_allowed_foreign_primary` | `django__django-16661` | `modeladmin` |
| 3 | `django_list_editable_atomicity` | `django__django-16100` | `admin_changelist` |

语料臂固定为 `none`；分支顺序固定为 `none → native_summary → pruner_v1`。

## 1. 本批要同时满足的三个硬标志（验收线，付费前写定）

| 标志 | 取值要求 | 由谁判定 |
|---|---|---|
| `frozen_experiment_sources_valid` | **true**，且**不使用** `--accept-post-gate-tooling` | v60 审计（源码内硬拒绝该开关） |
| `branch_host_verdicts_measured` | **true**（三条分叉各自有实测宿主判定） | runner + 审计各自独立判定 |
| 插件压缩次数 | **≥ 1**（`pruner_v1` 分叉 fork 后至少压缩一次） | runner + 审计各自独立判定 |
| `quality_verdict_obtained` | **true**（= 上面前两条同时成立） | 批次级三标志门（`branch_verdicts()`） |

**任何一条不成立，本批即报告 “quality verdict not obtained”，不主张任何质量结论**；
成本数字不构成质量结论。runner 在未满足时以退出码 4 结束并把原因写入
`branch-verdicts.json` / `comparison.json`。

## 2. v59 五批实测把“上限算术”这条路的边界量出来了（本批两次改动的唯一依据）

v59（`runs/stage5-openhands/django-multitask-v59-prefix-fork-0{1..5}`）的实测：

| 批次 | 结果 | 关键数字 |
|---|---|---|
| 01 | 整批中止（未捕获异常） | 第 1 次尝试第 15 次请求 |
| 02 | 无合格前缀 | 唯一“宿主未通过”的前缀 21 次请求、视图 **21,881** < 28,000（纠错窗口 22 先触底） |
| 03 | **前缀冻结 + 3/3 分叉** | 42 次请求、视图 **36,917** > 28,000、真实重放 `Condensation`、插件压缩 1 次 |
| 04 | 无合格前缀 | 三条任务全部**通过**宿主测试（9/12/6 次请求） |
| 05 | 无合格前缀 | 三条任务全部**通过**宿主测试（10/**41**/6 次请求） |

由 02/03 得到两条正面事实：前缀只要**用满 42 次预算**就能越过 28,000（36,917），
且**分支拿到的额度是可以量出来的**。由 03/04/05 得到两条负面事实：

1. **纠错轮太大、太少**：v59 只有 3 轮，单轮可一次性花掉 20+ 次请求；
   03 因此在 42 次正好触发（可以停），而 04/05 的单轮直接把工作做到“宿主通过”。
   要让规则“触发即停”真正生效，需要**更细的读数**；
2. **分叉窗口 10 次太窄**：03 的三条分叉把整个 10 次纠错窗口一次性用在第一轮 burst 里，
   burst 以 `Frozen correction request limit` 结束，宿主目标测试**根本没跑**，
   于是 `branch_host_verdicts_measured: false`。

## 3. v60 的两处预注册改动（机制阈值一律未动）

```
共享上限 132 = 前缀预算上限 42 + 每臂保留额度 30 × 3 臂
prefix_correction_window = min(132 − 30, 42, work_used + 36)   ← 仍以 42 为唯一前缀上限
分叉纠错窗口 = min(132, 前缀请求 + 30)                          ← 由 10 提到 30
prefix_host_feedback_rounds: 3 → 6                              ← 读数更细
max_total_estimated_input_per_sample: 4,670,000 → 7,330,000     ← 按 132/36 同倍
```

逐项理由与算术：

1. **前缀预算上限 42 不变**。03 已经证明 42 次足以越过 28,000（36,917）。前缀越长越容易
   被修好，所以前缀侧不该再长。
2. **每臂保留额度 14 → 30**，共享上限 84 → **132**（`132 = 42 + 30×3`，恒等式由
   `ensure_prefix_reserve` 断言，前缀越 42 即抛错）。分叉纠错窗口随之从 10 提到 **30**：
   03 的失败模式是“10 次窗口在第一轮 burst 内耗尽”，30 次给第一轮 burst 留出空间，
   使宿主目标测试有机会在该轮末尾照常运行 —— 这是 `branch_host_verdicts_measured: true`
   的前提。断言 `branch_request_reserve (30) ≥ host_feedback_correction_reserve (30)`。
3. **`prefix_host_feedback_rounds: 3 → 6`**：轮数是“读数”而不是“预算”，提高它不会改变
   任何压缩阈值，只让规则能在更接近 28,000 的位置停下（03 靠运气正好停在 42/36,917）。
   前缀仍然可以花满 42 次，也仍然可以提前停止；阶梯、选中规则、触发预测三条件
   （实测视图 ≥ 28,000、真实 condenser 返回 `Condensation`、账本末次估算 ≥ 30,000）
   与闸门余量（0 / 2,000）**逐字未改**。
4. **估算输入上限按同一倍数放大**（2,000,000 × 132/36 ≈ 7,333,333 → 取整 7,330,000），
   否则新增请求会被输入上限而不是请求上限挡住；单次估算输入上限 80,000 未改。
5. **未改**：触发 28,000 / 目标 22,400 / 硬 39,200 / 原生摘要 33,600、
   阶梯 `[24, 26, 28]` 与每档 phase-1 上限 `[26, 28, 30]`、`initial_work_requests = 24`、
   `prefix_correction_call_limit = 36`、工具边界、模型/温度/思考开关、三档任务集合与顺序、
   逐分叉持久化与审计方式、`minimum_prefix_requests_to_trigger = 30`。

## 4. 唯一一处提示词改动（预注册，逐字冻结）

v59 实测的张力是“跨过 28,000 触发所需请求数（约 30–42）恰好等于模型把该特性修对所需次数”。
v60 按任务允许的方式只改**冻结提示词**，在 `prefix_prompt` 里插入一段（其余字节不变）：

> Before your first edit you must complete the read-only investigation: read the named
> research source files and view the exact ranges that implement the behavior, trace the call
> path the contract exercises, and identify the failing assertion; keep the evidence in the
> TASK.md notes. **Edits made before that investigation is finished are premature and will be
> reverted by the reviewer.**

- 作用：把首阶段的**只读调查**变成显式前置条件，使模型在落编辑之前先把前缀做长，
  从而提高“前缀越过 28,000 且宿主仍失败”这一状态的出现概率。它**不改变**任何阈值、
  工具边界、评价方式或数据集，也不给任何一臂额外额度。
- 反作弊：契约仍然只由**宿主目标测试 + 选定的公开回归模块 + 文件边界**判定，
  提示词里没有任何“不要修好”的指令；模型若仍然一次修对，该次尝试按冻结规则
  **不再作为候选**（v59 04/05 就是这样被如实拒绝的）。

## 5. 选择规则（与 v55–v59 逐字相同，只有提示词与轮数变了）

- 预注册尝试序列：第 0 档（工作请求上限 24）按冻结顺序遍历三个任务；没有选中则第 1 档（26）；
  再没有则第 2 档（28）；三档都在共享上限之内。
- 已**通过**宿主目标测试的任务不在更高档重跑（每次跳过都写入 `prefix-select/selection.json`）。
- 选中条件（同时满足，取冻结顺序里**第一个**满足者）：
  1. 该次尝试结束状态**未通过**宿主目标测试；
  2. 冻结的零 API 触发预测判定**会压缩**（实测视图 ≥ 28,000 且真实 condenser 返回
     `Condensation` 且账本末次估算 ≥ 30,000）；
  3. **新增（v60）**：该次尝试结束时**保留额度完好**（`132 − 前缀请求 ≥ 90`），
     否则该尝试不入选、阶梯继续。
- 触发即停：每次宿主反馈轮后用同一零 API 预测测一次，**同时满足**“预测触发 ∧ 保留完好”即停止延长。
- 若整条阶梯跑完仍无合格前缀：本批**直接报告 “mechanism still not triggered”**，
  不发任何分叉请求，退出码 3，且**不修改任何阈值**。
- 尝试级/分叉级 burst 错误按 v59 §2b/§2d 记录并继续（绝不中止整批），
  且**绝不选中**没有宿主判定的尝试。

## 6. 工具、边界与计量（与 v53/v54 相同，除 §3 列出的两个上限外）

| 项 | 值 |
|---|---|
| 模型 / 温度 / thinking | `openai/deepseek-v4-flash` / 0 / disabled |
| 每样本 Agent 请求上限 | **132**（v59：84） |
| 前缀预算上限 | 42（未改） |
| 每臂保留额度 | **30**（v59：14） |
| 分叉纠错窗口 | **30**（v59：10，= `host_feedback_correction_reserve`） |
| 前缀纠错窗口 | `min(102, 42, work_used + 36)` |
| 触发 / 目标 / 硬 / 原生摘要 | 28,000 / 22,400 / 39,200 / 33,600（未改） |
| 单次估算输入 / 每样本估算输入上限 | 80,000（未改）/ **7,330,000** |
| 每样本摘要请求上限 / 单次最大输出 | 16 / 3,072（未改） |

工具：`scoped_editor`（仅能编辑数据集 `reference.patch` 触及的文件）、`scoped_symbols`（只读）、
`scoped_tests`（固定公开回归模块，最多 3 次）、`finish`；**没有注册任何 shell**。
分数副本在每次评测后立即删除。

## 7. 分叉与独立验证（v59 机制原样复用，唯一差别是窗口 30）

每条分叉在自己目录落盘 `final-files/`、`final-hashes.json`、`events.json`、`ledger.json`、
`report.json`、`artifacts.json`，批次根 `branch-artifact-index.json` 钉住这些文件自身 SHA256；
审计从冻结快照 + **该分叉自己的字节**重建最终工作区 → 核对全文件哈希表 →
在重建字节上**重跑宿主目标测试** → 与该分叉记录的判定逐项比较，并为每条分叉写出
`host_verdict_measured` 与 `host_verdict_source`。
分叉报告新增 `host_rounds_completed` / `host_verdict_measured` / `final_host_verdict`，
批次新增 `branch-verdicts.json` 与 `quality_verdict_obtained`（§1 的三标志门），
其中 `plugin_compaction_count` 与 `plugin_compaction_at_least_one` 由 runner 与审计各自复算。

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
    tests/test_openhands_v59_formal_runner_gate.py `
    tests/test_openhands_v60_formal_runner_gate.py -q `
    --basetemp="C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp\pt60"
```

v60 门控新增/加固的断言（全部零 API）：

1. **上限恒等式**：`132 = 42 + 30 × 3` 且 `prefix_budget_cap = 132 − 30 × 3`；
2. **保留额度覆盖分支窗口**：`30 ≥ 30`，否则拒绝 fork；前缀越 42 或跌破保留即抛错；
3. **机制阈值未动**：v60 与 v59 的 `trigger_input_tokens` / `pruner_target_tokens` /
   `pruner_hard_tokens` / `native_max_tokens` / 阶梯 / 闸门余量逐项相等，且
   `host_feedback_rounds_per_branch`、`prefix_correction_call_limit`、
   `minimum_prefix_requests_to_trigger` 相等；
4. **提示词守卫**：`prefix_prompt(...)` 必须包含只读调查那两句话；
5. **规则指纹**：v60 与 v59 的 `rule_fingerprint` 与 `SELECTION_RULE` 逐位相同；
6. **三标志门**：`branch_verdicts()` 在缺宿主判定或压缩 0 次时必须
   `quality_verdict_obtained: false`；
7. 逐分叉持久化、篡改拒绝、独立重建重跑、尝试级 burst 不中止整批（v59 门控原样保留）。

真实 Django 树门控：

```powershell
$env:DSH_RUNNER='integrations/openhands/run_validation_windows_v60.py'; $env:DSH_MODULE=''
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v60-01 --check'
$env:DSH_PYTHON='.venv-openhands\Scripts\python.exe'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1
```

冻结期望：124+125（17084，`errors=1`）、162+163（16661，`failures=1`）、
74+75（16100，`failures=1, skipped=7`）。

## 9. 付费运行与审计

```powershell
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v60-01 --run --resume'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1

& .\.venv-openhands\Scripts\python.exe integrations/openhands/audit_validation_v60.py `
    --out runs/stage5-openhands/django-multitask-v60-01
```

退出码：0 = 三标志全满足；3 = 阶梯走完无合格前缀；4 = 有三条分叉但缺实测判定或压缩 0 次。

## 10. 磁盘与计时

- `min_free_disk_gb = 1.5`，每次 prepare 与每条分叉前实测空闲空间；打分副本评测后立即删除。
- 分段计时逐阶段写入 `timing-*.json` 与 `comparison.json`：prepare 复制/哈希、快照创建与校验、
  快照恢复、恢复后全哈希、模型等待（前缀/分叉）、宿主测试、触发预测、分叉持久化、审计。

## 11. 与 v59 的差异清单（只此六项，机制阈值不在其中）

1. 共享上限 84 → **132**（= 42 + 30 × 3）；
2. 每臂保留额度 14 → **30**；
3. 分叉纠错窗口 10 → **30**（`host_feedback_correction_reserve`）；
4. 前缀宿主反馈轮数 3 → **6**；
5. 每样本估算输入上限 4,670,000 → **7,330,000**（同一倍数）；
6. 冻结提示词插入只读调查前置条件；批次新增三标志门
   （`branch_host_verdicts_measured` ∧ 插件压缩 ≥ 1）。
