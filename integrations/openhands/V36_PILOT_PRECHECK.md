# v36 外部任务离线门控与付费小试状态（2026-09-28）

已从公开 [SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) 固定 `pytest-dev__pytest-10356`，仓库基线提交 `3c1534944cbd34e8a41bc9e76818018fadefc9a1`。数据集 Parquet SHA-256 为 `030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25`。官方修复与测试补丁留在宿主侧，不进入 Agent 工作区。

隔离 Python 3.13 环境的离线门控：原版公开回归 88 passed、1 xfailed，加入目标测试后 1 failed、88 passed、1 xfailed；官方参考补丁加入目标测试后 89 passed、1 xfailed。门控原件见 `.tooling/validation-v36-reference-frozen/results.json`。三组运行器预检与 65 项源哈希冻结均通过。

首次启动因新增 pytest 外发上下文不在此前授权范围内，被自动审批拒绝，未产生付费请求。用户随后明确授权向 `https://api.deepseek.com` 发送本轮公开 pytest 基线源码、问题陈述、工具结果、压缩摘要及本机工作区路径，并使用现有环境密钥运行 1 任务 × 1 次 × 3 组付费小试（每样本最多 36 次 Agent、16 次摘要请求）。获授权后从原冻结目录启动，3/3 样本已完成，模型请求已停止。没有输出密钥，也没有覆盖旧结果。

结果和停止结论见 [v36 小试复盘](VALIDATION_V36_REVIEW.md)及 `runs/stage5-openhands/pytest-mark-mro-v36-pilot/REPORT.md`。三组目标测试均未通过；插件没有压缩，单对输入节省率 -8.71%。按预定规则停止扩大该任务。本地验收只覆盖选定测试，未运行完整上游测试或官方 Docker harness。
