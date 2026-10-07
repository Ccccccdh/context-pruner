# CrewAI r30 全新任务确认结果

本批在 r29 输出合同和三臂运行规则冻结后，使用两项未参与开发的新合成任务前瞻确认。任务分别有 3 个和 5 个来源；每任务 3 重复 × 3 臂，共 18/18 个付费样本。最终冻结与零 API 演练草稿逐字节一致，SHA256 为 `84c16d7b8e2660cfaa28ffd564e50a2ee5718d72ae006efbaa061747fe6ef682`。零 API 18 格网格及独立审计通过；正式批独立审计为 `complete=true`、`errors=[]`、`acceptance_met=true`，148/300 个持久化供应商请求槽。

| 新任务 | 三臂来源覆盖 | 三臂严格/语义成功 | 无压缩 token | Pruner token | Pruner 配对差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 三来源构件签名发布 `artifact_signing_publication` | 各 3/3 | 各 3/3 | 28,819 | 19,949 | +3,227 / +3,353 / +2,290 |
| 五来源副本重建审核 `replica_rebuild_pause` | 各 3/3 | 各 3/3 | 46,029 | 31,816 | +5,248 / +5,297 / +3,668 |

全批完整供应商 token 为无压缩 74,848、Pruner 51,765、原生摘要 96,948。Pruner **6/6 配对为正**，比无压缩少 23,083 token；本批池化描述性差额为 **30.84%**，逐任务分别为 30.78% 和 30.88%。供应商请求数分别为 42、44、62，已包含输出层的纠正请求与原生摘要请求。Pruner 实际压缩 31 次。来源覆盖、严格答案、语义答案和预注册成本门均通过；工具组恢复失败与守卫触顶为 0。

全臂输出合同在正式批没有修改任何模型文本。Pruner 的 `artifact_signing_publication` 第 3 次重复发生 1 次已计费有界纠正：模型初答使用 `artifacts=9`，缺少严格字段 `artifact_count 9`；提示只列缺失字段名，不给目标数值，随后模型自行补齐。独立审计从模型原文复核了这次请求。故本批的成本收益已经包含这一次额外请求，质量通过也没有依赖事后人工改判。

**结论：r29 的开发效果在 r30 的两项全新合成任务上得到一次前瞻复现，满足本批预注册验收。** 但审计仍标记 `can_be_quoted_as_saving=false`：这只证明固定 CrewAI 两角色宿主配置、同一合成任务族内的可重复性，尚无自然任务或跨 Agent 的外部效度；任务数仅 2，每任务仅 3 重复。不能把 30.84% 说成插件的通用节省率，也不能把 r28 失败追溯改判。

下一步应转入**自然任务或真实载荷**的独立开发门：先选已有客观答案与来源覆盖可核的任务，离线固定评判和额外调用预算，再做小规模新任务确认。保持 r30 原始任务、冻结、结果和审计不可改写；若自然任务失败，报告失败并分析机制，不在同题上追着调参重跑。

证据：`runs/stage5-crewai/crewai-r30-output-contract-confirm-01/{manifest.json,results.jsonl,request-slots.jsonl,audit.json}`；协议：`integrations/crewai/PROTOCOL_R30_OUTPUT_CONFIRM_20261007.md`；冻结：`integrations/crewai/PRE_RUN_FREEZE_R30_OUTPUT_CONFIRM_01.json`；任务：`tasks/stage5_autogen/natural_tasks_r30_fresh.json`。
