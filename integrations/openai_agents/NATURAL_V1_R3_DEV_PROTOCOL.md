# OpenAI Agents 固定同输入三任务三重复开发批

批次 `openai-natural-v1-r3-dev-01`。这是 `pilot-04` 三任务单重复质量门控后的下一步；旧批原样保存，不与本批合并。三个任务仍是人工策划的模拟运维工具任务，不称为真实仓库修复任务。

- 三任务 `incident_triage`、`invoice_reconcile`、`release_gate` × **相同输入**的三次独立模型运行 × `none`、`pruner_v1`、`native_summary` 三臂，共 27 个样本、9 个配对。重复索引只改变臂顺序，不改变任务历史、工具返回或正确答案。
- DeepSeek `deepseek-v4-flash`，temperature 0，禁用隐藏 thinking；provider soft/target/hard 为 1200/900/3000 token，经本宿主 0.368 标定换算。每次 Agent 输出最多 1024 token。全批至多 300 次 Agent＋摘要尝试，包含失败和重试；用量按供应商逐调用记录，金额以账单为准。
- `pilot-04` 三任务×单重复三臂均通过答案、工具顺序和结构门控；插件含辅助调用总 token 配对节省约 +69.73%（n=3）。本批目标是观察固定同输入重复的波动，而不是假定旧收益稳定。
- 零 API：`tests.test_openai_agents_natural_v1` 3/3，通过 `--plan` 得到 27 样本、9 对。独立审计脚本在付费后核对样本栅格、任务成功、所有 Agent 与摘要用量、源哈希和配对统计。
- 所有样本（包括失败、重试、超限）进入主口径；逐任务报告成功率、配对总 token、输入峰值、压缩/摘要触发、正收益次数。若部分样本未完成，本批只作诊断，不用成功子集替代全样本结论。
- 向模型只发送模拟任务历史、工具参数与返回；密钥和验收答案不发送。源码哈希、任务、预算和输出路径在 `NATURAL_V1_R3_DEV_FREEZE.json` 中于运行前冻结。

付费命令：

```powershell
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_natural_v1 `
  --confirm-send-synthetic-data --scenarios incident_triage,invoice_reconcile,release_gate `
  --methods none,pruner_v1,native_summary --repeats 3 --max-output-tokens 1024 `
  --max-api-requests 300 --experiment-id openai-natural-v1-r3-dev-01
```

此批仍是开发批；最终论证须换未参与调参的自然任务，并独立冻结确认批。
