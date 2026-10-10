# v12 决策边界对照（零 API，2026-10-05）

批次 `openai-repo-diagnostic-v12-exact-duplicate-dev-01`，任务 `django_long_investigation`，重复 0；本文件由 `experiments/runners/openai_agents_decision_boundary_v12.py` 生成，零付费请求（paid_requests: 0）。

冻结答案规则：`RESULT issue=django-16263 cause=.*existing_annotations.*subquery.* fix=.*referenced.*`；必需字面 ['existing_annotations', 'subquery', 'referenced']。

## 逐边界表

| 边界 | 无压缩字节（实测） | 插件字节（实测） | 字节差 | 工具调用/输出 | 配对 | 消息单元两侧逐字 | 事实命中（无压缩→插件） | 差异来源 |
|---:|---:|---:|---:|---|---|---|---|---|
| 0 | 2171 | 2171 | 0 | 0/0 | 一致 | 逐字存在 | existing_annotations 0→0，subquery 0→0，referenced 1→1 | none (payload byte-identical) |
| 1 | 3427 | 3427 | 0 | 1/1 | 一致 | 逐字存在 | existing_annotations 0→0，subquery 0→0，referenced 1→1 | none (payload byte-identical) |
| 2 | 8752 | 8752 | 0 | 2/2 | 一致 | 逐字存在 | existing_annotations 3→3，subquery 7→7，referenced 1→1 | none (payload byte-identical) |
| 3 | 9889 | 9889 | 0 | 3/3 | 一致 | 逐字存在 | existing_annotations 3→3，subquery 7→7，referenced 1→1 | none (payload byte-identical) |
| 4 | 11145 | 10373 | 772 | 4/4 | 一致 | 逐字存在 | existing_annotations 3→3，subquery 7→7，referenced 1→1 | older exact-duplicate outputs replaced by a 157-character pointer: item#0 |
| 5 | 16470 | 10857 | 5613 | 5/5 | 一致 | 逐字存在 | existing_annotations 6→3，subquery 14→7，referenced 1→1 | older exact-duplicate outputs replaced by a 157-character pointer: item#0, item#1 |
| 6 | 17607 | 11340 | 6267 | 6/6 | 一致 | 逐字存在 | existing_annotations 6→3，subquery 14→7，referenced 1→1 | older exact-duplicate outputs replaced by a 157-character pointer: item#0, item#1, item#2 |

合计：无压缩 69461 字节 → 插件 56809 字节，实测差 12652 字节（重建差 12635，v12 预筛投影差 12635）。

## 机械判定

- 判定：**model-side-wording**（机制侧不成立，属模型侧措辞）
- 判定依据 1：插件答复长度 208 字符，上限 160；三条并列格式条件里只有 ['at_most_160_chars'] 不成立（前缀与单行都成立）。
- 判定依据 2：必需字面在插件答复中齐备 True，冻结正则整串匹配 True——**没有任何必需事实丢失**，失败不是「说不出来」而是「说得更长」。
- 判定依据 3：格式约束（`RESULT issue=django-16263 cause=<decision> fix=<pruning_guard>` 与「恰好一行 / RESULT 开头 / 最多 160 字符」）写在 agent instructions 里，不是模型输入项，项级过滤器无法触及；七个边界上两臂的消息单元逐字相同、事实 presence 无一丢失。
- 判定依据 4：本模块重实现的 `answer_correct`/`final_format_correct` 规则对三臂全部复现原记录（none/native_summary 通过、pruner_v1 不通过）——reproduces_recorded_flags: [True, True, True]。
- 严格口径按原判保留：插件该样本仍记为失败，+14.63 % 仍不称为有效节省。

## 不变量门（预筛 §2 第 4 条所需的机械证据）

| 边界 | 任务约束（注册字面 presence 两侧） | 当前证据（唯一源全文副本） | 工具组哈希（去 id） | 恢复 / 整份回退 | 源定位 |
|---:|---|---|---|---|---|
| 0 | 一致（aggregation_decision=无，filter_references=有，ordering_references=有，other_annotation_references=有） | 唯一源 0，全文副本 无压缩 0 / 插件 0，缺全文 无 | 相同 (`c1149c4296…`) | 失败 0 / 整份回退 0 / 预算回退 0，reason 空 | 无指针（该边界没有可替换项） |
| 1 | 一致（aggregation_decision=无，filter_references=有，ordering_references=有，other_annotation_references=有） | 唯一源 1，全文副本 无压缩 1 / 插件 1，缺全文 无 | 相同 (`ee3857268a…`) | 失败 0 / 整份回退 0 / 预算回退 0，reason 空 | 无指针（该边界没有可替换项） |
| 2 | 一致（aggregation_decision=有，filter_references=有，ordering_references=有，other_annotation_references=有） | 唯一源 2，全文副本 无压缩 2 / 插件 2，缺全文 无 | 相同 (`d8a50ad2af…`) | 失败 0 / 整份回退 0 / 预算回退 0，reason 空 | 无指针（该边界没有可替换项） |
| 3 | 一致（aggregation_decision=有，filter_references=有，ordering_references=有，other_annotation_references=有） | 唯一源 3，全文副本 无压缩 3 / 插件 3，缺全文 无 | 相同 (`916db6a276…`) | 失败 0 / 整份回退 0 / 预算回退 0，reason 空 | 无指针（该边界没有可替换项） |
| 4 | 一致（aggregation_decision=有，filter_references=有，ordering_references=有，other_annotation_references=有） | 唯一源 3，全文副本 无压缩 3 / 插件 3，缺全文 无 | 相同 (`87f1969f1f…`) | 失败 0 / 整份回退 0 / 预算回退 0，reason 空 | 指针 item#[0] → 更新全文副本（pointer_completeness/coverage 记录均为 True/True） |
| 5 | 一致（aggregation_decision=有，filter_references=有，ordering_references=有，other_annotation_references=有） | 唯一源 3，全文副本 无压缩 3 / 插件 3，缺全文 无 | 相同 (`8a501a968e…`) | 失败 0 / 整份回退 0 / 预算回退 0，reason 空 | 指针 item#[0, 1] → 更新全文副本（pointer_completeness/coverage 记录均为 True/True） |
| 6 | 一致（aggregation_decision=有，filter_references=有，ordering_references=有，other_annotation_references=有） | 唯一源 3，全文副本 无压缩 3 / 插件 3，缺全文 无 | 相同 (`ec9dc3d113…`) | 失败 0 / 整份回退 0 / 预算回退 0，reason 空 | 指针 item#[0, 1, 2] → 更新全文副本（pointer_completeness/coverage 记录均为 True/True） |

- **不变量门结论：全部保持**——丢失约束 无、丢失证据 无、工具组哈希变化 无、未记账回退 无、源定位断裂 无。
- 记账（插件臂，行级记录）：`task_anchor_restore_failures=0`、`task_restore_fallbacks=0`、`budget_fallbacks=0`、逐调用账 [0, 0, 0, 0, 0, 0, 0]、字面守卫回退 0、回退原因表 空、未配对调用 0；`restore_failure_is_whole_prefix_fallback=True` 表示一旦发生恢复失败就是整份前缀回退，而本次一个都没发生。
- 规则（预筛 §2 第 4 条）：**任一丢失或未记账回退必须先修机制，不得改判质量**；反过来，门干净也不等于质量可接受。本次门干净 → 不需要修机制；严格失败仍按原判保留。

## 守卫后安全候选与质量的关系（预筛反例）

- 本批（v12）在**守卫后**安全候选 = **3**（判定边界上被替换的旧逐字重复输出；跨边界替换事件 6 次，为同一批副本的重复计数，按判定边界计数以避免重复）。守卫检查：registered literal presence kept for every label at every boundary；every distinct source keeps one full copy；id-free tool-group hash unchanged；source pointer resolves to a newer full copy in the same payload；no restore / whole-payload fallback, none unaccounted。
- 同批结果：配对完整总 token **+14.63 %**、严格质量 **0/1 vs baseline 1/1**。→ **安全候选非零 + 不变量门干净，仍然出现严格质量不达标**；这与预筛列出的 v9（候选 0 → 收益≈0）、v10（候选 5 → +27.68 % / 质量 0/3）、v11（候选 2 → +5.81 % / 质量 2/3）构成同一族反例。
- 因此：**候选数量只能当「筛除」信号（为零就别付费），不能当「收益可接受」的预测**；本宿主上「守卫后安全候选 × 质量」的关系仍未被证明，只有在预注册双轨质量下实测才能给出结论。

## 验证与反例

- 基线臂：记录里每个工具输出项的 `call_id`/`output_sha256`/`output_chars` 与按冻结任务注册表和公开基线源码重建的文本逐项相等（7/7 边界）。
- 插件臂：每个输出项要么与被替换前原文逐字节相同，要么**恰好等于**按冻结规则与「本臂自己的最新副本 call_id + 源码 SHA256」生成的 157 字符指针；重算的 SHA256 与记录完全一致，被替换位置集合也与记录中「字符数与基线不同」的位置集合逐一相同。
- 两臂的工具调用 id 天然不同（各自独立运行），因此跨臂只比较内容：保留项逐字节相同，指针项指向同一 payload 内仍然存在的全文副本，且不再有任何「未配对调用」。
- 反例（`tests/test_openai_agents_decision_boundary_v12.py`）：把一个**唯一**证据项也换成指针时，presence 检查必须报出丢失组件——该检查是 fail-closed，不是恒真断言。

## 未测量（不作断言）

- 重复次数：该批 1 任务 × 1 重复，措辞差异只在一条样本上观察到，不能作为稳定性结论。
- 命中次数下降的影响：边界 5/6 的 `existing_annotations` 6→3、`subquery` 14→7 是「旧重复副本变指针」的必然结果，presence 仍在；模型是否**用到**那些重复副本无法由本证据判定。
- 语义等价质量口径：本模块只做机械对照，未做语义判定，也没有改动任何评分。
- 传输字节：表中字节是记录的真实 chat-completions 传输值；重建字节用 Responses 项列表度量，两者绝对量不同（实测差 12652 vs 重建差 12635），但两个度量各自内部一致（见合计行）。

## 检查

- all_checks_ok: True；problems: []
