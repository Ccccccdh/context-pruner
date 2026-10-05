# OpenAI Agents v7：适用边界（只压更早的轮次）

## 目的

前三版各失败在不同方向：v4 丢字面（插件 0/3）、v5 全文钉住（质量 3/3 但配对完整总 token **−79.79 %**）、v6 指针式行范围省略（**−176.36 %**，插件 0/3，模型看到指针后重新调用工具取回被省略的行）。v5 的零 API 成本分解（`integrations/openai_agents/V5_COST_REGRESSION_DIAGNOSIS.md`）已经把方向钉死：

| 项 | provider input token | 占输入回退 |
|---|---:|---:|
| 多走一次模型调用 | **+2,335.8** | **99.85 %** |
| 被组基线假阳性挡掉的压缩 | +140.2 | 6.0 % |
| 固定文本（重传字节） | **−136.7** | −5.8 % |

即：**多走一轮的代价比整条基线（完整总 2,962 token）还大**，而重传字节其实是有利的。所以本轮**不发明第七种压缩写法**，而是把"插件在什么条件下才可能赢"写成一个可证伪的边界，并用 v7 去测边界靠下的一侧。

## 机制（`experiments/runners/openai_agents_recency_boundary_v7.py`）

v7 = v6 的缩减**原样复用** + 一条新近度策略：

```
elidable(turn) := newest_turn - turn >= RECENT_TURNS_KEPT
```

- 冻常常数 **`RECENT_TURNS_KEPT = 1`**：只允许压缩比最新工具轮**至少早一轮**的工具轮；最新的那一轮（当前轮正在推理的证据）与所有模型消息**逐字不动**。
- 缩减本体（注册字面行逐字保留 + 头部行 + 其余行区间换指针）、字面生存守卫、指针完整性检查、整份回退计数、注册表、证据层全部沿用 v6，**未改判定语义**。
- `N = 1` 是**对插件最有利**的设置：N 越大插件越弱。若插件在最有利的 N 下都赢不了短任务，任何更大的 N 都不会更好。取 N=1 也让零 API 门控在冻结载荷上仍能真正跑到（并检验）省略路径。
- 在**基线 2 次调用**的 Django 任务上，第 1 次调用没有可省略的历史、第 2 次调用的唯一候选输出属于最新轮 → **一次都不会省略，插件发出的载荷与基线逐字节相同**（审计用两臂逐边界的 `input_sha256` 比对）。

## 边界命题（可证伪）

> 设基线臂在 K 次模型调用内完成任务、最近 N 轮逐字保留。则插件可省略的轮数 = `max(0, K − 1 − N)`。
> **当 `K <= N + 1` 时，插件一个字节都省不下来**：它只能发出与基线相同的载荷（收益恒为 0）。
> 而任何**改变模型可见文本**的机制在该区间**必然为负**：v5 的成本分解给出多走一轮 = +2,335.8 token（占回退 99.85 %），大于整条基线（2,962 token）；重传字节只有 −136.7 token。

推论按 K 排列：

| 基线 K（轮） | 可省略轮数（N=1） | 预期 |
|---:|---:|---|
| 2 | 0 | 插件与基线载荷相同，**必然 0 %**（Django 本批） |
| 3 | 1 | 可省略 1 轮；但 v5/v6 都实测多走 1–3 轮，**仍为负** |
| ≫ 3（长会话，旧轮占绝大部分载荷） | K−2 | **唯一可能取胜的区间** |

已知的"能取胜"参考点是 OpenHands 的 933,719 token 前缀（全程 +15.51 %，见 `integrations/openhands/PILOT_PROTOCOL_V58.md` 与 v58 `RESULTS.md`）：那里的基线有大量远早于当前轮的历史，压缩不改变当前轮可见内容。

## 零 API 门控要证明什么

`tests.test_openai_agents_evidence_safe_retention_v7`：

1. 真实 SDK + 本地假模型跑完整 Django 栅格（3 重复 × 3 臂，无网络）；
2. **插件输入与基线逐样本相等**、`recency_boundary_elided_sources == 0`、两臂逐边界 `input_sha256` 相同、`projectable_saving_rate == 0`；
3. 被保护轮次（最新一轮）在每次调用里都**没有任何输出被替换**（逐条目检查 `output_elided` 全 False）；
4. 守卫与恢复计数全零（`task_anchor_restore_failures`、`task_restore_fallbacks`、`budget_fallbacks`）；
5. **省略路径没有被这条策略废掉**：构造 5 个工具轮的载荷，只有更早的轮被省略、最新轮保持原样、指针与行区间划分 100 % 通过、字面行全保留；
6. 命题自检：`K <= N + 1` → `plugin_wins_possible = False`；`K` 更大 → `True`；
7. 审计器自检：篡改边界列、伪造载荷一致性、抹掉字面、篡改质量或恢复失败数都必须被判不通过。

**离线投影为负（0 %）时禁止发起任何付费请求**，本批次因此只产出离线结果。

## 冻结矩阵与预算（若未来要付费）

- 任务：`django_count_annotations`；矩阵 1 × 3 × 3 = 9 样本；批次 ID `openai-repo-diagnostic-v7-applicability-boundary`。
- 模型 `deepseek-v4-flash`、endpoint `https://api.deepseek.com`、temperature 0、隐藏 thinking 关闭；provider soft/target/hard = 2000/1500/6000；`--max-output-tokens 1024`；全局请求上限 100；三臂轮换；逐样本落盘；`data_class = public_source_diagnostic`。

## 判定口径

- 质量与 v1–v6 相同的冻结严格合同；成本为**完整总 token 配对均值**（含每一次摘要辅助请求与失败尝试），全部样本照实报告。
- 预注册验收线不变：**插件严格质量 ≥ 基线 且 完整总 token 配对均值为正**。
- 本版的核心交付不是"再赢一次"，而是**边界**：`integrations/openai_agents/PLUGIN_APPLICABILITY_BOUNDARY.md`。

## 运行

```powershell
# 零 API 门控（本轮实际执行）
& .\.venv\Scripts\python.exe -m unittest tests.test_openai_agents_evidence_safe_retention_v7 -v

# 离线栅格（同样零 API）
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v7 --offline-gate --confirm-send-public-source --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v7-applicability-boundary

# 付费（本轮未执行：离线投影为 0 %，按约定不发请求）
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v7 --plan --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v7-applicability-boundary
& .\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_repo_diagnostic_v7 runs/stage5-openai-agents-api/openai-repo-diagnostic-v7-applicability-boundary --freeze integrations/openai_agents/REPO_DIAGNOSTIC_V7_APPLICABILITY_BOUNDARY_FREEZE.json
```

## 边界

- 本版只测 **OpenAI Agents 宿主的 Django 短任务**；未测其他宿主、未测长会话上的 v7（因此"长会话能赢"仍是推论，不是本批的测量），也不与 CrewAI / OpenHands 的百分比合并。
- N=1 是冻结常量；N 更大的设置只会使插件更弱，本批未逐一测量。
- v1–v6 源码、协议、冻结与结果目录保持不变；v7 使用新文件、新协议、新冻结与新批次 ID。
