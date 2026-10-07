# CrewAI r9 质量不劣勘误（2026-10-05）

本文件更正两处跨批总结的表述；不修改 r9–r13 的运行结果、冻结文件、审计或原文评分。

## 原文问题

- `runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-01/RESULTS.md` §6 说“r9/r10/r12/r13 的配对正收益都伴随质量下降”。
- `integrations/crewai/R14_FIRST_ROLE_UNCOMPRESSED_UPPER_BOUND.md` §6 第 4 条说“r9–r13 五批的插件配对正收益全部伴随质量下降”。

两句都把 **r9** 误归入“质量下降”。权威证据 `runs/stage5-crewai/crewai-two-role-r9-multitask-01/audit.json` 为 `complete: true`、`freeze_checked: true`、`errors: []`、27/27 行；其严格成功为插件 **6/9 = 基线 6/9**，语义成功 **9/9 = 9/9**，首轮事实齐全 **9/9 = 9/9**，`quality_non_inferior: true`、`first_pass_non_inferior: true`。插件完整总 token 配对节省均值 **+8.009%**，正 **8/9**。

## 正确表述

**r9 在自身预注册的三套质量门下“不劣于基线”，且配对成本为正；r10、r12、r13 未复现这一组合，r11 为负收益且质量崩塌。** r9 仍不足以证明跨任务稳定收益：任务间均值离散 **22.5023 个百分点**，收益集中在 `queue_backlog_replay`（+22.2466%），`region_failover` 均值 **−0.2557%**；仅 3 个任务×3 重复，且全部为合成双角色流程。

因此，“当前没有可推广、跨任务稳定的质量等价正收益证据”可以作为收口判断；“r9 本身质量下降”或“所有正收益批都质量下降”不成立。r9 的严格 6/9 含冻结判据的词边界问题，但旧批不能据此事后改为 9/9。

上述两处原文保持不动，供冻结与历史引用完整性核查。今后引用 r9 时应同时引用本勘误和 r9 的原始 `RESULTS.md` / `audit.json`。
