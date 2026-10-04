# CrewAI r4 三任务三重复开发批

本批沿用已通过小试的 r4 交接事实/格式分离与至多一次无工具补救机制，使用相同 DeepSeek 模型、任务输入、三臂预算和验收规则。小试 `crewai-two-role-r4-pilot-01` 在 `incident_triage` 上三臂各 3/3 成功，插件配对完整总 token 节省 +10.45%（n=3）；本批专门检验把任务扩至 `release_readiness`、`customer_migration` 后，质量与节省是否仍稳定。三个任务都为模拟运维流程，不能据此推断生产代理或真实代码修复效果。

运行矩阵：3 任务 × 3 次固定同输入重复 × `none` / `native_summary` / `pruner_v1` = 27 样本、9 对。臂顺序按 `(task_index + repeat) mod 3` 轮换。模型 `deepseek-v4-flash`、endpoint `https://api.deepseek.com`、禁用隐藏 thinking；provider soft/target/hard 为 1200/900/3000 token。每 Agent 输出最多 512 token；摘要输出最多 1024 token、每角色最多 4 次；全批所有 Agent、摘要和交接补救请求合计上限 **200 次**，失败请求同样计数。

质量同时报告：调查角色首轮事实齐全、首轮严格格式、补救次数与结果、决策角色最终答案、两角色各一次指定工具调用、工具结构安全。事实齐全的格式变体可零 API 规范化，缺事实须额外计费补救，不得本地编造。主成本为每臂完整总 token，所有失败和触顶样本均进入 9 对配对结果；只有质量不劣时才解释节省。

新入口 `run_crewai_handoff_v4_multitask.py` 调用冻结的 r4 `run_case`，保留每个本地样本失败并继续下一臂；仅提供商连接、认证、余额、限流错误或全局请求上限会中止批次。这样一次格式/补救失败不会使其余配对缺失。独立审计 `audit_crewai_handoff_v4.py` 核对冻结源码、任务、协议、参数、27 样本栅格、原始/规范化/补救质量、工具轨迹、所有请求与 token。

付费前：先用 `--plan` 核对矩阵，再在 `--mode mock` 下完成 27 样本并用派生 mock 冻结审计；确认 7 个当前 r4 单测、入口 `py_compile`、所有冻结 SHA256 匹配；把新批次目录写进 `.gitignore` 白名单。随后才运行 API 批次。若 mock 的质量或审计不全，不发送付费请求。
