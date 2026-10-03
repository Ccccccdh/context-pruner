# CrewAI v2-R2：三任务、三臂、双重复稳定性小试（冻结）

冻结于 2026-10-02，付费请求前。结果目录：`runs/stage5-crewai/crewai-natural-3arm-v2cal-rep2-01/`，不得覆盖 v2cal。任务文件 SHA256 `8d51add8e421f2cfd011e5417ca5c3d071073408ceab0c662b7fe2985a84e7f0`；运行器 SHA256 `45403d33c578156540a22a3fae190f9fbfb83833b9fb1c1f0188a4f565650353`。零 API CrewAI 门控退出码 0，门控产物 SHA256 `067152c7f5f7ebd1d932fe796cfd31c37f02e7024c9f4fc053222b1dc444b29c`；运行器单元测试 8/8。门控的脚本化工具轨迹在部分臂显示 success=False，但门控所检验的压缩/计费与无冗余控制断言通过；付费结果必须独立评价任务成功，不能用门控通过代替质量证据。

## 假设与变动

沿用 v2cal 的 3 个合成任务（incident_triage、release_readiness、customer_migration）、模型 `deepseek-v4-flash`、`https://api.deepseek.com`、三臂、`symmetric_budget` 与 provider 预算 soft 1200 / target 900 / hard 3000。每个任务新增 2 次重复，共 18 个新样本；比较插件与无压缩的同批配对输入、含辅助请求总 token、峰值与成功率。v2cal 仅 1 次重复，可作为**独立批次旁证**，不可与本批合并为同批 n=9。temperature=0 但供应商无固定 seed，旧 repeat=0 不能视为可复用的配对重复。

运行器安全修正：v2cal 的原生摘要请求未计入 `RequestBudget`，尽管其 manifest 称与 Agent 共享预算。本批每次真实摘要请求先占用全局请求槽，并保留下一次 Agent 调用的槽位。此修正不改变触发阈值、摘要或插件内容；若全局上限临近，原生摘要会跳过，故达到上限的样本不可直接与 v2cal 作机制等价比较。`max_summary_calls` 每样本从 16 降为 6；v2cal 实测每个原生样本至多 3 次，预计该护栏不触发，若触发须单独报告。

## 费用与中止边界

v2cal 9 样本合计 46,779 token（含原生摘要输入输出），均值 5,198 / 样本，最大 7,257 / 样本；本轮按旧均值估计约 93,558 token，按旧最大值乘 18 为 130,626 token，二者均是**历史估计而非硬 token 上限**。v2cal 27 个 Agent 请求 + 6 个摘要请求，推算本轮约 66 个真实请求。全局硬上限 96 个真实请求（Agent 与摘要合计）；摘要每样本最多 6 次，Agent 单次输出最多 512 token，摘要输出最多 1,024 token，每任务最多 1 次重试。若请求上限耗尽、凭据/供应商异常或安全门控失败，停止并保留已有结果，不覆盖旧批次。未设美元硬上限，价格以供应商账单为准。

只发送合成对话、合成标识、工具参数和合成工具结果；不读取或发送工作区源码、密钥、真实用户数据。`--plan` 已离线确认 18 executions / 54 expected Agent calls / 36 summary headroom / 96 global cap。

## 运行与判读

```powershell
$env:PYTHONPATH=(Get-Location).Path
& .\.venv-crewai\Scripts\python.exe experiments\runners\run_crewai_experiment.py `
  --mode api --confirm-send-synthetic-data `
  --methods none,pruner_v1,native_summary --repeats 2 `
  --max-summary-calls 6 --max-api-requests 96 `
  --experiment-id crewai-natural-3arm-v2cal-rep2-01
```

完成后独立审计：任务×重复×臂唯一性及完整性、运行器与任务哈希、全局请求数和摘要计数、每行 token 恒等式、质量与文件边界；逐重复、逐任务及总体配对统计。主结论必须同时报告质量、含辅助调用总 token 与失败/触顶数；只在全部样本完成且审计通过后作总体描述。正收益不能由单任务或单次重复外推到真实 CrewAI 工作流。

