# OpenAI Agents v5：证据安全的输入缩减（Django 小试）

## 目的与背景

v4 用「这段文本是否已在本次运行中逐字送达过」作为唯一压缩资格判据。Django 任务里 `existing_annotations` **只出现在** `read_django_aggregation` 这一个源码工具输出中，于是插件臂把它替换成注释后，最终 `model_input` 的字面命中由 True 变 False，插件严格质量 **0/3**（基线 3/3），配对完整总 token 虽是 **+7.21%**，但那不是有效节省 —— 信息被移出了模型可见范围。

v5 把资格判据换成「**先查已注册字面约束集**」，并同时补上 v4 结果里自己列出的其余缺陷。目标是四件事同时成立：

1. 承载已注册字面约束的工具输出**逐字固定**，任何一次调用都不允许被注释替换；
2. 被省去的文本必须能通过**文件路径 + 行范围**指针重新取回；
3. **最近一次工具组全文保留**；
4. 仍然产生**真实测得的输入缩减**（不能退化成 v3 的「全部保留」）。

## 机制（`experiments/runners/openai_agents_evidence_safe_retention_v5.py`）

注册表 `experiments/runners/openai_agents_literal_registry_v5.py` 在任何运行前固定两件事，并且**用源码文本自校验**（`verify()`：路径、行范围、归属字面必须都对得上，否则该任务不得运行）：

- 每个公开任务的**字面条件**（Django：`filter` / `other annotations` / `ordering` / `existing_annotations`）；
- 每个受注册源码范围的**仓库路径与行范围**（`read_django_aggregation` = `django-16263/django/db/models/sql/query.py:438-527` 等）。

过滤资格按**固定顺序**判定，顺序本身就是修复：

1. 该工具输出文本是否命中已注册字面 → **逐字固定**（`pinned_by_reason=registered_literal`），无论它已被送达过多少次。这正是 v4 缺的那一问。
2. 该输出是否属于**最近一次工具组**（最后一个 `function_call`/`function_call_output` 对）→ 逐字保留。
3. 是否小于最小可压缩长度 → 保留。
4. 该文本是否已在本次运行**逐字送达过** → 否则必须至少完整送达一次。
5. 能否解析出**注册的路径 + 行范围**指针 → 否则不省，因为注释无法让模型取回原文。
6. 只有以上全部通过，才替换为确定性注释，注释里写明 `repo/path`、`lines first-last`、字符数、行数，并提示重新调用该工具即可取回。

**任务陈述单元**：任务陈述中命中已注册字面（`filter operations` / `other annotations` / `ordering`）的单元额外注册为 `literal_units`，按 SHA256 逐字保护，永不被去重规则删除。

**逐次不变式（每次模型调用都跑）**：

- 受保护单元 / 字面单元必须全部存在；
- **字面生存守卫**：每个已注册字面在过滤后载荷中的出现次数不得少于观测输入，任一净损失即整份回退；
- 受保护 Responses 工具组（`type`/`call_id`/`name`/`arguments`/`role`/`id` 指纹）必须完全一致；
- 过滤后字节数必须严格小于观测输入；
- 过滤后字节数不得超过硬字节上限。上限由冻结的 provider 硬预算经宿主标定比换算而来：`provider_hard ÷ 0.368 × 4 bytes/estimated token` = `6000 ÷ 0.368 × 4` = **65,216 字节**（`BYTES_PER_ESTIMATED_TOKEN = 4`，与预算一同冻结）。

**回退语义**：受保护项缺失、字面净损失、工具组指纹变化、结构性不一致 → `task_anchor_restore_failures` 与 `task_restore_fallbacks` 各 +1，该次调用**整份回退**；硬字节上限或「过滤后没有严格变小」 → `budget_fallbacks` +1，同样整份回退。任何情况下都**不会静默丢掉承载约束的单元**，也不会声称该次压缩成功。

## 任务、冻结矩阵与预算

- 任务：`django_count_annotations`（公开 SWE-bench 问题 `django__django-16263` 与固定公开基线源码范围）。发送内容仅公开问题陈述、公开基线源码片段、SDK 工具结果与确定性注释；不发送参考修复、宿主测试补丁、密钥或本地私有文件。
- 矩阵：1 任务 × 3 重复 × 3 臂（`none` / `pruner_v1` / `native_summary`）= **9 样本**。
- 模型：DeepSeek `deepseek-v4-flash`，endpoint `https://api.deepseek.com`，temperature 0，隐藏 thinking 关闭。
- 预算（provider token，标定比 0.368）：soft/target/hard = **2000 / 1500 / 6000**，即估算单位 5435 / 4076 / 16304；单次输出上限 1024。
- 过滤硬字节上限 **65,216**（见上）。全局请求尝试上限 **100**。三臂按 `(repeat + scenario_index) mod 3` 轮换；逐样本落盘，支持 `--resume`。
- 三臂任务、公开输入、质量合同、答案正则与工具顺序与 v1/v2/v3/v4 完全一致；v5 只改变插件臂的压缩资格判据。

## 判定口径

- 主质量：与 v1–v4 相同的冻结严格答案合同（前缀 `RESULT `、单行、≤160 字符、答案正则与必要词、工具集合与顺序、`constraint_preserved` / `pairing_integrity` / `structure_safe`）。
- 约束保留：逐样本、逐模型边界核对四个已注册字面的布尔与出现次数；`existing_annotations`（只存在于工具输出）必须与非插件臂一致。
- 资格判据：逐样本、逐边界的 `output_manifest` 记录每个输出的 `call_id`、文本 SHA256、长度、命中字面、是否固定及原因、是否被注释替换、指针是否可解析；审计据此断言「承载字面的输出从未被注释替换」「被省去的输出都有路径+行范围指针」「最近一次工具组全文保留」。
- 成本：实际输入 token 与**完整总 token**（含每一次摘要辅助请求与失败尝试）配对节省，全部样本、全部失败照实报告。
- 预注册验收线：**插件严格质量 ≥ 基线 且 完整总 token 配对均值为正**，两者同时成立才可称本批为有效节省；单任务 3 重复不外推多任务，更不外推真实软件修复。

## 前置零 API 门控

`tests.test_openai_agents_evidence_safe_retention_v5`（真实 SDK + 本地假模型，无网络）：

1. 跑 v5 runner 自身入口的完整 Django 栅格（3 重复 × 3 臂）；用**与冻结轨迹一致的顺序工具调用**假模型（每次一个工具调用），栅格必须在无 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 的进程内跑完；
2. 逐样本断言四个字面（含 `existing_annotations`）出现在**每一次**与**最后一次** `model_input`，且跨过滤边界出现次数不下降；
3. 断言受保护工具组 id / 输出配对 / 组哈希在过滤前后一致，且唯一允许变化的工具输出文本就是本模块的注释；
4. 断言 `task_anchor_restore_failures == 0`（快乐路径）、`task_anchor_restore_failures` 计数为 0 且 `budget_fallbacks` 为 0，并在强制字面丢失时被计数、整份回退、且不声称压缩（含一次真实 SDK 边界的子进程端到端注入）；
5. 断言插件臂产生真实输入缩减，且被省去的输出条数等于带指针注释条数；
6. 审计器自检：把 `aggregation_decision` 从最终边界抹掉、把承载字面的输出标成被注释替换、去掉指针、把最近工具组标成未全文保留、篡改质量或恢复失败数，都必须被独立审计判为不通过。

独立审计 `experiments/audits/audit_openai_agents_repo_diagnostic_v5.py` 只读冻结哈希与持久化行，独立重算注册表指纹（并用公开源码范围重新自校验注册表）、质量、用量、请求账本、配对均值、工具边界、逐字面保留与恢复失败计数。

## 运行

```powershell
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v5 --plan --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v5-evidence-safe-retention-pilot-01
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v5 --confirm-send-public-source --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v5-evidence-safe-retention-pilot-01
& .\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_repo_diagnostic_v5 runs/stage5-openai-agents-api/openai-repo-diagnostic-v5-evidence-safe-retention-pilot-01 --freeze integrations/openai_agents/REPO_DIAGNOSTIC_V5_EVIDENCE_SAFE_RETENTION_FREEZE.json
```

## 边界

- 本批是机制开发小试：同一 Django 任务在 v1–v4 已参与调参，**不是**未参与调参的盲测确认批；单任务 3 重复不外推，也禁止与 CrewAI / OpenHands 的百分比合并。
- 「逐字固定」只保护**已注册**的字面；注册表之外的同义表述不在保护范围，本机制不评价语义等价。
- 注释只给结构性指针（路径、行范围、长度），不保留原文；模型是否愿意重新调用工具取回原文不由本机制保证，也不在本批测量。
- 证据只有哈希、长度、布尔与计数，不保存原文；审计通过只说明冻结哈希、账本、资格判据、质量口径与计数一致，不等于质量等价或成本有效的结论。
- v1–v4 源码、协议、冻结与结果目录保持不变；v5 使用新文件、新协议、新冻结与新批次 ID。
