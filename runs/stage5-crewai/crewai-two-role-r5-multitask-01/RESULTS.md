# CrewAI r5 双角色多任务（前瞻语义等价判据首次付费批）

状态：2026-10-04 完成 27/27 样本、**177 次 API 请求**，独立冻结审计 `complete: true`，但审计同时列出 **9 条错误**（插件臂 9 个样本都只产生 1 个角色输出）。**本批不是有效的质量等价节省证据，也不能用于成本比较**；它暴露的是**答案合同与补救输入的缺陷**，已由 r6、r7 修正并另批重跑（`crewai-two-role-r6-multitask-01`、`crewai-two-role-r7-multitask-01`）。

冻结文件 `integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R5_MULTITASK_01.json`；协议 `PILOT_PROTOCOL_TWO_ROLE_R5_MULTITASK_01.md`；判据 `experiments/runners/crewai_semantic_equivalence_v5.py`；审计 `experiments/audits/audit_crewai_handoff_v5.py`。r3/r4 未被重打分。

## 结果（全部样本，含失败）

| 臂 | 严格质量 | 语义质量 | 完整总 token | 压缩事件 | 补救调用 | 未完成样本 |
|---|---:|---:|---:|---:|---:|---:|
| 无压缩 `none` | 3/9 | 6/9 | 74,445 | 0 | 0 | 0 |
| 原生摘要 `native_summary` | 3/9 | 6/9 | 94,858 | 0 | 0 | 0 |
| 插件 `pruner_v1` | **0/9** | **0/9** | 54,437（**不完整**） | 36 | 9 | **9** |

审计 `errors`（全部为插件臂）：`wrong number of role outputs` × 9。原因：这 9 个样本的交接缺少**第一个工具**的事实，触发一次无工具补救；而 r5 的补救提示只携带**最后一个**工具观测，模型无法补出早期事实，于是抛出 `HandoffRecoveryError: recovery still lacks required facts`，批次按"保留失败并继续"的策略跳过该样本的决策角色。

因此插件臂的 token 只覆盖角色 1（部分调用），配对均值 +26.50%（逐任务 +43.13%、+18.97%、+17.41%）**没有可比性**，不得引用为节省；任务间离散度 25.72 个百分点在这里同样无意义。

## 失败类型统计（54 角色输出中的门控命中）

- `handoff_missing_fact`（首轮交接缺事实）9
- `recovery:handoff_missing_fact`（补救仍缺事实）9、`recovery:handoff_forbidden_fact` 8
- `answer_length`（超过冻结 300 字符行长）15：`none`/`native_summary` 的自然答案约 350–400 字符，合同行长上限不可满足
- `answer_prefix` 9、`answer_missing_fact` 9、`answer_wrong_decision` 9（都来自被中止的插件样本与行长失败样本）

## 结论与边界

1. **本批不可用于任何成本或质量结论。** 插件臂 9/9 样本不完整，`none`/`native_summary` 也被自身合同缺陷（行长、要求改写字面事实）压低到 3/9 严格通过。
2. 缺陷属于**合同设计**，不属于压缩机制：触发门 9/9 触发、36 次压缩事件全部发生，节省量级与 r4 诊断（`R4_SAVINGS_DROP_DIAGNOSIS.md`）一致。
3. r5 的判据（v5）仍然保留：语义等价判据在这种合同下也无法救回"缺事实"的样本，这一点符合设计（缺事实永不被规范化补救），但当时无法与合同缺陷区分。
4. 旧批不重打分：r5 的 `results.jsonl`、`audit.json`、manifest 与冻结哈希保持原样。
