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
