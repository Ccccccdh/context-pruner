# CrewAI r9 双角色多任务批：必需事实钉住机制（v9）+ 首轮事实齐全门

本协议冻结在付费运行**之前**。冻结文件 `integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R9_MULTITASK_01.json`（付费）与 `..._MOCK_01.json`（零 API 栅格）。

## 1. 本批是机制消融，不是新任务批

r9 与 r8 **故意复用同一批任务、同一套事实、同一判据、同一预算、同一三臂、同一 3×3×3 采样矩阵**，目的就是让"机制"成为 r8 与 r9 之间唯一的变量：

| 项目 | r8（冻结） | r9（本批） |
|---|---|---|
| 任务文件 | `tasks/stage5_autogen/natural_tasks_r8.json` | **同一个文件**（哈希相同） |
| 判据 | `experiments/runners/crewai_semantic_equivalence_v8.py` | **同一个模块**（严格 + 语义两套判据不变） |
| 三臂 / 重复 / 上限 | `none, pruner_v1, native_summary`、3 重复、全局 320 | 变为全局 **200**（本批实际请求数远低于此，仅作硬顶） |
| 机制 | `ContextPrunerMiddleware`（v8 冻结） | `crewai_pinned_evidence_v9.PinnedEvidenceMiddleware`（唯一变量） |
| 首轮事实齐全 | 事后统计 | **一级冻结门指标**（见 §3） |

r8 的目录、冻结、`RESULTS.md`、逐样本答案与评分**一律不改、不重跑、不重打分**。r9 不重新判定 r8 存储的答案；两者的对比只在同一任务的配对均值与门指标之间进行。

## 2. 机制：必需事实钉住（v9）

`experiments/runners/crewai_pinned_evidence_v9.py` 包装冻结中间件 `ContextPrunerMiddleware`，只重写 `before_model` 一个方法，按**确定性三层**处理（只看冻结任务记录与压缩前历史，绝不看模型行为）：

1. **tier 1 逐字工具证据钉住**：`handle_facts` 里每个必需字面量，取冻结工具结果里包含它的最短证据片段（就是 `build_tools` 实际返回的 `json.dumps(result, sort_keys=True)` 字节），作为额外消息**逐字**附加到压缩视图；这些字节与工具真实返回完全一致，压缩无法改写它们。
2. **tier 2 任务合同钉住**：当压缩视图已不再包含第一角色任务陈述（唯一非 system 且带冻结 `HANDOFF` 标记的消息）时，把该消息**逐字**附加回去，使"只输出一行 HANDOFF"的输出格式指令不再被压掉。
3. **tier 3 全前缀回退（确定性且计数）**：若钉住后仍有必需字面量缺失，或钉住使视图越过冻结硬预算，则改为直接投递**未压缩的全前缀历史**，并计入 `required_fact_whole_prefix_fallbacks`，同时记录原因（`required_fact_missing` / `pin_exceeds_hard_budget`）。

每次模型调用的层命中都计入 `pin_tier_counts`；钉住文本按 provider 上报的输入 token 计入本臂账本。上限 `MAX_PINNED_MESSAGES=8`、`MAX_PINNED_CHARACTERS=2400`，超出即触发 tier 3 而不是静默丢字面量。

补救调用（无工具、1 次上限）**保留且仍计入本臂与全局账本**：机制若让首轮变好，收益应体现在首轮，而不是靠删掉补救省出来。

## 3. 新的一级冻结门指标：首轮事实齐全（first_pass_facts）

**定义（先冻结，后运行）**：一个样本"首轮事实齐全"，当且仅当在**任何补救调用之前**由第一角色产出的 HANDOFF 行

1. 在冻结语义判据下包含该任务冻结 `handle_facts` 的**每一个字面量**（`missing_facts == []`），且
2. 是单行（`one_line == true`）且长度在 450 字符合同内。

该指标与严格质量、语义质量**并列报告**，并由独立审计 `experiments/audits/audit_crewai_handoff_v9.py` 从记录的 `role_raw_outputs[0]` 重算、与行内布尔值逐样本比对。任何"质量等价节省"的表述必须同时满足：严格与语义质量不劣、首轮事实齐全已记录、完整总 token 为正。

## 4. 矩阵、预算与成本口径（与 r8 相同）

3 任务 × 3 重复 × 3 臂 = 27 样本；臂顺序轮换、重复间载荷完全一致；`deepseek-v4-flash`、温度 0、禁用 thinking；provider soft/target/hard = 1200/900/3000；Agent 输出 ≤512 token、摘要 ≤1024 token/角色 ≤4 次；全局请求上限 200；失败样本保留并继续；每样本落盘。主成本为**完整总 token**（agent 输入+输出+摘要输入+输出），失败与触顶样本全部进入配对；逐任务报告配对均值、正收益对数、输入/输出侧差与**任务间离散度**。

## 5. 零 API 先行的门

1. `tests.test_crewai_handoff_v9`：27 样本 mock 完整栅格 + 冻结篡改检测 + 首轮事实齐全不依赖补救 + tier 3 回退计数。
2. 既有 CrewAI 零 API 套件（v4/v5/v6/v7/v8 运行器、质量、语义判据）必须全绿；本批不修改这些文件。
3. `--plan` 先跑并核对任务×重复×三臂、模型、预算与全局上限；`--plan` 不发 API。

## 6. 边界

单批为**合成运维任务**：固定工具返回、模拟双角色流程；不含真实代码修复、真实仓库或生产工作流。温度 0 仍有独立轨迹差异，跨批差值不是自动因果证据。**3 任务 × 3 重复无法建立跨任务稳定节省**：本批只报告逐任务配对均值与离散度，并把离散度与首轮齐全、补救次数一起引用。三宿主数据不得合并。
