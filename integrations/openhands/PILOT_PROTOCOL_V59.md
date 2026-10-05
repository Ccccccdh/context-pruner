# OpenHands v59：预注册共享请求上限（前缀预算 + 分支保留额度）+ 实测逐分支宿主判定 + 付费前冻结审计器

状态：**已冻结（本文件与 `manifest.json` 中全部源文件 SHA256 在付费前固定）**。
冻结内容 = `integrations/openhands/run_validation_v59.py` 里的 `PROTOCOL` 字典 + 本文件描述的规则 +
`manifest.json` 记录的源文件哈希（**包含审计器 `audit_validation_v59.py` 与门控
`tests/test_openhands_v59_formal_runner_gate.py`**）。协议字段一旦与 `manifest.json` 不一致，
runner 直接拒绝运行（`Frozen protocol changed`）。

批次目录：`runs/stage5-openhands/django-multitask-v59-prefix-fork-03`
（本批 01 `…-v59-prefix-fork-01` 是失败批次：付费段在第 1 次尝试的突发异常处整批中止，
无冻结前缀、无分支请求，原因与修法见 §2b；本批 02 `…-v59-prefix-fork-02` 走完阶梯但
**未选中前缀**（唯一宿主未通过的前缀 21,881 < 28,000，纠错窗口先触底），原因与批 03 的
预注册修订见 §2c。两个结果目录都保留，不覆盖。）

固定任务集合（`validation_tasks_v49` 的冻结顺序，与 v55–v58 逐字相同）：

| 顺序 | 任务 | 实例 | 目标测试模块 |
|---:|---|---|---|
| 1 | `django_referenced_window_wrapping` | `django__django-17084` | `aggregation` |
| 2 | `django_lookup_allowed_foreign_primary` | `django__django-16661` | `modeladmin` |
| 3 | `django_list_editable_atomicity` | `django__django-16100` | `admin_changelist` |

语料臂固定为 `none`（无压缩）；分支顺序固定为 `none → native_summary → pruner_v1`。

> v59 只做两件事，都在**任何付费请求之前**完成：(1) 把共享请求上限按算术抬高，使每条分叉
> 拿到**足以完成一次编辑 + 一次宿主目标测试**的保留额度；(2) 把审计器连同其余工具一起
> **先冻结再使用**，因此本批审计报 `frozen_experiment_sources_valid: true`，**不需要**
> `--accept-post-gate-tooling`（v59 审计直接拒绝该开关）。
> **压缩机制阈值 28,000 / 22,400 / 39,200 / 33,600、模型、温度、工具边界、提示词字节、
> 预算策略数值、任务集合、筛选阶梯规则与触发预测一律未改。**

## 0. 本批新增/冻结的文件

| 文件 | 作用 |
|---|---|
| `integrations/openhands/run_validation_v59.py` | 正式 runner（前缀预算上限 + 保留额度 + 逐分支宿主判定） |
| `integrations/openhands/run_validation_windows_v59.py` | Windows 兼容包装（TEMP 重定向、原子写重试、0 次 API 重试） |
| `integrations/openhands/audit_validation_v59.py` | 独立审计（拒绝 `--accept-post-gate-tooling`；逐分支重建 + 重跑宿主测试 + 分叉级隐藏验证） |
| `integrations/openhands/PILOT_PROTOCOL_V59.md` | 本文件（预注册） |
| `tests/test_openhands_v59_formal_runner_gate.py` | 零 API 门控（保留额度算术 / 持久化 / 篡改拒绝 / 阶梯顺序 / 触发预测复算 / 尝试级失败处理 / 审计器冻结） |
| `runs/stage5-openhands/django-multitask-v59-prefix-fork-02` | 本批结果目录（`-01` 为失败批次，保留不覆盖） |
| `prefix_selection_v55.py`、`branch_artifacts_v55.py`、`same_prefix_fork_v54.py`、`budget_policy_v54.py`、`fork_tools_v54.py` | **只复用，不修改**（与 v58 逐字节相同） |

## 1. v59 修的两个缺陷（v58 审计的实测结论）

v58 批次 01 的审计（`branches/…/django-multitask-v58-prefix-fork-01/audit.json`）报出：

1. `branch_host_verdicts_measured: false`。前缀消耗了共享 36 次里的 **32** 次，每条分叉只剩
   **4** 次，三条分叉都在第一个宿主轮内撞上共享上限、**没有落下任何编辑**、`host_rounds` 为空。
   因此 `final_host_passed` 是报告默认值而不是实测值，该批只能是**成本侧证据**。
2. `frozen_experiment_sources_valid: False`。111 个冻结源里
   `integrations/openhands/audit_validation_v58.py` 在门控之后被修改，然后被用来审计本批
   （记录为 `post_gate_tooling_edits_recorded` / `--accept-post-gate-tooling`）。
   这是“**审计器在冻结后被改，然后用它去审计**”的溯源弱点。

v59 的对应修法：把上限按算术抬高（§2）并把审计器提前冻结（§3）。

## 2. 预注册的请求上限算术（v59 唯一的行为性改动）

测得的结构性张力：本任务上前缀要长到越过冻结的 28,000 token 触发阈值，需要约 **30** 次 Agent
请求（v55 批次 01：26 次请求实测 28,775 视图 token；v58 批次 01：32 次请求实测 29,490），
而 v54–v58 的**每样本共享上限只有 36** 次 —— 前缀越长，留给分叉的越少，v58 因此只剩 4 次。
v59 **不改任何压缩机制阈值**，而是预注册一个新的共享上限：

```
共享上限 84 = 前缀预算上限 42 + 每臂保留额度 14 × 3 臂
```

- `max_agent_calls_per_sample = 84`（v54–v58 为 36）；
- `prefix_budget_cap = 42`：共享前缀**最多**消耗 42 次请求（含 phase-1 工作请求与全部宿主反馈纠错轮）；
  `42 = 84 − 14 × 3`，即前缀预算上限由保留额度反推，runner 用恒等式断言两式计算一致，
  前缀一旦越过 42 次即抛错（**拒绝运行，不静默收缩**）；
- `branch_request_reserve = 14`（v58 为 4）：每条分叉在 fork 点至少保有 14 次请求；
- **为什么 14 次就够一次编辑 + 一次宿主测试**：分叉的纠错窗口按冻结规则固定为
  `min(84, 前缀请求数 + host_feedback_correction_reserve)`，`host_feedback_correction_reserve = 10`，
  即分叉最多在纠错阶段发 **10** 次请求，宿主目标测试在**每一轮纠错之后立即运行**
  （无需额外请求）。所以 `14 ≥ 10` 已足以让分叉走到“读到源码 → 落一次编辑 →
  `scoped_tests` 公开验证 → Finish → 宿主轮”，并留下 4 次余量。
  runner 断言 `branch_request_reserve ≥ host_feedback_correction_reserve`，否则拒绝运行。
- `max_total_estimated_input_per_sample` 由 2,000,000 按**同一倍数**放大为 4,670,000
  （`round(2000000 × 84 / 36) = 4666667`，取整 4,670,000）：请求上限乘以 2.33 后，
  输入账本必须保住“每次请求的估算输入”的原义，否则新增的请求会被估算输入上限而不是请求上限挡住。
  单次估算输入上限 80,000 **未改**。
- `minimum_prefix_requests_to_trigger = 30`：把实测到的“越过触发阈值所需前缀长度”写进协议，
  使 42 的预算上限可以被事后对照实测复核，而不是一句愿望。

**触发即停规则的两处收口**（v58 的规则在保留额度不足时会自相矛盾）：

1. 前缀延长在**每一次宿主反馈轮之后**用冻结的零 API 预测（实测视图 token ≥ 28,000 **且**
   真实 condenser 返回 `Condensation` **且** 账本末次估算输入 ≥ 30,000）测一次；
2. **同时满足**“预测触发”**且**“`剩余 ≥ 14 × 3` 保留额度完好”时**立即停止**延长，
   前缀随即冻结（不再多花一次请求）；
3. 若预测触发但保留额度**不**完好（前缀已到预算上限、剩余跌破 42 次），该次尝试
   **不被选中**并记录原因，阶梯继续（这是防御性分支：预算上限本身就让“已触发的合格前缀”
   不可能跌破保留额度）；
4. 若预测未触发且前缀已到预算上限（剩余仍 ≥ 42），同样不选中并继续阶梯；
5. fork 前 `ensure_prefix_reserve` 再断言一次：前缀请求数 ≤ 42 且剩余 ≥ 42，
   否则 **raise**（不 fork、不声称判定）。

前缀的 phase-1 阶梯、选中规则、触发预测三条件、账本下界 2,000 与 v55–v58 **逐字相同**：
`phase1_work_request_ladder = [24, 26, 28]`，`phase1_request_ceiling_per_rung = [26, 28, 30]`，
`trigger_prediction_margin_tokens = 0`，`trigger_prediction_ledger_margin_tokens = 2000`。

## 2b. 预注册的尝试级失败处理（付费前冻结，本批 01 的实测缺口）

本批 01（失败批次，保留在 `runs/stage5-openhands/django-multitask-v59-prefix-fork-01`）在第 1 次
尝试的第 15 次请求上让**整批中止**：预算守卫拒绝了一个把 `finish` 与真实编辑混在一起的响应
（`bounded_llm_v30` 抛 `RuntimeError: Budget policy blocked an unexpected tool before execution`），
异常穿过 runner 的 `ConversationRunError` 边界，批次在“无冻结前缀、无分支请求”的状态下退出。
本轮该缺口按**预注册规则**修好，且修法不放宽任何预算或机制：

1. **phase-1 突发（burst）错误**：记入该次尝试的 `burst_error_type` / `burst_diagnostic`，
   该次尝试 `host_passed = None`、不参与选中（选中规则要求 `host_passed is False`），
   阶梯继续走下一次尝试；
2. **纠错轮 burst 错误**：该轮结束即停止延长，并在**该轮的最终工作区**上照常跑一次宿主目标测试，
   把结果写进 `host_rounds`（带 `burst_error_type`），因此该次尝试仍有宿主判定，
   后续触发预测仍在该真实末态上测量；
3. **任何逃逸出单次尝试的异常**：`run_select` 记录为一次失败尝试
   （`error_type` / `diagnostic` / 空宿主判定），**阶梯继续**；
4. **绝不选中没有宿主判定的尝试**：freeze 前再断言一次
   （`refusing to freeze a prefix that was never host-scored`）。
5. 失败的尝试与失败请求**全部保留**在 `prefix-select/attempts.json`、`select.json` 与各自
   `report.json` / `ledger.json` 中，并按“各计一次”计入全程成本。

## 2c. 批次 03 的预注册修订：把纠错窗口放到预算上限（依据本批 02 的付费实测）

批次 02（`…-v59-prefix-fork-02`，$0 之外另计 681,941 完整总 token，4 次尝试全部保留）
走完阶梯后**没有选中任何前缀**，根因**不是**新的 84 上限、也不是压缩机制阈值，而是
**前缀纠错窗口**先于预算上限触底：

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | 纠错窗口 | 停止原因 | condenser 视图 token | 真实重放 |
|---|---|---:|---|---:|---:|---|---:|---|
| 1 | 17084 | 0 | **通过** | 10 | 22 | 第 1 轮 burst 错误（预算守卫拒绝混合批） | — | 未预测 |
| 2 | 16661 | 0 | 未通过 | 21 | **22** | **窗口耗尽** | **21,881** | `View`（未触发） |
| 3 | 16100 | 0 | **通过** | 6 | — | — | — | 未预测 |
| 4 | 16661 | 1 | **通过** | 18 | 19 | 第 1 轮后结束 | — | 未预测 |

- 尝试 2 是该批**唯一**“宿主未通过”的前缀：21 次请求、视图 **21,881** token，
  距冻结的 28,000 还差 **6,119**；它的 `prefix_correction_window` =
  `min(84 − 14, 42, 6 + 16) = 22`，在第 2 轮末尾耗尽时**只用了 21 次**，
  而当时共享上限还剩 **63** 次请求、每臂下限 21 次（保留额度完好）。
  也就是说：**挡住前缀变长的是纠错窗口 22，而不是 42 的前缀预算上限**。
- 另一侧：批 02 里 17084（10 次）与 16100（6 次）两个任务都**通过了**宿主目标测试，
  按冻结规则不再作为候选；这与 v55/v56/v57 观察到的“provider 常在 3–6 次请求后关掉首阶段”
  是同一现象。

批次 03 因此只预注册**一处**改动（其余逐字不变）：

```
prefix_correction_call_limit: 16 -> 36
prefix_correction_window = min(84 - 14, 42, work_used + 36) = 42   （前缀预算上限成为唯一约束）
```

- 依据：既然预注册已经保证“前缀最多花 42、分支一定保住 14 × 3”，那么纠错窗口就不该比这个
  保证更早触底；17 次（= 42 − 6 − 19 收尾）的纠错额度正好能把一个 6 次工作的失败前缀
  推到 42 次，而 42 次是本任务实测能越过 28,000 触发的长度区间（v55：26 次 → 28,775；
  v58：32 次 → 29,490）。
- **未改**：触发 28,000 / 目标 22,400 / 硬 39,200 / 原生 33,600、三档阶梯 24/26/28、
  选中规则、触发预测三条件与余量（0 / 2,000）、提示词字节、工具边界、模型与温度、
  共享上限 84、前缀预算上限 42、每臂保留 14、每样本估算输入上限 4,670,000。
- 风险如实记录：若批 03 仍无合格前缀，则按冻结规则报告
  “mechanism still not triggered”，并给出**失败的那条算术**（前缀长度 vs 28,000 触发 vs
  可用请求），不再改任何阈值。

## 2d. 批次 04 的预注册修订：分支 burst 错误之后仍必须跑宿主目标测试（依据本批 03 的付费实测）

批次 03（`…-v59-prefix-fork-03`）**成功冻结了合格前缀，并第一次让三条分叉都拿到 10 次请求**：

- 前缀：`django_lookup_allowed_foreign_primary`、第 0 档、**42** 次 Agent 请求（恰好用满
  预注册的前缀预算上限）、condenser 视图 **36,917** token（> 28,000，余量 +8,917）、
  真实 condenser 重放返回 `Condensation`（36,917 → 14,777，忘掉 80 个事件）、
  账本下界 45,606 ≥ 30,000、宿主目标测试**未通过** → 命中“未通过 ∧ 预测触发”，前缀立即冻结；
- 分叉：三条各携带 **42** 次请求，各自又消耗 **10** 次（= `host_feedback_correction_reserve`），
  `restore_hash_equal` / `source_prefix_unchanged` / `trigger_prediction_matches_freeze` 全为真，
  文件边界 0 违规，插件臂与原生臂各压缩 **1** 次；
- **但 `host_rounds` 全为空、`branch_host_verdicts_measured: false`**：三条分叉把整个 10 次
  纠错窗口**一次性用在了第一轮 burst 里**，burst 以
  `RuntimeError: Frozen correction request limit`（`budget_policy_v42` 第 35 行）结束，
  而 runner 当时把该异常当作分叉失败处理，**跳过了紧随其后的宿主目标测试**。

批次 04 因此只预注册**一处**改动，方向与本批 §2b 对前缀的修法完全一致：

```
run_branch：把 fork.send_message + fork.run() 包在 try/except 里；
            无论 burst 是否以异常结束，都在该轮的最终工作区上跑宿主目标测试，
            把结果写进 host_rounds（带 burst_error_type），该轮有实测判定后停止。
```

- 依据：宿主目标测试**不消耗任何 Agent 请求**（它在本机打分副本上跑），所以
  “纠错窗口用尽”不该等于“没有宿主判定”；§2b 已经用同一条规则修好了前缀侧的同类缺口。
- **未改**：预算策略与 `host_feedback_correction_reserve = 10`（分叉窗口仍是 10 次，
  仍然不是新额度）、共享上限 84、前缀预算上限 42、每臂保留 14、三档阶梯、选中规则、
  触发预测三条件与余量、压缩机制阈值 28,000 / 22,400 / 39,200 / 33,600、提示词字节、
  工具边界、模型与温度、逐分叉持久化与审计方式。
- 成功条件不变：**`branch_host_verdicts_measured: true`（三条分叉全部实测）**，
  否则本批报告 “quality verdict not obtained”，不主张任何质量结论。

## 3. 付费前冻结审计器（v59 修掉的第二个缺陷）

顺序被写进协议，且由门控与审计双向断言：

1. **先把审计工具改完**（`audit_validation_v59.py`、`run_validation_v59.py`、
   `run_validation_windows_v59.py`、`tests/test_openhands_v59_formal_runner_gate.py`），
   本文档与零 API 门控同时定稿；
2. **再**运行 `--check` 阶段，`manifest.json` 在此期间写出，`source_paths()` 把
   **审计器本身**（`integrations/openhands/audit_validation_v59.py`）、正式 runner、Windows 包装、
   本协议文档、v59 门控测试、全部复用模块与数据集文件一起钉进 `source_hashes`；
3. 付费阶段与审计阶段任何一次启动都会逐文件比对 `source_hashes`：**任何漂移都直接终止**
   （`Frozen protocol changed (sources: …)`），不存在“接受门控后修改”的路径；
4. 审计器再自查：`audit_validation_v59.py` 必须在 `manifest['source_hashes']` 里，
   且运行时字节的 SHA256 等于冻结值；`--accept-post-gate-tooling` 在 v59 **被拒绝**
   （`assert not args.accept_post_gate_tooling`）。

因此 v59 的审计报 `frozen_experiment_sources_valid: true`，且**没有**
`post_gate_tooling_edits_recorded` 记录。这是与 v58 的关键差异：v58 的“审计器事后被改再用”
在本批结构上不可能发生。

## 4. 实测逐分支宿主判定（本批的核心交付）

- 每条分叉在**自己的目录** `branch-<position>-<arm>/` 下持久化 `final-files/`、
  `final-hashes.json`（全文件无缓存哈希）、`events.json`、`ledger.json`、`report.json`、
  `artifacts.json`；批次根写 `branch-artifact-index.json` 把上述文件自身的 SHA256 钉住（v58 机制，原样复用）。
- 分叉报告新增三个字段：`host_rounds_completed`、`host_verdict_measured`（该分叉**是否**在自己的
  最终工作区上跑过至少一次宿主目标测试）、`final_host_verdict`（`passed` / `failed` / `None`）。
- 批次级写 `branch-verdicts.json`，并写入 `comparison.json`：
  `branch_host_verdicts_measured`（三条分叉是否**全部**实测）、`quality_verdict_obtained`、
  `quality_verdict`。**只要有一条分叉没有实测宿主判定，本批即报告
  “quality verdict not obtained”，不主张任何质量结论**（成本数字不算质量结论）。
- 审计**独立重跑**：从冻结快照 + 该分叉自己持久化的字节重建最终工作区 → 核对重建后的
  全文件哈希表 → 在重建字节上重跑宿主目标测试 → 与该分叉记录的判定逐项比较；
  每条分叉记录 `host_verdict_measured` 与 `host_verdict_source`。
- 成功条件（预注册）：**`branch_host_verdicts_measured: true`（三条分叉全部实测）**，
  否则本批明确报“quality verdict not obtained”。
- 分支顺序仍冻结为 `none → native_summary → pruner_v1`，**同一绝对路径顺序续跑**，
  每条开始前从只读快照恢复并比对全哈希；分支之间绝不并发。

## 5. 预算、请求与计量（除 §2 抬高的两个上限外，全部与 v53/v54 相同）

| 项 | 值 |
|---|---|
| 模型 / 温度 / thinking | `openai/deepseek-v4-flash` / 0 / disabled |
| 每样本 Agent 请求上限 | **84**（v54–v58：36） |
| 前缀预算上限 | **42**（= 84 − 14 × 3） |
| 每臂保留额度 | **14** |
| 每样本摘要请求上限 | 16（未改） |
| 纠错保留 / 收尾保留 | 10 / 2（未改） |
| 单次估算输入 / 每样本估算输入上限 | 80,000 / **4,670,000** |
| 单次最大输出 | 3,072（未改） |
| 触发 / 目标 / 硬上限 / 原生摘要 | 28,000 / 22,400 / 39,200 / 33,600（未改） |

- 前缀账本的**已消耗请求数与估算输入**继续通过 `PrefixCarryingBudgetPolicy` 带入每条分叉：
  纠错边界在 fork 点固定为 `min(84, 前缀请求 + 10)`。**没有任何分叉获得新额度**，前缀越长，
  分叉可用越少，这一关系在报告与审计里逐条列出。
- 每个请求（含失败请求与摘要请求）落盘到该臂自己的 `ledger.json`。
- **主统计量**：分叉后分支自身的完整总 token（provider 输入+输出，含所有摘要请求）；
  失败与触顶分支全部保留。
- **次要统计量**：把前缀筛选的每一次尝试**各计一次**，再加强该分支的分叉后增量
  （`whole_run_tokens_all_selection_counted_once`）：共同前缀不会被任何一臂重复计入，
  筛选失败尝试的成本也不被隐藏。

## 6. 工具允许范围与文件边界（三臂与前缀逐字相同）

| 工具 | 允许范围 |
|---|---|
| `scoped_editor` | 只能**编辑**该任务数据集 `reference.patch` 触及的单一文件（`--check` 逐任务核对）；越界或工作区外路径在 executor 层直接拒绝 |
| `scoped_symbols` | 只读，只能在冻结的研究文件集合内按 AST 查询定义 |
| `scoped_tests` | 只能跑固定的公开回归模块，最多 3 次、代码未变时返回缓存；不接受命令、路径或测试 ID |
| `finish` | 由预算策略在 `finish` 阶段单独放行 |

没有注册任何 shell；工具名与 `budget_policy_v54.TOOL_NAMES` 由零 API 门控断言一致。

## 7. 零 API 门控（付费前）

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

v59 门控新增/加固的断言（全部零 API）：

1. **上限算术**：`max_agent_calls_per_sample == prefix_budget_cap + branch_request_reserve × arms`
   且 `prefix_budget_cap == max_agent_calls_per_sample − branch_request_reserve × arms`；
2. **保留额度保证**：`branch_request_reserve ≥ host_feedback_correction_reserve`，
   并在合成流程上断言每条分叉 `post_fork_agent_requests ≥ 1` 且
   `prefix + post_fork ≤ 84`，`budget_state.correction_stop_at == min(84, 前缀请求 + 10)`；
3. **拒绝路径**：前缀越过预算上限时 `prefix_reserve_guard()` 抛错；
   前缀若会让分支跌破预注册下限，`ensure_prefix_reserve()` 抛错（不 fork、不声称判定）；
4. **筛选规则**：阶梯顺序（rung-major × 冻结任务顺序）、已通过任务不重跑、
   “宿主未通过 ∧ 预测触发”取第一个满足者；
5. **触发预测复算**：用真实 `ContextPrunerCondenserV51` 在真实 view 上重放，
   并复算冻结 v54 前缀的 22,303 视图 token（未触发的历史对照）；
6. **筛选规则指纹**：v59 的规则指纹与 v58 的逐位相同（阶梯与闸门未变）；
7. **逐分叉持久化 + 篡改拒绝 + 独立重建重跑**：与 v58 同一套门控，全部保留；
8. **逐分支宿主判定**：合成流程里 `host_verdict_measured` 为真、`final_host_verdict ∈ {passed, failed}`，
   且 `branch-verdicts.json` / `comparison.json` 的批次级字段与逐分叉记录一致；
9. **审计器冻结**：v59 审计源码在 `manifest['source_hashes']` 的键集合里，
   且 `--accept-post-gate-tooling` 被拒绝。

真实 Django 树门控（三个冻结任务各准备一次工作区 + 负/正宿主基线，20–40 分钟）：

```powershell
$env:DSH_RUNNER='integrations/openhands/run_validation_windows_v59.py'; $env:DSH_MODULE=''
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v59-prefix-fork-01 --check'
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

## 8. 付费运行与审计

```powershell
$env:DSH_RUNNER_ARGS='--out runs/stage5-openhands/django-multitask-v59-prefix-fork-01 --run --resume'
powershell -NoProfile -ExecutionPolicy Bypass -File .tooling\launch_with_key.ps1

& .\.venv-openhands\Scripts\python.exe integrations/openhands/audit_validation_v59.py `
    --out runs/stage5-openhands/django-multitask-v59-prefix-fork-01
```

`--run` 的退出码：0 = 三臂齐全且 `branch_host_verdicts_measured: true`；
3 = 阶梯走完仍无合格前缀（“mechanism still not triggered”，不发任何分叉请求）；
4 = 三臂跑完但至少一条分叉没有实测宿主判定（“quality verdict not obtained”）。

## 9. 磁盘与计时

- `min_free_disk_gb = 1.5`：每次 prepare、每次分叉前都实测空闲空间，不足即中止并记录。
- 每次宿主评测产生的打分副本（`host-workspace`）在记录文件数与判定后**立即删除**；
  批次不再为每次评测保留一整份 Django 树。
- 分段计时（沿用 v54/v55 并在报告里逐阶段列出）：`prepare` 复制/哈希、快照创建与校验、
  快照恢复、恢复后全哈希、模型等待（前缀/分叉）、宿主测试、**触发预测**、**分叉持久化**、
  打分副本清理、独立审计。

## 10. 与 v58 的差异清单

1. 共享每样本请求上限：36 → **84**（= 前缀预算上限 42 + 14 × 3）；
2. 每臂保留额度：4 → **14**，且 `14 ≥ 分支纠错窗口 10`，由 runner 与审计双向断言；
3. 前缀预算上限：无 → **42**，越界即抛错（不静默收缩）；
4. 触发即停规则：只看“预测触发” → **“预测触发 ∧ 保留额度完好”**；
5. 每样本估算输入上限：2,000,000 → **4,670,000**（同一倍数，保住逐请求语义）；
6. 逐分支宿主判定：只记 `final_host_passed` → 另记 `host_rounds_completed` /
   `host_verdict_measured` / `final_host_verdict`，批次写 `branch-verdicts.json`
   与 `quality_verdict_obtained`；
7. 审计器：门控后可改并用（`--accept-post-gate-tooling`）→ **付费前冻结**，该开关被审计直接拒绝；
8. 未改：三个冻结任务、三档阶梯、选中规则、触发预测三条件与余量、提示词字节（除首阶段请求数
   与总请求数两个由协议数值生成的句子）、工具边界、模型/温度、预算策略其余数值、
   **压缩机制阈值 28,000 / 22,400 / 39,200 / 33,600**、逐分叉持久化与审计方式。
