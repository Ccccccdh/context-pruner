# CrewAI r13 工具循环终止对照（零 API replay）：插件视图没有缺少基线视图的任何内容

对象：`runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-01`（48/48 样本、325 请求、审计 `not_yet_valid`、预注册结局 `tool_sequence_not_caused_by_compression`）。本文件回答 r13 收口前唯一未做的对照：**在第一角色停止工具循环的那次模型调用上，插件臂视图是否缺少无压缩臂视图拥有的任何内容？** 全部为零 API replay，**不发任何付费请求、不重跑、不重打分**。复算入口：

```powershell
& .\.venv-crewai\Scripts\python.exe .tooling\control_crewai_r13_tool_loop.py
```

产物：批次目录 `R13_TOOL_LOOP_CONTROL.json`（含逐样本逐边界明细）。

## 1. 预注册结局：命中第一种

**`outcome = same_content_at_decision_boundary`** —— 逐边界比对未发现插件视图缺少基线的任何冻结内容。因此：

> **r13 那 4 个失败是模型提前结束工具循环（模型行为），与压缩无关。**

**残留不确定性（如实标注）**：本对照是**从已记录数据的 replay**，不是"先验完整前缀（完全不压缩）"的**付费对照批**。它证明的是"在冻结任务内容这一层，两臂视图逐边界相同"，而不是"任何压缩都不可能影响模型行为"。

## 2. Layer A：已记录数据的逐样本不变量（r13 全部 16 个插件样本）

| 不变量 | 数值 | 它排除什么 |
|---|---:|---|
| `required_fact_whole_prefix_fallbacks` 合计 | **0** | 钉住后仍有必需字面量缺失（会触发计数型全前缀回退） |
| 出现 `crewai_group_restore_failure_count` 的样本数 | **0** | 受保护工具轮在恢复时丢失 |
| 出现 `crewai_unmatched_call_count` 的样本数 | **0** | 工具调用/结果配对错位 |
| `recency_omitted_tool_rounds_total` 合计 | **0** | 任何被省略的工具轮 |
| 首轮事实齐全 | **16/16** | 事实在首轮交接中丢失 |
| tier-1 证据钉住合计 | 60 | （正向证据）冻结工具证据被逐字钉回 |
| 含 tier-2 任务合同钉住的样本 | 12/16（无该钉住的 4 个是 `shard_split/0..3`） | 见下方互补关系说明 |

**tier-2 的互补关系（修正 r13 `RESULTS.md` 中的一处计数错误）**：tier-2 只在"投递视图已不含任务陈述"时才触发，因此**没有** tier-2 钉住 ≠ 合同缺失——它表示任务陈述本身存活下来了。r13 的 16 个插件样本中，12 个由 tier-2 钉住、另 4 个（全部 `shard_split`）的任务陈述直接存活；**没有任何样本两条路径都没走到**。因此"按顺序对每个可用工具各调用一次"这条指令在 16/16 的决定性调用里都到达了模型。

r13 的 4 个 `tool_sequence` 失败样本，逐样本：

| 任务 | 重复 | 第一角色记录序列 | 调用数 | tier-1 | tier-2 | 回退 | omitted | 首轮齐全 | 答案决策 | 答案缺事实 |
|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| `credential_rotation` | 1 | `get_credential_status`→`verify_failover_region`→`check_trust_chain`（顺序不符） | 3 | 4 | 3 | 0 | 0 | true | `renew`（正确） | 无 |
| `credential_rotation` | 3 | `get_credential_status`→`verify_failover_region`（缺第三个） | 2 | 3 | 2 | 0 | 0 | true | `renew`（正确） | 无 |
| `window_gate` | 1 | `check_window_lock`→`inspect_change_guard`（缺第三个） | 2 | 3 | 2 | 0 | 0 | true | `clear`（正确） | 无 |
| `window_gate` | 2 | `check_window_lock`（缺后两个） | 1 | 2 | 1 | 0 | 0 | true | `clear`（正确） | 无 |

（上表由 `.tooling/control_crewai_r13_tool_loop.py` 的 Layer A 直接从批次 `results.jsonl` 生成，逐样本明细见 `R13_TOOL_LOOP_CONTROL.json`。）

同批历史数据的同类样本（同类不变量，供交叉核对）：r12 的插件质量失败共 **9/16**，分成两类——**5 个工具序列被打断**（`credential_rotation` 重复 1/2/3 顺序不符、`window_gate` 重复 1/2 只调 1 个工具）与 **4 个决策者回抄合同模板**（`credential_rotation/0`、`batch_replay` 重复 0/1/2）。前 5 个的 `recency_omitted_tool_rounds_total` 分别为 3/3/3/0/0（K=1 时更早轮次被省略），但**回退 0、首轮齐全 16/16**——即"轮次被省略"与"事实缺失"是两件独立的事，v9 钉住为后者兜底；后 4 个的工具序列完好，属合同措辞问题（r13 已消除）。r10 的 16 个同类（工具序列被打断）样本**没有逐行工具轨记录**（该字段是 r12 才加入记录口径的），因此**不进入 replay**，按计数保留（`samples_without_recorded_trace = 16`），不当作证据使用。

## 3. Layer B：确定性结构化 replay 的逐边界内容集

对 r13 的 4 个失败样本，用**冻结任务数据 + 冻结 r9 钉住中间件 + 冻结 v13 结构规则 + 真实适配器 `_protect_tool_groups`/`_restore_tool_groups`** 重建第一角色在"无压缩臂"与"插件臂"视图，并比较内容集。插件臂按**该样本实际记录到的调用序列**重建（含顺序不符的真实形态），基线臂按冻结顺序跑满三轮。

| 任务 | 插件记录序列 | 未执行轮次 | 插件视图工具名 | 插件视图工具证据 | 插件视图 handle_facts | 插件视图合同锚点 | 缺少基线内容 |
|---|---|---|---|---|---|---|---|
| `credential_rotation` | 3 个（顺序不符） | 无 | 3/3 | 3/3 | 3/3 | `HANDOFF`/`Thought:` | **无** |
| `credential_rotation` | 2 个 | `check_trust_chain` | 3/3 | 3/3 | 3/3 | 同上 | **无** |
| `window_gate` | 2 个 | `verify_risk_review` | 3/3 | 3/3 | 3/3 | 同上 | **无** |
| `window_gate` | 1 个 | `inspect_change_guard`、`verify_risk_review` | 3/3 | 3/3 | 3/3 | 同上 | **无** |

要点：

- **工具名 3/3 始终可见**（宿主系统提示逐字包含全部工具及其 schema），**三个工具的证据片段 3/3 始终可见**（受保护轮逐字投递：`protected = seen`、`readded = 0`），**`handle_facts` 3/3 全部命中**，**输出合同锚点（`HANDOFF`、`Thought:`）命中**；
- **基线视图更大并不是"内容更多"**：基线是 70 条消息 / 约 5.3k 估计 token，插件是 8–9 条消息 / 约 1.8–2.1k。差额**全部来自历史垫料（filler）被合法压缩**——那正是节省本身，不是被扣下的要求；
- 唯一"基线有而插件没有"的东西是**该角色自己从未调用过的工具的证据**（表中"未执行轮次"列）。这属于模型没调用它，而不是压缩扣下它：同一视图里那个工具的**名字与 schema 都在**（`tool_names_all_present = true`）。
- `estimated_tokens` 用同一 `estimate_tokens` 计数，仅作规模参考；结论不依赖它。

## 4. 归因与收口

| 问题 | 证据 | 归属 |
|---|---|---|
| 插件视图是否缺少基线拥有的冻结内容？ | Layer A 全部不变量 + Layer B 逐边界内容集（缺少项为空） | **否** |
| 那 4 个 `tool_sequence` 失败的成因 | 工具名/schema/证据/合同事实与指令全部可见、回退 0、恢复失败 0，但角色只调 1–2 个工具且答案正确 | **模型提前结束工具循环（模型行为）** |
| 是否与压缩无关？ | 受保护工具轮 `omitted = 0`、`protected = seen`、回退 0；模型在**内容不缺**的前提下停止 | **是（在"冻结任务内容"这一层）** |

**边界**：本对照是 replay 证据，不是付费的"先验完整前缀"对照；它排除的是"压缩扣下了第一角色所需的冻结内容"，不能排除"视图更短这一事实本身影响了模型倾向"。后者需要一个不压缩第一角色前缀的付费臂，本轮按约定**不做、不付费**。

## 5. 与 r13 RESULTS.md 的关系

- r13 `RESULTS.md` §4 已写"这 4 个失败属于模型提前结束工具循环"；本文件把该结论从**推断**升级为**逐边界证据**，并修正其中一处计数错误：**tier-2 合同钉住在 16 个插件样本中是 12 个**（另 4 个任务陈述直接存活），而非"全部 ≥1"；
- r13 的评分、冻结与 `audit.json` **未改动、未重跑、未重打分**；本文件只新增只读对照证据。
