# CrewAI v2-R2FIX：摘要传输与尝试计数修复验证（付费前冻结）

批次目录 `runs/stage5-crewai/crewai-natural-3arm-v2cal-r2fix-01/`。沿用 v2-R2 的三个合成任务、`deepseek-v4-flash`、`https://api.deepseek.com`、无压缩/插件/原生摘要三臂、provider 预算 soft 1200 / target 900 / hard 3000 和 `symmetric_budget`。每任务两种历史长度（r00 8 条历史单元，r01 9 条），共 18 个新样本。三臂在同一个 task×repeat 内配对；两种 repeat 是场景变体，不是纯随机重复。温度 0，不保证供应商完全确定性。

## 唯一运行器修复

第一批 v2-R2 的原生摘要 `AsyncOpenAI` 被多个样本的不同 `LoopRunner` 复用，批末调用 `close()` 未 await；15 个摘要预算槽仅 11 个有 usage，4 个槽的传输状态未持久化。第一批运行器字节已快照，SHA256 与其 manifest 一致；原批保留为诊断证据。

本批每个原生摘要样本在其专有事件循环中创建、使用并 await 关闭 `AsyncOpenAI`。摘要预算槽在尝试前占用，保留下一次 Agent 请求槽；`max_summary_calls=6` 现在按**尝试**计数（失败也计），每行持久化 `summary_attempts`、`summary_failures`、失败详情和关闭异常。插件压缩策略、任务提示、阈值、模型和工具不变。若摘要请求失败且无供应商 usage，账单成本仍可能未知，报告必须揭示。

## 费用与停止

18 样本、预计约 54 个 Agent 调用。旧诊断批使用 69 个预算槽，其中 15 个摘要槽；本批全局上限 96 个槽（Agent+摘要），每原生样本最多 6 次摘要尝试，每任务最多 1 次重试。每 Agent 输出最多 512 token，每摘要输出最多 1,024 token。旧 v2cal 9 样本已记录总 token 46,779，按此估算本批约 93,558，但第一批 r2-R2 的摘要用量存在缺口；这些不是硬 token 或美元上限，最终费用以供应商账单为准。凭据/供应商错误或请求槽耗尽时停止，保留已有结果，不覆盖旧批。

只发送合成对话、合成工具参数及结果；不读取或发送工作区源码、密钥或真实用户数据。不改变 OpenHands 和 OpenAI Agents 文件。源、任务、门控产物、协议的 SHA256 单独写入 `PRE_RUN_FREEZE_R2FIX.json`，该文件在付费前生成并保持不变。

## 门控与判读

运行器单元测试必须包含两个相继样本的同循环 create/use/await close、失败摘要计入尝试上限、全局预算预留；真实 CrewAI 三臂 loopback 门控必须退出 0。付费后独立复算样本栅格、源哈希、请求槽、摘要尝试/失败、token 恒等式、质量和配对节省。原生摘要需报告完整记录用量与无 usage 的失败尝试。第一批与新批不可合并为同批 n；若两批差异显著，先检查失败轨迹和基线波动，不直接归因于生命周期修复。

```powershell
$env:PYTHONPATH=(Get-Location).Path
& .\.venv-crewai\Scripts\python.exe -u experiments\runners\run_crewai_experiment.py `
  --mode api --confirm-send-synthetic-data `
  --methods none,pruner_v1,native_summary --repeats 2 `
  --max-summary-calls 6 --max-api-requests 96 `
  --experiment-id crewai-natural-3arm-v2cal-r2fix-01
```
