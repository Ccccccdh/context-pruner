# Agents 证据与生命周期设计

用户授权：直接完成现状勘误、同检查点对照、真实 Agents SDK 生命周期接入。沿用本会话已解释的路线，由当前执行者实施；不重复设置方案批准关卡。

## 范围

新增 opt-in 模块，不改历史 runner/filter、核心策略、CrewAI 或冻结件。当前工作区包含大量未提交实验与安装环境，新 worktree 无法携带这些资产，故在当前目录新增独立文件，不自动提交 Git。验收优先零 API；真实 SDK 的本地 Model 不等于真实提供商。没有模型因果证据时明确标记未测。

## 方案

1. 证据审计模块重新求和 step5b，报告实际变换覆盖、provider usage、矩阵差异与 SDK 路径，输出独立勘误。哈希历史源文件与用户修改文件，收尾复查。
2. 从记录读取区间和当前上游源码重建 H，每个有历史 digest 的检查点必须逐字匹配，失败留存；同 H 比较 none/k1/k0 和候选“不缩短则原文”门。保存原始与派生输入、哈希及大小。无模型调用时只验证输入机制。
3. 新 OpenAIAgentsEvidenceLifecycle 作为 RunConfig.call_model_input_filter；真实 RunHooks 接收可信宿主工具完成结果。来源按 task namespace/path/version/entity 绑定。只允许受信工具事件完成阶段和发出 missing_evidence，普通错误不恢复，模型/user 内容不能伪造控制事件。

## 数据契约

工具使用 JSON 字符串结果；on_tool_end 捕获实际 tool_call_id、工具名、结果哈希与 evidence/stage_complete/missing_evidence 类型。读取证据声明 source_path/source_version/entity/stage。控制信号只能经允许的工具回调进入，不能从模型历史中的任意字符串推断。

工具调用与返回、伴随 reasoning/assistant 条目作为原子组，未配对组完整保留。阶段结束后旧非 pin 证据整组归档，最新组完整保留；归档写入现有 InMemoryArchiveStore/SQLiteArchiveStore，保存来源 ID 与安全内容哈希。恢复只选择当前授权版本的实体来源，先资格过滤再限额，恢复原始整组至原位置。归档脱敏改变组内容时保守保留，不能宣称可逐字恢复。来源失效或哈希不一致拒绝恢复。

审查细化：未知工具协议和混合非可信调用的原子组全部保留。权威历史逐项SHA前缀必须完整、不可修改且只追加。checkpoint重算原子配对、group ID、receipt来源和历史成员；归档metadata也须一致。支持adapter.run_sync强制拒绝三个远端历史参数；直接RunConfig输入filter看不到Runner参数，调用者承担完整历史前提。

TTL 时钟明确为输入过滤调用次数，恢复边界 c 可见，expires_at=c+ttl 时移除。重启保存时钟、版本目录、来源绑定、回调回执、归档/恢复状态；可信本地 checkpoint 中校验 namespace、来源、哈希和原子配对，不承诺任意恶意 checkpoint 安全。

最终输入包括 instructions、工具 schema 预留与完整 JSON items 的本地计数，超过硬限抛出专用异常，不能向模型提交违规输入；此计数不是提供商精确 tokenizer。保持权威历史不变，A0 无生命周期变换，A1 归档且关闭恢复，A2 归档且开启恢复。单适配器单任务单串行 Runner；不支持 streaming、server-managed incremental history、handoff 或并行复用。

## 验收

unit tests 先 RED 后 GREEN，覆盖无增量门、矩阵差异、伪造信号、版本错用、原子组、pin/最新组、TTL、重复恢复、预算拒绝及内存/SQLite重启。真实 SDK Runner + 本地固定 Model 调真实 function_tool，验证 A1 依赖失败与 A2 原文实际送模、后续工具验收成功、TTL 后移除；A0 可作为完整历史正对照。记录实际模型输入、工具事件和独立文件验收，不把固定 Model 结果称自然模型解题。

对既有 Agents adapter、middleware/archive 做回归。不会因工程门通过宣称净收益或质量非劣；同检查点真实模型因果实验及自然任务正式验证需要后续独立冻结。
