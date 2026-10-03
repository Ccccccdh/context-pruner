# CrewAI 双角色固定输入开发小试（付费前协议）

本批用 CrewAI 1.15.22 的两个真实 `Agent` 顺序执行同一决策任务。调查角色只能调用第一项合成工具，形成 `HANDOFF`；决策角色收到其实际输出，只能调用第二项合成工具，形成最终 `RESULT`。两个角色各有独立的 `CrewAIContextAdapter`，在单个 `with adapter.attached()` 范围内注册全局 hook；样本顺序执行，避免 hook 串扰。此轮仍是**合成运维任务**，但执行步骤和交接是真实 CrewAI Agent 生命周期，不构成自然真实工作流外部效度证据。

## 固定实验单元

- 开发小试只选 `incident_triage`，一个任务、一个固定输入重复、三臂：无压缩、原生摘要、插件，共 3 个付费样本。随后若门控通过，另选 `release_readiness` 与 `customer_migration`，使用完全相同的任务输入做多次随机重复；这些任务先不作为确认批。
- repeat 仅是配对键，**不改变**历史、提示、工具数据或模型参数；三臂相同 `task_id×repeat` 的输入字节相同。温度 0 不保证服务商确定性，顺序效应仍需后续轮换臂顺序。
- 模型 `deepseek-v4-flash`，接口 `https://api.deepseek.com`；仅发送合成任务、合成历史、合成工具结果和交接内容，不读取真实项目源码或用户数据。密钥只从环境变量取。
- provider 预算 soft/target/hard 为 1200/900/3000 token，经 CrewAI 既有校准换算为估算器阈值；固定保留 300 估算 token。输出上限每次 Agent 512、摘要 1024。插件与原生摘要都以同一 soft 预算触发。
- 每摘要角色最多 4 次摘要尝试；全批 Agent＋摘要全局请求槽 48，供应商重试 0；遇请求预算触顶、凭据或传输错误则停止并报告。费用以供应商账单为准，不用估算 token 当金额。

## 门控与判读

付费前须通过：固定 repeat 的请求视图完全一致；两个角色各调用指定工具一次；三臂 mock 结果通过；hook 无未匹配调用、无待恢复视图；`--plan` 零输出目录、零 API；已有 CrewAI 单元回归通过。源与任务 SHA256 写入运行 manifest，并另存付费前冻结文件。

每样本保存角色交接、工具轨迹、post-hook 模型输入摘要、Agent 请求记录、摘要尝试/成功/失败和用量。独立审计重算角色质量、结构安全、请求槽与总 token 恒等式。主指标是含摘要在内的总 token 配对节省，同时报告成功率、峰值、压缩触发次数与逐任务表现。三样本只是可运行性门控，不汇报稳定收益；未触发的臂不算压缩效果。

执行命令（先检查冻结文件，再运行）：

```powershell
$env:PYTHONPATH=(Get-Location).Path
$env:PYTHONIOENCODING='utf-8'
& .\.venv-crewai\Scripts\python.exe -u experiments\runners\run_crewai_handoff.py `
  --mode api --confirm-send-synthetic-data --task-ids incident_triage `
  --methods none,native_summary,pruner_v1 --repeats 1 `
  --max-api-requests 48 --experiment-id crewai-two-role-fixed-r1-pilot-01
```
