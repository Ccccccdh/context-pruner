# OpenAI Agents v2 输入证据机制试验草案

目的：补齐 v1 开发批没有保存压缩后模型输入的审计盲点，判断 Django 引用条件和 pylint 正则边界在**真实送往模型的输入**中是否存在。v1 冻结运行器、审计和结果原样保留。本文件记录开发过程；v2 付费批已在新的冻结协议下完成，结论见 `runs/stage5-openai-agents-api/openai-repo-diagnostic-v2-evidence-dev-01/RESULTS.md`。

## 证据结构

新增 `experiments/runners/openai_agents_input_evidence_v2.py`：

- `EvidenceCaptureFilter` 包裹原 `BudgetTriggeredFilter`，在过滤前和过滤后分别采样；它把底层过滤器的同一个返回对象原样交还 SDK。
- `EvidenceModelProxy` 在调用内层模型前采样 `input` 与 `system_instructions`，再将原参数原样转发。这样能覆盖 SDK 未调用过滤器的首轮，也能以哈希核对过滤器输出与最终模型输入。
- 每条 `snapshot` 仅保存阶段、序号、整体 SHA256、条目数/字节数、公开任务约束的布尔命中、保护组 ID/哈希/成员数/工具输出哈希。约束命中分为全部输入、未保护普通消息、受保护组。它**不持久化原文、工具输出、instructions、请求头或环境变量**。SHA256 不能揭示模型如何理解证据；布尔值是逐字匹配，不能替代语义判断。
- 逐调用证据写进新批次目录中的 `input-evidence/<task>-<repeat>-<arm>.jsonl`；路径由新 runner 控制，不由模型提供。任务/臂/重复与样本账本一一对应。

## 零 API 非干预门控

`tests.test_openai_agents_input_evidence_v2` 当前 **6/6 通过**：真实 SDK `ModelInputData` 上，带采集器与不带采集器的压缩结果 `input` 和 `instructions` 完全相同；完整 SDK `Runner` 的真实工具调用轨迹中，带采集与不带采集的模型输入逐调用相同，过滤后哈希匹配模型边界哈希；同步过滤器返回对象 identity 保持；模型代理收到的 provider 参数与响应保持同一对象；输出 JSONL 不含模拟敏感词、源码文本或指令文本；缺失引用条件可由布尔字段发现。测试还验证保护组及其工具输出哈希在过滤前后相同。

采集层的非干预测试之外，新的 v2 runner 已用真实 SDK、假模型跑通 3 任务×3 臂的逐样本接线。**付费前必须**再走完整 3 任务×3 重复×3 臂的零 API 栅格，并由独立审计核对：

1. `model_input` 记录数 = 每样本模型调用数；`filter_before`/`filter_after` 成对，且各对保护组哈希一致。
2. 对同一次调用，若存在过滤后记录，其输入哈希与实际 `model_input` 哈希一致；无过滤的首轮单独计数。
3. 本地证据文件中不存在原始问题、源码、模拟密钥标记；所有公开输入/采集代码/协议哈希在运行前冻结。
4. 三臂任务、预算、输出合同和质量门控在新 manifest 明示；所有失败样本计入质量与成本。

## 预注册的解释规则

- Django：若插件最终 `model_input` 的 `filter_references`、`other_annotation_references` 或 `ordering_references` 由 true 变 false，而基线仍 true，可定位为字面约束丢失；若保留而答案仍错，说明问题可能在关注点/推理，需要结合输入与模型输出分析。
- pylint：先比对三臂首次 `model_input` 哈希与工具调用轨迹，再判断失败是否发生在压缩之后；首次已分叉时不得归因于后续压缩。原生摘要若 `summary_not_smaller`，不能称其摘要实际改变了输入。
- 若某臂根本未触发压缩，该配对只报告质量/宿主可行性，不记为压缩收益证据。严格答案格式门控与语义反例应分别报告，不能事后改写旧冻结质量。

新增 `run_openai_agents_repo_diagnostic_v2.py`，仅在新入口运行期间包裹共享 runner 的过滤器与模型边界，不改 v1 冻结代码；每样本保存证据文件并在样本行记录证据调用数。`tests.test_openai_agents_repo_diagnostic_v2` 已用真实 SDK 和假模型跑 3 任务×3 重复×3 臂零 API 栅格，27 个样本的模型调用数与证据记录数一致，并用另一假模型覆盖真实 provider 模型工厂路径。独立审计已在付费批核对 27 份证据、冻结源码、用量和质量。

下一步：根据已审计的 Django 输入丢失证据，在新版本前瞻性保护当前问题陈述和任务合同，先做零 API 反例门控，再用新冻结批次检验质量与成本。旧结果不改写。
