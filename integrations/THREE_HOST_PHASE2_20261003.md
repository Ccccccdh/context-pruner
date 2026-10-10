# 三宿主第二阶段进度（2026-10-03）

依据 `THREE_HOST_EVIDENCE_20261003.md`，本轮同时推进 OpenHands、OpenAI Agents 与 CrewAI。以下是**开发验证**，不是结项确认批；三宿主任务与计费载荷不同，禁止合并节省率。

| 宿主 | 本轮进展 | 插件结果 | 能支持的判断 |
|---|---|---|---|
| OpenHands | 同前缀 `fork()`、Django 20,129 文件原路径恢复、三分支同路径工具续跑及独立账本零 API 门控通过；尚无 v54 付费样本 | 暂无新节省率 | 可构造可审计的共同前缀；正式 Django 工具范围及预算用量门控未完，不应付费 |
| OpenAI Agents | 3 项固定同输入模拟多步任务×3 重复×3 臂=27/27；修正字段读取后独立审计无问题 | 9/9 质量；完整总 token 配对节省 **+69.84%，正 9/9** | 在这些策划模拟工具任务上重复正向；需未参与调参的任务确认 |
| CrewAI | 3 项固定同输入模拟双角色任务×3 重复×3 臂=27/27；独立账本审计无问题 | 原质量门控插件 **3/9**、基线 **3/9**，配对总 token +4.46%、正 8/9 | 质量未达门槛，不能宣称有效节省；需修交接合同并复验失事实样本 |

## 重要审计边界

- OpenAI Agents 首次审计因冻结脚本读错 `provider` 字段而报一项假阳性；付费前原字节快照与冻结哈希一致。付费后独立修正版读取实际 `provider_tokens`，27/27、144 次请求、质量与配对值均通过。两版身份与修正时间分别保留在批次的 `AUDIT_SCHEMA_AMENDMENT.md`。
- CrewAI 的 126 次请求账本、任务哈希、冻结源码与 27 个样本均审计通过。失败主要集中在调查角色把 `HANDOFF ` 写作 `HANDOFF:`、全角冒号或不写标记；这些格式差异不能事后改写冻结质量。还有 1 个插件样本仅输出 `HANDOFF`，遗漏当前工具事实，属于实质失败。该样本压缩后的第二次模型输入仍含关键事实与交接指令，不能断定压缩删掉了事实。
- OpenHands 真实树快照门控在并行哈希和复制后耗时 412 秒；旧 `prepare()` 的串行基线复制与哈希仍占时间。SDK fork 不继承调用方事件 callback；补接后的真实 SDK 三分支工具续跑已验证事件持久化与回调逐项对应、独立 LLM 调用账本及源前缀不变。正式付费分叉前仍要验证 Django 工具的允许范围和 `BudgetAwareLLM` 的实际预算记录。

## 下一阶段的顺序

1. **OpenHands**：补齐正式 Django 工具范围与预算用量零 API 门控，冻结同前缀 runner；从共同的无压缩代码任务前缀及同一路径快照启动三臂纠错小试。通过后扩到多任务×多前缀，并同时报告分叉后的增量成本和共同前缀计入后的全程成本。
2. **CrewAI**：把交接内容事实与交接格式分列为质量指标；修正对 `HANDOFF` 标记的任务合同，保留对工具事实的硬检查。针对插件丢失事实样本查看压缩后模型输入，再在新冻结批次运行，不能以本批重打分替代复验。
3. **OpenAI Agents**：冻结当前策略，换未参与任务设计与调参的自然长任务；继续保持同输入重复、三臂、完整 usage 和独立质量门控。现有 +69.84% 仅作为开发批结果。

索引：OpenHands `integrations/openhands/PILOT_PROTOCOL_V54_DRAFT.md`；OpenAI Agents `runs/stage5-openai-agents-api/openai-natural-v1-r3-dev-01/RESULTS.md`；CrewAI `runs/stage5-crewai/crewai-two-role-fixed-r3-dev-01/RESULTS.md`。

## 后续推进（同日）

- **OpenHands v54**：真实 SDK 同绝对路径三分支工具续跑零 API 测试 1/1 通过：已完成的共同前缀被 fork，分支前恢复相同工作树哈希，工具写入只发生在当前分支，事件回调与持久化后缀逐项对应，各分支 LLM 调用账本独立。另有前缀账本深拷贝门控，防止分支重新获得已用请求额度；相关本地测试 7/7。正式 Django 工具及 `BudgetAwareLLM` 集成门控仍待验证，没有新付费样本。
- **OpenAI Agents 公开源码诊断开发批**：pytest/pylint/Django 三任务×三重复×三臂，27/27 付费样本、104 次请求独立审计通过；无压缩 6/9、插件 **2/9**、原生摘要 4/9。插件完整总 token 配对均值 +9.04%（正 9/9），但质量下降，**不构成有效节省**。详见 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v1-dev-01/RESULTS.md`。该批次使用新阈值与预选公开源码片段，属于开发诊断而非 r3 的盲测确认。
- **CrewAI r4**：新增前瞻性的交接事实/格式分离判定器、独立 runner 与审计。r3 原始交接重放：无压缩、原生摘要、插件事实齐全分别 9/9、9/9、8/9；缺事实的插件样本不得靠本地格式规范化补全。27 样本 mock 已完成，独立审计 `complete: true`、`errors: []`、0 API 请求；人为注入的缺事实样本触发一次无工具补救并将额外调用单列。mock 的 9/9 完成只验证流程可运行；付费前仍须冻结源码与参数并核对 API 异常边界。

## 2026-10-04 续进

- **CrewAI r4 多任务开发批**：三项模拟双角色任务×3 重复×3 臂 27/27 完成，126 次 API 请求；独立冻结审计 `complete: true`、`errors: []`。插件成本配对节省均值 +5.46%、正 8/9，但冻结严格质量 8/9，基线 9/9，不能宣称满足质量门槛的有效节省。失败插件样本的事实和决策正确，答案把冻结词组 `0 failed` 写成 `fail 0`；原生摘要也有 `28%` 写成 `28 percent` 的严格失败。原批评分不回写。详见 `runs/stage5-crewai/crewai-two-role-r4-multitask-dev-01/RESULTS.md`。
- **OpenAI Agents**：公开源码诊断开发批的质量退化仍是主要问题。新增不改变 SDK 模型输入的哈希/约束布尔证据采集层及零 API 测试；待新 runner、独立证据审计和整批零 API 门控完成，才能前瞻性判断压缩是否删去问题约束。v1 原始 27 样本保持不动。
- **OpenHands v54**：已过共同前缀、原绝对路径三分支工具续跑、独立账本门控；正式 Django 工具范围及 `BudgetAwareLLM` 预算用量集成尚未门控，因此仍无 v54 付费效果结论。
- **OpenAI Agents v2 输入证据开发批**：带哈希/字面约束证据的三任务×三重复×三臂 27/27 完成，102 次请求，独立审计无问题。基线成功 6/9、原生摘要 5/9、插件 3/9；插件总 token 配对节省 +9.06%、正 9/9，但质量下降，不构成有效节省。Django 插件三个重复的压缩前输入均含 `other annotations`，压缩后实际模型输入均不含该字面约束；保护组未变。详见 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v2-evidence-dev-01/RESULTS.md`。
- **OpenAI Agents v3 任务锚点小试**：Django 1 任务×3 重复×3 臂，9/9 样本、23 次请求，独立审计通过。把最初两条用户消息逐字保留后三次模型输入都含此前丢失的引用条件；插件严格成功 2/3、基线 3/3，插件配对总 token −33.79%（正 0/3）。其中一个插件样本语义正确但超过冻结 160 字符合同，且多走两次模型调用。该全量保护机制仍不满足质量和节省门槛；结果行未单列任务锚点回退次数。详见 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v3-task-anchor-pilot-01/RESULTS.md`。
- **OpenAI Agents v4 选择性保留小试**：Django 1 任务×3 重复×3 臂，9/9 样本、23 次请求，独立审计 `complete: true`、`issues: []`。插件臂改为确定性选择性保留：注册任务陈述逐单元保护、完全重复行只留第一份、已逐字送达过的工具输出文本换成短注释；任何受保护单元缺失或工具组变化都整份回退。`filter`/`other annotations`/`ordering` 三次都在最终模型输入中（v2 的字面丢失已修好），实际输入确有下降（重复 0/1 为 1,603/1,601 vs 基线 2,874 token）。但插件严格成功 **0/3**、基线 3/3、原生摘要 3/3，配对完整总 token **+7.21%**（+42.88%/+42.85%/−64.08%），因此**不是有效节省**。诊断：`existing_annotations` 只存在于被压缩的源码工具输出里，最终模型输入的该字面命中由 True 变为 False，三次答案都变长并超 160 字符合同 —— 机制把结论所依据的证据搬出了模型可见范围。`task_anchor_restore_failures` 已逐样本持久化（总数 1，触发整份回退，`restore_failure_is_whole_prefix_fallback: true`），补上了 v3 的缺口。按约定不扩样，先改机制。详见 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v4-selective-retention-pilot-01/RESULTS.md`。

## 2026-10-04 OpenHands v54 同前缀分叉

- **正式 runner 与零 API 门控**：新增 `integrations/openhands/run_validation_v54.py`、`run_validation_windows_v54.py`、`audit_validation_v54.py`、`fork_tools_v54.py`、`budget_policy_v54.py`，并冻结 `integrations/openhands/PILOT_PROTOCOL_V54.md`。零 API 门控 `tests/test_openhands_v54_formal_runner_gate.py` 覆盖：正式 Django 编辑/读取范围与数据集 `reference.patch` 一致、executor 层越界拒绝、`scoped_tests` 宿主反馈路径、`BudgetAwareLLM` 前缀请求携带（分支不会重新获得 36 次额度）、三分支同绝对路径顺序恢复与全哈希相等、插件分叉后至少压缩一次、前缀可从 SDK 持久化重建；与既有 fork/loopback 测试合计 **15/15 通过**。
- **真实 Django 树 `--stage check`**：编辑范围等于 `reference.patch` 触及文件；未改动基线上公开 `aggregation` 模块 **124 通过**；宿主补丁副本 **125 项 1 error**（与 `.tooling/gates/v49-django-17084.json` 冻结记录一致）；工作区 20,128 文件。
- **门控中发现并修掉两个真实缺陷**：(1) 工具类名派生出的 `scoped_editor_tool_v54` 与预算白名单 `scoped_editor` 不匹配，会让工作阶段静默失去编辑器（现在显式设置工具名并由门控断言）；(2) 分支账本复制前缀请求行，与携带计数重复计费（现在分支账本只记录自身请求，污染值单列）。
- 批次 `runs/stage5-openhands/django-multitask-v54-prefix-fork-01`：1 真实任务 × 1 共同前缀 × 3 臂（`none`/`native_summary`/`pruner_v1`），同一绝对路径顺序续跑。结果与独立审计见该批次 `RESULTS.md` / `audit.json`。
- **本批结论只能是「机制未触发」**：共同前缀 18 次请求、247,769 完整总 token、宿主目标测试通过、0 压缩（按协议如实记录该前缀不需要纠错，未重跑挑前缀）；三条分叉宿主测试均通过、边界 0 违规、携带前缀请求 18/分支。分叉后 `pruner_v1` 压缩次数 **0**，因此 −43.29% / −45.85% 的相对差是轨迹波动，**与压缩无关**。分段计时：快照恢复 682.90s、恢复后全哈希 86.74s、模型等待 71.33s（前缀）+274.40s（分支）、分支宿主测试 775.03s、独立审计 175.39s —— 模型等待只占分叉阶段约 24%，其余是非模型开销。

## 2026-10-04 CrewAI r5→r8（前瞻语义判据 + 新任务）

- **r4 节省衰减诊断**（零 API 重算）：三个任务**都越过触发门且都真的压缩过一次**，衰减不是「低于门没触发」；`release_readiness`/`customer_migration` 被压到 2314/2332 token（冻结 `target_tokens` 保留下限），17 条历史只删 239 token（9.3%），而 `incident_triage` 删 931 token（36.6%）。`customer_migration` 的 +0.55% 不是压缩收益：输入省 76.7、输出多 55 token/样本，净 **−21.67 token/样本（插件更贵）**，配对正值完全由输出波动产生。角色 2（决策者）9/9 从未触发（估算 1894–2045 < 3085）。见 `integrations/crewai/R4_SAVINGS_DROP_DIAGNOSIS.md`。
- **前瞻语义等价判据**（反例优先，先写测试再冻结）：`experiments/runners/crewai_semantic_equivalence_v5..v8.py`。允许 `0 failed`↔`fail 0`、`28%`↔`28 percent` 等**同事实同决策同工具证据**下的格式差异；缺事实/错版本/错区域/错决策一律不可被规范化补救。**旧批不重打分**；r7 官方评分保持 0/27。
- **三个批次各暴露并修正一个合同/判据缺陷**，均以新 ID 重跑、旧目录未动：r5（补救提示只带最后一个工具观测 → 9/9 插件 `HandoffRecoveryError`；答案行长 300 不可满足）、r6（`APIConnectionError` 中止，9/27 样本，未审计，不作为结论）、r7（禁用事实规则把正确否定判为违规 16 次、`=` 未折叠 9 次 → 双判据 0/27，属判据误判）。
- **r8 为最终结论批**（`runs/stage5-crewai/crewai-two-role-r8-multitask-01`，190 请求，审计 `complete: true`、`freeze_checked: true`、`errors: []`）：三臂严格各 **6/9**、语义各 **9/9**；唯一严格失败原因是 `region_failover` 三臂一致把 `headroom 45%` 写成 `45% headroom`（纯格式，无语义缺失）。插件配对 **+29.78% / +0.53% / +2.65%**，全批 **+10.99%（7/9 正）**，**任务间离散度 29.24 个百分点**。**关键保留**：插件**首轮事实齐全仅 3/9**（`none`/`native_summary` 各 9/9），6 个样本靠一次无工具补救调用补齐（计入插件本臂与全局账本）。结论：**无质量下降，但收益集中在一个任务，不支持跨任务稳定等价节省**。
- **2026-10-04 更正（零 API 重算，依据 `.tooling/diagnose_crewai_r8.py`）**：r8 那 6 个插件样本的首轮丢失**不是压缩把事实搬出可见范围**。逐样本重算显示：14/14 个丢失事实位在其决定性模型调用（第一角色最后一次调用）的输入视图里**仍然存在**，工具观测由受保护组逐字恢复后 6/6 完整；压缩确实在决定性调用前跑过（各 4 次），但没有删掉任何必需事实。真正缺席的是**任务陈述这条输出格式合同**：6/6 丢失样本的决定性视图不再含 `HANDOFF` 合同（压缩视图只剩 8–9 条消息），而无损的 21 个样本 21/21 都带合同；6 次决定性调用的 provider 输出 token 为 232–313，而记录下来的 content 只有 9–32 字符（其余调用 87 token 对应 283 字符），这六次输出去了哪里在本批记录里**无法判定**，保留为未解释观测。诊断全文 `integrations/crewai/R8_FIRST_PASS_FACT_LOSS_DIAGNOSIS.md`。据此把机制改为**必需事实钉住 v9**（逐字钉住工具证据片段 + 任务合同，缺失即确定性全前缀回退并计数），并把**首轮事实齐全升为一级冻结门指标**；r9 批次复用同一批任务、同一判据、同一三臂与矩阵，机制是唯一变量，r8 结果不重打分。


## 2026-10-04 OpenAI Agents v5 证据安全保留小试

- **机制**：压缩资格判据从「这段文本是否已逐字送达过」改为**先查已注册字面约束集**。新增 `experiments/runners/openai_agents_literal_registry_v5.py`（注册字面 + 每个受注册源码范围的路径与行范围，并用公开源码文本自校验）、`openai_agents_evidence_safe_retention_v5.py`、`openai_agents_evidence_v5.py`、`run_openai_agents_repo_diagnostic_v5.py`、独立审计 `experiments/audits/audit_openai_agents_repo_diagnostic_v5.py`、协议与冻结 `REPO_DIAGNOSTIC_V5_EVIDENCE_SAFE_RETENTION_{PROTOCOL.md,FREEZE.json}`、零 API 门控 `tests/test_openai_agents_evidence_safe_retention_v5.py`。装载 `literal` 的工具输出逐字固定；最近一次工具组全文保留；只有既非约束承载、又已送达、且能解析出**文件路径+行范围**指针的输出才被替换为确定性注释；每次调用跑字面生存守卫，任一净损失或超硬字节上限（65,216 = 6000÷0.368×4）即整份回退并计数。v1–v4 源码、协议、冻结与结果目录未改动。
- **零 API 门控**：`tests.test_openai_agents_evidence_safe_retention_v5` 21 tests（20 通过 + 1 父门控子进程跳过项）通过，覆盖 3 重复×3 臂栅格无网络、四字面逐边界保留、工具组边界与组哈希、恢复失败计数与整份回退、真实输入缩减、以及「承载字面的输出被注释替换」必须被审计判失败；冻结的 v1–v4 五个测试模块 25 tests 全通过，v2/v3 审计器在各自冻结批上仍 `complete: true`。
- **批次** `runs/stage5-openai-agents-api/openai-repo-diagnostic-v5-evidence-safe-retention-pilot-01`：9/9 样本、24 次请求（上限 100），独立审计 `complete: true`、`issues: []`。**v4 的字面丢失缺陷已闭合**：四个已注册字面（含只存在于源码工具输出里的 `existing_annotations`）在插件臂每一次与最后一次 `model_input` 中都命中，出现次数与基线、原生臂完全相同，`samples_with_a_missing_registered_literal` 三臂全 0。
- **判定（预注册线：插件严格质量 ≥ 基线 且 完整总 token 配对均值为正）**：插件严格质量 **3/3**、基线 **3/3**、原生摘要 **3/3**；插件完整总 token 配对 **−79.79%（0/3 正）**，原生摘要 **−14.08%**。**质量门槛通过，成本门槛不通过，本批不是有效节省，不扩样。**
- **成本方向反转的机制诊断**：v4 删掉三个源码输出后两轮收口；v5 把两个约束承载输出整体固定后模型需要第三轮（`model_calls_delta = +1`），因此 v5 的规则**必然**每轮重传 4,896 字符的 `read_django_aggregation`。本批还含一个自我造成的假阳性：`_group_hashes` 用 `setdefault` 在**每一次**调用（含过滤后观测）写基线，而过滤后那份 4,896 字符输出已成注释，导致下一次未过滤观测「与基线不一致」→ `protected_tool_group_changed` 整份回退（证据里该次 `filter_before` 与 `filter_after` 字节完全相同）。零 API 诊断 `tests/test_openai_agents_group_baseline_diagnosis_v5.py` 复现该缺陷（现场版 646 字节 / 1 次回退 / 1 次恢复失败；仅把基线改为只从未被改写的观测取、并按公共前缀比较后：1,292 字节 / 0 回退 / 0 恢复失败），并断言修好后仍为负节省，故未回写 v5 冻结源码、也未因此再花付费请求，留作 v6 版本化修复项。
- **边界**：单任务 3 重复、Django 任务在 v1–v4 已参与调参，不是盲测确认批；禁止外推多任务/其他宿主，禁止与 CrewAI、OpenHands 百分比合并。`task_anchor_restore_failures` 插件 3（每样本 1，均为上述假阳性）、基线/原生 0。未参与调参的新任务确认批仍未开始。详见 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v5-evidence-safe-retention-pilot-01/RESULTS.md`。

## 2026-10-04 OpenHands v55→v58 同前缀分叉（机制首次真实触发 + 逐分叉可独立验证）

- **v55 批次 01**（`runs/stage5-openhands/django-multitask-v55-prefix-fork-01`）：预注册的两档三任务
  阶梯（固定顺序 17084 → 16661 → 16100）走了 4 次付费尝试（1,065,205 token），按冻结规则判定
  “mechanism still not triggered”，未发分叉请求。**但第 2 次尝试（16661，26 次请求）已经越过触发**：
  condenser 视图 **28,775** token > 28,000，真实 `ContextPrunerCondenserV51` 返回
  **Condensation**（28,775 → 14,640，忘掉 52 个事件），账本末次估算 34,643 ≥ 30,000——
  唯一挡住分叉的是 v55 **自己预注册的 2,000 token 实测余量**。据此量化出 v54 的预测错在哪：
  v54 拿账本 `estimated_input`（28,275）与阈值比，而 condenser 量的是 SDK view 事件 token
  （v54 冻结前缀实测 22,213，差 6,062），两者不是同一个量。
- **v56 批次 01**（`.../django-multitask-v56-prefix-fork-01`）：把实测重放余量降为 0（机制自身条件）
  并把阶段阶梯扩到三档（24/26/28），**压缩机制阈值一字未改**。5 次尝试（582,493 token）仍全部
  落空：17084、16100 通过宿主测试（不需纠错），16661 三次都只跑了 3–6 次请求就关掉首阶段
  （视图 14.5k–16.5k），提高档位并不能让 provider 多工作。
- **v57 批次 01**（`.../django-multitask-v57-prefix-fork-01`）：按冻结规则自己的补救办法
  ——“在冻结预算内提高前缀长度”——把前缀用**同一语料臂的宿主纠错轮**延长。第 2 次尝试成功产出
  合格前缀（宿主未通过、视图 **34,474**、预测触发、前缀已冻结），但前缀吃满了 36 次共享额度，
  分叉阶段按“任何分支都不获得新额度”的冻结规则**正确地拒绝开工**（
  `Prefix already consumed every agent request`）。
- **v58 批次 01**（`.../django-multitask-v58-prefix-fork-01`，正式结果见该批 `RESULTS.md`）：
  在两处预注册修订后（**触发即停** + `branch_request_reserve = 4`）**成功跑完**：
  前缀 16661、第 0 档、宿主目标测试未通过、**32 次请求**、视图 **29,490** > 28,000、
  真实 condenser 重放 Condensation；三条分叉在同一绝对路径顺序续跑、各携带 32 次前缀请求
  （各自只剩 4 次，绝无新额度）。**插件臂分叉第一步真实压缩 1 次**（29,490 → 15,124 view token），
  分叉后完整总 token **71,469**，`none` 133,071、`native_summary` 133,087 →
  分叉后 **+46.29%**；独立审计 `complete: true`、`mechanism_triggered_plugin: true`、
  `trigger_prediction_reproduced_offline: true`、文件边界违规 0、凭据匹配 0。
- **限制（必须随结果一起引用）**：前缀占 36 次共享额度中的 32 次，分叉只剩 4 次请求，
  三条分叉都没能在 4 次内落地编辑、并在第一个宿主轮内撞上共享上限而中止，因此
  **分叉后的宿主判定没有实测值**（`host_rounds` 为空，`final_host_passed` 是报告默认值；
  审计以 `branch_host_verdicts_measured: false` 记录）。本批因此是**成本侧的压缩效果**
  （同一前缀、同样 4 次预算、插件实测压缩一次），**不是**质量等价证据。
- **逐分叉持久化（关闭 v54 的审计缺口）**：每条分叉在自己目录落盘 `final-files/`（与前缀不同的
  文件字节）、`final-hashes.json`（19,972 项无缓存哈希）、`events.json`、`ledger.json`、
  `report.json`、`artifacts.json`，批次根另有 `branch-artifact-index.json` 钉住这些文件自身的
  SHA256。审计不再“恢复共享路径再重跑”，而是**从冻结快照 + 每条分叉自己的字节**重建其最终
  工作区、核对完整哈希表、再在该字节上重跑宿主目标测试（v58：3/3 重建成功，
  `branch_bytes_rebuilt_from_own_artifacts: true`）。零 API 门控
  `tests/test_openhands_v58_formal_runner_gate.py`（9 项，v55/v56/v57/v58 各 9 项）覆盖
  持久化存在性、**篡改拒绝**（改字节 / 改索引 / 改哈希表 / 删被索引文件 / 恢复被记为删除的文件）、
  预注册筛选顺序与“注入不触发预测 → 拒绝分叉”的诚实中止路径。
- 索引：`integrations/openhands/PILOT_PROTOCOL_V55.md`、`PILOT_PROTOCOL_V56.md`、
  `PILOT_PROTOCOL_V57.md`、`PILOT_PROTOCOL_V58.md`；四个批次的 `RESULTS.md`/`select.json`
  与 v58 的 `audit.json` 均在各自批次目录内。
## 2026-10-04 CrewAI r10（新任务确认批：v9 机制在未参与开发的四任务、四重复上的可复现性）

- **本批只换任务集合与重复数**：新增冻结 `tasks/stage5_autogen/natural_tasks_r10.json`（4 条从未参与 r3–r9 的双角色任务），矩阵为 4 任务 × 4 重复 × 3 臂 = **48 样本**；机制仍是 `experiments/runners/crewai_pinned_evidence_v9.py`（冻结哈希与 r9 完全相同 `5ccc86ab…`）、判据规则仍是 `crewai_semantic_equivalence_v8.py`（v10 只做任务路径路由）；r5–r9 的目录、冻结与评分未改动。付费批 `runs/stage5-crewai/crewai-two-role-r10-fresh-multitask-01`：48/48 样本、319 次请求（上限 440）。
- **结果（冻结口径）**：钉住机制的两项承诺在新任务上守住了——首轮事实齐全 **16/16**、补救调用 **0 次**、全前缀回退 **0 次**（基线同为 16/16）；但插件臂严格与语义质量从基线的 **16/16 掉到 6/16**。10 个插件失败里 **6 个是第一角色工具序列被压断**（`window_gate` 整条任务只调用了 1/3 个工具；`credential_rotation` 两个样本调用顺序不符），**4 个是决策者回抄合同占位符或给出错误决策**（`decision=<RETRY>`、`decision=PROCEED`）。同一占位符回抄在原生摘要臂也出现 3 次、无压缩臂 0 次；工具序列被压断则是插件臂独有。
- **预注册问题的直接答案**：**r9 的 +22% 级配对收益未能在新任务上复现。** 三条工具序列合规的新任务配对均值只有 **+0.98% / +1.83% / +3.11%**（均值 +1.97%）；唯一量级接近 +22% 的 `window_gate`（+41.34%）由少调用工具制造，该任务插件严格/语义 **0/4**。全批配对均值 +11.81%（15/16 正）、任务间离散度 **40.37 个百分点**，但质量不劣前提被打破，因此**不构成质量等价节省**。
- **独立审计**：`complete: true`、`rows: 48/48`、`request_attempts: 319`、`freeze_checked: true`，但 **`errors` 非空（6 条 `tool evidence mismatch`，审计进程 exit 1）**，与上述工具边界失败完全对应；故本批任何数字都不得作为有效节省引用。原生摘要臂配对均值 −25.45%（0/16 正），仍是最差的一臂。
- **本批较 r9 多得到的教训**：只报成本不报工具边界，会把“没干活”记成“省了钱”——`window_gate` 的 agent 输入从 31,973 降到 18,127 token，全部来自少一次工具调用与少两轮会话，agent 输出反而略升。后续任何压缩机制若要在新任务上声称收益，都必须同时给出工具序列一致性与质量门。
- 结果/诊断/冻结：`runs/stage5-crewai/crewai-two-role-r10-fresh-multitask-01/RESULTS.md`、`integrations/crewai/R10_FRESH_TASK_CONFIRMATION_DIAGNOSIS.md`、`integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R10_FRESH_MULTITASK_01.json`、零 API 门 `tests/test_crewai_handoff_v10.py`。


## 2026-10-04 OpenAI Agents v6（指针式行范围保留：机制改动后仍是负结果）

- **先做零 API 归因**：`integrations/openai_agents/V5_COST_REGRESSION_DIAGNOSIS.md`（脚本 `.tooling/diagnose_v5_cost_regression.py`，产物 `V5_COST_REGRESSION_DIAGNOSIS.json`）。v5 的 −79.79 % 可重建分解（provider input token，残差 ≈ 9e-13）：多走一次模型调用 **+2,335.8（99.85 %）**、被 v5 组基线假阳性挡掉的压缩 **+140.2（6.0 %）**、固定文本造成的载荷膨胀 **−136.7（−5.8 %）** —— **主导项是调用轮次，不是重传字节**（v5 插件第 2 次调用比基线还便宜 137 token）。固定本身不导致多走一轮：插件最大载荷 10,382 字节，距冻结硬上限 65,216 字节有 6.28 倍余量，整批 `budget_fallbacks = 0`。
- **v6 唯一机制变更**：承载约束的工具输出不再整份固定，改为**行范围保留**（注册字面所在行逐字保留 + 头部行，其余行区间换成写明路径/省略行区间/保留行号的指针；保留行与省略区间必须恰好划分注册范围，被省略行不得承载注册字面），并修掉 v5 的组基线假阳性（只从未被改写的观测取基线、按公共项前缀比较）。v5 源码/协议/冻结/结果未改。
- **零 API 门控通过**：`tests.test_openai_agents_evidence_safe_retention_v6` 24 项（23 通过 + 1 父门控子进程跳过）；v1–v5 六个冻结测试模块与 v5 组基线诊断测试全通过。门控测得末次调用载荷 7,921 → 4,481 字节（−43.4 %），投影插件完整总 token 约 3,873（对 v5 的 5,325 为 −27.3 %），投影为准正，故付费。
- **付费小试**：`runs/stage5-openai-agents-api/openai-repo-diagnostic-v6-pointer-retention-pilot-01`，9 样本、**33 次请求**（上限 100），独立审计 `complete: true`、`issues: []`、`evidence_checked: 9`；冻结集包含审计器自身源码。
- **结果：not-yet-valid（质量与成本双双不达标）**。插件严格 **0/3**、基线 **2/3**（基线重复 1 自身失败）、原生 **3/3**；插件完整总 token 配对均值 **−176.36 %**（正 0/3，逐对 −341.12 %/−119.68 %/−68.26 %）；插件模型调用 **4/6/6** vs 基线 **2/4/2**，两样本以 `MaxTurnsExceeded` 结束。
- **机制层面诊断**：真实模型在载荷里看到指针后**重新调用工具取回被省略的行**，于是轮次与历史一起增长并触顶作答失败。证据安全项全部通过（逐字面以基线为下界核对无损失、`task_anchor_restore_failures = 0`、`budget_fallbacks = 0`、指针与行区间划分 100 %、工具组边界不变、v5 假阳性归零 3→0），但**"省略会诱发重新取回"这一点零 API 假模型无法表现**（固定轨迹按固定调用数作答），只能在付费批次里测到。**结论：把模型已看过的大段证据移出载荷、只留指针，在本任务上不成立；不扩样，不建议在该任务上继续付费。**
- 文件：协议 `integrations/openai_agents/REPO_DIAGNOSTIC_V6_POINTER_RETENTION_PROTOCOL.md`、冻结 `..._V6_POINTER_RETENTION_FREEZE.json`、机制 `experiments/runners/openai_agents_span_retention_v6.py`、注册表 `openai_agents_literal_registry_v6.py`、证据层 `openai_agents_evidence_v6.py`、runner `run_openai_agents_repo_diagnostic_v6.py`、审计 `experiments/audits/audit_openai_agents_repo_diagnostic_v6.py`、门控 `tests/test_openai_agents_evidence_safe_retention_v6.py`、结果 `.../RESULTS.md`。禁止与 CrewAI / OpenHands 的百分比合并。

## 2026-10-05 OpenHands v59（预注册共享请求上限 84 = 前缀 42 + 每臂 14 × 3；五批付费实测）

- **修掉 v58 的两个缺口**：(1) v58 前缀吃掉共享 36 次里的 32 次，三条分叉只剩 4 次、无一落下编辑，`branch_host_verdicts_measured: false`；v59 预注册 `max_agent_calls_per_sample = 84`（= 前缀预算上限 42 + 每臂保留 14 × 3），并把 `max_total_estimated_input_per_sample` 按 84/36 同倍放大到 4,670,000，使「前缀够长」与「分叉有额度」不再互斥；(2) v58 的审计器在门控之后被改再用（`--accept-post-gate-tooling`）；v59 把审计器连同 runner/门控一起**付费前冻结**，审计**拒绝**该开关。压缩机制阈值 28,000 / 22,400 / 39,200 / 33,600 与三档阶梯一字未改。
- **本批最关键的一条正面实测**（`runs/stage5-openhands/django-multitask-v59-prefix-fork-03`）：前缀首次用满 42 次请求并把 condenser 视图推到 **36,917** token（> 28,000，余量 +8,917），真实 condenser 重放 `Condensation`（36,917 → 14,777，忘掉 80 个事件），宿主目标测试仍未通过，三条分叉各携带 42 次、各再消耗 10 次，`restore_hash_equal` / `source_prefix_unchanged` / `trigger_prediction_matches_freeze` 全为真、文件边界 0 违规，**插件臂与原生臂各真实压缩 1 次**（`mechanism_triggered_plugin: true`）；分叉后成本 `pruner_v1` 212,833 / `native_summary` 288,610 / `none` 442,486 完整总 token（各尝试计一次：1,251,133 / 1,326,910 / 1,480,786）。
- **仍未取得质量判定，且原因与 v58 不同**：三条分叉把整个 10 次纠错窗口一次性用在**第一轮 burst** 里，burst 以 `Frozen correction request limit` 结束，而 runner 当时把该异常当成分叉失败、**跳过了紧随其后的宿主目标测试**，因此 `branch_host_verdicts_measured: false`（0/3）。该缺口已按预注册 §2d 修好（burst 之后仍必须跑宿主目标测试；宿主测试不消耗 Agent 请求），但修好后需要重新冻结，时间窗内未能再跑出一个「宿主未通过 ∧ 触发」的前缀。
- **抬高上限反而暴露了新张力（本批 04/05 的实测）**：窗口放到 42 之后，模型在 41 次请求内真的把该特性写对了，宿主目标测试随之**通过**，于是「fork 点必须未通过」不再成立（05：17084/16661/16100 三条全部通过；04 同样三条全部通过）。也就是说这条路要求**同一份 42 次预算既把前缀推过 28,000 token、又要求模型别修好**，而跨过触发所需的请求数（约 30–42）恰好就是模型修对所需的数量。要同时满足，需要**新任务集合或新提示词**，属于新协议，本轮按约定不改阈值、阶梯与提示词字节。
- **审计状态如实记录**：批 03 有分叉结果，但其冻结快照早于最终审计器（工具在冻结后仍被修订），v59 的严格规则要求**审计器必须是冻结版本**，因此该批**未完成**独立审计 —— 不以运行记录冒充审计结论；只有批 05（`manifest.json` SHA256 `F83286C724DC49CFCC340DE07B2367CAF767D026166DDA7AB1C4B0E921B45800`，当前工具零漂移）与最终工具逐字节一致，但它没有选中前缀。五批 `--check` 全部通过（124+125 / 162+163 / 74+75 的公开与宿主基线逐项一致），零 API 门控 **68 passed**（七个既有文件 51 项 + v59 门控 17 项），凭据扫描 118 个生成物文件命中 0。
- 文件：协议 `integrations/openhands/PILOT_PROTOCOL_V59.md`（含 §2b/§2c/§2d 三次预注册修订）、runner `integrations/openhands/run_validation_v59.py`（Windows 包装 `run_validation_windows_v59.py`）、审计 `integrations/openhands/audit_validation_v59.py`、门控 `tests/test_openhands_v59_formal_runner_gate.py`、结果 `runs/stage5-openhands/django-multitask-v59-prefix-fork-03/RESULTS.md`（批 01/02/03/04/05 全部保留）。禁止与 CrewAI / OpenAI Agents 的百分比合并。

- **2026-10-04 OpenAI Agents v7（适用边界：不给第七种压缩写法，给出可证伪的边界）**：v7 = v6 已审计的缩减**只用于比最新工具轮至少早一轮的轮次**（冻结 `RECENT_TURNS_KEPT = 1`，缩减本体/守卫/回退计数不改），并**未付费**——离线投影为 0 %，按约定不发请求。Django 短任务（基线 2 次调用、完整总 2,962 token）上 `recency_elidable_indices` 每次调用都是空集，插件载荷与基线**逐字节相同**（审计比对两臂逐边界 `input_sha256`；`elided_sources = 0`、`projectable_saving_rate = 0`、守卫与恢复计数全零、最新轮逐条目未被替换），样本 9/9、严格质量两臂 3/3。数字链：v5 多走一轮 = **+2,335.8** input token = 输入回退 **99.85 %** = 整条基线的 **79 %**，而重传固定字节是**收益 -136.7**；故 `K <= N + 1` 时"不改可见文本 -> 0 %"、"改可见文本 -> 必然为负"（v5 -79.79 %、v6 -176.36 %）。边界文件 `integrations/openai_agents/PLUGIN_APPLICABILITY_BOUNDARY.md`（含未测量项：本宿主长会话未测、`N >= 2` 未测、其他宿主不合并；OpenHands 933,719 token 前缀 +15.51 % 全程仅为**另一宿主**的已知取胜区间）。门控 `tests.test_openai_agents_evidence_safe_retention_v7` 13/13 通过，既有 v1-v6 模块 75/75 通过。批次 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v7-applicability-boundary`（离线，`manifest.offline_gate = true`、`paid_requests_sent = 0`）。
## 2026-10-04 CrewAI r11（工具序列批：修「压缩打断工具调用序列」失败，not-yet-valid）

- **只改机制、同任务集**：沿用 r10 的四个冻结任务与判据，新增 `experiments/runners/crewai_tool_sequence_v11.py`（在 v9 三层之上加两条 host 指令：① 工具序列指令——按 host 自己的 ReAct 标记从压缩前历史统计已调用工具，只要冻结工具表还有未调用项就追加一条给出冻结顺序与「下一个必须调用的工具」的指令；② 决策合同指令——投递视图丢失冻结 `RESULT` 合同句时逐字追回并显式禁止输出尖括号）。付费批 `runs/stage5-crewai/crewai-two-role-r11-toolseq-multitask-01`：48/48 样本、362 请求（上限 440）。
- **结果（冻结门控，审计 `verdict: not_yet_valid`）**：插件 `tool_sequence_consistent` **0/16**（基线 16/16）、`tool_sequence_matches_baseline` **0/16**、严格与语义 **0/16**（基线各 13/16）、首轮事实齐全 **4/16**（基线 16/16）、补救调用 **12**、完整总 token **228,075**（基线 135,561），配对均值 **−68.28%（0/16 正）**、任务间离散度 11.11 pp。全批无正收益任务。
- **失败机制**：12/16 插件样本以 `HandoffRecoveryError: recovery still lacks required facts` 硬失败；记录显示第一角色**把同一个工具重复调用了 6 次**（观测 JSON 逐字重复 6 条），并自述 `check_trust_chain=not_called; verify_failover_region=not_called`。工具序列指令在整段会话里持续命中（24 次），即"还有未调用工具"始终为真——追加指令没能推进工具循环，反而与重复调用形成死循环。v9 各层未损坏（全前缀回退 0、工具组恢复/未匹配/悬挂视图计数全 0）。决策合同指令本批 0 次命中，未获评价。
- **r10→r11 直答**：v11 **没有**修好 r10 的工具序列缺陷，反而把 6/16 扩大成 16/16（插件一次也没跑出冻结顺序），首轮齐全 16/16→4/16，成本 +11.81%（无效）→−68.28%。唯一仍达标的是 v9 层（回退 0、工具组完好）。按预注册的迭代上限（只允许一次版本升级）**不再开 v12/v13**。
- 审计：`complete: true`、`rows: 48/48`、`request_attempts: 362`、`freeze_checked: true`，但 `errors` 非空（20 条 = 12 `wrong number of role outputs` + 4 工具序列不符 + 4 工具证据不符），审计进程 exit 1 并明文写出「本批数字不得当作有效节省」。
- 结果/协议/冻结：`runs/stage5-crewai/crewai-two-role-r11-toolseq-multitask-01/RESULTS.md`、`integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R11_TOOLSEQ_MULTITASK_01.md`、`integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R11_TOOLSEQ_MULTITASK_01.json`、零 API 门 `tests/test_crewai_handoff_v11.py`。

## 2026-10-05 OpenHands v60（三硬标志批：门控通过，但本批未出现合格前缀）

- **目标**：同时满足 `frozen_experiment_sources_valid: true`（审计器付费前定稿、零漂移、不用 `--accept-post-gate-tooling`）与 `branch_host_verdicts_measured: true`（三条分叉各实测宿主判定），且插件臂压缩 ≥1。机制阈值 28,000 / 22,400 / 39,200 / 33,600 与阶梯 24/26/28 一律未动。
- **预注册的六处改动**（依据 v59 五批实测）：共享上限 84 → **132**（`= 前缀上限 42 + 每臂保留 30 × 3`，恒等式由 runner 断言）；每臂保留 14 → 30；分叉纠错窗口 10 → **30**（v59 批 03 的失败模式是 10 次窗口在第一轮 burst 内耗尽、宿主测试根本没跑）；前缀宿主反馈轮 3 → **6**（读数更细，让「触发即停」真正生效）；估算输入上限 4,670,000 → 7,330,000（同倍）；冻结提示词新增**只读调查前置条件**（"read-only investigation"）。
- **零 API 门控**：九个测试文件 **85 passed**（既有 51 + v59 门控 18 + v60 门控 16），含上限恒等式、`保留 30 ≥ 分支窗口 30`、前缀越 42 即抛错、v60 与 v59 的机制阈值/阶梯/闸门余量逐项相等、提示词守卫、规则指纹逐位相同、三标志门在缺判定或压缩 0 次时必须 `quality_verdict_obtained: false`；真实 Django 树 `--check` 三项基线（124+125 / 162+163 / 74+75）逐项一致。
- **付费实测（`runs/stage5-openhands/django-multitask-v60-01`，`manifest.json` SHA256 `584A89CDC6BA5FFB5D369068D75244F271A9381AF41B44C2627053BBB344A052`，111 源、零漂移）**：三次尝试（17084 / 16661 / 16100，第 0 档）分别用 **15 / 20 / 8** 次请求就**把宿主目标测试修对**（`host_passed: true`），合计 724,764 完整总 token；按冻结规则「fork 点必须未通过」，阶梯走完后**无候选前缀**，如实报 `mechanism still not triggered`，未发任何分叉请求，退出码 3。
- **额度算术本身成立（可复用的正面结论）**：三次尝试的 `prefix_reserve_at_end` 为 `used=15/each=39`、`used=20/each=37`、`used=8/each=41`（`reserve_satisfied: true`），前缀纠错窗口实测 `min(102, 42, 6+36) = 38`，与预注册一致；`132 − 42 = 90 ≥ 30×3` 从未被触碰。
- **本批的负面结论**：把窗口与轮数放宽、并加上只读调查前置条件之后，该 provider 反而**更快**交出正确修复（15/20/8 次，v59 为 9–41 次），「宿主未通过 ∧ 前缀越过 28,000」这一 fork 前提没有出现。三硬标志因此在本批**不适用**：本批没有 `prefix-freeze.json`、没有分叉，审计不可运行（不以运行记录冒充审计结论）；`frozen_experiment_sources_valid` 只能以「运行期零漂移」这一较弱形式陈述。
- 下一步两个方向（未执行，需先报告再决定）：(1) 换**更硬的实例**（`reference.patch` 触及更多文件/契约更长），需新增数据集门控与 `--check` 基线；(2) 把「只读调查」从提示词升级为**预算阶段**（属于新机制版本，需单独预注册）。两条都不构成本批结论。
- 文件：协议 `integrations/openhands/PILOT_PROTOCOL_V60.md`、runner `integrations/openhands/run_validation_v60.py`（包装 `run_validation_windows_v60.py`）、审计 `integrations/openhands/audit_validation_v60.py`、门控 `tests/test_openhands_v60_formal_runner_gate.py`、结果 `runs/stage5-openhands/django-multitask-v60-01/RESULTS.md`。禁止与 CrewAI / OpenAI Agents 的百分比合并。

## 2026-10-05 OpenHands v61（选项 (b)：强制只读调查阶段 —— **三个硬标志在同一批内全部取得**）

- **唯一改动是冻结提示词**：`prefix_prompt` 把原来一句「use scoped_symbols to locate definitions in the named research source files」换成**强制两阶段**——FIRST PERIOD（只读、强制）必须按冻结顺序**查看该任务全部 6–7 个 `research_files`** 并写出五字段阶段性报告（`FILES VIEWED` / `FAILING ASSERTION` / `OWNER` / `PLAN`）才允许第一次编辑；`correction_prompt` 也要求每个纠错轮**重新查看 allowed 文件并点名失败测试 id 与断言行**。调查对象来自自 v45/v49 就冻结的任务表，渲染器自己写出文件数量，故「规定量」不会与任务表漂移。**阈值 28,000 / 22,400 / 39,200 / 33,600、阶梯 `[24, 26, 28]`、额度算术 `132 = 42 + 30 × 3`、工具边界、模型/温度一律未动**（门控断言 v61 与 v60 在 PROTOCOL 数值上逐项相等）。
- **零 API 门控**：十个测试文件 **102 passed**（既有 51 + v59 18 + v60 16 + v61 17）；v61 新增断言：三个任务**每一个**冻结研究文件都必须出现在渲染后的提示词里、提示词写出的文件数必须等于任务表数量、两阶段骨架与四字段必须在、纠错提示词必须含「重新查看 + 点名失败断言」。真实 Django 树 `--check` 三项基线（124+125 / 162+163 / 74+75）逐项一致。
- **付费实测（批 01，`manifest.json` SHA256 `CEA2F51B1E1227844E51BF033C798783DA99E2A4B6FC29433346EC286EAFAD9F`，111 源）**：第 4 次尝试（16661、第 1 档）**用 38 次请求把前缀做长到越过触发**并**宿主仍未通过**：condenser 视图 **38,390** > 28,000（余量 +10,390）、真实 `ContextPrunerCondenserV51` 重放 `Condensation`（38,390 → 15,129，忘掉 80 个事件）、账本末次估算 47,187 ≥ 30,000、`132 − 38 = 94 ≥ 90` 保留额度完好 → **触发即停、前缀冻结**；选择段合计 1,852,547 token（4 次尝试各计一次，含两次被拒）。
- **三条分叉全部拿到实测宿主判定（3/3）**：`none` 21 次请求 / **1,068,562** token / 压缩 0；`native_summary` 30 次 / **792,454** / **压缩 2**（改了 `django/contrib/admin/options.py`）；`pruner_v1` 24 次 / **590,571** / **压缩 2**（同样改了一个文件）。三条 `host_rounds_completed = 2`、`host_verdict_measured: true`、`final_host_verdict: "failed"`；`restore_hash_equal` / `source_prefix_unchanged` / `trigger_prediction_matches_freeze` 全真、文件边界违规 0、失败请求 0、每条分叉在自己的目录落盘 `final-files/` + `final-hashes.json` + `events.json` + `ledger.json` + `report.json` + `artifacts.json`，批次根 `artifact-index` 钉住其 SHA256。分叉后相对 `none`：`pruner_v1` **+44.73%**、`native_summary` **+25.84%**；全程（前缀只计一次）**+16.36% / +9.45%**。**被拒的两次尝试**（17084 31 次请求、16100 9 次请求，均宿主通过）按规则不再作为候选，没有事后挑前缀。
- **质量边界（必须随数字引用）**：三臂最终宿主判定**全部 failed** —— 本批是「同样失败前提下的成本对比 + 三条分叉都有实测判定」，**不是**质量等价证据，也**不是**「插件修好了」。
- **`frozen_experiment_sources_valid` 本批未取得审计器判定，原因与哈希证据如实记录**：批 01 的审计器在冻结后被修正过一次（预注册阶梯顺序检查未建模冻结的「已通过的任务不再重跑」跳过规则，也错误要求选中点之后的尝试存在——阶梯在第一个合格尝试处停止）。审计因此报 `the auditor is not the revision this batch's gate froze`，**这是规则生效而非绕过**；修正被哈希级复现证明只差这一处（`.tooling/v61_audit_drift.py` → 还原该段后 SHA256 精确等于冻结值 `0cbce38420f1737c…`，且本批 110/111 源与冻结值逐字节一致，唯一差异就是审计器）。故本批只能表述为「运行期零漂移」这一较弱形式，**独立审计结论记为未取得**，不以运行记录冒充；修正后的审计器已用于批 02（冻结值与当前值同哈希、零漂移）。
- **方差证据**：姊妹批 `…-v61-02`（同一冻结工具与提示词）走完阶梯**无合格前缀**——16661 在第 0/1/2 档分别 12/15/11 次请求且宿主**均未通过**，但视图只有 **17,342 / 21,745 / 16,219**，都低于 28,000；17084 与 16100 都在第 0 档通过。即「宿主未通过」在 v61 下稳定出现，**「视图越过 28,000」取决于该任务轨迹是否足够长（批 01 需要 38 次请求）**，两批差异来自模型自发工作量而非阈值或阶梯。
- 分段计时（批 01，秒）：`host_test_seconds` **10,682.67**、`prefix_model_wait_seconds` 2,785.16、`prepare_copy_hash_seconds` 5,607.57、触发预测 10.15、前缀快照+校验 261.15/44.34、快照恢复 772.24、恢复后全哈希 87.27、分叉模型等待 584.48、逐分叉持久化 62.77；墙钟选择段 208.27 + 分叉段 1,114.70，付费段约 2 小时 15 分。磁盘 4.36 → 3.68 GB；凭据扫描 87 + 18 个生成物文件命中 0。
- 文件：协议 `integrations/openhands/PILOT_PROTOCOL_V61.md`、runner `integrations/openhands/run_validation_v61.py`（包装 `run_validation_windows_v61.py`）、审计 `integrations/openhands/audit_validation_v61.py`、门控 `tests/test_openhands_v61_formal_runner_gate.py`、结果 `runs/stage5-openhands/django-multitask-v61-01/RESULTS.md`（批 01/02 均保留）。**按预注册失败线，本轮到此收口，不再开 v62。** 禁止与 CrewAI / OpenAI Agents 的百分比合并。
## 2026-10-05 OpenHands v62（新任务 django__django-15957 + 有界前缀搜索：3/3 实测判定，1 项硬标志未达成）

- **补 V62 门的两项失败项**：① `runner_and_audit_gate` —— 新增 `validation_tasks_v62.py`（新任务注册，旧任务条目从冻结 v49 表原样继承）、`run_validation_v62.py`、`run_validation_windows_v62.py`、`audit_validation_v62.py`、`tests/test_openhands_v62_formal_runner_gate.py`、`.tooling/gates/v62-django-15957.json`；② `authentic_prefix_gate` —— 本批的**有界付费前缀搜索**。阈值 28,000 / 22,400 / 39,200 / 33,600、阶梯 [24,26,28]、`initial_work_requests`、`132 = 42 + 30 × 3`、工具边界、强制只读调查提示词、逐分叉持久化**全部沿用 v61**；门控断言 v62 与 v61 的 PROTOCOL 数值逐项相等（`changed` 只有版本串与任务绑定/变更清单字段）。
- **新任务**：`django__django-15957`（base commit f387d024fc75569d2a4a338bfda76cc2f328f627），可编辑 `django/db/models/fields/related_descriptors.py`，读集合 = allowed + `django/db/models/query.py`、`django/db/models/sql/query.py`、`tests/prefetch_related/tests.py`（共 4 个，提示词逐字列出并自报数量），4 条目标测试，回归模块 `prefetch_related`。零 API 宿主门：公开回归 **109 passed**、未改动基线宿主 **4 errors / 4**、参考修复 **4 passed / 4**；本批 `--check` 独立重测为 109 / **113 项 errors=4**（宿主评估跑整模块 + 4 条目标）逐项一致。**开发用途选择，不是盲测确认任务。**
- **零 API 门控**：十一个文件（v54–v62）**120 passed**（v62 新增：与 v61 逐项相等、4 个 research_files 全部出现在提示词且数量相符、额度算术与保留恒等式、审计器拒绝非冻结批次、新任务表/编辑范围/宿主门记录三者一致、scoped_tests 工具已绑定 v62 任务表）。
- **定价前缀搜索（预注册 ≤6 次尝试、每次 ≤42 请求）**：**第 1 次尝试即合格**——第 0 档、**18 次请求**、condenser 视图 **28,272 > 28,000**（余量 +272）、真实 condenser 重放 `Condensation`（28,272 → 15,914，忘 46 事件）、账本末次估算 34,391 ≥ 30,000、宿主目标测试**未通过**、`132 − 18 = 114 ≥ 90` 保留完好 → 触发即停、前缀冻结（前缀 334,369 完整总 token，事件 62）。尝试上限 1/6、请求上限 18/42，**没有事后挑前缀**。
- **三臂分叉（携带前缀 18 次）**：`none` 19 次请求 / **794,145** token / 压缩 0；`native_summary` 30 次 / **877,564** / 压缩 3；`pruner_v1` 29 次 / **679,427** / 压缩 **3**；三者各改 `related_descriptors.py`。分叉后相对 `none`：**+14.45% / −10.50%**；全程（前缀只计一次）：**+10.17% / −7.39%**。**`branch_host_verdicts_measured: true`（3/3，全部 failed）**、`quality_verdict_obtained: true`、文件边界违规 0、失败请求 0。**质量边界**：三臂最终判定全部 failed —— 本批是「同样失败下的成本对比 + 3/3 实测判定」，不是质量等价证据。
- **独立验证**（`.tooling/verify_v62_flags.py`，脚本自述不是冻结审计的替代品）：由每条分叉**自己持久化的字节**从冻结快照重建 → 全文件哈希表一致 ✔、最终源码摘要一致 ✔ → 在重建字节上重跑宿主目标测试 → 三条**均为 failed**，与记录一致（`branch_host_verdicts_reproduced_by_re_run: true`）。源哈希 **100/101 与冻结值一致**。
- **未达成的硬标志：`frozen_experiment_sources_valid: false`**。唯一漂移是审计器：其**冻结版本本身有崩溃缺陷**（每分叉额度算术检查里先引用 `arithmetic` 后绑定 → `UnboundLocalError`，**根本没判定**），修正后按 v62 规则**正确地拒绝**判定本批。我尝试独立证明漂移范围并**失败**（撤销全部已记录的门控后编辑仍未复现冻结哈希），因此不声称「漂移仅限一处」。本批因此**没有 `audit.json`**，该标志记为 false 而不冒充。要取得它需用修正后工具重新冻结一个新批次（同一任务、同一规则）；按「不连开多版」的约定本轮不再开新批。
- **三个真实缺陷（门控后/付费后发现，均入 RESULTS 与门控回归）**：① `scoped_tests` 工具用**它自己模块**的 TASKS/evaluate/code_revision，第一次付费尝试 1 次请求即 `KeyError` 崩溃（修法：`validation_tasks_v62` 导入时重绑定这三个模块级引用，不改任何冻结文件，并加回归断言）；② 宿主评估跑整模块（113 项）而非门记录的 4 项目标选择运行，首版 `EXPECTED_BASELINE` 写成 4 导致 `--check` 失败，按实测改为 113（付费前、零 API）；③ 冻结审计器崩溃（见上）。
- **分段计时（秒）**：`host_test` **1,017.42**、模型等待 624.44、`prepare_reuse_verify` 87.44、快照 107.76 + 校验 17.91、恢复 297.71 + 全哈希 30.83、触发预测 2.70、分叉持久化 23.39、独立验证 **426.16**；墙钟选择段 25.65 + 分叉段 639.35。磁盘：为腾出冻结规则要求的 1.5 GB 余量删除了**已被取代的临时工作区**（v60/v61 的 `workspaces`/`snapshot`、v62 门控批 `workspaces`，均为可按冻结上游重建的副本，全部证据 JSON 保留），结束时空闲 **1.81 GB**；凭据扫描命中 0。
- 文件：协议 `integrations/openhands/PILOT_PROTOCOL_V62.md`、任务表 `validation_tasks_v62.py`、runner `run_validation_v62.py`（包装 `run_validation_windows_v62.py`）、审计 `audit_validation_v62.py`、门控 `tests/test_openhands_v62_formal_runner_gate.py`、门记录 `.tooling/gates/v62-django-15957.json`、结果 `runs/stage5-openhands/django-multitask-v62-01/{RESULTS.md,verify-flags.json,post-gate-audit-drift.json}`。禁止与 CrewAI / OpenAI Agents 的百分比合并。

### 2026-10-05 OpenHands v62 预筛补记（真实载荷五道顺序门，零 API）

- **按 `integrations/REAL_PAYLOAD_PRESCREEN_20261005.md` 的五道顺序门回溯核 v62 的冻结前缀**（`.tooling/prescreen_v62.py` → `runs/stage5-openhands/django-multitask-v62-01/prescreen.json`，17.78 秒，零 API、不重跑宿主测试、不改冻结）：**4/5 通过**，唯一未通过的是第 5 道小试与确认门。
- **门 1 证据门：通过（附限制）**——真实冻结前缀 62 事件、事件 ID 摘要 `efb770d5…`、manifest `52F5EE4D…`；但**独立审计未签署**（冻结审计器崩溃），故只用于机制诊断，证据侧数字来自对每条分叉自身字节重建后重跑宿主测试的工具级验证器。
- **门 2 曝光门：通过**——实测视图 **28,272 ≥ 28,000**（余量 **+272**，三批里最薄的余量）、真实重放 `Condensation`（28,272 → 15,667）、账本末次估算 34,391、fork 点宿主目标测试**未通过**；保留恒等式 **`132 − 18 = 114 ≥ 90`**（每臂余 38 ≥ 分支窗口 30）。**非伪曝光**：前缀是该任务真实轨迹（强制只读调查 + 真实编辑），在触发判定成立那一刻停止，没有靠延长无效调查历史跨阈值。
- **门 3 非零收益空间门：通过，且按要求分开报两个量**——**潜在旧单位上界 46**（冻结 condenser 在真实前缀视图上遗忘的事件数）与**守卫后安全候选 46**（扣除任务契约、字面证据、工具调用配对后仍允许丢弃的单位；重放显示守卫保留 16 个单位、12,542 保护 token），省略 token 体量 12,605。与 v58（183）/ v61-01（209）相比新任务的候选集合更小但仍非零。**逐调用累计口径**（§4 勘误要求、取代已作废的「首帧一次性文本」上界）：`none` 19 次调用累计估算输入 913,095 / 完整总 794,145；`pruner_v1` 29 次累计 684,070 / 完整总 679,427（3 次压缩）→ 累计节省 229,025 估算输入、完整总 **+14.45%**（与分叉后成本差独立复算一致）。
- **门 4 不变量门：通过**——`restore_hash_equal` 全真、文件边界违规 0、失败分支请求 0、`trigger_prediction_matches_freeze` 全真。
- **门 5 小试与确认门：未通过**——本批 1 任务 × 1 前缀 × 3 臂（单前缀单重复），且**三臂最终宿主判定全部 failed**：只能支持「等失败前提下的成本对比 + 3/3 实测判定」，**不能**写结项收益或质量等价。
- **顺序说明（如实）**：预筛要求在付费前缀搜索**之前**核门；v62 的付费搜索已在此前完成，因此这是**在真实冻结载荷上的回溯预筛**（与预筛文档对 v58/v61 的回溯口径一致），不是运行前预测。若后续再开新任务批次，将在付费前先走这两道门。

### 2026-10-05 OpenHands v62 收益口径更正（逐调用累计，旧"首帧一次性文本"口径作废）

- **按勘误要求改用逐调用累计口径**：`Σ_calls (无压缩每调用输入 − 压缩后每调用输入)`，扣除项单列（`.tooling/percall_v62.py` → `runs/stage5-openhands/django-multitask-v62-01/percall-yield.json`，零 API，只读记录账本与 metrics）。**旧口径（只记"首帧一次性文本"）在本批的任何 token 读数一律作废、不得用于筛除或放行**——CrewAI r15 实测证伪该口径（旧口径 +10.247% vs 同工作量实测 +35.16%，3.43 倍；错因是整段首帧在之后每次调用被重复投递）。
- **逐调用分解（v62 批次 01）**：无压缩 `none` **19** 次 Agent 调用、`Σ` 每调用输入 **913,095** token（33,955 / 36,751 / … / 61,767）、provider prompt/completion 911,360 / 2,785、完整总 **794,145**；压缩 `pruner_v1` **29** 次调用、`Σ` 每调用输入 **684,070**（16,481 / 17,051 / … / 32,363）、provider 660,986 / 18,441、完整总 **679,427**（3 次压缩）。
- **差额与扣除项**：逐调用累计节省 **+229,025 输入 token**（每调用平均 **12,054**，对无压缩每调用均值 ≈ **19.2%**）、provider prompt 节省 +250,374、完整总节省 **+114,718 = +14.45%**。扣除项：辅助摘要请求 **0 次 / 0 token**（两臂一致，按自身 provider token 计入完整总口径）、**整份回退 0 次**、钉住/受保护内容 **12,855 token**（**留在压缩后的每调用输入里，因此不从节省中扣减**，单列以防误读）、插件多花 **10** 次调用（已在其自身合计内，不二次扣减）。
- **两处必须分列的量**：**潜在旧单位上界 46**（单位计数）与**守卫后安全候选 46**（守卫保留 16 个单位、12,542 保护 token）——按预筛要求分列，且明确**单位计数不是 token 上界**，两者不可互相替代；token 侧判断只用上面第 2、3 条的逐调用口径。
- **判定**：逐调用累计节省与完整总节省均为正（+14.45%），但**三臂最终宿主判定全部 failed** → 这是「等失败前提下的成本对比 + 3/3 实测判定」，**不是**质量等价证据，也不构成结项收益。

### 2026-10-05 OpenHands v63 审计修正（防复发门）+ v62-01 非冻结版事后审计

- **新审计器 `integrations/openhands/audit_validation_v63.py`**（新 ID，不动 v62 及更早文件）：把冻结前缀的额度算术**在每分叉循环之前绑定一次**，并把那段检查抽成可被门控直接调用的 `branch_arithmetic_checks(freeze, report, arithmetic, ceiling)`；其余规则不变（必须是被冻结的审计器版本、硬拒 `--accept-post-gate-tooling`、逐分叉从自身字节重建后重跑宿主测试）。它接受 v62/v63 两个批次族标记，因此能用于它存在的那一批。
- **防复发门 `tests/test_openhands_v63_audit_gate.py`（4 passed）**：① 用 AST 断言 `arithmetic` 的绑定行号**早于**每分叉循环、且循环体确实调用 `branch_arithmetic_checks`（按模块代码对象检查，不是读文本）；② **直接执行那段曾经崩溃的代码**——在 v62-01 的三条真实冻结记录上跑完，并在把 `branch_total` 调成 10⁹ / 把 `identity_holds` 调成 False 时**必须**以具名断言失败（`reserved branch requests` / `cap identity`）；③ 事后逃逸开关仍被拒绝；④ 在**合成的极小批次**上把审计器从 `main()` 端到端驱动一遍，要求它**以判定或具名断言终止，且永不出现 `UnboundLocalError`**。含 v54–v62 全部文件复跑 **124 passed**。
- **非冻结版事后审计（明确标注）**：用 v63 审计器对 v62-01 跑了一次，产物 `audit.json` + `AUDIT_REPORT.md` + `audit-posthoc-status.json`。**三件事同时写在批次里**：① 这是**非冻结版**事后审计（`post_hoc_non_frozen_audit: true`）；② 它**不是**本批冻结修订版的判定（`not_the_frozen_revision_verdict: true`，因为本批冻结的是崩溃的 v62 审计器）；③ **`frozen_experiment_sources_valid` 仍为 false**。
- **该次机械核对的结果**（仅供参考，不作为冻结判定）：`branch_host_verdicts_measured: true` 且 **审计重跑复现**（`branch_verdicts_reproduced_by_the_audit: true`）；三条分叉 `host_verdict_measured: true` / `final_host_verdict: failed` / 均改 `related_descriptors.py` / 分叉后 794,145、877,564、**679,427** token / 各携带前缀 18 次请求；`plugin_compaction_count: 3`；`quality_verdict_obtained: true`；`branch_restores_hash_equal: true`、`branch_bytes_rebuilt_from_own_artifacts: true`、`file_boundary_violations: 0`、`failed_branch_requests: 0`、`shared_ceiling_respected: true`、`prefix_cap_arithmetic_holds: true`、`trigger_prediction_reproduced_offline: true`、`credential_matches: 0`；审计自身耗时 334.82 秒。
- **保留的旧 drift 记录 + 新增更明确的表述**：`post-gate-audit-drift.json` 原样保留；`audit-posthoc-status.json` 另写明「**冻结版审计器崩溃**（`produced_a_verdict: false`）」与「**修正版无法复现冻结哈希**」（`frozen_auditor_sha256 017f4dab… vs reconstruction a131e1c0…`，`reconstruction_reproduces_frozen_auditor: false`），即漂移范围**不可自证**；源哈希普查 100/101 一致、唯一漂移是 `audit_validation_v62.py`。
- **磁盘（硬约束）**：占用前几名——`runs/stage5-openhands/django-multitask-v49-pilot-01/workspaces` **2.149 GB**、`django-17084-v48-pilot-01/workspaces` **1.071 GB**、`django-17084-v47-pilot-02/workspaces` 0.357 GB、`django-multitask-v49-gate-01/baseline` 0.352 GB、`django-17084-v47-pilot-01/baseline` 0.119 GB、`django-17084-v48-gate-01/baseline` 0.119 GB、其余 `workspaces`/`baseline` 各 0.039–0.117 GB。删除提案只针对 `.gitignore` 已声明为**可重建**的类别（`workspaces/`、`baseline/`、`prefix-snapshot/`、`sdk-persistence/` 与 `.tooling/scratch/`），**禁止**删 RESULTS/audit/manifest/冻结/ledger/events/索引等证据文件。执行结果：空闲由 **1.811 GB → 5.647 GB（+3.836 GB）**；删除包含 `.tooling/scratch`（含 requests-probe）。因该文件系统上遍历极慢，清理**部分完成**（仍有 `baseline/` 类约 0.97 GB 与少量 `workspaces/` 可再释放），已验证 `RESULTS.md`/`audit.json`/`manifest.json`/`prefix-freeze.json` 等证据文件全部完好。

## 2026-10-04 CrewAI r12（结构保护：最近 K=1 个工具轮逐字保留，not-yet-valid 但机制生效）

- **用结构取代指令**：新增 `experiments/runners/crewai_recency_tool_rounds_v12.py`——在 v9 三层之上，把压缩前列表里**最新的 K=1 个受保护工具轮占位消息**在压缩视图丢掉时**原样追加回**（追加的是适配器自己的占位 dict，因此适配器逐字恢复真实工具消息），更早轮次仍可压缩并逐调用/逐样本计数（`recency_omitted_tool_rounds_total`）。**全批不添加任何指令消息**，r11 的自我维持循环因此消失。付费批 `runs/stage5-crewai/crewai-two-role-r12-recency-toolrounds-01`：48/48 样本、327 请求（上限 440 未触顶）、0 错误、0 补救、0 全前缀回退。
- **结构规则按预期生效**：插件臂 44 次工具轮 **44/44 逐字保住**（`recent_rounds_readded_total = 0`，即冻结压缩本身没丢最新轮），同时 **42 个更早轮次被省略、60 次压缩事件真实发生**——压缩确实在省更早的轮次。首轮事实齐全回到 **16/16**（r11 是 4/16）。
- **但验收线未通过（审计 `verdict: not_yet_valid`、`errors` 10 条、exit 1）**：插件 `tool_sequence_consistent` **11/16**（基线 16/16）、严格与语义 **7/16 / 7/16**（基线各 13/16）、配对完整总 token **+6.36%（13/16 正）**、任务间离散度 18.84 pp。`window_gate` 的 +19.76% 由两个"只调用 1 次工具"的样本制造（+42% / +42%），另两个合规重复是 −0.78% / −4.38%。
- **两类根因都不在 K=1 的保护范围内**：① 决策者**回抄合同模板**（`<RETRY>`/`<CLEAR>`/`<RENEW>`，6 个样本）——逐样本核对显示**两个臂的决策者最终输入都是 16/16 含锚点+模板**，即模板没被压掉，模型是**看着模板抄下来**，所以"合同缺失时追回文本"这一类修法（v11）根本不适用；② 第一角色仍**少调工具或顺序不符**（`credential_rotation` 三次全调但顺序不符，`window_gate` 两次只调 1 个）。
- **r10 / r11 / r12 直答**：工具序列 10/16 → 0/16 → **11/16**；插件严格 6/16 → 0/16 → **7/16**；首轮齐全 16/16 → 4/16 → **16/16**；错误 0 → 12 → **0**；配对均值 +11.81%（无效）→ −68.28% → **+6.36%（仍无效）**。r12 修好了 r11 造成的破坏并略微超过 r10，但**没有**修掉 r10 的两个根因，因此**不构成质量等价节省**；`still_compressible = true`，故也不能写成"保住质量后已无可压缩"的边界结论。
- 结果/协议/冻结：`runs/stage5-crewai/crewai-two-role-r12-recency-toolrounds-01/RESULTS.md`、`integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R12_RECENCY_TOOLROUNDS_01.md`、`integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R12_RECENCY_TOOLROUNDS_01.json`、零 API 门 `tests/test_crewai_handoff_v12.py`。按预注册节奏，第二次版本升级（v13）需先报告再决定。

- **2026-10-04 OpenAI Agents v9（守卫口径修正 + 长基线付费小试：正投影、零收益）**：v9 只修 v8 暴露的**守卫分类矛盾**（受保护集合从「任务陈述全部单元」收窄为「**承载注册字面的单元** + 注册字面单元 + 字面承载行 + 工具输出结构」；陈述单元 8 → 2，离线 `restore_failures` 5 → 0、省略 0 → 15 源/632 行）；缩减本体、注册表、去重规则、回退语义、阈值与预算一律不动。**两条必测断言**均通过：**正保护**（逐样本逐边界逐字面，插件 `registered_literal_counts` 不低于同一边界基线臂的计数；最终输入以基线最终输入为下界）；**负控制**（收窄**按文本内容**判定：承载 `filter operations` 的行受保护、不含字面的请求行不受保护，把字面**注入请求行**后该行立即受保护 2→3；人为丢掉字面时 `_carrier_loss` 报损失、过滤器拒绝并计数，干净路径零回退）。**付费**：离线投影 +34.15 %（零 API，K=7，留档 `integrations/openai_agents/V9_OFFLINE_PROJECTION.json`）→ 按「投影为正才付费」跑 9 样本 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v9-long-baseline-boundary`，69 次请求、独立审计 `complete: true`、`issues: []`、9/9。**结果 not-yet-valid**：插件严格 3/3、基线 3/3、原生 2/3；插件输入 17,003.0、完整总 17,187.0，**与基线逐字节相同**，省略源 **0**，配对完整总 **−0.000004 %**（正 1/3）。**根因（新证据）**：真实 provider 载荷里工具条目**连续**（不插入 assistant 消息），`turn_count` 恒为 1（逐边界 0/1/1/1/1/1/1，`elidable_indices` 全空），于是「保留最新 N 轮」= 保留整段历史、无可省略轮次；离线桩在轮间插入了 assistant 消息、虚构了 `turn_count=6`，故给出 +34.15 % 的假正投影。**边界修正为双侧实测**：短侧 v7（K=2）0.0000 %、长侧 v9（K=7）−0.000004 %，**在该口径下"更长会话"不会让插件可压缩**；下一方向只能是把新近度计量单位从「连续工具组」改为「工具调用序号」（未实现、未测，迭代上限已用尽）。边界文档 `integrations/openai_agents/PLUGIN_APPLICABILITY_BOUNDARY.md`。门控 `tests.test_openai_agents_long_baseline_boundary_v9` 9/9 通过，既有 v1-v8 模块 88/88 通过（3 跳过）。禁止与 CrewAI / OpenHands 的百分比合并。

- **2026-10-04 OpenAI Agents v8（长基线构造成功；离线门控被守卫分类矛盾挡住，未付费）**：v8 = v7 新近度规则（N=1）**原样复用**，只改任务输入——冻结 Django 问题与请求两条消息逐字不动，**追加一条用户消息**承载预注册只读调查协议（6 次一轮一工具读取 + 第 7 轮作答）。零 API 栅格实测 **K = 7** 次调用、`plugin_can_elide_anything = true`（可省略 5 轮），插件确实进入省略路径（15 源 / 632 行），但**每次调用都以 `missing_protected_unit` 整份回退**（`task_anchor_restore_failures = 5/5/5`），载荷与基线逐字节相同、配对 0.0000 %。**根因**：守卫把任务陈述全部文本单元列为受保护，而冻结缩减里还有「丢弃本次已逐字送达过的消息单元」的规则，请求那一行同时出现在陈述与请求消息中 → 被去重丢掉 → 守卫报缺失；同一守卫在 v7 短基线上也报过 5 次 `protected_item_dropped`（当时无可省略轮次，故无害）。**未付费**（投影不为正），已按约定先报告并获批准，在 v9 中只修这一处口径。文件：`experiments/runners/openai_agents_long_task_registry_v8.py`、`openai_agents_long_baseline_boundary_v8.py`、`openai_agents_evidence_v8.py`、`run_openai_agents_repo_diagnostic_v8.py`、`.tooling/gate_openai_agents_long_baseline_v8.py`。
## 2026-10-04 CrewAI r13（收口批：K=3 全部工具轮 + 预注册合同措辞；预注册结局第三种）

- **两处预注册改动（仍不加任何指令消息）**：① 结构 `crewai_toolrounds_contract_v13.py` 把"最近 K 个受保护工具轮被压缩视图丢掉就原样追加回"的 K 从 1 提到 **3**（等于第一角色三条冻结任务的工具轮数）；② 决策角色提示词 = 冻结 r8 合同句 + 一句预注册追加句（尖括号占位符必须替换为本次工具证据的实际值、不得原样输出尖括号）。判据未改、严格性未降、旧批未重打分。付费批 `runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-01`：48/48 样本、325 请求（上限 440 未触顶）、0 错误样本、0 补救、0 全前缀回退。
- **改动效果**：插件臂 44 次工具轮 **44/44 逐字保住**、`recency_omitted_tool_rounds_total = 0`（第一角色已无可压缩工具轮，`still_compressible = false`）；尖括号回抄由 r12 的 6 个样本 **降到 0**（三臂 `contract_placeholder_echo` 全 0/16）。
- **但验收线仍未通过（审计 `verdict: not_yet_valid`、`errors` 8 条、exit 1）**：插件工具序列 **12/16**（基线 16/16）、严格与语义 **12/16 / 12/16**（基线各 16/16）、配对完整总 token **+6.25%（14/16 正）**、任务间离散度 **13.33 pp**。失败分类逐样本：插件 `none 12 / tool_sequence 4 / contract_placeholder_echo 0 / other 0`，基线 16/0/0/0，原生摘要 16/0/0/0（分类由 runner 机械推导、审计独立重推导并比对）。
- **预注册结局机械命中第三种**：`tool_sequence_not_caused_by_compression`。4 个失败样本（`credential_rotation` ×2、`window_gate` ×2）答案本身正确、必需事实齐全、受保护轮 `omitted = 0`、回退 0、工具组恢复失败 0，且 **tier-2 任务合同钉住在全部 16 个插件样本都 ≥1**（"按顺序对每个可用工具各调用一次"可见）；其中 `credential_rotation/1` 的钉住与压缩计数（tier1 4 / tier2 3 / 压缩 4）与合规样本完全相同却仍顺序不符 → **第一角色少调工具/顺序不符是模型提前结束工具循环的行为**，不是压缩搬走了证据。边界：本批未做"先验完整前缀"对照，故严格表述是"与工具轮保护无关"。
- **r9 → r13 链条**：工具序列 9/9 → 10/16 → 0/16 → 11/16 → **12/16**；插件严格/语义 6/9·9/9 → 6/16·6/16 → 0/16·0/16 → 7/16·7/16 → **12/16·12/16**；插件配对均值 +8.01%（无效）→ +11.81%（无效）→ −68.28% → +6.36%（无效）→ **+6.25%（仍无效）**；离散度 22.50 → 40.37 → 11.11 → 18.84 → **13.33 pp**。
- **收口结论（该宿主）**：**该宿主目前没有可引用的正收益**——五轮里"质量不劣 + 配对为正"从未同时出现。根因归属：首轮事实丢失已被 v9 修好且可迁移（16/16、补救 0）；决策者回抄合同占位符属**模型行为×合同措辞**（r13 已用预注册措辞消除，6→0）；第一角色提前结束工具循环属**模型行为**（工具轮已逐字保护、合同可见）；host 指令是可证伪杠杆（r11 自我维持循环，0/16、−68.28%）；在该宿主上保住"第一角色所需的全部轮次"后压缩收益空间已被质量门吃掉（K=1 时省更早轮次但质量 7/16，K=3 时工具轮无可省略）。
- 结果/协议/冻结：`runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-01/RESULTS.md`、`integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R13_TOOLROUNDS_CONTRACT_01.md`、`integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R13_TOOLROUNDS_CONTRACT_01.json`、零 API 门 `tests/test_crewai_handoff_v13.py`。这是最后一次版本升级，不再开 v14。
## 2026-10-04 CrewAI r13 收口对照（零 API replay：视图逐边界相同 → 少调工具属模型行为）

- **做了什么**：用 `.tooling/control_crewai_r13_tool_loop.py` 对 r13 的 4 个 `tool_sequence` 失败样本做零 API 对照——Layer A 从批次 `results.jsonl` 取已记录不变量，Layer B 用冻结任务数据 + 冻结 r9 钉住中间件 + 冻结 v13 结构规则 + 真实适配器 `_protect_tool_groups`/`_restore_tool_groups` 重建两臂视图并比较内容集。**不发任何付费请求、不重跑、不重打分。**
- **结论**：`outcome = same_content_at_decision_boundary`。插件视图**工具名 3/3、三个工具证据 3/3、`handle_facts` 3/3、输出合同锚点命中**，`missing_in_plugin_vs_baseline = []`；基线 70 条消息/约 5.3k 估计 token vs 插件 8–9 条/约 1.8–2.1k，差额**全部是被合法压缩的历史垫料（即节省本身）**；唯一"基线有而插件没有"的是**该角色自己从未调用过的工具的证据**，而同一视图里该工具的**名字与 schema 都在**。Layer A 在 16/16 插件样本上：回退 **0**、工具组恢复失败 **0**、工具配对错位 **0**、`recency_omitted_tool_rounds_total` **0**、首轮齐全 **16/16**、tier-1 钉住 60 次。
- **属模型行为**：r13 那 4 个失败（`credential_rotation` ×2、`window_gate` ×2）在**内容不缺、合同指令可见**的前提下提前结束工具循环，答案本身正确（`renew`/`clear`）。因此 r13 的预注册结局 `tool_sequence_not_caused_by_compression` **由逐边界证据确认**，不再只是推断。
- **顺带修正一处计数**：r13 `RESULTS.md` 原写"16 个插件样本 tier-2 合同钉住都 ≥1"，实测为 **12/16**（另 4 个是 `shard_split`，其任务陈述本身存活，tier-2 按设计不触发）——两条路径互补、**无样本两条都没走到**，故工具循环指令在 16/16 的决定性调用里都到达了模型。r12 的同批交叉核对：9 个质量失败中 **5 个工具序列被打断**（`omitted` 3/3/3/0/0、回退 0、首轮齐全 16/16）、**4 个是合同模板回抄**（序列完好）；r10 的 16 个同类样本**没有逐行工具轨记录**，按计数保留、不当作 replay 证据。
- **该宿主总收口结论（不变）**：r9→r13 链条与各根因归属（首轮事实丢失=v9 已修；合同占位符回抄=模型行为×措辞、6→0；少调工具=模型行为；host 指令=已证伪；保住全部所需轮次后压缩收益空间被质量门吃掉）见 `runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-01/RESULTS.md` §6/§9；**该宿主目前没有可引用的正收益**。
- **残留不确定性**：对照是 replay 证据，不是付费的"先验完整前缀"对照；它排除"压缩扣下第一角色所需的冻结内容"，不排除"视图更短这一事实本身影响模型倾向"。产物：`integrations/crewai/R13_TOOL_LOOP_TERMINATION_CONTROL.md`、`runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-01/R13_TOOL_LOOP_CONTROL.json`。本轮不开 v14、不付费。

- **2026-10-04 OpenAI Agents v10（真实载荷 replay + 工具调用序号新近度：首次成本为正 +27.68 %，但质量 0/3 → 收口 not-yet-valid）**：**(1) 门控改建在已记录的真实载荷上（replay）**：不再自造桩，改用公开确定输入逐边界重建载荷，并对 v9 付费批次的 `input-evidence` 逐边界断言结构一致——`message_items` 3/3/3/3/3/3/3、`item_count` 3/5/7/9/11/13/15、`tool_outputs` 0/1/2/3/4/5/6、工具项**恰好一个连续段**、记录的 `elidable_indices` 全空、结构指纹记入冻结并由审计重算。**(2) 被检验假设**：新近度单位从"连续工具组"改为"**工具调用**"（`elidable(call) := 更新的工具调用数 >= 1`），v9 收窄守卫分类与 v6 缩减/守卫/回退/阈值/预算一律不动（门控 `assertIs` 断言）。**(3) replay 投影（零 API）**：末边界可省略索引 `[3,4,5,6,7,8,9,10,11,12]`（连续工具组口径下为空）、省略 **15 源/632 行**、`restore_failures = 0`、`budget_fallbacks = 0`、字面零回退、载荷字节 63,026 → 38,569 = **+38.8 %** → 判定 **positive**，允许付费。**(4) 付费 9 样本**（`runs/stage5-openai-agents-api/openai-repo-diagnostic-v10-tool-call-recency-boundary`，66 次请求、独立审计 `complete: true`、`issues: []`、9/9）：无压缩 **3/3**（输入 17,003.0 / 完整总 17,189.0）、插件 **0/3**（**12,248.7 / 12,431.0**，省略 45 源）、原生摘要 1/3；**配对完整总 +27.68 %（正 3/3）→ 成本门槛通过**，但**质量门槛不通过**（插件答案缺 `existing_annotations`/`subquery`）→ **not-yet-valid**。**(5) 边界收口**：本宿主"省 token"与"保质量"从未同时成立——不改可见文本则收益恒 0（v7 0.0000 %、v9 −0.000004 %），改可见文本则 v10 +27.68 %/质量 0/3、v6 −176.36 %/质量 0/3、v5 −79.79 %/质量 3/3。**(6) 方法论规则（三宿主适用）**：**零 API 桩不仅能错"轮次数"，还能错"载荷结构"；此后所有离线门控必须建立在已记录的真实载荷上（replay），而不是自造桩**（依据：v9 桩插 assistant 消息 → 虚构 `turn_count=6` → +34.15 % 假正投影；真实载荷 `turn_count ≡ 1` → 实测 −0.000004 %）。边界文档 `integrations/openai_agents/PLUGIN_APPLICABILITY_BOUNDARY.md` 已改为收口版（含未测量项：省略比例×质量的安全分界未做消融、`n ≥ 2` 未测）。门控 `tests.test_openai_agents_tool_call_recency_v10` 9/9 通过、既有 v1-v9 模块 97/97 通过（3 跳过）。按迭代上限不再开 v11。禁止与 CrewAI / OpenHands 的百分比合并。
## 2026-10-04 CrewAI r14 离线上限估算（不付费）与该宿主最终收口

- **先估上限再决定付费**：对"第一角色永不压缩、只压决策者"这条臂，用按角色拆分的 token 估算（估计成本 = 无压缩臂的第一角色 token + 插件臂的决策者 token）。**r13 全批乐观估计 +5.48%**，但该值被 4 个"插件少调工具"样本污染（少一次调用同时省掉两个角色的工作），而这条臂恰恰是为了**不跳过工作**而付出代价。
- **真正的上限**：限制在插件工具序列与基线一致的 12 对后为 **+0.86%**（8/12 对为正，调用次数不一致样本 0）；逐对分解显示**第一角色视图侧只贡献 +0.35 pp**（同调用次数下第一角色输入每调用仅缩减 0.57% × 约 59% 占比），其余来自决策者侧（占无压缩总 token 39.58%）与逐对涨落（−0.62% ~ +2.62%）。r10/r12 的同法估计分别为 +8.45% / +4.79%（同样被"少调工具"样本抬高）。
- **决策：不付费**（预注册阈值 3%）。未新建 r14 批次、冻结、runner 或审计；未修改 r13 及更早的评分与冻结；因此本轮没有 `*_FREEZE_AMENDMENT_*.json`（没有既有冻结被修订）。流程改进自本轮起对后续冻结生效：冻结一经写定不可再改，修订须另建 amendment（改了什么／原因／新旧 SHA256），批次 manifest 记录**冻结文件自身**的 SHA256。
- **该宿主最终收口结论**：**在该宿主上，保住"第一角色视图完整"后节省上限不足 3%（真正上限 +0.86%，可能改变模型行为的第一角色侧上限仅约 +0.35 pp），不值得再付费；该宿主目前没有可引用的正收益。** 链条与根因归属：首轮事实丢失＝v9 已修（16/16、补救 0）；合同占位符回抄＝模型行为×措辞（r13 预注册措辞 6→0）；少调工具＝模型行为（r13 逐边界对照 `same_content_at_decision_boundary`）；host 指令＝已证伪杠杆（r11）；压缩收益空间＝被质量门与"跳过工作"涨落吃掉（本轮 +0.86% 上限）。r9–r13 五批的插件正配对收益全部伴随质量下降，均不得作为有效节省引用。
- 产物：`integrations/crewai/R14_FIRST_ROLE_UNCOMPRESSED_UPPER_BOUND.md`、`integrations/crewai/R14_FIRST_ROLE_UNCOMPRESSED_UPPER_BOUND.json`、`integrations/crewai/R14_FIRST_ROLE_UNCOMPRESSED_CALL_EFFECT.json`、`.tooling/estimate_crewai_r14_upper_bound.py`、`.tooling/estimate_crewai_r14_call_effect.py`；r13 批次 `RESULTS.md` 新增 §10 记录该估算与收口。

- **2026-10-04 OpenAI Agents v11（省略比例 × 质量消融：M\* = 4 省 +5.81 % 但质量 2/3 → 该任务安全分界为空集）**：(1) **唯一变量**：`elidable(call) := 更新的工具调用数 >= M`，预注册阶梯 **M ∈ {6,4,2}**（M=6 最保守），机制/注册表/守卫/回退/阈值/预算一律不动，M 由 `DSH_V11_RECENT_CALLS_KEPT` 选择。(2) **零 API replay 投影**（真实载荷夹具，`fixture_ok = true`）：M=6 → `elidable_indices = []`、0 源、**0.00 %**；**M=4 → `[3,4,5,6]`、3 源/130 行、+8.04 %**；M=2 → `[3,4,5,6,7,8,9,10]`、10 源/401 行、+23.96 %；三者恢复失败 0、字面零回退。按"取正投影中最大 M"选 **M\* = 4**。(3) **付费 9 样本一次**（`runs/stage5-openai-agents-api/openai-repo-diagnostic-v11-elision-ratio-boundary`，68 次请求，独立审计 `complete: true`、`issues: []`、9/9）：无压缩 **3/3**（输入 17,003.0 / 完整总 17,187.0）、插件 **2/3**（**16,004.0 / 16,187.7**、省略 9 源）、原生摘要 2/3（13,210.0 / 15,029.7）；**配对完整总 +5.81 %（正 3/3）→ 成本门槛通过**，**质量门槛不通过** → **not-yet-valid**。(4) **命中预注册第二种结局"质量仍掉"**：{6,4,2} 内不存在"质量 ≥ 基线且配对为正"的设置（M=6 无收益、M=4 省 5.81 % 但掉到 2/3、M=2 更激进），并与同批**原生摘要臂同现象互证**（省 +12.55 %、质量同为 2/3）→ **该任务对"模型能看到什么"敏感，任何改变可见文本的缩减都在此付出质量代价**；结论"安全分界为空集"，且已由阶梯消融证明不是比例没调好。(5) **流程改进**：v11 冻结**写定后未再修改**（构建脚本拒绝覆盖），批次 manifest 记录**冻结文件自身 SHA256** 并由审计重算比对；首次付费尝试因家族辅助引用错误中止（未产生样本行），修复 3 个文件，连同更早 v7/v9/v10 的原地哈希刷新一并记录在 `REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json`（字段/原因/新旧 SHA256）。(6) 门控 `tests.test_openai_agents_elision_ratio_v11` **12/12** 通过；全部 `tests.test_openai_agents_*`（15 个模块）**128/128** 通过（3 跳过）。(7) 边界文档 `integrations/openai_agents/PLUGIN_APPLICABILITY_BOUNDARY.md` 已更新为收口版（含未测量项：{6,4,2} 之外的 M、省略"哪一边"的变体、语义等价质量口径）。(8) **本轮回填的一致性修复**：该边界文档被 v7/v9/v10/v11 四个冻结文件登记为哈希输入，而冻结文件不得再改，故新增 `experiments/audits/amendment_hashes.py`——审计现在只接受两种哈希：仍等于冻结值，或精确等于修订文件登记的当前内容（修订文件缺失则一律不豁免，任何未登记改动仍报 hash mismatch），并把 v11 审计原先按目录类整体豁免 `experiments/`、`tests/` 的写法收紧为按文件登记；v7/v9/v10/v11 四个审计重跑均为 `issues: []`、`complete: true`，付费样本与判定数字未变。禁止与 CrewAI / OpenHands 的百分比合并。

## 三宿主总收口（2026-10-04）

判据照 `HANDOFF_DEEPSEEK_HARNESS_2026-09-29.md` §5：**多任务、多重复、质量不下降、稳定正节省**四条同时成立，才支持「综合插件可行」。

| 宿主 | 是否出现可引用的正收益 | 成本与质量是否同时成立 | 收口依据（不再开新版本） |
|---|---|---|---|
| OpenAI Agents | 无 | **从不**：不改可见文本 → 收益恒 ≈ 0（v7 `0.0000%`、v9 `−0.000004%`）；一改可见文本 → 收益出现但质量掉（v5 `−79.79%`/3-3、v6 `−176.36%`/0-3、v10 `+27.68%`/0-3、v11 M\*=4 `+5.81%`/2-3） | 省略比例阶梯 `{6,4,2}` 内不存在「质量 ≥ 基线 ∧ 配对为正」的设置 → **安全分界为空集** |
| CrewAI | 无 | **否**：保住「第一角色视图完整」后的真实上限 **+0.86%**（第一角色侧仅约 +0.35 pp）< 预注册 3% 阈值 | r14 离线上限估算（**未付费**）：不值得再付费；r9–r13 五批的正配对收益全部伴随质量下降 |
| OpenHands | **成本侧有，质量侧没有**：v61-01 分叉后 `+44.73%`（插件）/ `+25.84%`（原生摘要），全程口径 `+16.36%` / `+9.45%`，插件真实压缩 2 次 | **否**：三臂最终宿主判定**全部 failed** | 该批只能表述为「**同样失败前提下**的成本对比 + 三条分叉都有实测判定」，**不是**质量等价证据；结构性张力（越过 28,000 触发需 ~30–42 次请求 ≈ 模型修对该任务所需次数）已两次确认 |

**总结论：三宿主中没有任何一个满足「多任务、多重复、质量不下降、稳定正节省」四条同时成立，因此「综合插件可行」这一说法目前不被证据支持。** 三宿主的证据指向同一机制性张力：**减少模型可见文本 → 成本降、质量降；不减可见文本 → 质量不变、成本不变**；OpenHands 是唯一在等质量前提下出现成本正收益的宿主，而它的「等质量」目前只等于「三臂同样失败」。

**v61-01 的独立性边界（必须随数字引用）**：该批的 `branch_host_verdicts_measured: true` 与压缩次数 `2` 来自 runner 与逐分叉持久化字节，**独立审计结论记为未取得**（审计器在冻结后被修正过一次，修正在哈希级被复现证明只差那处阶梯顺序检查；`frozen_experiment_sources_valid` 只能表述为「110/111 源零漂移」这一较弱形式）。集成者的独立复算记在
`runs/stage5-openhands/django-multitask-v61-01/INTEGRATOR_SPOT_CHECK_20261004.md`：`branch-artifact-index.json` 的 15 个 SHA256 全部复算一致；另记录一个汇总未展开的硬字段 `workflow_verification_ok` = `true`/`true`/`false` —— 它统计的是**分支内 `scoped_tests` 自测**（`budget_policy_v30.py:34`），不是宿主判定，其含义是 `none` 与 `native_summary` 的自测曾报「通过」而宿主判定为 `failed`。

**本轮统一验证（`.tooling/verify_round.py --run-suites`，该脚本不入库）**：38 个冻结文件逐一核对一致——其中 v7/v9/v10/v11 的 14 处差异全部**精确等于** `REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json` 登记的当前内容，且该修订文件声称「冻结文件自身未被编辑」已用 SHA256 核对为真、缺失修订文件时一律不豁免；三宿主零 API 套件 **118 / 214 / 102 全通过**，含 v6–v11、r10–r13、v59–v61 的本轮门控。

**引用禁令（仍然有效）**：三宿主的百分比禁止合并；CrewAI r10/r11/r12/r13 的审计为 `exit 1` / `not-yet-valid`，其配对均值不得作为有效节省引用；OpenAI Agents v7 的 `plugin_can_elide_anything` / `plugin_wins_possible` 字段与其结论矛盾，不得引用；v7 的离线数字（K=4、14,715）不得与付费数字（K=2、2,962）混用；OpenHands 各版本的节省率不得跨版本或跨任务合并。
## 2026-10-05 CrewAI r15 载荷获取（仅无压缩臂）与三门判定：草案待批，不跑三臂

- **为什么付费**：`R15_CANDIDATE_ZERO_API_GATE_20261005` 的停止原因是新任务**没有真实 CrewAI 宿主载荷**（第 1 门未过），而不是纪律问题——没有载荷就无法算第 2/3 门。因此做了一次**有界、预注册的工具性获取**：冻结 `PRE_RUN_FREEZE_R15_PAYLOAD_ACQUISITION_01.json`（SHA256 `40b5c385…`）与协议 `PILOT_PROTOCOL_R15_PAYLOAD_ACQUISITION_01.md`，**只跑无压缩臂**，批次 `runs/stage5-crewai/crewai-r15-payload-none-01`：12/12 样本（4 任务 × 3 重复）、**84 次请求**（上限 120 未触顶）、0 失败/0 触顶/0 错误、完整总 token 156,502。manifest 内 `purpose=payload_acquisition_only`、`citable_as_saving=false`、`citable_as_quality_equivalence=false`：**本批不得被表述为节省或质量等价证据**。
- **四项新任务（同形质量合同）**：`tasks/stage5_autogen/natural_tasks_r15.json` —— `service_release_gate`(RELEASE/rel-2207/us-east-2)、`schema_migration_gate`(REVERT/schema-88/mig-7702)、`flag_promotion_gate`(PROMOTE/v16/flag-orion)、`cache_promotion_gate`(DISABLE/build-3341/cache-node-44)。每项都是第一角色按固定顺序读 4 份独立当前证据（配置 → 运行指标 → 变更记录 → 校验结果，4 个工具各一次），第二角色作一个可核验的发布/回滚决定；四项均有独立验收规则、固定事实、工具顺序与失败标签（禁用事实）；历史为自然产生的过期讨论（archive 片段），**不重复铺设合成"第 N 次历史交接"**；零 API 探测断言任务 id/工具名/事实/区域/版本/决策词均不与 r3–r13 复用。
- **第 1–3 门（`experiments.commands.crewai_candidate_gate_r15_real_payload`，只读复算）**：**全部通过**。第 1 门真实载荷（mock 在非 `--force-mock` 下被拒）；第 2 门 12/12 单元工具序列 = 冻结工具表、首轮事实齐全、补救 0（四任务各 3 单元）；第 3 门可实现上界全批 **+10.25%**，逐任务 +9.90% / +10.35% / +10.35% / +10.38%（4 个任务非零，≥3 达标）。上界算法：`一次性文本 = 首次调用帧字符 − 后续调用帧增长`（后续增长=压缩替代文本被重复计费，必须扣除）→ 除以实测每 token 字符数 → 减去保护成本（钉住/整份回退/辅助摘要，本批全 0，接入插件臂后即生效）；另报更松的 ceiling 上界 +15.18% 并注明**不是**判定口径。同工作量由第 2 门强制，**少调工具/少调用模型不计作收益**。
- **产出与边界**：三臂小试**预注册草案**见 `integrations/crewai/R15_THREE_ARM_DRAFT_20261005.md`（4×3×3=36 样本、上限 280 及其算术、三条验收线、与 +10.25% 上界的实现率对照义务、停止线与独立审计要求）——**本轮未运行三臂、未写三臂冻结、未发新请求**。冻结纪律照做：本批冻结写定后未修订、无 amendment。r9/r10/r13 的 results/audit/manifest 哈希核对**零漂移**，未覆盖、未重跑、未重打分任何旧批。
## 2026-10-05 CrewAI r15 预筛对齐（分面报告 + 潜在/守卫后分离 + 门命令冻结修订）

- **按 `REAL_PAYLOAD_PRESCREEN_20261005.md` §1/§2 对齐三项要求**（零 API 复算，未重跑已记录载荷）：①门命令现**逐任务分面报告**——历史可删量（assistant 747–755 / user 1,730–1,845 字符，占首帧 54.8–55.7%；与 r4/r9 的 572–646 同量级，但预筛已证该项不能单独预言收益，故只作结构描述）、当前证据与工具轮（4 工具 / 首角色 5 轮 / 证据标记 230–254 字节，逐字保护故永不可删）、角色决策行为（12/12 首轮齐全、补救 0、严格与语义全通过）、任务间离散度（上界 max−min **0.48 pp**、sd 0.20）；②第 3 门判定用**可实现上界 +10.25%**（≥3%，4 任务非零），更松的 ceiling +15.18% 仅作对照并注明非判定口径，**潜在旧单位上界（4）与守卫后安全候选（3）分开报告、不合并**；③**必要性判定：必须付费获取**——R15 门第 1 条要求真实宿主记录，且预筛记录假模型桩曾虚构 6 个工具轮／投影 +34.15% 的实际偏差，故 mock 不得用于上界；执行被限为仅无压缩臂、预注册冻结、84 请求（上限 120）、manifest 写死非节省证据。
- **冻结纪律**：为对齐预筛新口径只改了**门命令**（`crewai_candidate_gate_r15_real_payload.py`，`048cc99b9148…` → `3705ea083da8…`）；runner／任务文件／协议／已记录 `results.jsonl`（`f09fa84e8037db84…`）**逐字节未变**，故按 r15 纪律另建 `R15_PAYLOAD_ACQUISITION_01_GATE_SCOPE_FREEZE_AMENDMENT_20261005.json`（改动内容／原因／新旧 SHA256），**原冻结文件一字未改**，未重跑、未重打分。
- **判定与产出不变**：第 1–3 门仍全部通过，三臂草案 `R15_THREE_ARM_DRAFT_20261005.md` 待批（**本轮仍未运行三臂**）。对齐说明见 `crewai/R15_PAYLOAD_NECESSITY_AND_PRESCREEN_ALIGNMENT_20261005.md`。引用跨批数字时须同时引用 r9 勘误：**r9 自身质量不劣、成本为正**，其限制在集中度（`queue_backlog_replay` +22.2466%）与离散度（22.5023 pp）。
## 2026-10-05 CrewAI r15 三臂小试（四项新任务 × r13 冻结机制）：未通过质量门，收口结论维持

- **批次与冻结**：`runs/stage5-crewai/crewai-two-role-r15-release-gate-3arm-01`（前 35 样本，**恰好触顶 280/280**）+ 续跑 `...-resume-a-01`（**完整 36 样本，正式审计对象**）；冻结 `PRE_RUN_FREEZE_R15_RELEASE_GATE_3ARM_01.json`（self-hash `38cea63f…`，未修订）与 `..._RESUME_A_01.json`（`057a85ae…`），修订记录 `R15_RELEASE_GATE_3ARM_FREEZE_AMENDMENT_20261005.json`（含审计发现的 manifest 记账差与 runner 双哈希修正）。协议分母按批注改为**每臂 12/12**（非 r13 的 16/16），并写入同构性边界与失败计入口径。
- **运行**：4 任务 × 3 重复 × 3 臂 = 36/36 样本；首批 280 次请求 + 续跑 6 次 = **286**（超预注册上限 280，审计如实报错）；插件机制 = **r13 冻结机制逐字复用**（v9 三层 + K=3 全轮保护 + r13 合同措辞），**不加任何指令消息**。
- **判定：未通过质量门（审计 `verdict: not_passed_quality_gate`、`errors` 10、exit 1）**。①行为等价未过：插件 `tool_sequence_consistent` **6/12** vs 基线 11/12，`flag_promotion_gate`/`cache_promotion_gate` 逐任务 0/3；②质量不劣未过：插件严格/语义 **6/12 / 6/12** vs 基线 11/12、11/12；③成本为正通过：全批配对均值 **+37.98%**（11/12 正），同工作量子集 **+35.16%（6/6 正）**。首轮事实齐全插件 **12/12**（> 基线 11/12），失败全在工具序列轴，`contract_placeholder_echo` **0**。
- **逐任务与离散度**：`service_release_gate` +34.43%、`schema_migration_gate` +35.89%（两者各 3/3 同工作量同序列）；`flag_promotion_gate` +49.03%、`cache_promotion_gate` +49.49%（**均无同工作量对**，收益来自少调一个工具，按口径不计作节省）。全批离散度 **16.45 pp**；同工作量子集 **1.46 pp**，但仅两个任务进入且四任务**同形同构**，均匀性**不代表**任务间异质性证据。
- **两条附带事实（不改变判定）**：① 载荷批的机制上界 **+10.247% 被实测超出约 3 倍**（实现率 343%），该上界在本任务形状下**不成立**，今后不得再作成本门；② 保护成本占比 **9.48%**（钉住 20,625 字符 → 8,558 token、受保护轮 90、回退 0、摘要 0）。
- **收口维持**：该宿主仍**没有可引用的正收益**；本批不得与任何宿主百分比合并。引用跨批数字须同时引用 r9 勘误（**r9 自身质量不劣、成本为正 +8.009%**，限制在集中度 +22.2466% 与离散度 22.5023 pp）。详见 `runs/stage5-crewai/crewai-two-role-r15-release-gate-3arm-resume-a-01/RESULTS.md`。

- **2026-10-05 OpenAI Agents v13（留出任务 `psf__requests-1766`：首次通过预注册验收线，但只是单任务确认）**：(1) **任务**：`psf/requests` base_commit `847735553aeda6e6633f2b32e14ba14ba86887a4`，三宿主此前从未引用该 repo；三只读视图 `requests/auth.py` 58-149、`models.py` 451-472、`sessions.py` 232-270，两轮读取共 6 次工具调用；取源经两个独立 GitHub 镜像 HTTPS 完成（**未执行 git**），`auth.py` 两镜像 SHA256 一致，base 失败态由源码确立（`auth.py:147` 发出未加引号的 `qop=auth`；FTP 里的 `test_DIGESTAUTH_QUOTES_QOP_VALUE` 不在 base 树，属评测 test patch 引入）。(2) **一次性冻结** `V13_REQUESTS1766_FREEZE_20261005.json`（SHA256 `e1f5bb3a…3d694`，manifest 记录并由审计重算），Pattern A/字面表/视图行范围/两轮协议/预算/失败线/data_class 齐备；Stage C 前另建修订文件（登记 A1 补注册陈述侧字面 `qop-options` 使守卫保护集合由空变 2、A2 记录 v13 集成模块哈希、A3 说明请求下界算术）。(3) **Stage A 载荷获取**：单臂 `none`、1 任务×1 重复、**7/12 请求**、0 失败，逐边界项数 3/5/7/9/11/13/15、`turn_count≡1`、六个输出为三组逐字重复；该批 manifest 实际写入 `purpose`、`citable_as_saving=false`、`citable_as_quality_equivalence=false`，独立审计 `complete=true`。(4) **零 API replay 门**：重建逐边界复现记录（结构+每个输出 SHA256/字符数）、插件项指针文本逐字节可复算、presence fail-closed 且负控制通过、不变量五列全绿；**投影 +20.71 %（≥3 %）且守卫后安全候选 3（非空）→ 判定 proceed**。(5) **Stage C**：1 任务×3 重复×3 臂、**66/66 请求**、0 失败；**Track A 基线 3/3、插件 3/3**；配对完整总 token **+17.51 %/+17.52 %/+17.49 %（正 3/3，均值 +17.50 %）**；原生摘要臂 −4.07 %（正 0/3，其摘要预算被限为每样本 1 次以把 harness 下界压进 66 上限，故其成本是无界成本的下界）；独立审计 `complete=true`、`errors=[]`、`acceptance.met=true`。(6) **预注册验收线（Track A ≥ 基线 且 配对完整总节省 ≥ 3 %）两条都满足**——本宿主**首次在未参与 v1-v12 调参的任务上通过**。**但不得越读**：1 任务×3 重复不是多任务稳定性；预筛反例（候选数量不预测收益）仍然成立；Track B 通过 ≠ 质量不降且不追溯 v11/v12；`requests-1766` 自此为已用掉的留出任务（不得在其上调参重试，重试须换机制并另选新候选）。旧批冻结/评分/答案未动；跨宿主百分比不合并。
## 2026-10-05 CrewAI r15 分裂诊断与收益上界作废（零 API，不付费）

- **逐任务分裂已机械判定**（`.tooling/diagnose_crewai_r15_tool_loop.py`，只用已记录载荷）：`service_release_gate` 与 `schema_migration_gate` 是**同工作量 3/3 + 严格/语义 3/3 + 配对 +34.43% / +35.89%**；`flag_promotion_gate`（0/3）与 `cache_promotion_gate`（0/2）的 **+49% 来自插件少调用一个工具**（`run_flag_check` / `run_cache_check`），按预注册口径**不计作节省**。同工作量子集 = 6 个单元、**+35.16%（6/6 正）**。
- **6 个失败单元逐边界结论：`mechanism_withheld_required_content = false`**。视图侧（冻结任务 + 冻结 14 组 filler + 记录工具轨 + r13 冻结机制重建）：4 个工具名全在、已执行工具证据逐字可见、`recency_omitted = 0`、`readded = 0`、整份回退 0、工具组恢复失败 0、`HANDOFF` 合同在位；记录侧（真实逐调用模型输入）：决定性调用（第一角色最后一次、索引 3）的系统提示**声明全部 4 个工具、含 JSON schema 块与列出全部工具名的工具选择行**，被跳过的那个工具在其中。→ **模型在内容齐备的前提下提前结束工具循环（模型行为），机制侧无缺陷**；`contract_placeholder_echo` 全程 0（r13 措辞修正继续有效）。
- **收益上界口径作废**：载荷批"可实现上界 **+10.247%**"被同工作量实测 **+35.16%** 超出 **3.43 倍**。实测分解（service_release_gate / schema_migration_gate）：该上界只记账"首帧一次性文本"（1,267 / 1,270 token），而**整段首帧在之后每次调用被重复投递**（1,878 / 1,869 token 每调用，占实测节省 **42.6% / 40.3%**），这一项完全不在其记账范围内，其余差额来自少调工具与逐单元波动。→ **`REAL_PAYLOAD_PRESCREEN_20261005.md` §2 第 3 条的收益上界口径在此之前是错的，不得再用它筛除或放行任何批次**；正确形式须按 `(无压缩每调用输入 − 压缩后每调用输入) × 调用数` 逐调用累计并显式扣除钉住/受保护轮/回退/摘要。
- **本批正确表述**：**批级不通过、不可引用**；结构事实如实写出——4 个同形任务里 **2 个**出现"同工作量 + 同质量 + 约 +35%"，另 **2 个**的 +49% 靠少调工具；边界为 **2 任务 × 3 重复、同构构造、均匀性不代表任务间异质性**，且 `none` 臂有 1 个触顶行。请求账目：首批 **280/280 恰好触顶**（35/36）+ 续跑 6 = **286 > 280**，已在 RESULTS 顶部显著标注；**两个 manifest 不回写**（冻结不可改，修订另建 amendment）。
- 若后续仍要在该形状上试，唯一未被证伪的方向是**不改视图内容、只改运行控制器**（把"必须调用全部证据源"从提示词层提升为宿主层工具轮计数守卫）——本文件**只提议**，不在留出任务上直接调参、不发付费请求。诊断全文 `crewai/R15_SPLIT_DIAGNOSIS_AND_BOUND_ERRATUM_20261005.md`。

- **2026-10-05 OpenAI Agents v14（多任务确认：成本稳、质量在 1/3 任务上掉 → 验收线未通过）**：(1) **付费前修好三处 runner 缺陷**：单臂批次不再在共享 report 步骤抛 `KeyError: paired_n`（运行时补丁，冻结哈希的共享模块未改）、manifest 由 runner **一次写全**（`purpose` + `citable_as_saving=false` + `citable_as_quality_equivalence=false` + 冻结哈希，**无任何事后修订工具**）、插件行现在直接持久化 `exact_duplicate_replacements` / `trigger_gate_*` / `narrow_guard_protected_unit_count`。(2) **四个全新留出实例**：`psf__requests-5414`、`psf__requests-2931`、`pallets__flask-5014`、`pydata__xarray-3095`——三宿主此前在 integrations/experiments/tests/tasks 中**零命中**、无已提取目录；只读取源（`fetch --depth 1` + `checkout FETCH_HEAD`，未 add/commit/push、未写上游），逐文件登记 SHA256。(3) **逐任务有界获取**：各 7/12 请求、0 失败；零 API replay 门四项全过（重建复现、指针可复算、presence fail-closed + 负控制、不变量五列），**逐调用投影 15.59/14.04/17.62/18.96 %、安全候选各 3**；按预注册准入规则 3 个合格、1 个被拒（其基线答案事实正确但未含冻结令牌 `prepare_url`——**不因看到答案而改契约**）。(4) **小试 3 任务 × 3 重复 × 3 臂 = 27 样本、207/300 请求、每样本摘要上限 3 次（预注册）**，0 触顶：插件逐任务均值 **+8.09 % / +12.98 % / +16.87 %（正 9/9，极差 8.77 个百分点）**、池化 **+12.65 %**；原生摘要臂 −9.13 %（正 0/9）；Track A 基线 9/9、插件 **7/9**（`xarray_copy_dtype` 1/3）。(5) **验收线：Track A 插件 ≥ 基线（逐任务与合计）且 配对 ≥3 % → 成本侧满足、质量侧不满足 → `acceptance.met=false`，多任务确认未达成**。两处失败**只违反 ≤160 字符长度条件**（179 字符），必需字面齐备、presence 台账无丢失、指针逐字节可复算 → **模型侧措辞，机制侧干净**（与 v12 同现象）。(6) 独立审计：四个获取批与 27 样本小试批均 `complete=true`、`errors=[]`，两轨质量按冻结合同自行计算，逐样本边界/指针/presence/配对/回退重算，验收判定独立重算为 false。(7) **如实局限**：3 任务 × 3 重复不足以称多任务稳定；这三个任务的守卫保护集合为空（字面在工具视图而非陈述单元），字面保全由机制不变量与 presence 台账证明；四个实例自本轮起**已用**，不得在其上调参重试。旧批冻结/评分未动；跨宿主百分比不合并。

## 2026-10-06 OpenHands v64 审计器冻结输入 + v62 只读复盘（零 API，不付费）

- **本轮为零 API**：未发出任何模型请求、未付费、未改动任何旧批字节、未执行 Git add/commit。对应
  `THREE_HOST_NEXT_ROUND_PLAN_20261006.md` §2.B / §4.A / §4.B / §4.C。
- **v62 只读复盘**（`integrations/openhands/V62_READONLY_POSTMORTEM_20261006.md`，复现脚本
  `.tooling/v62_postmortem.py`，记录 `.tooling/tmp/v62-postmortem/branches.json`）：三条臂都只改
  `django/db/models/fields/related_descriptors.py`，最终 SHA256 分别 `8e9702cb…`（none，8 个变更块）/
  `069d91e5…`（native_summary，10 个）/`2c62717a…`（pruner_v1，3 个）。**关键事实：三条臂没有任何一条
  收到过自己最新一次编辑之后的宿主反馈**——`none` 只在 burst 1 收到前缀反馈（随后 burst 以
  `Budget policy blocked an unexpected tool` 终止，30 次窗口只用了 **19** 次、**剩 11 次作废**）；
  `native_summary` 在同一 burst 内用光 **30/30**，第二轮 `send_message` 直接撞
  `Frozen correction request limit`，**第 1 轮反馈从未进入上下文**；`pruner_v1` 第 1 轮反馈在 burst 2
  开场送达（25+4=**29/30**），第 2 轮反馈因循环上限未送达。最终宿主判定：`none` `FAILED (failures=1)`
  （对公开基线 112/113，**唯一没有引入新破坏的臂**）、`pruner_v1` `FAILED (failures=8)`、
  `native_summary` `FAILED (failures=1, errors=19)`（多出 **20** 个失败/错误）。**隔离规则机器可读**：
  `host_target_patch_read: false`、`hidden_test_patch_read: false`、`reference_fix_read: false`、
  `scoring_target_patch_reachable_from_agent: false`；复盘用的 Agent 可见文本由 runner 当时**同一个**
  `feedback_v42.format_correction_feedback` 在**同一批**宿主日志上重生成，故不引入新信息。
- **审计器作为冻结输入**：预冻结门抽成可执行模块 `integrations/openhands/audit_gates_v63.py`
  （`audit_validation_v63.py` 改为调用它；`branch_arithmetic_checks` 保留为兼容包装）。四类拒绝各有名字
  —— `missing_field` / `path_escape` / `hash_drift` / `target_test_inconsistency`（另有 `provenance` /
  `cap_arithmetic`），异常 `AuditGateError`（`AssertionError` 子类，带 `gate` 与 `detail`）。
  **批次 manifest 必须登记审计器自身 SHA256**：缺登记 → `missing_field`；登记了别的修订 → `hash_drift`；
  `audit.json` 新增 `auditor_provenance` 块。这使得"付费后发现审计器有问题、修好它再声称旧批通过"
  在机制上不可能。**本轮冻结的审计器 SHA256：
  `d8d5eaca1a630951268d90742da97a0ecb9ec4ebcad3bfc8a87a4487b65bb802`**
  （`integrations/openhands/audit_validation_v63.py`；下一批 manifest 必须写下这个值）。该审计器接受的
  批次版本集合由 `audit_gates_v63.manifest_gate` 统一决定（v54 分叉形状、v55–v68 prefix-selection 家族，
  含 v59 的 `-b04` 后缀变体；v69 及以后按 `provenance` 拒绝，必须显式扩展）。
- **冻结字节上的端到端烟测**：`integrations/openhands/V64_AUDIT_FROZEN_INPUT_SMOKE_20261006.json`
  （`.tooling/v64_audit_frozen_input.py --write`）。在由 v62-01 真实冻结字节物化的**合成但完整的三分支
  记录**上（快照 6,644 文件、分支账本、逐分支持久化字节、批次索引全部为真实字节）跑完整审计：
  `complete: true`、3/3 分支、`frozen_experiment_sources_valid: true`、`changed_sources: []`、
  `auditor_provenance.is_the_frozen_revision: true`、`branch_host_verdicts_measured: true`、
  `plugin_compaction_count: 3`、三臂判定均 `failed` 且为实测、`file_boundary_violations: 0`、
  `shared_ceiling_respected: true`、`prefix_cap_arithmetic_holds: true`、逐分支 `final_source_digest`
  与 `artifacts.json` 记录**逐位一致**（`0d2910fa…` / `5dbb901a…` / `7eca1a65…`）。9 项负控制全部按预期
  结果结束（8 项按名字拒绝，1 项为刻意的**非**拒绝：无宿主轮的臂必须记成 `final_host_verdict: null`
  而不是判为失败）。该烟测**不跑 Django**，并在 `audit.json` 与产物里显式记录 `host_test_rerun: false`
  与 `host_verdict_source: NOT re-run …`；其余每一步都在真实冻结字节上执行，**付费前完成**。
- **门控测试**：新增 `tests/test_openhands_v64_audit_frozen_input.py`（8 项全过），覆盖 manifest 审计器
  哈希登记 / 缺登记拒绝 / 登记别的修订按 drift 拒绝、三分支额度算术路径、三分支账本门、四类负控制、
  篡改持久化字节必须被拒、冻结字节端到端、同一份字节两次审计结果一致。既有
  `tests/test_openhands_v63_audit_gate.py` 同步更新（缺字段现在按 `missing_field` 具名拒绝）。
- **环境修复（必要一步）**：`%TEMP%` 下 `pytest-of-LENOVO` 属于另一个 Windows 账户，pytest 旋转临时目录时
  `PermissionError [WinError 5]`，导致**所有**使用 `tmp_path` 的门控无法启动。新增仓库根 `conftest.py`
  把 base temp 固定在 `.tooling/tmp/pytest-basetemp`，无需机器级 ACL 改动。此修复对所有宿主的门控通用。
- **质量优先的小试规则已写进协议**：`THREE_HOST_NEXT_ROUND_PLAN_20261006.md` §4.C —— 只改一个可归因变量
  （候选变量：宿主失败反馈的传递时机 + 纠错预算按轮预留）、新 ID / 同前缀三臂 / 新冻结、三臂都必须取得
  宿主判定、插件真实压缩、独立审计无错、且**至少看到成功路径的插件质量不低于基线**；三臂再次全 failed
  则**立即停在开发观察**，不扩重复、不扩任务、不把更长轨迹解释为质量改善；旧 v62 不重判，v62 同任务
  只能作**开发对照**，不得再称"未用留出任务"。
