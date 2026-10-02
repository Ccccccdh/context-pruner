# 新主机三臂协议：OpenAI Agents SDK 与 CrewAI（零 API 门控冻结版）

冻结说明。本文档冻结**两个新主机**的三臂实验协议（`none` / `native_summary` / `pruner_v1`）。
门控与离线校验**已在零 API 条件下全部通过**（见 §4、§5），付费批次按本协议执行。

## 1. 为什么先做这两个主机

`HANDOFF_DEEPSEEK_HARNESS_2026-09-29.md` 要求框架无关的验证：同一插件要在不同宿主上给出可比的
三臂对照。OpenHands 已完成 v45–v52；本协议把**工具调用型**（OpenAI Agents SDK）和
**多 Agent 交接型**（CrewAI）补齐，并保持与 OpenHands 相同的三臂定义与统计口径。

| 主机 | 宿主类别 | 循环形态 | 插件挂载点 |
|---|---|---|---|
| OpenAI Agents SDK 0.22.3 | Agent SDK | `Runner` 单 Agent + Responses 工具项 | `RunConfig.call_model_input_filter` |
| CrewAI 1.15.22 | 多 Agent 框架 | Agent `kickoff` + 文本 ReAct 循环 | `CrewAIContextAdapter.attached()`（before/after LLM 与 after tool） |

## 2. 三臂定义（两主机一致）

| 臂 | 定义 | 是否挂载 Context-Pruner |
|---|---|---|
| `none` | 原样历史，无任何压缩钩子 | 否 |
| `native_summary` | **宿主原生基线**：超过软预算时，把较老前缀折叠成**一次**辅助模型调用生成的摘要；最近窗口逐字保留；不归档、不打分、不排序、不可恢复 | 否（适配器保持禁用实例，仅用于共用指标字段） |
| `pruner_v1` | 插件：分层打分 + 压缩 + 归档 + 选择性恢复 | 是 |

**公平性约束（两主机逐条相同）**：

1. 三臂使用**同一个全局请求预算对象**，辅助摘要请求按一次请求计费；
2. 原生摘要臂的辅助 token（输入与输出）**单独记录**并计入该臂总成本，不能藏在未计量的旁路里；
3. 摘要仅在**确实变小**时被接受；提供方报错时原样放行（退化为 `none`，而不是报错）；
4. 两臂使用**同一保留预算**（插件 `target_tokens`）与同一 token 估算函数
   （`context_pruner.types.estimate_tokens`），避免"保留策略不同"污染对比；
5. 臂顺序按 `(repeat_index + task/scenario_index) mod 3` 轮转；
6. **再增长规则（两主机一致，`regrowth_ratio = 0.25`）**：某次摘要落地后，若当前载荷没有比
   "上次压缩后的体积"增长超过 25%，该臂**不再重复摘要**。理由：内容已被摘要过，重复摘要等于对同一
   载荷重复收费，那是记账假象而不是策略差异；真实宿主摘要器也有同类触发条件
   （OpenHands `native_summary` 为 `max_size=240` 事件数）。
   门控用 `.tooling\check_native_summary_openai_agents.py::check_growth_guard_prevents_resummarising`
   专门验证：未增长时摘要调用数不增加（`no_growth_since_summary`），真实增长后才允许再次摘要。

## 3. 主机特有实现

### 3.1 OpenAI Agents SDK

- 文件：`experiments/runners/openai_agents_native_summary.py`、
  `experiments/runners/native_summary_common.py`；
- 主运行器：`experiments/runners/run_openai_agents_api_experiment.py`（`--methods` 默认三臂）；
- 切分规则：Responses 项按**组**切分，`function_call` 永不与其 `function_call_output` 分离；
- **钉住单元**：首条用户消息（任务身份）永远逐字放在摘要之前，且同时进入摘要输入；
- 场景：`single_tool`、`parallel_tools`、`multi_tool_chain`（共 9 次执行 = 3 场景 × 3 臂）；
- 预算：`soft=1200 / hard=1800 / target=900`，`fixed_reserved=512`；
- 上限：`max_turns=6`、`max_output_tokens=256`、`max_summary_tokens=1024`、
  `max_summary_calls=16`、`max_api_requests=80`；
- 计划请求数：模型调用 21（3 场景 × 3 臂 × 7）+ 摘要余量 48 = 69 ⇒ 运行器默认 60 **不足**，
  因此付费运行必须显式给出 ≥69 的 `--max-api-requests`（本协议冻结为 **80**）。

### 3.2 CrewAI

- 文件：`experiments/runners/run_crewai_experiment.py`（`NativeSummaryCrewAILLM`）、
  `experiments/runners/native_summary_common.py`；
- 切分规则：按**消息边界**切分，`pinned` 首条（系统/任务约束）与最近 `tail` 逐字保留，
  只有中间 `head` 允许折叠；
- 任务：`tasks/stage5_autogen/natural_tasks.json` 的 3 个自然任务（共 9 次执行 = 3 任务 × 3 臂）；
- 预算：`soft=1800 / hard=6000 / target=1500`，`fixed_reserved=300`；
- 上限：`max_output_tokens=512`、`max_summary_tokens=1024`、`max_summary_calls=16`、
  `max_api_requests=90`（运行器默认值）；
- 计划请求数：27（9 次执行 × 3 次模型调用最小值）+ 摘要余量 48 = 75 ⇒ 硬上限冻结为 **90**；
- 传输分离：Agent 循环用同步 `OpenAI` 客户端，辅助摘要用异步客户端
  （`summary_options["summary_client"]`），两者互不干扰——这正是门控暴露并修正过的一类错误。

## 4. 零 API 门控记录（付费前）

### 4.1 OpenAI Agents SDK

`python .tooling\gate_openai_agents_3arm.py`（真实 SDK `Runner` + 真实过滤器 + 桩提供方）：

| 项 | 结果 |
|---|---|
| 三臂 × 3 场景 × 2 重复 = 18 次执行 | 全部 `success=True`、`structure_safe=True` |
| 对照（无可压缩历史） | `none` 与两臂 payload 完全相同（1279 tokens），摘要调用 0 次 |
| 配对（n=6） | `pruner_v1` 输入节省 **86.6%**（含辅助开销 86.2%）；`native_summary` 输入节省 **81.9%**，含辅助开销 **−12.2%** |
| 成功率差 | 两臂均为 **0.0000**（同轨迹下不得改变结果） |
| 产物 | `.tooling/gates/openai-agents-3arm-gate.json` |

另有两个零 API 单测/校验：

- `.tooling\check_native_summary_openai_agents.py`：未触发时与基线逐字节相同、切分不拆散调用对、
  钉住单元不被折叠、失败退化为基线、调用上限生效、共享预算被计费、SDK 载荷形状正确；
- `.tooling\check_native_summary_transport.py`：对本地回环服务器走**真实** `AsyncOpenAI`
  chat-completions 通道，校验请求格式、预算计费、用量记录、摘要位置与 500 错误降级。

### 4.2 CrewAI

`..\.venv-crewai\Scripts\python.exe .tooling\gate_crewai_3arm.py`（真实 CrewAI ReAct 循环 + 回环提供方）：

| 臂 | 发送输入 tokens | 压缩事件 | 摘要调用 | 含辅助开销总 tokens | 任务身份保留 |
|---|---:|---:|---:|---:|---|
| `none` | 11,579 | 0 | 0 | 11,916 | 是 |
| `pruner_v1` | **2,450** | 1 | 0 | **2,586** | 是 |
| `native_summary` | **2,767** | 1 | 1 | 5,678 | 是 |

- 对照（无可压缩历史）：两臂 payload 完全相同（1838 tokens），摘要调用 0 次；
- 产物：`.tooling/gates/crewai-3arm-gate.json`、`.tooling/gates/crewai-gate-log.txt`；
- **门控不判定轨迹正确性**：脚本化提供方不会对 Observation 作出反应，因此工具轨迹发散是桩脚本的
  性质，不是臂的性质。轨迹正确性由付费批次测量；`mock` 模式的 `tests.test_crewai_experiment`
  仍断言 `success/tool_calls/state_roundtrip` 全绿。

## 5. 门控修掉的两个真实缺陷（记录在案）

1. `ContextPluginConfig(method="native_summary")` 直接抛 `ValueError`——原生摘要臂必须先映射到
   一个合法插件方法（`none`）并置 `enabled=False`，状态恢复路径同样要修正；
2. 摘要传输走**异步** chat-completions 表面，用同步客户端会得到
   `'coroutine' object has no attribute 'choices'`——两个客户端必须分开持有。

两处都是"只有真跑才会暴露"的接口错误，正是零 API 门控存在的理由。

## 6. 统计口径（与 OpenHands 一致）

- 主统计量：同一任务/场景同一重复内，各臂相对 `none` 的**分数制输入节省**，**包含失败样本**；
- 同时报告"含该臂自身辅助开销的总 token 节省"，因为它才与厂商账单可比；
- `both_success` 子集只作敏感性分析，**不得替代**主统计量；
- 不同主机、不同任务之间的节省**不得合并**；
- 成功判定：Agents = 工具集合/顺序/并行批次 + 答案 + 单行格式 + 结构安全 + 任务身份；
  CrewAI = 工具多重集 + 答案 + 单行格式 + 结构安全 + 状态往返 + 任务身份。

## 7. 付费批次命令（冻结）

```powershell
# OpenAI Agents SDK（项目根为工作目录，PYTHONPATH 指向项目根）
$env:PYTHONPATH='<repo>'
& .\.venv\Scripts\python.exe experiments\runners\run_openai_agents_api_experiment.py `
    --confirm-send-synthetic-data --methods none,pruner_v1,native_summary `
    --max-api-requests 80 --experiment-id deepseek-runner-3x3-v3arm

# CrewAI
$env:PYTHONPATH='<repo>'
& .\.venv-crewai\Scripts\python.exe experiments\runners\run_crewai_experiment.py `
    --mode api --confirm-send-synthetic-data --methods none,pruner_v1,native_summary `
    --max-api-requests 90 --experiment-id crewai-natural-3arm-v132
```

两批均为**合成载荷**：不读取工作区文件、不发送源码。API key 经 `.tooling\launch_with_key.ps1`
从 Windows 用户级环境注入（DSH 继承的环境块是旧的）。

## 8. 后续（本协议之外）

1. 三臂结果落地后，把三主机（OpenHands / OpenAI Agents / CrewAI）一起改、一起跑；
2. OpenHands 的**阈值提高消融**仍按 `V48_V49_DIAG_REVIEW.md` §3.4 单独执行；
3. 审计并行化（§3.3 的两段式墙钟测量）仍待办：共享模块全局变量使线程内审计不安全；
4. 每个批次保留**不可变预运行 manifest 快照**。
