# OpenAI Agents v9：长基线一侧的守卫口径修正（适用边界双侧实测）

## 目的

v7 只实测了边界的一侧：短基线（Django 冻结任务，基线 **K = 2** 次调用）上可省略轮数 `max(0, K − 1 − N) = 0`，插件载荷与基线**逐字节相同**、配对节省 **0.0000 %**。

v8 用**预注册的只读调查协议**把基线拉长到 **K = 7**（零 API 栅格实测），插件确实进入省略路径（15 个源、632 行），但每次调用都以 `missing_protected_unit` **整份回退**：守卫把**任务陈述的全部文本单元**列为受保护单元，而冻结缩减里还有一条「丢弃本次已逐字送达过的消息单元」的规则，请求那一行同时出现在陈述与请求消息中 → 被去重丢掉 → 守卫报缺失。两条规则互相矛盾；同一守卫在 **v7 短基线上也报过 5 次** `protected_item_dropped`（当时无可省略轮次，故无害，v7 仍报载荷与基线相同）。

**v9 只修这一处分类口径**：受保护集合收敛为「**承载注册字面的消息单元** + 注册字面单元 + 注册字面承载行 + 工具输出结构」。

## 改动与不变

**只改**（`experiments/runners/openai_agents_long_baseline_boundary_v9.py`）：

- `NarrowGuardBoundaryFilter.protected_unit_sha256` = 任务陈述中**含至少一个已注册字面**的单元（`protectable_units()`），不再是全部陈述单元；
- 记录 `narrow_guard_*` 计数（受保护单元数、被移出的陈述单元数、全文陈述单元数），供证据层与审计核对「确实收窄了」。

**一律不变**（门控以 `assertIs` 逐项断言对象同一性）：

- 行范围省略本体（`SpanRetentionFilter._elide`）、结构检查（`_structural_violation`）、字面生存守卫（`_carrier_loss`）、工具组基线（`_group_change`）、去重规则、指针完备性、整份回退语义、阈值与预算（soft/target/hard 2000/1500/6000、输出 1024）；
- 任务输入（v8 的冻结问题 + 请求 + 一条协议消息）、注册表、证据 schema 只加字段不改语义；
- v1–v8 全部冻结文件不改。

## 两条必测断言（门控通过证据）

`tests/test_openai_agents_long_baseline_boundary_v9` 逐条实现并在报告中给出数字：

1. **正保护**（`test_literal_carriers_are_never_below_the_baseline_arm`）：逐样本、逐模型边界、逐字面，插件的 `registered_literal_counts` **不低于同一边界上基线臂的计数**；最终输入同样以基线最终输入为下界；`aggregation_decision` 在携带注册源码的边界上必须在场。
2. **负控制**（`test_negative_control_repeated_literal_unit_is_kept_and_guarded`）：构造一个**承载注册字面（`ordering`）且在两处重复出现**的单元，断言 (i) 过滤后该字面的出现次数**不低于**过滤前（去重命中也不丢），(ii) 随后把承载文本真的移走时守卫**仍然拒绝**、`task_anchor_restore_failures ≥ 1` 且原因含 `registered_literal`。即：本次改动只修分类矛盾，没有把任务陈述的保护整体关掉。

## 矩阵、预算与判定

- 任务 `django_long_investigation`（与 Django 同一公开问题与同三条公开源码范围，另加一条只读调查协议消息）；矩阵 1 任务 × 3 重复 × 3 臂 = **9 样本**；批次 ID `openai-repo-diagnostic-v9-long-baseline-boundary`。
- 模型 `deepseek-v4-flash`、endpoint `https://api.deepseek.com`、temperature 0、隐藏 thinking 关闭；`--max-output-tokens 1024`、`--max-turns 10`、`--max-api-requests 200`；三臂轮换、逐样本落盘；`data_class = public_source_diagnostic`。
- 预注册验收线：**插件严格质量 ≥ 基线 且 完整总 token 配对均值为正**。
- **付费条件**：零 API 栅格必须先给出正投影（插件省略更早轮次、全部守卫计数为零）；投影不为正就不发任何请求。

## 边界命题（双侧）

> 设基线完成同任务需 **K** 次模型调用、最近 **N** 轮逐字保留，则插件可省略轮数 = `max(0, K − 1 − N)`。
> **短侧（`K ≤ N + 1`）**：v7 实测 —— 无可省略轮次、载荷与基线逐字节相同、节省恒 **0 %**；且任何**改变模型可见文本**的机制在该区间必然为负（多走一轮 = +2,335.8 input token = v5 输入回退 99.85%）。
> **长侧（`K > N + 1`）**：v9 实测 —— 可省略轮数 = K − 2，插件压缩更早的轮次而不动最新轮。

## 运行

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_openai_agents_long_baseline_boundary_v9 -v
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v9 --offline-gate --confirm-send-public-source --scenarios django_long_investigation --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-turns 10 --max-api-requests 200 --experiment-id openai-repo-diagnostic-v9-long-baseline-boundary
& .\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_repo_diagnostic_v9 runs/stage5-openai-agents-api/openai-repo-diagnostic-v9-long-baseline-boundary --freeze integrations/openai_agents/REPO_DIAGNOSTIC_V9_LONG_BASELINE_BOUNDARY_FREEZE.json
```

## 边界

- 本批只有 1 个任务、3 个重复、9 个样本；任务输入与 v8 相同，不是未参与调参的盲测确认批；禁止与其他宿主的百分比合并。
- **离线桩的 K 与真实 K 可能不同**：v6 已证明零 API 假模型按固定轨迹作答、不能预测真实轮次；因此长侧的"能取胜"必须由付费批次复核，本批先给离线投影。
- 守卫收窄只影响**任务陈述单元**这一类；注册字面单元、字面承载行与工具输出结构仍受保护，负控制给出该项证据。
- 证据只有哈希、长度、布尔、计数与行区间边界，不保存原文；审计通过不等于语义等价或成本有效。
- v1–v8 源码、协议、冻结与结果目录保持不变；v9 使用新文件、新协议、新冻结与新批次 ID。
