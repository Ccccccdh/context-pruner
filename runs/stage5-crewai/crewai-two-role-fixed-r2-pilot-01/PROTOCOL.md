# CrewAI 双角色交接修正小试（付费前冻结）

r1 三臂 `crewai-two-role-fixed-r1-pilot-02` 已完成真实模型请求与独立账本审计，14 个 Agent＋摘要请求槽，原生摘要 2 次，插件压缩 2 次。但三臂质量均失败：调查角色收到旧单 Agent 的 `RESULT` 最终答题合同，因此没有按双角色协议输出 `HANDOFF`。r1 保留为**合同冲突诊断批**，不得汇报节省收益。

r2 唯一任务合同修正：`fixed_history()` 的最新用户消息改为明确的调查阶段要求，只调用第一个工具一次、输出单行 `HANDOFF`，保留固定标识和当前工具关键数值，不作最终决策。决策角色仍读取真实调查输出并调用第二工具，最终按 `RESULT` 合同作答。质量判据要求调查交接含当前工具关键事实；两角色各自调用指定工具一次，最终决策含完整证据与正确结论。三臂、工具数据、模型、预算和 hook 算法保持一致。独立审计对旧批的 0/3 失败判定可重现。

本批开发小试仍只选 `incident_triage`：固定输入 1 次×无压缩、原生摘要、插件 3 臂，3 个付费样本。`repeat` 只作索引，不改变输入。模型 `deepseek-v4-flash`、接口 `https://api.deepseek.com`，温度 0；仅合成任务、合成历史、工具数据、角色交接发送至接口。密钥通过环境变量读取，日志不写密钥。每次 Agent 最多输出 512 token；摘要最多 1024 token、每角色最多 4 次；全批 Agent＋摘要最多 48 个请求槽。provider 阈值 soft/target/hard=1200/900/3000 token，固定保留估算 300 token；三臂使用相同触发规则。供应商传输失败后保留当前批次，不在同目录覆盖。

付费前门控：3 单元测试、9 个三任务 mock 样本、独立 mock 审计全部通过；协议、runner、审计与任务的 SHA256 写入 `PRE_RUN_FREEZE_TWO_ROLE_FIXED_R2.json`。付费后独立复核 3/3 样本、两个角色的质量/结构、全部 Agent＋摘要请求槽与 token 恒等式，并报告逐臂完整总 token、输入峰值和压缩触发。小试只证明运行路径，不证明稳定收益。随后须用未用于调整的自然任务做同输入多次重复确认。

```powershell
$env:PYTHONPATH=(Get-Location).Path
$env:PYTHONIOENCODING='utf-8'
& .\.venv-crewai\Scripts\python.exe -u experiments\runners\run_crewai_handoff.py `
  --mode api --confirm-send-synthetic-data --task-ids incident_triage `
  --methods none,native_summary,pruner_v1 --repeats 1 `
  --max-api-requests 48 --experiment-id crewai-two-role-fixed-r2-pilot-01
```
