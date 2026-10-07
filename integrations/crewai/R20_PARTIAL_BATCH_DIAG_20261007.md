# CrewAI r20 三臂部分批只读诊断（2026-10-07）

只读取 `runs/stage5-crewai/crewai-r20-orderfree-3arm-01/`，未修改旧冻结、manifest 或结果，未发起模型请求。`results.jsonl` SHA256 为 `ABB2941E7000D0AFB483EF1094A92E233C2FDC1D36913631DA697AF3D58DC7EA`。

| 项 | 当前记录 |
|---|---:|
| 冻结网格 | 4 任务 × 3 重复 × 3 臂 = 36 行；全局上限 390 请求 |
| 已写入 | **7 行**，全部属于首个任务 `ledger_lock_attestation`；未记录样本错误 |
| 已入账请求 | **56**（Agent 尝试 47、原生摘要 9） |
| 完整供应商 token | **88,966** |
| 冻结独立审计 | `complete=false`；缺 29 行、一个未配平区块，且 manifest 列出三臂安装守卫 |

逐行审计显示 0 重复、0 额外单元；`none/pruner_v1/native_summary` 分别已有 2/2/3 行。当前 manifest 的 `guard_installed_arms` 仍为 `[none, pruner_v1, native_summary]`，与冻结的“全臂不装守卫”不一致。`run_crewai_r20_orderfree_3arm.py` 只在委托的整批 `v17.main` 返回后才调用 `augment_manifest()`；这份部分批没有该收尾，不能通过修改 manifest 来追认。

**禁止直接使用现有 `--resume` 付费续跑。** `run_crewai_handoff_v17.py` 的恢复路径会跳过已有样本，但在第 676 行以零 `used` 新建 `RequestBudget(args.max_api_requests)`，没有将旧 56 次请求扣入累计预算。因此单靠当前代码不能证明跨续跑总数仍 ≤390。冻结 r20 源不得原地改；后续应新版本、新 ID，先对累计预算、逐尝试写入及中断恢复做零 API 负控制，再运行新批。旧 7 行保留为部分批证据，不计入质量或节省结论。
