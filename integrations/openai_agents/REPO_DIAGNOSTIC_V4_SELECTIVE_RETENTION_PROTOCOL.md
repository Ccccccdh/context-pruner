# OpenAI Agents v4：Django 选择性保留机制小试

## 目的与背景

v2 的独立证据表明：Django 插件臂三次都在第二次模型调用前把公开问题陈述里的字面约束（`filter`、`other annotations`、`ordering`）从最终模型输入中删除，插件严格质量 0/3，基线 3/3。v3 用「逐字锚定最初两条 user 消息」修好了这个字面丢失，但代价是把插件**唯一**在压缩的东西（任务文本）完整保留，前两对样本的输入与完整总 token 与基线完全相同，第三对反而更长，插件配对完整总 token **−33.79%**（正 0/3）。v3 的结果行还**没有单列锚点恢复失败数**，所以事后无法排除某次触发了安全回退。

v4 的目标是同时满足三件事：字面约束必须在最终 `model_input` 中、受保护 Responses 工具组哈希不变、并且仍然产生**实际测得的输入缩减**。

## 机制（`experiments/runners/openai_agents_selective_retention_v4.py`）

只压缩**可证冗余**的文本，不做摘要、不改写、不用模型、无随机性：

1. **受保护任务单元**：注册的任务陈述文本（公开问题陈述 + 固定两条 user 消息）按行切成单元，每个单元保存规范化文本的 SHA256。过滤后所有单元必须仍出现在送往供应商的载荷里；任一缺失即触发**整份回退**（该次模型调用返回观测到的原始输入），并计入 `task_anchor_restore_failures`。
2. **约束单元**：命中已注册字面条件（Django：`filter` / `other annotations` / `ordering` / `existing_annotations`）的任务单元按同样规则保护。结果行分别记录 `selective_retention_task_unit_count` 与 `selective_retention_constraint_unit_count`。
3. **受保护 Responses 工具组**：`function_call` / `function_call_output` 分组不删除、不重排、不跨组切分；过滤前后用组指纹（`type`/`call_id`/`name`/`arguments`/`role`/`id`）比对，必须完全一致。工具组指纹**不含**输出文本，输出文本另由证据层单独记录 SHA256。
4. **缩减来源一：完全重复单元**。同一载荷中后出现的完全重复行被删除，第一个（规范）副本必定保留。
5. **缩减来源二：已经逐字送达过的工具输出文本**。某 `*_output` 文本若已在本次运行**更早的模型调用中逐字发送过**，则被替换为确定性的短注释（保留 item、`call_id` 与 call/output 配对）。模型在工具执行那一轮已经看到完整内容，宿主不再为逐轮重传同一段公开源码付费。第一次送达必定是全文。
6. 注释只在严格短于被替换文本时才生成；若该次调用过滤后字节数没有严格变小，或任一受保护项发生变化，则整份回退并计数。

**恢复失败的可核对语义**：`task_anchor_restore_failures > 0` 表示该次模型调用**整份回退**（`task_restore_fallbacks` 同步 +1，`restore_failure_is_whole_prefix_fallback: true`），绝不会发送部分损坏的任务陈述，也绝不声称该次压缩成功。结果行同时持久化 `restore_failures_by_model_call` 逐次序列，证据文件每次 `model_input` 记录都带该计数，独立审计逐项核对。

## 任务、冻结矩阵与预算

- 任务：`django_count_annotations`（公开 SWE-bench 问题 `django__django-16263` 与固定公开基线源码范围）。发送内容仅公开问题陈述、公开基线源码片段、SDK 工具结果与确定性注释；不发送参考修复、宿主测试补丁、密钥或本地私有文件。
- 矩阵：1 任务 × 3 重复 × 3 臂（`none` / `pruner_v1` / `native_summary`）= **9 样本**。
- 模型：DeepSeek `deepseek-v4-flash`，endpoint `https://api.deepseek.com`，temperature 0，隐藏 thinking 关闭。
- 预算（provider token，`experiments/runners/token_policy.py` 校准比 0.368）：soft/target/hard = **2000 / 1500 / 6000**，即估算单位 5435 / 4076 / 16304；单次输出上限 1024。
- 全局请求尝试上限 **100**（`--max-api-requests`），计划内最少 36 次模型请求 + 48 次摘要预留。三臂按 `(repeat + scenario_index) mod 3` 轮换顺序；逐样本落盘（`samples.jsonl` 追加 + fsync），支持 `--resume`。
- 三臂任务、公开输入、质量合同、答案正则与工具顺序与 v1/v2/v3 完全一致；v4 只改变插件臂的输入过滤机制。

## 判定口径

- 主质量：冻结严格答案合同（前缀 `RESULT `、单行、≤160 字符、答案正则与必要词、工具集合与顺序、`constraint_preserved` / `pairing_integrity` / `structure_safe`）。
- 同时报告：每样本最终 `model_input` 是否保留 `filter`、`other annotations`、`ordering`；受保护工具组是否变化；`task_anchor_restore_failures` 与逐次序列；实际输入 token 配对节省；**完整总 token**（含每一次摘要辅助请求与失败尝试）配对节省与正收益对数。
- 只有插件质量不低于基线且完整总 token 稳定正向时，才可称本批为有效节省。单任务 3 重复不外推多任务，更不外推真实软件修复。基线配对节省为 0 的样本照实报告。

## 前置零 API 门控

`tests.test_openai_agents_selective_retention_v4`：

1. 用真实 SDK + 本地假模型跑 v4 runner 自身入口的完整 Django 栅格（3 重复 × 3 臂）**无网络**；
2. 在真实公开任务载荷上直接断言：最终 `model_input` 保留三条字面约束、受保护任务单元逐字存在、工具组哈希前后一致、且发生真实输入缩减；
3. 注入受保护单元缺失，断言 `task_anchor_restore_failures` 被计数、整份回退、且不声称压缩（含一次通过真实 SDK 边界的子进程端到端注入）；
4. 证据层逐样本核对：模型调用数、过滤前后配对、工具组边界、`input_sha256` 出现在模型边界、无密钥样文本。

独立审计 `experiments/audits/audit_openai_agents_repo_diagnostic_v4.py` 只读冻结哈希与持久化行，重新计算质量、用量、请求账本、配对均值、工具边界与恢复失败计数。

## 运行

```powershell
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v4 --plan --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v4-selective-retention-pilot-01
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v4 --confirm-send-public-source --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v4-selective-retention-pilot-01
& .\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_repo_diagnostic_v4 runs/stage5-openai-agents-api/openai-repo-diagnostic-v4-selective-retention-pilot-01 --freeze integrations/openai_agents/REPO_DIAGNOSTIC_V4_SELECTIVE_RETENTION_FREEZE.json
```

## 边界

- 本批是机制开发小试：同一 Django 任务在 v1–v3 已参与调参，**不是**未参与调参的盲测确认批；单任务 3 重复不外推。
- 压缩只作用于「已逐字送达过的工具输出文本」与完全重复行；它不评价语义等价，也不保证删除后模型一定不会重新请求工具。
- 证据只有哈希、长度、布尔与计数，不保存原文；不能据此证明模型的因果或理解。
- 独立审计通过只说明冻结哈希、账本、质量口径与计数一致，不等于质量等价或成本有效的结论。
