# Agents 三项推进结果（2026-10-14）

本轮完成证据勘误、同检查点输入对照和真实 Agents SDK 的受控生命周期工程闭环。新增 opt-in 文件；旧 runner/filter、历史批次、README及CrewAI文件没有被改写。没有自动Git提交。提供商请求0。

## 1. 证据与口径

独立勘误：`integrations/openai_agents/AGENTS_EVIDENCE_AMENDMENT_20261014.md`。

重算45样本、112请求，原provider与验收主表一致；k1实际变换1/15、k0为5/15。披露旧预注册samples=60但矩阵实际45；区分自定义DeepSeek修复loop与真正SDK Runner；收窄“压缩导致多探索”和“安全上限使收益不可能”等解释。原结论不覆盖，新增记录独立引用。

## 2. 同检查点比较

从实际已经交付的读取区间及base_commit源码重建78个历史检查点，78/78原before_digest相同，15个历史调用发生变换。每个H生成none/k1/k0及两个guarded候选；guarded在本地未严格缩短时保留原文。

全部78个相同H上，k1有6次输入增大、k0有5次；guarded各为0。它们的本地累计减少647及2,821 token。这是机制输入对照，不是新模型实验，不是provider累计节省。没有依据该数字宣称模型行为、补丁质量或货币成本改善。

完整原始及派生输入、逐检查点索引和SHA在 `runs/agents-evidence-lifecycle-v1-20261014/checkpoint-replay/`。源JSON与旧过滤器哈希保持不变。

## 3. 真实SDK生命周期接入

新增 `context_pruner/adapters/openai_agents_lifecycle_v1.py`，直接使用已安装的openai-agents0.22.3。

运行路径：受保护的adapter.run_sync → SDK Runner → RunHooks.on_tool_end →可信来源回执 → RunConfig.call_model_input_filter →原子组投影→Model.get_response。不是手写循环假冒SDK。

支持：

- 可信阶段完成后，在容量阈值之前归档旧证据；不可压缩用户/系统历史、最近工具组、pin组完整保留。
- 复用核心InMemoryArchiveStore/SQLiteArchiveStore及ContextItem来源类型；每个证据组绑定task namespace、path、version、entity、调用/返回回执和内容SHA。新增宿主策略独立管理生命周期，不声称复用了通用核心全部策略。
- 可信missing_evidence后，仅选择当前版本、具有完整来源与工具配对的原始组，放回模型输入的原位置。先版本资格过滤再应用数量上限。
- TTL按输入过滤调用时钟：恢复边界c可见，expires_at=c+ttl时再归档；重复信号不续租，实体恢复尝试有上限。
- 保存和恢复可信本地checkpoint；Memory与SQLite关闭重开测试覆盖来源目录、历史digest、状态和原文恢复。
- 对修改/删除权威历史、重复调用ID、来源损坏、错误版本和工具回执不一致拒绝；未知工具协议或混合未知批次保守保留。
- 最终输入本地计量包括完整JSON、instructions、工具schema预留；超硬限时抛异常，不提交违规输入。预留量必须为非负整数。该计数不是提供商精确tokenizer。

## 4. SDK受控对照结果

最终版本证据：`runs/agents-evidence-lifecycle-v1-20261014/sdk-final/summary.json`。

| 臂 | 策略 | 内存后续文件验收 | SQLite后续文件验收 | 归档原文恢复送模 | TTL后原文移除 |
|---|---|---|---|---|---|
| A0 | 完整历史 | 通过 | 通过 | 不需要 | 不适用 |
| A1 | 阶段归档，关闭恢复 | 失败 | 失败 | 没有 | 不适用 |
| A2 | 同策略，开启恢复 | 通过 | 通过 | 两后端均完成 | 两后端均完成 |

每组7个本地Model调用、6个真实SDK工具调用，六组共42/36。所有实际模型输入中的工具调用/返回配对通过。独立验收读取宿主源文件与模型提交的receipt.json比较，不用工具的success字段替代验收。

模型是固定本地Model：后续route参数仅从实际送入Model的evidence输出读取，不访问归档、来源文件、适配器内部状态或答案。阶段与工具调度固定，缺失信号由可信工具发出；这用于证明A1/A2通路差异，不证明自然模型能自主发现信息缺失、完成真实代码修复或产生净收益。

`sdk-controlled/`为审查前的开发快照，保留但不替代上述最终源码结果。失败样本也保存failure.json、模型输入与工具/生命周期事件，输出目录禁止覆盖。

## 5. 审查与验证

独立只读审查复现5项Important，无Critical：活动历史完整性、checkpoint/归档来源绑定、未知未完成工具组、SDK远端历史参数检查位置、负数预留绕预算。执行者分别先建立RED复现，再修复并GREEN。另补混合未知工具组及失败留档回归。

最终相关回归68项通过：新证据/适配器/SDK测试26项及既有SDK、middleware、lifecycle、plugin、stage1/archive相关测试42项。完整记录在 `runs/agents-evidence-lifecycle-v1-20261014/final-regression.log`。本轮没有运行全仓库全部历史宿主套件，不声称全仓库全绿。

没有做第二次独立审查；修复由复现测试和最后组合回归验收。独立审查原发现与逐项处理在进度ledger保留。旧文件16项哈希保护结果与最终源码清单见同目录final_gate.json。

## 6. 接入与复现

SDK实例先按任务创建适配器；工具只允许返回宿主构造的JSON字符串信封，来源元数据由宿主计算，原始文件正文放在text字段，不能直接把外部JSON当控制信号。

```python
from context_pruner.adapters.openai_agents_lifecycle_v1 import OpenAIAgentsEvidenceLifecycle

adapter = OpenAIAgentsEvidenceLifecycle(
    namespace="one-task-id",
    trusted_tools={"read_source", "close_stage", "check_dependency"},
    current_versions={"docs/route.txt": "host-computed-content-version"},
    ttl_model_calls=2,
)
result = adapter.run_sync(agent, input=full_authoritative_input, max_turns=10)
```

run_sync在模型调用前拒绝previous_response_id、conversation_id、auto_previous_response_id。SDK的CallModelData.context只是应用上下文，无法检查实际Runner参数。因此如果自行把adapter与sdk_hooks装到RunConfig，调用者必须保证完整客户端历史；该直接配置不具备首调用远端参数拦截保证。

同步、单任务、单串行Runner是本轮支持范围；streaming/handoff、多任务复用、远端增量历史未支持。checkpoint包含原始工具组，只可作为可信本地资产，不应公开上传；内存checkpoint恢复与SQLite重开测试不等于SDK进程中断后自然任务续跑已经验证。

在code目录执行，使用新的输出ID：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_agents_evidence_review_v1 tests.test_openai_agents_lifecycle_v1 tests.test_agents_lifecycle_sdk_v1 -q
.\.venv\Scripts\python.exe -m experiments.runners.agents_evidence_review_v1 --out runs/agents-checkpoint-new-id
.\.venv\Scripts\python.exe -m experiments.runners.run_agents_lifecycle_v1 --out runs/agents-sdk-lifecycle-new-id
```

## 7. 下一阶段边界

这三项的工程交付已具备。下一阶段是自然模型开发验证：对已冻结的相同历史比较后续动作，以及真实SDK多阶段任务的自然缺失发现、恢复后补丁/依赖质量与完整usage。必须绑定新模型、任务、预算和停止条件；不能直接复用已消费门，也不能把本轮零API结果当作正式留出模型证据。本轮未建立或消费新的付费准入。
