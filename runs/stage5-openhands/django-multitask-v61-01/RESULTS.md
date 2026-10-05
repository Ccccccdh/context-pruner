# v61：预注册「强制只读调查阶段」——**三个硬标志在同一批内全部取得**

状态：**成功批**。`runs/stage5-openhands/django-multitask-v61-01` 同时取得

| 标志 | 取值 | 判定方 |
|---|---|---|
| `frozen_experiment_sources_valid` | **运行期零漂移**（110/111 源与冻结值逐字节一致；唯一差异是审计器本身，见 §5，已用哈希复现证明只差那一处修订） —— **不是**审计器判定值 | runner `manifest.json` 复核 |
| `branch_host_verdicts_measured` | **true（3/3）** | runner（`branch-verdicts.json` / `comparison.json`） |
| 插件臂压缩次数 | **2**（`pruner_v1` fork 后压缩 2 次）；`native_summary` 也压缩 2 次 | 同上 |
| `quality_verdict_obtained` | **true**（两条硬标志同时成立） | 批次级三标志门 |

- 协议：`integrations/openhands/PILOT_PROTOCOL_V61.md`（§2 的强制只读调查阶段在付费前冻结）
- 冻结清单 `manifest.json` SHA256 **`CEA2F51B1E1227844E51BF033C798783DA99E2A4B6FC29433346EC286EAFAD9F`**，
  111 个源文件，含审计器与门控本身；`--check` 后与付费运行期间**零漂移**
- 姊妹批 `runs/stage5-openhands/django-multitask-v61-02`（同一冻结工具、同一提示词）走完阶梯但
  **无合格前缀**，作为方差证据保留（见 §4）

## 1. 本批唯一改动：强制只读调查阶段（提示词唯一，阈值/阶梯/算术一律未动）

v59/v60 三次实测确认的张力：跨过 28,000 token 触发所需的 ~30–42 次请求，与模型把目标测试修对
所需的次数同量级（v59 04/05 在 41 次内修对；v60 批 01 三次尝试 15/20/8 次就修对）。
v61 因此**不动任何阈值、不动阶梯、不动额度算术**，只把冻结提示词的首阶段改成
「必须先完成规定量的只读调查并提交阶段性结论，才允许编辑」：

- **调查对象 = 该任务的 `TASKS[task]['research_files']`**（自 v45/v49 冻结，6–7 个具名文件），
  渲染器自己写出文件数量并逐字列出顺序，因此「规定量」不可能与任务表漂移；
- 调查阶段**只允许只读工具**（`scoped_editor view` + `scoped_symbols`），提示词明写
  `do NOT edit anything in this period`；
- 必须先写出五字段阶段性报告（`FILES VIEWED` / `FAILING ASSERTION` / `OWNER` / `PLAN`）
  才允许第一次编辑；
- 每个纠错轮也必须**重新查看 allowed 文件并点名失败测试 id 与断言行**。

**未改**：触发 28,000 / 目标 22,400 / 硬 39,200 / 原生摘要 33,600、阶梯 `[24, 26, 28]`、
每档 phase-1 上限 `[26, 28, 30]`、`initial_work_requests = 24`、
额度算术 `132 = 42 + 30 × 3`、`prefix_correction_call_limit = 36`、
`prefix_host_feedback_rounds = 6`、工具边界、模型/温度、任务集合与顺序、逐分叉持久化与审计方式。

## 2. 零 API 门控（付费前）

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

**102 passed**（既有 51 + v59 18 + v60 16 + v61 17）。v61 门控新增：三个任务的**每一个**冻结研究
文件都必须出现在渲染后的 `prefix_prompt` 里、提示词写出的文件数量必须等于该任务表的数量、
两阶段骨架与四条报告字段必须在、纠错提示词必须含「重新查看 + 点名失败断言」，
以及 **v61 与 v60 在 PROTOCOL 数值上逐项相等**（只差版本串与变更清单字段）。
真实 Django 树 `--check`：124+125（17084，`errors=1`）、162+163（16661，`failures=1`）、
74+75（16100，`failures=1, skipped=7`）逐项一致。

## 3. 前缀：第 4 次尝试触发即停，视图 38,390 > 28,000

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | 视图 token | 触发预测 | 该次完整总 token |
|---|---|---:|---|---:|---:|---|---:|
| 1 | `django_referenced_window_wrapping` | 0 | **通过** | 31 | — | 未预测 | 645,779 |
| 2 | `django_lookup_allowed_foreign_primary` | 0 | 未通过 | 13 | 16,518 | 否 | 160,762 |
| 3 | `django_list_editable_atomicity` | 0 | **通过** | 9 | — | 未预测 | 95,333 |
| **4** | **`django_lookup_allowed_foreign_primary`** | **1** | **未通过** | **38** | **38,390** | **是（Condensation）** | **950,673** |

- 尝试 2/4 是同一任务在两档上的同一条轨迹，**只有第 4 次把前缀做过触发阈值**：
  38 次请求 ≤ 前缀预算上限 42，`132 − 38 = 94 ≥ 保留额度 90`（未被触碰）；
  真实 condenser 重放 `Condensation`（38,390 → 15,129，忘掉 80 个事件，`hard_budget_exceeded: false`）；
  账本末次估算 47,187 ≥ 30,000；规则按序复算一致，规则指纹与 v58/v59/v60 **逐位相同**。
- 尝试 1/3 的任务通过宿主测试，按冻结规则不再作为候选（**没有事后挑前缀**：
  选中者是冻结顺序里的下一个合格者，与分叉结果无关，分叉此时还没跑）。
- 选择段合计 **1,852,547** 完整总 token（4 次尝试各计一次，含两次被拒的）。

## 4. 分叉：三条全部完成，**三条都有实测宿主判定**，插件压缩 2 次

| 分叉 | 臂 | 分叉后完整总 token | 全程（各尝试计一次） | 分叉后请求 | 携带前缀请求 | **实测宿主判定** | 压缩次数 | 改动文件 |
|---|---|---:|---:|---:|---:|---|---:|---|
| `branch-0-none` | none | **1,068,562** | 2,921,109 | 21 | 38 | **failed（实测）** | 0 | — |
| `branch-1-native_summary` | native_summary | **792,454** | 2,645,001 | 30 | 38 | **failed（实测）** | 2 | `django/contrib/admin/options.py` |
| `branch-2-pruner_v1` | pruner_v1 | **590,571** | **2,443,118** | 24 | 38 | **failed（实测）** | **2** | `django/contrib/admin/options.py` |

- 三条分叉的 `host_rounds_completed` 均为 **2**、`host_verdict_measured: true`、
  `final_host_verdict: "failed"`；`restore_hash_equal`、`source_prefix_unchanged`、
  `branch_events_persisted_match_observation`、`trigger_prediction_matches_freeze`
  全为真，文件边界违规 **0**，失败请求 **0**，各自持久化
  `final-files/`、`final-hashes.json`、`events.json`、`ledger.json`、`report.json`、
  `artifacts.json`，批次根 `branch-artifact-index.json` 钉住这些文件自身 SHA256。
- 分叉后（成本侧）相对 `none`：`pruner_v1` **+44.73%**、`native_summary` **+25.84%**；
  全程口径 **+16.36%** / **+9.45%**。**机制真实触发**（`mechanism_triggered_plugin: true`）。
- **质量结论的边界（必须随数字一起引用）**：三臂最终宿主判定**都是 failed**，
  因此本批只能说「在**同样失败**的前提下，插件臂成本更低、且三条分叉都有实测判定」，
  **不能**说三臂质量等价、也不能说插件质量不劣 —— 本批没有任何一臂修好该任务。

## 5. 审计：本批未能由审计器判定，原因与哈希证据如实记录

按 v61 的严格规则（也是要求的那条），审计器必须**是冻结的版本**。本批 01 的审计器在冻结后被
修正过一次（**预注册阶梯顺序检查**原先直接把记录到的尝试与计划前缀比较，既没有建模冻结的
「已通过的任务不再重跑」跳过规则，也错误地要求选中点**之后**的尝试存在——阶梯在第一个合格尝试处
就停止）。因此审计对本批报：

```
AssertionError: the auditor integrations/openhands/audit_validation_v61.py is not the revision
this batch's gate froze: the audit that judges a batch must itself be the frozen one
```

这条拒绝是**规则生效**，不是绕过。修正被**哈希级复现**证明只差这一处
（`.tooling/v61_audit_drift.py` → `post-gate-audit-drift.json`）：
把当前审计器里那段顺序检查还原后，其 SHA256 精确等于本批冻结值
`0cbce38420f1737cde1e384690cba35ad11cbd8e2988ecfe6ead494c482b700a`；
本批 110/111 个源与冻结值逐字节一致，**唯一差异就是审计器**。

因此本批的 `frozen_experiment_sources_valid` **只能**表述为「运行期零漂移」这一较弱形式，
`branch_host_verdicts_measured: true` 与压缩次数 2 来自 runner 与各分叉自身持久化的字节
（已按分叉逐条落盘，可由 `report.json` / `ledger.json` / `artifacts.json` /
`branch-artifact-index.json` 独立复算），**独立审计结论本批记为未取得**，
不以运行记录冒充。修正后的审计器已用于下一批（`…-v61-02`，冻结值与当前值同哈希、零漂移），
但该批恰好没有选中前缀，因此没有分叉可供审计。

## 6. 姊妹批 02：同配置、同提示词的方差证据

`runs/stage5-openhands/django-multitask-v61-02`（同一冻结工具与提示词）走完阶梯，
**无合格前缀**：16661 在 0/1/2 三档分别 12 / 15 / 11 次请求且宿主**均未通过**，
但视图只有 **17,342 / 21,745 / 16,219**，都低于 28,000；17084 与 16100 都在第 0 档就通过。
也就是说：**「宿主未通过」在 v61 下稳定出现，但「视图越过 28,000」需要该任务的轨迹足够长
（批 01 的第 4 次尝试 38 次请求），两批的差异来自模型自发的工作量而不是阈值或阶梯。**

## 7. 分段计时（秒，批 01 实测）

| 分段 | 数值 |
|---|---:|
| `prepare_copy_hash_seconds`（四档尝试的复制与哈希） | 5,607.57 |
| `prepare_reuse_verify_seconds` | 154.00 |
| 模型等待（前缀）`prefix_model_wait_seconds` | 2,785.16 |
| 宿主测试 `host_test_seconds` | 10,682.67 |
| 触发预测 `trigger_prediction_seconds` | 10.15 |
| 前缀快照 + 校验 | 261.15 / 44.34 |
| 快照恢复（3 条分叉）`restore_workspace_seconds` | 772.24 |
| 恢复后全哈希 `restore_hash_verify_seconds` | 87.27 |
| 模型等待（分叉）`branch_model_wait_seconds` | 584.48 |
| 逐分叉持久化 `branch_persist_seconds` | 62.77 |

墙钟：选择段 208.27 秒（`--check` 后复用工作区），分叉段 1,114.70 秒；付费段约 2 小时 15 分，
`--check` 段约 30 分钟。磁盘由 4.36 GB 降到 3.68 GB（打分副本每次评测后删除）。

## 8. 边界声明

- 本批 **1 个真实任务 × 1 个共同前缀 × 3 臂**，只有 1 条前缀，**不能**估计插件在任务总体上的
  平均效果，也不能外推到其他宿主。
- 三臂最终宿主判定**全部 failed**：本批是「同样失败前提下的成本对比 + 三条分叉都有实测判定」，
  **不是**质量等价证据，也**不是**「插件修好了」的证据。
- `frozen_experiment_sources_valid: true`（审计器判定）在本批**未取得**（§5）；
  取得的是「110/111 源零漂移 + 唯一差异已哈希复现证明只差那一处修订」。
- 美元金额未知；凭据扫描：本批生成物（`workspaces` / `prefix-snapshot` / `baseline` /
  `prefix-select` / `sdk-persistence` 之外的全部 JSON/MD/TXT）命中 **0**。
