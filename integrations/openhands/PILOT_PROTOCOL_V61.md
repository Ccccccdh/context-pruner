# OpenHands v61：预注册「强制只读调查阶段」制造合格前缀（提示词唯一改动）

状态：**已冻结（本文件与 `manifest.json` 中全部源文件 SHA256 在任何付费请求之前固定）**。
冻结内容 = `integrations/openhands/run_validation_v61.py` 的 `PROTOCOL` 字典 + 本文件描述的规则 +
`manifest.json` 记录的源文件哈希（**包含审计器 `audit_validation_v61.py` 与门控
`tests/test_openhands_v61_formal_runner_gate.py`**，两者与 runner 一起在 v61 门控前定稿）。

批次目录：`runs/stage5-openhands/django-multitask-v61-01`

固定任务集合（`validation_tasks_v49` 冻结顺序，与 v55–v60 逐字相同）：

| 顺序 | 任务 | 实例 | 目标测试模块 |
|---:|---|---|---|
| 1 | `django_referenced_window_wrapping` | `django__django-17084` | `aggregation` |
| 2 | `django_lookup_allowed_foreign_primary` | `django__django-16661` | `modeladmin` |
| 3 | `django_list_editable_atomicity` | `django__django-16100` | `admin_changelist` |

语料臂固定为 `none`（无压缩）；分支顺序固定为 `none → native_summary → pruner_v1`。

本批新增/冻结的文件：

| 文件 | 作用 |
|---|---|
| `integrations/openhands/run_validation_v61.py` | 正式 runner（v60 算术原样 + 强制只读调查提示词） |
| `integrations/openhands/run_validation_windows_v61.py` | Windows 兼容包装（TEMP 重定向、原子写重试、0 次 API 重试） |
| `integrations/openhands/audit_validation_v61.py` | 独立审计（逐分叉重建 + 重跑宿主测试；硬拒 `--accept-post-gate-tooling`） |
| `integrations/openhands/PILOT_PROTOCOL_V61.md` | 本文件（预注册） |
| `tests/test_openhands_v61_formal_runner_gate.py` | 零 API 门控（提示词守卫 / 上限算术 / 保留额度 / 阶梯顺序 / 触发预测 / 三标志门 / 篡改拒绝） |
| `integrations/openhands/prefix_selection_v55.py`、`branch_artifacts_v55.py`、`same_prefix_fork_v54.py`、`budget_policy_v54.py`、`fork_tools_v54.py` | **只复用，不修改**（与 v58/v59/v60 逐字节相同） |

## 1. 本批要解决的问题（v59/v60 的四批实测，逐条可查）

| 批次 | 前缀与修复的实测 |
|---|---|
| v59 批 03 | 42 次请求、视图 **36,917** > 28,000、宿主**未通过** → 合格前缀（唯一一次成功分叉，但分叉侧无实测判定） |
| v59 批 04 | 三条任务 9/12/6 次请求就**通过**宿主测试 → 无候选前缀 |
| v59 批 05 | 三条任务 10/**41**/6 次请求就**通过**宿主测试 → 无候选前缀 |
| v60 批 01 | 三条任务 **15/20/8** 次请求就**通过**宿主测试 → 无候选前缀 |

即：**跨过 28,000 token 触发所需的 ~30–42 次请求，与模型把该任务修对所需的次数同量级**。
v61 因此**不动任何阈值、不动阶梯、不动额度算术**，只把**冻结提示词**里的首阶段
改成「必须先完成规定量的只读调查并提交阶段性结论，才允许编辑」。

## 2. 预注册的调查阶段（调查对象来自已冻结的任务表，不新增数据集工作）

调查对象 = 该任务的 `TASKS[task]['research_files']`，**自 v45/v49 起就已冻结**，
每任务 6–7 个具名文件（允许编辑的文件 + 相关源码 + 公开测试模块），逐任务如下：

| 任务 | 调查文件数 | 文件（冻结顺序，提示词按此顺序逐字列出） |
|---|---:|---|
| `django_referenced_window_wrapping` | 7 | `django/db/models/sql/query.py`、`django/db/models/aggregates.py`、`django/db/models/expressions.py`、`django/db/models/sql/compiler.py`、`django/db/models/sql/subqueries.py`、`tests/aggregation/tests.py`、… |
| `django_lookup_allowed_foreign_primary` | 6 | `django/contrib/admin/options.py`、`django/contrib/admin/checks.py`、`django/contrib/admin/utils.py`、`django/db/models/fields/related.py`、`tests/modeladmin/tests.py`、`tests/runtests.py` |
| `django_list_editable_atomicity` | 6 | `django/contrib/admin/options.py`、`django/contrib/admin/checks.py`、`django/contrib/admin/utils.py`、`django/db/models/sql/query.py`、`tests/admin_changelist/tests.py`、`tests/runtests.py` |

`prefix_prompt` 里冻结的原文（渲染后提示词共 4,465 字符，渲染器自身写出文件数量，
所以「规定量」不可能与任务表漂移）：

> This experiment has TWO periods. **FIRST PERIOD (read only, mandatory)**: read every one of the
> {N} research files named below, in the order given; for each file view the defining or relevant
> range with `scoped_editor` (use "view") or locate its definitions with `scoped_symbols`;
> **do NOT edit anything in this period**; treat the file research as finished only after you have
> actually viewed a range of every named file. Name the files as you work so the record shows which
> file each view belongs to, and never claim to have read a file you did not view.
> **SECOND PERIOD (edit)**: now locate the owning function and implement.
> When the first period is finished, write the staged investigation report in exactly this skeleton,
> one line per file, before your first edit:
> `FILES VIEWED: <path> <start>-<end>; …` (must list every named research file with the exact
> ranges viewed) / `FAILING ASSERTION:` <the exact assertion text and the file and line where it
> lives> / `OWNER:` <the function or method that must change, and why the current code does not
> satisfy the assertion> / `PLAN:` <the smallest change that satisfies it>.
> The research files to view, in this order: {冻结顺序的全部 research_files}。

纠错轮（`correction_prompt`）也加了一句，防止它在没有重新读证据的情况下猜测修改：

> Before you edit again, re-view the allowed file with `scoped_editor` "view" and name the failing
> test id and the exact assertion line from the feedback above; a correction that cannot name them
> is a guess.

**调查阶段只允许只读工具**：`scoped_editor` 的 `view` 命令与 `scoped_symbols`（两者都只读）。
工具白名单、编辑范围（只允许 `allowed` 列出的单一文件）、文件边界判定**一字未改**；
提示词里没有任何「不要修好」的指令，契约仍只由**宿主目标测试 + 选定的公开回归模块 +
文件边界**判定。

## 3. 额度算术（沿用 v59/v60，断言齐全，未改）

```
共享上限 132 = 前缀预算上限 42 + 每臂保留额度 30 × 3 臂
prefix_correction_window = min(132 − 30, 42, work_used + 36)   ← 仍以 42 为唯一前缀上限
分叉纠错窗口 = min(132, 前缀请求 + 30)                         ← = host_feedback_correction_reserve
```

- `ensure_prefix_reserve` 断言 `42 + 30×3 = 132` 与 `prefix_budget_cap = 132 − 30×3`；
  前缀越 42 即 `raise`（**拒绝运行，不静默收缩**）；`branch_request_reserve (30) ≥
  host_feedback_correction_reserve (30)` 否则拒绝 fork。
- `max_total_estimated_input_per_sample = 7,330,000`（= 2,000,000 × 132/36 取整）；
  单次估算输入上限 80,000。
- `prefix_host_feedback_rounds = 6`（读数更细，让「触发即停」在接近 28,000 处生效）。
- **未改**：触发 28,000 / 目标 22,400 / 硬 39,200 / 原生摘要 33,600、
  阶梯 `[24, 26, 28]`、每档 phase-1 上限 `[26, 28, 30]`、`initial_work_requests = 24`、
  `prefix_correction_call_limit = 36`、`minimum_prefix_requests_to_trigger = 30`、
  闸门余量 0 / 2,000、工具边界、模型/温度/思考开关、逐分叉持久化与审计方式。

## 4. 选择规则与失败线（预注册）

- 预注册尝试序列：第 0 档（工作上限 24）按冻结顺序遍历三任务；无选中则第 1 档（26）；再则第 2 档（28）。
- 已**通过**宿主目标测试的任务不在更高档重跑（每次跳过写入 `prefix-select/selection.json`）。
- 选中条件（同时满足，取冻结顺序第一个）：① 结束状态**未通过**宿主目标测试；
  ② 冻结的零 API 触发预测判定会压缩（实测视图 ≥ 28,000 ∧ 真实 condenser 返回
  `Condensation` ∧ 账本末次估算 ≥ 30,000）；③ 结束时**保留额度完好**（`132 − 前缀请求 ≥ 90`）。
- 触发即停：每次宿主反馈轮后用同一零 API 预测测一次，同时满足①②③即停止延长。
- **失败线（本轮预注册，必须遵守）**：若走完阶梯仍无「触发 ∧ 未修复」的合格前缀，
  或分叉后仍拿不到实测宿主判定，本批**如实报告并停止，不开 v62**；
  按选项 (c) 收口：写明「在该任务族上同前缀分叉无法取得质量判定，
  因为触发与未修复互斥」，并给出**具体失败算术**（前缀在第几次请求触发 / 第几次被修对）。

## 5. 验收线（两个硬标志，付费前写定）

| 标志 | 要求 | 判定方 |
|---|---|---|
| `frozen_experiment_sources_valid` | **true**，且**不使用** `--accept-post-gate-tooling`（源码内硬拒绝） | v61 审计 |
| `branch_host_verdicts_measured` | **true**：三条分叉各自基于**自己持久化的字节**重建后重跑宿主目标测试，逐条给出通过/失败 | runner + 审计各自独立判定 |
| 插件压缩次数 | **≥ 1** | runner + 审计各自独立判定 |
| `quality_verdict_obtained` | 上面两条同时成立 | 批次级三标志门（`branch_verdicts()`） |

任一条不成立即报告 “quality verdict not obtained”，**不主张任何质量结论**；
runner 在未满足时以退出码 4 结束。

## 6. 逐分叉持久化与独立验证（v59 机制原样，唯一差别是分叉窗口 30）

每条分叉在自己目录落盘 `final-files/`、`final-hashes.json`、`events.json`、`ledger.json`、
`report.json`、`artifacts.json`，批次根 `branch-artifact-index.json` 钉住这些文件自身 SHA256。
审计从冻结快照 + **该分叉自己的字节**重建最终工作区 → 核对全文件哈希表 →
在重建字节上**重跑宿主目标测试** → 与该分叉记录的判定逐项比较，并写出
`host_verdict_measured` / `host_verdict_source`。
分叉报告含 `host_rounds_completed` / `host_verdict_measured` / `final_host_verdict`，
批次含 `branch-verdicts.json`、`plugin_compaction_count`、`quality_verdict_obtained`。
burst 以异常结束时**仍必跑宿主目标测试**（v59 §2d 的修订原样保留）。

## 7. 零 API 门控（付费前）

```powershell
cd 'C:\Users\LENOVO\Desktop\AI Agent\code'
New-Item -ItemType Directory -Force -Path .tooling\tmp | Out-Null
$env:TEMP='C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp'; $env:TMP=$env:TEMP
& .\.venv-openhands\Scripts\python.exe -m pytest `
    tests/test_openhands_same_prefix_fork_v54.py tests/test_openhands_v54_branch_loopback.py `
    tests/test_openhands_v54_formal_runner_gate.py tests/test_openhands_v55_formal_runner_gate.py `
    tests/test_openhands_v56_formal_runner_gate.py tests/test_openhands_v57_formal_runner_gate.py `
    tests/test_openhands_v58_formal_runner_gate.py tests/test_openhands_v59_formal_runner_gate.py `
    tests/test_openhands_v60_formal_runner_gate.py tests/test_openhands_v61_formal_runner_gate.py -q `
    --basetemp="C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp\pt61"
```

v61 门控新增：三个任务的每一个冻结研究文件都必须出现在渲染后的 `prefix_prompt` 里、
渲染器写出的文件数量必须与该任务表一致、两阶段骨架与五条报告字段必须在提示词里、
纠错提示词必须包含「重新查看 + 点名失败断言」两句；同时断言 v61 与 v60 的
**版本字符串之外仅差 `v61_changes` / `v60_changes` / `v58_carryover_marker` 三个字段**
（即提示词之外的一切逐项相等），以及全部 v59/v60 既有限额、篡改拒绝与三标志门断言。

真实 Django 树门控：

```powershell
$env:DSH_RUNNER='integrations/openhands/run_validation_windows_v61.py'; $env:DSH_MODULE=''
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v61-01 --check'
$env:DSH_PYTHON='.venv-openhands\Scripts\python.exe'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1
```

冻结期望：124+125（17084，`errors=1`）、162+163（16661，`failures=1`）、
74+75（16100，`failures=1, skipped=7`）。

## 8. 付费运行与审计

```powershell
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v61-01 --run --resume'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1

& .\.venv-openhands\Scripts\python.exe integrations/openhands/audit_validation_v61.py `
    --out runs/stage5-openhands/django-multitask-v61-01
```

退出码：0 = 三标志全满足；3 = 阶梯走完无合格前缀（报告 “mechanism still not triggered”）；
4 = 有三条分叉但缺实测判定或压缩 0 次。

## 9. 与 v60 的差异清单（唯一一处，且不在 PROTOCOL 数值里）

1. `prefix_prompt`：把原来一句 "Use scoped_symbols to locate definitions in the named research
   source files; view those exact ranges." 替换为 §2 的两阶段强制只读调查 + 五字段阶段性报告骨架 +
   逐字列出的冻结研究文件顺序；
2. `correction_prompt`：新增「重新查看 allowed 文件 + 点名失败测试 id 与断言行」要求。

`PROTOCOL` 的**所有数值字段**（含 132 / 42 / 30 / 6 / 36 / 7,330,000 与四个机制阈值）与 v60
逐项相同，仅版本字符串与变更清单字段不同。
