# Agents natural checkpoint v2 design

用户要求执行后续路线第1、2步，含新有界真实模型小试。沿用已批准路线，当前执行者实施；不改v1/历史资产、不自动Git提交。

## Scope

1. 真SDK OpenAIChatCompletionsModel→AsyncOpenAI(max_retries=0)→受审计httpx transport；tracing关闭，SDK重试不启用，串行执行。每外发请求先检查计数/费用/体积、写持久化dispatch事件，再发送。保留原请求JSON、原response usage/输出（凭据不落盘）、状态、ID、耗时。缺失usage保留null；HTTP/API故障停批，保留已发送计数与费用未知，不自动重发。
2. 按既定顺序从v1全部78个H选每任务最早2个有guarded非零差异的H，不依质量筛。当前2任务4H；36样本，3臂×3重复。每样本首次一调用，仅解析失败可加一次格式修复，72总请求硬上限。不同H嵌套相关，只作开发局部比较。

## Frozen settings

模型deepseek-flash，官方表对应DeepSeek-V4.1-Flash。非thinking、temperature0、max_tokens2048、JSON对象回复。旧修复提示词与H原样保留；无SDK函数工具，本轮只要求下一步read或patch，read不执行、不自由续跑。真函数工具、来源/TTL安全沿用v1相关回归，生命周期三臂自然任务不在本轮范围。

请求完整body最多24000 UTF8 bytes，输入计数保守预留body bytes+4096，输出预留2048。采用2026-10-14核验的高峰全miss$0.30/M input、$1.20/M output估算，预算$1.00；每次预留不退款，失败也保留。此为保守估算，实际账单不直接可见，报告offpeak/peak usage价格区间，未知字段不得冒充0或免费。价格来源https://api-docs.deepseek.com/quick_start/pricing/；关闭thinking来源https://api-docs.deepseek.com/guides/thinking_mode/。

## Freeze and outputs

零API使用httpx.MockTransport经过真实SDK捕获转换后的请求body，冻结每H/臂的首次请求hash、SDK输入hash和源码/环境sha清单；修复请求严格遵循原始消息前缀+原回复+统一修复提示。首paid请求前verify所有hash并独占claim；禁止覆盖/续跑/并发及重复sample-phase请求。

patch只在本地新隔离工作区执行既有真质量端点，目标测试/回归独立验收；公开源码与隐藏测试物理隔离，不把reference/test patch输入模型。read仅记录对象范围，不假装任务完成。保存失败和usage。旧端点测试环境有凭据白名单隔离；批次密钥只在进程环境读入客户端，不打印，不在测试子进程传递。

## Verification

TDD覆盖规则选择、manifest损坏、计数/费用/体积上限、并发/重复/未授权repair、HTTP故障不重试、null usage、输入转换、凭据不记录和真实SDK返回。独立审查并修复Important后重新过零API门、冻结、付费运行全队列。结果不提升为端到端收益/自然生命周期证据。
