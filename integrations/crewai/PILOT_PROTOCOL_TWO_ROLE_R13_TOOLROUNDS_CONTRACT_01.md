# CrewAI r13 收口批：保护全部工具轮（K=3）+ 预注册合同措辞

本协议冻结在付费运行**之前**。冻结文件 `integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R13_TOOLROUNDS_CONTRACT_01.json`（付费）与 `..._MOCK_01.json`（零 API 栅格）。**这是最后一次版本升级**，不再开 v14。

## 1. 本批只做两处预注册改动（仍不加任何指令消息）

| # | 改动 | 具体内容 | 不动的部分 |
|---|---|---|---|
| 1 | **结构** | `experiments/runners/crewai_toolrounds_contract_v13.py`：沿用 v12 的「占位消息被压缩视图丢掉就原样追加回」机制，把 **K 从 1 提到 3**——恰好等于第一角色三条冻结任务的工具轮数，因此**第一角色所需的全部轮次都被逐字保护，更早轮次不再可压缩** | 不改压缩算法、不改受保护组、不改 v9 三层（逐字证据钉住 / 任务合同钉住 / 计数型 tier-3 全前缀回退） |
| 2 | **合同措辞（协议层）** | 决策角色收到的提示词 = 冻结 r8 合同句 **+ 一句预注册追加句**（见 §2），明确要求「尖括号占位符必须替换为本次工具证据中的实际值，且不得原样输出尖括号字符」并给出必须逐字出现的字段形状 | **不改判据、不降低严格性、不重打分旧批**：判据仍是冻结 r8 模块逐字不变，仍只读 `decision` 标签字段并比较规范化值 |

**两处都不引入任何 host 指令消息**：中间件唯一的追加物是适配器自己的受保护组占位 dict，适配器据其逐字恢复真实工具消息（与 v12 完全一致）。r11 的自我维持指令循环不会被重新引入。

## 2. 冻结的合同追加句（付费前写定，逐字）

```
The angle-bracket placeholder above is a template: replace it with the actual value
from the current tool evidence of this run, and never output the angle-bracket
characters as part of the final line. The example shape is: RESULT task=<task id>
decision=<the current decision word> evidence=<the key facts and values returned by
the tools now>, with every angle-bracket part replaced by its actual value.
```

该句由 `run_crewai_handoff_v13.decider_role_prompt` 拼接在冻结 r8 合同句之后，并被零 API 测试断言（含"第一角色提示词不含该句"的反向断言）。它**只影响决策角色的提示词文本**；第一角色（调查者）的提示词与 r10–r12 完全一致。

## 3. 预注册失败分类（逐样本记录，禁止事后归类）

每个样本由 runner 按**记录到的那一行**机械分类为以下四类之一；独立审计**从同一行重新推导**分类并逐样本比对，不一致即报错。**判定优先级冻结为：先工具序列轴，再成功，再合同轴**：

| 类别 | 判定（按此顺序） | 含义 |
|---|---|---|
| `tool_sequence` | 第一角色记录的工具有序列 ≠ 冻结工具表（含硬错误行：角色没跑完冻结序列） | 工具序列轴失败 |
| `none` | 工具序列一致，且严格与语义两门都通过 | 成功样本 |
| `contract_placeholder_echo` | 工具序列一致、两门未全通过，且**交付答案里仍含尖括号 `<` 或 `>`**，或 `decision` 标签字段规范化后 ≠ 冻结决策词 | 合同措辞/模型行为轴失败 |
| `other` | 其余 | 未归类缺陷单列，不允许藏进已命名类别 |


报告必须给出**逐样本计数**（按臂、按任务），且 `failure_class == 'none'` 必须与 `strict_success and semantic_success` 完全等价（审计硬断言）。

## 4. 预注册的三种结局（写死在协议里，由审计机械选取）

| 观察（冻结阈值：工具序列 16/16、严格与语义各 ≥13/16） | `pre_registered_outcome` | 结论 |
|---|---|---|
| 工具序列 16/16 **且** 质量 ≥13/16 **且** `still_compressible = false` | `boundary_nothing_left_to_compress` | **边界结论**：在该宿主上"保住第一角色所需的全部轮次"就等于放弃全部节省（与 Agents v7 同构） |
| 工具序列 16/16 但质量仍 <13/16，且剩余失败集中在 `contract_placeholder_echo` | `compression_exonerated_residual_is_model_behaviour` | 压缩在工具序列轴上被**免责**；剩余失败是**模型行为/合同措辞**问题；该宿主**无可引用的质量等价节省** |
| 工具序列 <16/16 | `tool_sequence_not_caused_by_compression` | 少调工具**不是**压缩造成的 |

三种结局都合格，**都不硬凑正收益**。若质量未恢复，则按 §7 写收口结论。

## 5. 判据与一等门控指标（付费前冻结）

| 指标 | 定义 | 历史值 |
|---|---|---|
| `tool_sequence_consistent` | 第一角色记录的工具有序列 = 冻结任务工具表（同序、各一次） | r10 插件 10/16、r11 插件 0/16、r12 插件 11/16 |
| `tool_sequence_matches_baseline` | 插件臂某 (任务, 重复) 序列 = **无压缩臂同键**记录序列 | r12 插件 11/16 |
| `first_pass_facts` | 冻结 r9/r10 定义（任何补救之前首轮 HANDOFF 含全部 `handle_facts` 字面量、单行 ≤450 字符） | r10 三臂 16/16、r12 插件 16/16 |
| 严格 / 语义质量 | 冻结 r8 判据（v13 只路由任务路径 + 声明模板，规则逐字不变） | r10 基线 16/16、r12 插件 7/16 |
| `failure_class` 计数 | §3 四类逐样本 | 新字段 |
| 补救调用 / 全前缀回退 / `recency_*` | 计入本臂与全局账本 | r12：补救 0、回退 0、44/44 轮被保护 |

## 6. 矩阵、预算与上限算术

**任务集不变**（`tasks/stage5_autogen/natural_tasks_r10.json`，哈希 `873a306b…`），矩阵 **4 任务 × 4 重复 × 3 臂 = 48 样本**；臂顺序按 r4 `balanced_plan` 轮换；`deepseek-v4-flash`、温度 0、禁用 thinking；provider soft/target/hard = 1200/900/3000；`fixed_reserved_tokens=300`；输出 ≤512；摘要 ≤1024 token/角色 ≤4 次。

**全局请求上限 440**：最少 agent 请求 = 2 × 48 = **96**；`native_summary` 病态最坏摘要请求 = 2 × 4 × 48 = **384**；440 = 96 + 344（最坏值的约 90%），比 r10 实测 319 高约 38%、比 r11 实测 362 高约 22%、比 r12 实测 327 高约 35%，预期本批总量约 320–330。触顶则停止、保留部分目录、按新 id + `--resume` 续跑。

## 7. 收口条件（本批是最后一次升级）

若本批仍不达验收线（工具序列与严格/语义均不劣于基线、且配对为正），则为该宿主写下**收口结论**，内容必须包含：

1. r9 → r13 的完整链条与每轮机制、质量、成本数字；
2. **每条根因的归属**：压缩（本批由 `tool_sequence_consistent` 与 `recency_*` 直接判定）、模型行为/合同措辞（由 `contract_placeholder_echo` 计数判定）、或未归类（`other`）；
3. 明确写出「**该宿主目前没有可引用的正收益**」。

## 8. 零 API 先行的门

1. `tests.test_crewai_handoff_v13`：K=3 结构与「只追加占位、不追加指令」断言、合同追加句逐字断言（含第一角色提示词的反向断言）、判据对回抄模板必须仍判失败、失败分类四类的机械判定与审计重推导比对、48 样本 mock 完整栅格、审计对残缺工具序列必须选出 `tool_sequence_not_caused_by_compression`、冻结声明篡改拒绝。
2. 既有 CrewAI 零 API 套件（v4–v12 运行器、质量、语义判据）必须全绿；本批不修改这些文件。
3. `--plan` 先跑：核对任务×重复×三臂、`K`、合同追加句、失败分类表、模型、预算、上限与请求算术；`--plan` 不发 API。

## 9. 边界

- 全部为**合成运维任务**（固定工具返回、模拟双角色流程）；不含真实代码修复或生产工作流。4 任务 × 4 重复只有 4 个任务层观测点，不能给出置信区间。
- 温度 0 仍有独立轨迹差异；r10 / r11 / r12 / r13 是四次独立付费运行，跨批差值不是自动因果证据。
- r5–r12 的目录、冻结、`RESULTS.md`、逐样本答案与评分**一律不改、不重跑、不重打分**。
- 三宿主数据不得合并；审计 `errors` 非空时本批任何数字都不得作为有效节省引用。
