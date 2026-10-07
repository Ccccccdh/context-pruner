# CrewAI R17 三臂小试协议（预注册；本轮未发任何请求）

批次 ID：`crewai-two-role-r17-guarded-3arm-01`。协议 ID：`crewai-two-role-r17-guarded`。
冻结文件：`integrations/crewai/PRE_RUN_FREEZE_R17_GUARDED_3ARM_01.json`（文件 SHA256 `67963116…`，自声明 SHA256 见文件内 `freeze_sha256`）。
修订记录：`integrations/crewai/PRE_RUN_FREEZE_R17_GUARDED_3ARM_01_FREEZE_AMENDMENT_20261005.json`。

## 0. 冻结纪律（r17 起强制）

**冻结文件只写一次，永不重写。** 写定后的任何变更**只能**记录在同目录的 `*_FREEZE_AMENDMENT_<日期>.json` 里（改了什么／原因／新旧 SHA256）。

### 0.1 被冻结文件钉住的源，写定后不得再编辑

**任何被冻结文件钉住的源都不得在冻结后再编辑。** 新功能一律落到**新版本文件**（r17 已照此建立 `crewai_loop_guard_v17.py`、`crewai_handoff_v17_tasks.py`；r17 runner 亦为新文件）。若新版本**复用** v16 的模块，必须**断言被复用模块的哈希等于钉住值**，否则把所需代码放进 v17 自己的文件里。

**r16 已发生一次违反并已按纪律登记**：`run_crewai_handoff_v16.py`、`audit_crewai_handoff_v16.py` 在 r16 批次跑完后被编辑（r17 臂作用域改动），造成 12 项钉住源中 2 项漂移。处理方式：**不重写冻结**，新增 `integrations/crewai/R16_SOURCE_PIN_AMENDMENT_20261005.json`（逐项：路径／冻结哈希／当前哈希／改了什么／为什么／何时／**哪些批次用了哪一版**），并声明 **`frozen_experiment_sources_valid = false`**；时间线证据见 `integrations/crewai/R16_SOURCE_PIN_TIMELINE_20261005.json`。

### 0.2 修订感知（fail-closed）与"不许放宽断言"

- `experiments/audits/crewai_amendment_hashes.py`（对齐 agents 侧 `experiments/audits/amendment_hashes.py`）把修订记录变成查表：当前哈希**只有精确等于**修订文件登记的当前值才被接受；**未登记改动一律继续报错**。
- 该机制**只**用于审计路径的漂移分辨，**不得**用于放宽门测试：`tests/test_crewai_handoff_v16.py` 的严格钉住断言**故意保持失败**，直到漂移被复核；门的失败本身就是信号。
- 登记值一经写入即不可变；若源再次被编辑，必须再开一份修订文件，否则验证重新报错。

**写入器拒绝覆盖断言**：`.tooling/write_crewai_r17_freeze.py` 的目标文件已存在即报错退出（实测第二次运行 `exit=1`，`refusing to overwrite an existing freeze`）。

## 1. 本轮唯一改动：守卫触发条件（v16 → v17）

| 项目 | v16（已作废） | **v17（本次）** |
|---|---|---|
| 触发条件 | 出现 `Final Answer:` 标记且工具表未走完 | **本回合没有产生任何有效 `Action`（工具调用）且工具表未走完** |
| 判据来源 | 模型文本 | **宿主自己的解析结果**（`accepts_turn` / `completed_tool_rounds`） |
| `Final Answer:` | 必要条件 | **既不必要也不单独使用**（带标记但无动作的回合由"无动作"规则拒收，二者在该情形下重合） |
| 拒收动作 | 追加**一条**控制消息并重发同一次请求 | 同 v16（消息文本更新为"你这一回合没有调用工具"） |

仍是**只改控制器、不改视图**：压缩视图由**同一个冻结 r13 机制**、**同一份 prepared messages** 产生；守卫不导入、不调用任何视图层符号；唯一新增文本只在拒收路径上。

### 1.1 臂作用域（可证伪的预注册断言，不得默认）

**守卫是宿主侧控制器配置，不是插件机制的一部分。** r16 只在插件臂装守卫，等于给该臂多加了一个基线臂没有的控制器——**这是一个潜在混淆**；r16 之所以还能接受，是因为基线臂 12/12 走完工具表、非插件臂守卫活动为 0，即守卫在其上是**空操作**。r17 把这件事变成预注册的可证伪断言，而不是默认：

1. **安装方式：所有臂都装守卫**（`--guard-scope all_arms`，r17 默认；`pruner_only` 仅保留用于审计对照）。manifest 写入 `guard_scope`、`guard_installed_arms`、`guard_enabled_all_arms`，因此配置本身是记录在案的事实。
2. **空操作断言（成本侧理由）**：守卫只在"冻结工具表未走完且本回合无动作"时才拒收并重发，因此在**合规臂上是空操作 ⇒ 该臂的 token 与调用数不变**；据此要求非插件臂 **零拒绝、零耗尽**。
3. **停止条件**：若在 r17 新任务上守卫在**基线臂**触发（基线也提前收手），说明该主机配置下的工具表约束**不是单向的**，本批**立即停止并报告**，且**不得**把该批当作插件效果证据。审计器已实装：`guard_installed_arms` 声明的臂上出现拒绝即报 `guard fired on a non-plugin arm (r17 stop condition)`。
4. **付费前的现成证据**：`integrations/crewai/R17_ARM_SCOPE_CHECK_ON_R16_20261005.json` —— 用 v17 谓词重放 r16 已记录回合序列、**强行把守卫装在非插件臂上**：24 个非插件单元**零拒绝**，7 个插件单元被拒。即在该主机配置下守卫对合规臂确为空操作；但**下一批仍必须逐单元断言**，不能靠这次重放代替。

**四条负面控制（零 API，真实宿主路径，脚本化模型）**：

| 控制 | 场景 | 结果 |
|---|---|---|
| 1 | 走完 3 个工具后写 `Final Answer:` | 拒收 1 次（`reject_actionless`）→ 继续调用第 4 个工具 → 走完 4/4 |
| 2 | 只在**正文里声称**已调用其余工具 | 拒收 1 次 → 继续按序调用 → 走完 4/4 |
| 3 | **重复调用**已调过的工具 | 宿主自身丢弃重复动作；守卫按"有动作"放行 → 之后仍拒收裸交接 → 走完 4/4 |
| 4 | **先调一个工具、再裸交接**（新增） | 拒收 1 次 → 继续调用 → 走完 4/4 |

## 2. 零 API 反证（能否继续的关键证据）

产物：`integrations/crewai/R17_TRIGGER_FALSIFICATION_ON_R16_20261005.json`（复算：`.tooling/falsify_r17_trigger_on_r16.py`，用 v17 自己的谓词重放 r16 已记录的回合序列）。

| 检查 | 结果 |
|---|---|
| ① 全部裸早停单元被拒收 | **7/7**（`artifact_publish_gate` 0/1/2、`quota_scale_gate` 0/1/2 各 `3/4`、`traffic_shift_gate/2` `3/4`） |
| ② 合法完成单元零误伤 | **5/5**（`complete_with_action`，全部放行；动作回合全部放行） |
| ③ 非插件臂零触发 | 24 个非插件单元 `guard_enabled = false`、`recorded_rejections = 0`；它们是裸交接，**若被误装守卫会被拒收**——检查项正是用来排除这种臂作用域错误 |
| ④ 预计拒绝数 | r16 记录在 v16 下为 **15**；v17 下**下界 = 11**（7 个裸早停各 +1，其中 `traffic_shift_gate/2` 记录已有 4 次） |

## 3. 请求上限算术（依据上一步的真实序列）

产物：`integrations/crewai/R17_REQUEST_ARITHMETIC_20261005.json`。基线 252（每臂 84）；r16 实测一个**耗尽单元**要 15–18 次请求（两单元共烧掉 33 次），这才是成本量级的主因。

| 推回后完成率 | 每臂耗尽单元 | 每臂拒绝 | 全批请求 | 余量（上限 336 / 280） |
|---:|---:|---:|---:|---:|
| 0% | 7.0 | 42.0 | **294** | +42 / **−14** |
| 25% | 5.25 | 33.25 | 285 | +51 / −5 |
| 50% | 3.5 | 24.5 | 276 | +60 / +4 |
| 75% | 1.75 | 15.75 | 268 | +68 / +12 |
| 100% | 0.0 | 7.0 | 259 | +77 / +21 |

**新上限＝336**（最小必要 294）：即使按最悲观的情形（每臂 7 个早停全部耗尽、每次推回 6 次）也留有余量。**触顶即停 + 保留部分目录 + 新 ID `--resume`** 仍是计划内行为；**部分栅格永不当作完整栅格**；`guard_exhausted` > 3/12 即中止且不扩上限。r16 实测 595 次请求即"两个耗尽单元"造成的量级。

## 4. 任务集

**运行批**：`tasks/stage5_autogen/natural_tasks_r17.json` —— `audit_trail_retention_gate`／SHORTEN、`zone_drain_gate`／DRAIN、`compaction_window_gate`／COMPACT、`rollback_point_gate`／DISCARD。
**确认批（守卫冻结后另跑）**：`tasks/stage5_autogen/natural_tasks_r17_confirmation.json` —— `watermark_publish_gate`／HOLD、`autoscale_policy_gate`／ADOPT、`key_rotation_gate`／ROTATE、`index_rebuild_gate`／REINDEX。

两个文件各 4 任务 × 4 工具；垫料为**同一构造模板、本轮自己的 10 组 filler**（repeat 0 时 23 条消息 / 1,753–1,822 字符）；机械断言：与 r9–r16 **无任何** id／工具名／事实字面量／区域／版本／决定复用，且每个任务在冻结 r8 判据下构造样本严格+语义均通过（`R17_CONFIRMATION_TASKS_AND_GUARD_PROBE_20261005.json`）。

**同构性边界**：两文件共享构造 filler，跨任务均匀性**不是**任务间异质性的证据。

## 5. 验收线（不得放宽）与判定

1. 插件臂 `tool_sequence_consistent` **12/12** 且逐任务不低于基线；`first_pass_facts` 不低于基线；`guard_exhausted = 0`；
2. 严格与语义成功**逐任务 ≥ 基线**且全批 ≥ 基线；
3. **同工作量**（两臂 agent 调用数相同且第一角色工具轨相同）前提下，改正后的逐调用口径（`Σ_calls(无压缩每调用输入 − 压缩后每调用输入) − 保护成本`）配对多数为正且全批均值为正；**少调工具一律不计节省**。

三条全中才记"有效节省候选"，仍须限定"该形状"；只中一部分如实写"未通过质量门"；审计 `errors` 非空即明文"不得当作有效节省"。

## 6. 表述边界

- **归因措辞（硬性）**：本次机制链里**守卫与压缩是两个不同的介入**。验收通过时必须写成"**在装上工具轮守卫的主机配置下**，插件在等质量前提下省 X%"，**不得**写成"压缩单独省了 X%"。措辞模板与禁用句式见 `integrations/crewai/R17_ATTRIBUTION_WORDING_TEMPLATE_20261005.json`（含 `control_configuration_sentence`、`allowed_pass_wording`、`forbidden_pass_wording`、`boundary_sentences`）。
- **守卫是宿主侧控制器配置，不是插件机制的一部分**；**压缩收益是在该配置下测得的**——这两句话必须与任何收益数字一起出现。
- **这些任务对守卫不再盲**（r15/r16/r17 都是设计守卫时构造的）⇒ 即使三条全中，也只能写"**该形状上守卫使第一角色走完冻结工具表**"的**开发证据**；确认须用 §4 的确认批任务，且必须在守卫冻结之后。
- 上界是**解析界**（压缩侧受界/估计），不是实测；只有三臂小试能给出实测配对值。
- 4 任务 × 3 重复只有 4 个任务层观测点，**不能给出置信区间**。
