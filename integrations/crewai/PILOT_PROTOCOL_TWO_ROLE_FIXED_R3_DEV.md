# CrewAI 双角色固定同输入三任务三重复开发批

批次 `crewai-two-role-fixed-r3-dev-01`。r2 的一个任务×一重复三臂小试 3/3 成功，插件完整总 token 节省 +11.17%（n=1）；本批扩大到三任务重复，旧 r1 合同冲突批与 r2 小试不并入统计。

- 三个模拟运维任务 `incident_triage`、`release_readiness`、`customer_migration` × 三次**同输入**重复 × `none`、`native_summary`、`pruner_v1` 三臂，共 27 样本、9 对。两名真实 CrewAI Agent 顺序执行：调查角色调用一个工具并产生 `HANDOFF`，决策角色接收实际交接、调用第二工具并产出 `RESULT`。
- 同一任务的历史、工具返回、两个角色目标和验收答案跨重复不变。执行顺序用 `(task_index + repeat) mod 3` 在三臂间均衡轮换；只改变顺序，不改变内容。
- DeepSeek `deepseek-v4-flash`，temperature 0，provider soft/target/hard=1200/900/3000 token；每 Agent 输出至多 512 token，摘要至多 1024 token、每角色最多 4 次。全批 Agent＋摘要尝试上限 180，失败尝试也占槽；用量以供应商返回记录为准。
- 质量要求：调查交接含指定当前工具事实，两个角色各调用指定工具一次，最终答案符合任务判据；结构化工具调用不丢失、不串角色。独立审计核对样本栅格、角色工具轨迹、摘要尝试/用量、总 token、源哈希及逐任务结果。
- 零 API：`tests.test_crewai_handoff` 4/4；`--plan` 输出 27 样本。运行前哈希与参数写入 `PRE_RUN_FREEZE_TWO_ROLE_FIXED_R3_DEV.json`。不覆盖旧批。
- 全 9 对含失败样本计算插件和原生组对基线的完整 token 节省；分别报告每任务成功率、配对分布、峰值、摘要/压缩实际触发、请求错误。此批是开发验证；合成任务正收益不能推广为自然生产流程收益。
- 向供应商只发送模拟历史、工具结果与两个角色交接文本；密钥和宿主答案不进入提示。

付费命令：

```powershell
$env:PYTHONPATH=(Get-Location).Path
$env:PYTHONIOENCODING='utf-8'
& .\.venv-crewai\Scripts\python.exe -u experiments\runners\run_crewai_handoff.py `
  --mode api --confirm-send-synthetic-data `
  --task-ids incident_triage,release_readiness,customer_migration `
  --methods none,native_summary,pruner_v1 --repeats 3 `
  --max-api-requests 180 --experiment-id crewai-two-role-fixed-r3-dev-01
```
