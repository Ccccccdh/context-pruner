# CrewAI r27 可变来源开发小试结果：未过质量门

冻结批 `crewai-r27-variable-source-dev-01` 完成 18/18 样本，使用 145/270 次供应商请求。独立审计 `complete=true`、`errors=[]`，但 `acceptance_met=false`，**不得引用成本差作为有效节省**。

| 任务 | 来源覆盖（三臂） | 严格成功：无压缩 / Pruner / 原生 | Pruner 配对 token 差 | 判定 |
| --- | ---: | ---: | ---: | --- |
| 三来源货运发运 | 各 3/3 | 0/3、0/3、0/3 | +3,305 / +3,749 / +3,402 | 质量门失败 |
| 五来源集群转移 | 各 3/3 | 2/3、3/3、3/3 | +4,870 / +5,272 / +5,542 | 该任务通过 |

总量上无压缩 76,183 token、Pruner 50,043、原生摘要 98,757；6/6 配对成本为正，但货运任务三臂质量全失败，故不能称本批实现了有效节省。Pruner 请求 42 次、无压缩 42 次；全批工具组恢复失败 0，表明任务尺度 K 修复了零 API 演练中五来源任务的恢复故障。

货运失败是**任务合同与判定器冲突**：决定词 `SHIP` 被真实工具字段 `manifest_sha=ship-6ab1` 带入交接，判定器报 `handoff_decision_leak`。九个货运样本均在这一项失败，其他答案事实与来源覆盖均通过。零 API mock 的交接只拼了最低必需事实，没有携带该 SHA 字段，因此没有暴露冲突。这是门控缺口；不能事后改判，也不在同任务换决定词后重跑付费批。

下一步是在新任务上增加静态门控：遍历全部工具结果、历史约束和可生成的交接字段，预先排除决定词与证据值的词面冲突；mock 交接应含更完整的工具证据。然后重新冻结、进行新的多任务确认。r27 本身保留为失败的开发证据。

证据：`runs/stage5-crewai/crewai-r27-variable-source-dev-01/{manifest.json,results.jsonl,request-slots.jsonl,audit.json}`；冻结：`integrations/crewai/PRE_RUN_FREEZE_R27_VARIABLE_DEV_01.json`。
