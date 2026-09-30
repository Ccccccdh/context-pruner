# v38 Django 三组小试：零 API 预检

已使用 `.venv-openhands/Scripts/python.exe` 执行
`run_validation_windows_v38.py --out runs/stage5-openhands/django-16263-v38-pilot-frozen --repeats 1 --check`。
输出目录包含冻结 `manifest.json`、`baseline.json`、测试日志和 Windows 兼容记录；未创建实验样本，未发起模型请求。

| 检查 | 结果 |
|---|---|
| 固定原版公开 aggregation 模块 | 116/116 通过 |
| 宿主应用目标测试后的 aggregation 模块 | 120 项，3 项按预期失败 |
| 宿主参考实现（先前正负门控） | 120/120 通过 |
| 预检时测试修改 Agent 工作区 | 否 |
| Agent 工作区 `tests/aggregation/tests.py` 与上游基线 | SHA-256 相同 |
| Agent 工作区参考补丁、目标补丁 | 均无 |
| 冻结来源文件 | 64 个，预检后复核无哈希漂移 |
| 冻结组别与重复 | `none`、`native_summary`、`pruner_v38`；1 次 |

运行器已把每次模型请求的代码版本检查缩小到四个允许编辑文件，
并在最终比较时只做一次工作区哈希扫描。Windows 兼容层不增加 API
重试。压缩实现仍是固定的 `ContextPrunerCondenserV36`；`pruner_v38`
只是本轮组名。

下一步为 1×1×3 付费小试。需单独授权使用现有环境密钥，向
`https://api.deepseek.com` 发送公开 Django 基线源码、SWE-bench 问题陈述、
Agent 工具结果、压缩摘要及本机工作区路径。每个样本最多 36 次 Agent
请求和 16 次摘要请求；不会发送密钥，也不会覆盖旧结果。若未触发压缩
或质量失败，按选择规则停止扩大实验并保留原始结果。
