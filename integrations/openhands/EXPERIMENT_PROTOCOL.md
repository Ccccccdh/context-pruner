# OpenHands 原生接入与三组实验协议

本模块使用独立 `.venv-openhands`。OpenHands SDK/tools 1.49.6、LiteLLM 1.93.2；完整依赖见 `requirements.lock.txt`，本项目另以 `pip install -e .` 安装。主环境与 CrewAI 环境保持隔离。

## 安装与复现

在 `code` 目录使用 Python 3.13：

```powershell
python -m venv .venv-openhands
.\.venv-openhands\Scripts\python.exe -m pip install -r integrations/openhands/requirements.lock.txt
.\.venv-openhands\Scripts\python.exe -m pip install -e .
git clone --depth 1 --branch v1.2.1 https://github.com/theskumar/python-dotenv.git .tooling/upstream/python-dotenv-v1.2.1
.\.venv-openhands\Scripts\python.exe -m pip check
.\.venv-openhands\Scripts\python.exe -B -m unittest tests.test_openhands_adapter -v
.\.venv-openhands\Scripts\python.exe -B integrations/openhands/run_smoke.py --run --out runs/stage5-openhands/new-smoke-id
.\.venv-openhands\Scripts\python.exe -B integrations/openhands/run_comparison.py --check --out .tooling/new-protocol-check-id
.\.venv-openhands\Scripts\python.exe -B integrations/openhands/run_comparison.py --run --out runs/stage5-openhands/new-comparison-id
.\.venv-openhands\Scripts\python.exe -B integrations/openhands/audit_comparison.py --out runs/stage5-openhands/new-comparison-id
```

真实运行要求当前进程有 `DEEPSEEK_API_KEY`，不要把值写入文件、命令记录或报告。首次运行会下载 tokenizer 缓存。`--check` 和适配器单元测试不请求模型。SDK 的 Python 版本要求独立于主库的 Python 3.10 支持。

## 固定实验

- 源码：MIT 许可的 python-dotenv v1.2.1，提交 `eaf2a9129ccec6febda0f741eb3bb852c3f947bd`。
- 任务：写入反斜杠值后读取不一致；空值后的空白行内注释；无效行后 CRLF 的完整恢复。各问题来自公开 issue/PR，任务文本自行编写。
- 分组：`none` 使用 NoOpCondenser；`native_summary` 使用官方 LLMSummarizingCondenser；`pruner_v1` 使用本项目 ContextPrunerCondenser。
- 3 个任务 × 3 次重复 × 3 组 = 27 样本。按任务和重复轮换组顺序，工作区、会话、LLM metrics、插件状态分别隔离。
- 模型相同：`openai/deepseek-v4-flash`、DeepSeek 官方端点、温度 0、thinking 关闭、API 自动重试 0；每次输出最多 1536 token。
- 压缩阈值相同：SDK 输入估算超过 12000 token。插件目标 10000、硬目标 16000；原生摘要保留首两事件，事件上限 240，使用同一模型摘要。
- 每样本最多 14 次 Agent 调用、5 次摘要调用；单次估算输入最多 40000、累计最多 300000。完整 27 样本最多 513 次调用、810 万估算输入 token；这是安全上限，不是实际使用量。
- 三阶段固定：调查且不编辑 → 修复 → 宿主测试反馈后的检查/修正。调查阶段固定读取源码与文档的相关范围，不提供参考补丁或评分测试答案。
- 工具仅受限文件编辑器和 FinishTool。禁止读工作区之外的路径，仅允许修改指定实现文件；没有终端或浏览器工具。
- 判分：新增行为测试 + 未修改的上游 parser/variables 测试 + 文件哈希边界 + 调查阶段未编辑。上游 `test_main.py` 依赖 POSIX-only `sh`，Windows 运行不包含它。
- 测试子进程只继承必要系统变量，不继承模型密钥；每次测试使用独立临时目录。测试失败的反馈不含参考实现。
- 请求前逐次校验工具调用/返回是否闭合。提供商实际输入、输出 token 用于效果比较，包含摘要；估算 token 仅用于调用预算。SDK 价格映射缺失，因此不报告金额收益。

## 接入方式

```python
from context_pruner.adapters.openhands import ContextPrunerCondenser
from openhands.sdk import Agent

agent = Agent(llm=llm, tools=tools, condenser=ContextPrunerCondenser())
```

该适配器在官方 condenser 扩展点处理模型视图。只删除 SDK 认定安全边界内的旧事件前缀，用确定性 Context-Pruner 输出建立 Condensation 派生记忆；保留系统、初始任务、最新用户要求和最近完整工具批次。若变换破坏结构或未减少 token，则保持原视图。原 SDK 事件日志不删除；压缩结果和中间件状态可以导出供审计。

这是原生视图压缩接入。当前不提供 SDK 自动读取归档的恢复工具，也没有映射全部 SDK 的错误/模型/工具生命周期到独立 middleware 的所有钩子；不能把本实验说成已经验证完整选择性恢复闭环。核心恢复能力已有其他实验，但不是这次三组的评分项。

## 冻结与解释

`manifest.json` 在第一次运行前记录协议、版本、任务和运行器/任务集/适配器源文件哈希。`--resume` 仅跳过有最终报告的样本，哈希改变时拒绝继续；中断样本需要人工诊断，禁止静默覆盖或只重跑不满意的结果。

预实验保留在独立目录，不混入冻结后的 27 样本。正式运行中所有失败均保留并计入成功率；不会按成功筛选 token 指标。只有三个同库任务，重复不等于增加独立任务数；数据支持受控工作流的结果，不能外推所有 Agent 或宣称质量等价。

官方参考：[SDK 入门](https://docs.openhands.dev/sdk/getting-started)、[condenser](https://docs.openhands.dev/sdk/guides/context-condenser)、[SDK 源代码](https://github.com/OpenHands/software-agent-sdk)。
