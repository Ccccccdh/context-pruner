# OpenAI Agents v10：真实载荷 replay 与"按工具调用序号"的新近度（收口验证）

## 这一轮要回答的问题

v9 的付费批次否证了自己的离线投影（投影 **+34.15 %**、实跑 **−0.000004 %**）。根因不是机制，而是**夹具**：离线桩在工具项之间插入了 assistant 消息，于是桩的 `turn_count = 6`，而真实 SDK 载荷的工具项**连续**、`turn_count ≡ 1`。**零 API 桩不只可能错"轮次数"，还可能错"载荷结构"。**

v10 因此做两件事：

1. **把离线评估改建在已记录的真实载荷上（replay）**：不再自造桩，而是用公开确定的输入（冻结任务历史 + 注册读取器返回的三段源码视图）逐边界重建载荷，并对**已付费 v9 批次的 `input-evidence` 记录**断言结构一致；
2. 用它检验 v9 唯一未被否证的方向：把新近度计量单位从"连续工具组"改为**工具调用序号**（实现为被检验假设）。

## 机制（`experiments/runners/openai_agents_long_baseline_boundary_v10.py`）

```
elidable(call) := 载荷中更新的工具调用数量 >= RECENT_TOOL_CALLS_KEPT   （冻结 = 1）
```

- 一个"工具调用"= 一次 `function_call` 与应答它的 `function_call_output`（同一 `call_id`）；
- 只保留**最新 1 次调用**逐字不动，更早的调用进入既有省略路径；
- **其余一律不动**：v9 的收窄守卫分类、v6 的行范围省略、字面生存守卫、指针完备性、结构检查、整份回退、阈值与预算。门控以对象同一性断言（`_elide` / `_structural_violation` / `_carrier_loss` / `_group_change` 仍是冻结函数对象）。

## 真实载荷夹具与结构断言

`experiments/runners/openai_agents_real_payload_replay_v10.py` 重建 7 个边界，并对 v9 付费批次的记录逐边界断言：

| 断言 | 结果 |
|---|---|
| `message_items` 与记录一致 | 3 / 3 / 3 / 3 / 3 / 3 / 3 ✅ |
| `item_count` 与记录一致 | 3 / 5 / 7 / 9 / 11 / 13 / 15 ✅ |
| `tool_outputs` 与记录一致 | 0 / 1 / 2 / 3 / 4 / 5 / 6 ✅ |
| 工具项**恰好一个连续段**（`tool_runs ≤ 1`） | ✅（记录 `turn_count = 0/1/1/1/1/1/1`） |
| 每个边界恰好新增一对 call/output | ✅ |
| 记录的 `elidable_indices` | 全为空 ✅（与 v9 实测一致） |

字节数不与记录相同：付费批次走的是 chat-completions 传输，夹具按 Responses 条目列表构造；**replay 度量的是结构与载荷内容，不是传输字节**，该差异在夹具里明示。

## Replay 结果与投影（零 API）

`integrations/openai_agents/V10_REPLAY_PROJECTION.json`：

| 指标 | 值 |
|---|---:|
| 夹具结构一致 | **ok，0 处不符** |
| 边界的可省略索引非空 | **是**（如末边界 `[3,4,5,6,7,8,9,10,11,12]`；对比：连续工具组口径下为空） |
| 省略源 / 行 | **15 / 632** |
| `task_anchor_restore_failures` / `budget_fallbacks` | **0 / 0** |
| 字面出现次数回退 | **0 处** |
| 载荷字节 | 基线 **63,026** → 插件 **38,569** |
| **投影节省** | **+38.8 %（字节口径）** |
| 预注册判定 | **positive** → 允许付费 |

## 付费（若投影为正）

批次 `openai-repo-diagnostic-v10-tool-call-recency-boundary`，1 任务 × 3 重复 × 3 臂 = 9 样本；模型 `deepseek-v4-flash`、温度 0、禁隐藏 thinking、provider soft/target/hard 2000/1500/6000、`--max-output-tokens 1024`、`--max-turns 10`、`--max-api-requests 200`；三臂轮换、逐样本落盘、`data_class = public_source_diagnostic`。

验收线（预注册，与 v5–v9 相同）：**插件严格质量 ≥ 基线 且 配对完整总 token 均值为正**。

## 运行

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_openai_agents_tool_call_recency_v10 -v
& .\.venv\Scripts\python.exe -m experiments.runners.openai_agents_replay_projection_v10
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v10 --plan --scenarios django_long_investigation --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-turns 10 --max-api-requests 200 --experiment-id openai-repo-diagnostic-v10-tool-call-recency-boundary
& .\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_repo_diagnostic_v9 runs/stage5-openai-agents-api/openai-repo-diagnostic-v10-tool-call-recency-boundary --freeze integrations/openai_agents/REPO_DIAGNOSTIC_V10_TOOL_CALL_RECENCY_FREEZE.json --batch-name openai-repo-diagnostic-v10-tool-call-recency-boundary
```

## 方法论教训（对三宿主适用）

> **零 API 桩不仅能错"轮次数"，还能错"载荷结构"。此后所有离线门控必须建立在已记录的真实载荷上（replay），而不是自造桩。**

v9 的 +34.15 % 假正投影就是这条规则的代价：桩插入 assistant 消息 → `turn_count = 6` → 可省略 5 轮；真实载荷 `turn_count ≡ 1` → 可省略 0 轮。

## 边界

- 只有 1 个任务、3 个重复、9 个样本；任务输入与 v8/v9 相同，不是未参与调参的盲测确认批；禁止与其他宿主百分比合并。
- replay 用的是**已记录的真实载荷结构**，但仍然是公开输入的**重建**；重建与记录一致的结构断言在门控里，字节差异已说明。
- 按迭代上限，v10 是本轮最后一个版本，不再开 v11；除非 replay 投影为正，否则不发任何付费请求。
