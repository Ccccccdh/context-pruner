# OpenAI Agents 固定输入自然任务 v1：付费小试协议

## 问题与任务范围

旧 v2cal-r3 批次的 `repeat` 改变代号与历史长度，只能视为三种场景变体。本批将同一任务的历史、工具、数据、问题和验收合同固定；重复索引只用于三臂执行顺序轮换。前两次事故任务单任务小试用于排除网络、输出上限和验收误判；当前冻结的 `pilot-04` 为三任务×一重复×三臂，随后再根据质量结果决定是否扩大到三任务×三重复。三个任务是**人工策划的模拟业务任务**，不是公开软件修复任务；不能据此声称真实生产任务收益。

任务为事故处置、账单对账、发布门控。每个任务按顺序读取三份当前记录，结合记录给出判断。旧历史是长段、重复、可能过时的协作记录；当前工具结果才是决策依据。答案正则和工具调用合同只存在于宿主代码及独立审计器，未写入 Agent 的任务消息。Agent 可看到响应格式，但不看到验收值。

## 冻结运行条件

- 模型 `deepseek-v4-flash`，端点 `https://api.deepseek.com`，temperature 0；三臂依次 `none`、`pruner_v1`、`native_summary`，按现有 runner 轮换。
- provider soft/hard/target 为 1200/3000/900 token，经该宿主校准后本地估算 soft/hard/target 为 3261/8152/2446。两主动臂使用 `symmetric_budget`，含摘要请求计入同一请求预算。
- 每样本 Agent 最多 6 轮，每轮最多 2 次重试；原生摘要最多 16 次；九个样本全局最多 120 次 API 尝试。输出上限 Agent 1024、摘要 1024 token；三臂统一关闭隐藏 thinking，避免工具调用被推理 token 挤掉。允许模型重复读取已列出的工具，但三份必需证据的首次读取必须按指定顺序，且不得调用额外工具。重复读取计入全部成本。记录 provider 返回的逐调用输入和输出、辅助调用、失败与重试。模型返回 token 不是发票价格。
- 任务和 runner 源码 SHA256、实验 ID、任务、重复数、请求上限冻结在 `NATURAL_V1_FREEZE.json`。运行期间不改冻结源文件。当前目录为 `runs/stage5-openai-agents-api/openai-natural-v1-pilot-04/`，不覆盖旧结果。
- `pilot-01` 已运行但默认沙箱无法连接供应商：三臂共 10 次尝试均为 `APIConnectionError`，模型响应 0 次。该目录保留为网络诊断，不能参与效果统计。端口测试确认沙箱外可连接 443，复跑使用新的 `pilot-02` 目录，源码和预算保持一致。
- `pilot-02` 共 14 次 API 尝试；无压缩在首轮出现 `finish_reason=length`、插件在第四轮出现相同错误，原生组重复读部署记录一次。基线无成功 provider 用量，不可计算节省。`pilot-03` 统一关闭 thinking 并提高输出上限，同时将重复读视为成本而非自动质量失败；此改动在付费前重新冻结。
- `pilot-03` 三臂工具、结构和答案均相同且正确，但 runner 的旧字面词检查要求 `rollback`，模型写 `roll back`，三臂被误判失败；独立正则已识别这个等价写法。原始批次和源码快照保留，不以其成功率报告效果。`pilot-04` 在付费前修正该宿主验收项，并增加另外两个任务。
- 向端点发送模拟历史、任务、工具参数和模拟工具结果；不发送密钥、工作区源码或验收答案。密钥仅从环境变量读取，不输出。

## 零 API 门控与付费调用

先运行 `python -m unittest tests.test_openai_agents_natural_v1 -q`，再运行下述 `--plan`。单测通过意味着固定重复输入、真实 SDK Runner 的三臂工具回路、压缩器接入与基础质量门控均可执行；这不预测真实模型会成功。

```powershell
.\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_natural_v1 --plan --scenarios incident_triage,invoice_reconcile,release_gate --methods none,pruner_v1,native_summary --repeats 1 --max-output-tokens 1024 --max-api-requests 120 --experiment-id openai-natural-v1-pilot-04

.\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_natural_v1 --confirm-send-synthetic-data --scenarios incident_triage,invoice_reconcile,release_gate --methods none,pruner_v1,native_summary --repeats 1 --max-output-tokens 1024 --max-api-requests 120 --experiment-id openai-natural-v1-pilot-04

.\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_natural_v1 runs/stage5-openai-agents-api/openai-natural-v1-pilot-04
```

## 预先判读

小试仅做功能门控：三样本齐全，工具调用及顺序、结构安全、逐调用 usage 与辅助请求账本可独立审计，且至少基线与插件都成功。失败先归因于任务合同、模型轨迹还是压缩，不用改过的同一批数据做确认结论。三任务×三重复应重新冻结，记录所有 27 样本及失败/触顶样本，按任务报告配对总 token、中位数、正收益对数、成功率、压缩触发次数及峰值；不得把模拟任务外推为真实软件任务。
