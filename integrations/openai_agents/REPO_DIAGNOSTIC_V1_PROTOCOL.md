# OpenAI Agents 跨公开仓库源码诊断开发批 v1

## 任务与边界

从本机已保存、Git 工作树干净的 SWE-bench Verified 公开基线选 `pytest-dev__pytest-10356`、`pylint-dev__pylint-8898`、`django__django-16263`。这些任务未参与 OpenAI Agents r3 模拟运维任务的调参，但其中 pytest 和 Django 问题在本项目其他宿主的早期探索中出现过；本批又重新选择了阈值和固定源码片段，故为**跨公开仓库诊断开发批，不是 r3 策略的盲测确认批**。三个任务读取原始问题陈述与三个固定源码片段，输出根因和修复约束；不改源码、不运行真实修复测试。此批若成功，只证明真实公开源码诊断工作流的质量与成本，不能证明软件修复率。

初始上下文只有公开问题陈述与当前请求，没有合成重复历史。三个只读 SDK 工具按任务顺序返回真实基线源码片段；文件和行范围在 `run_openai_agents_repo_diagnostic_v1.py` 固定，完整文件 SHA256 在冻结文件中。pytest 的三份视图分别是 `src/_pytest/mark/structures.py:338–363`、`:365–392` 与既有测试 `testing/test_mark.py:540–589`，不存在人为重复视图。禁止读取或发送 `reference.patch`、`host-tests.patch`、密钥或验收答案。问题陈述中本来出现的复现代码是公开 issue 的一部分。

任务、答案 regex、工具顺序独立写入审计脚本。三臂固定同输入、每任务 3 次独立重复，`none`、`pruner_v1`、`native_summary` 共 27 样本 / 9 对；重复标签只改变运行顺序，不改变输入。DeepSeek `deepseek-v4-flash`、温度 0、禁用隐藏 thinking。provider soft/target/hard 为 **2000/1500/6000 token**，按既有 0.368 标定换算为估算 5435/4076/16304；各臂同一个 soft 触发门。每样本最多 6 turn、1024 输出 token、16 次摘要调用；全批最多 300 次 Agent 与摘要请求尝试（包含重试/失败）。完整 total token 包括摘要辅助调用。最终金额以供应商账单为准。

## 零 API 门控与压力检查

- 3 个基线 HEAD 与数据集 `instance.json` 对齐且工作树干净。冻结文件记录 10 份公开输入哈希，以及 runner、基座、过滤器、审计和协议源码哈希。
- `tests.test_openai_agents_repo_diagnostic_v1` 用真实 SDK dispatch、纯本地假模型走 3 任务×3 臂；同时核查重复同输入、工具顺序、回答合同、消息结构与三臂的触发。当前 3/3 测试通过，9/9 本地假回答合格；独立审计自测能发现质量和 usage 篡改。
- 不使用填充历史的零 API 压力测量：三任务基线估算峰值分别为 **5958 / 9162 / 6769**；soft 为估算 5435 时，插件压缩触发分别 **0 / 2 / 1**，原生摘要分别 **0 / 2 / 1**。pytest 在这个工具轨迹中未触发，仅可作宿主可行性/质量样本，不能充当压缩收益样本。这些是本地假模型轨迹，真实模型工具重复、输出和触发次数会变化。
- 曾试过 provider soft 4000（估算 10869），三任务零 API 轨迹均 **0 次压缩**，故不采用该阈值。2000 是公开记录的门控选择，不能以付费结果再反向调参后当同一确认批。
- 如果真实运行某任务/某配对未触发，仍计入所有样本和质量，但该配对只支持“无触发时的宿主可行性”，不作压缩节省证据。

## 付费前尚需完成

1. 冻结文件、审计脚本及其零 API 自测全部通过；确认所有哈希与当前文件一致。
2. 核对 `.tooling/upstream` 三棵树仍为指定 HEAD 且 clean、endpoint 与密钥环境变量存在；密钥绝不输出。
3. 用 `--plan` 复核 27 样本、108 次最低 Agent 调用、144 次摘要预留、300 次全局上限。

付费运行在上述门控后由主 Agent 决定。本任务准备阶段**不发起模型 API**。运行命令预案：

```powershell
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v1 `
  --confirm-send-public-source `
  --scenarios pytest_mro,pylint_regex_csv,django_count_annotations `
  --methods none,pruner_v1,native_summary --repeats 3 `
  --provider-soft 2000 --provider-target 1500 --provider-hard 6000 `
  --max-output-tokens 1024 --max-api-requests 300 `
  --experiment-id openai-repo-diagnostic-v1-dev-01
```

独立审计对输入/源码哈希、样本栅格、工具顺序、答案、所有调用用量与配对统计逐项核对。失败、重试和触顶均进入主口径；逐任务另报成功率、正收益对数与是否压缩，不挑成功子集替代全样本。只有质量不下降且插件触发的配对可以支持“有效节省”的陈述。
