# v49 多任务冻结说明（3 实例 × 3 组 × 3 重复 = 27 样本）

本文件替代 [PILOT_PROTOCOL_V48.md](PILOT_PROTOCOL_V48.md) 作为当前批次依据。
v48 已完成的"多重复"（单任务 3 重复）结论保留不变；v49 在**同一协议**下把任务数从 1 扩到 3，
用于回答"跨任务是否仍成立"。

## 1. 三个实例（均在付费前完成零 API 正负门控）

| 任务键 | 实例 | 基线提交 | Django | 可编辑文件 | 回归模块 | 门控（原版 → 参考修复） |
|---|---|---|---|---|---|---|
| `django_referenced_window_wrapping` | `django__django-17084` | `f8c43aca467b…` | 5.0.0a1 | `django/db/models/sql/query.py` | `aggregation` | 125 项 1 error → 125 项全绿 |
| `django_lookup_allowed_foreign_primary` | `django__django-16661` | `d687febce586…` | 5.0.0a1 | `django/contrib/admin/options.py` | `modeladmin` | 163 项 1 failure → 163 项全绿 |
| `django_list_editable_atomicity` | `django__django-16100` | `c6350d594c35…` | 4.2.0a1 | `django/contrib/admin/options.py` | `admin_changelist` | 75 项 1 failure (7 skipped) → 75 项全绿 |

门控记录：`.tooling/gates/v49-<instance>.json`（已哈希进 manifest）。
选型依据：`.tooling/reselection_final.json` 的合格候选池，按组内从新到旧取 16661、16100。
两个新实例的整树导出出处：`.tooling/export_django_django-{16661,16100}.json`（codeload + 提交校验）。

## 2. 与 v48 的差异

| 项 | v48 | v49 |
|---|---|---|
| 任务数 | 1 | **3** |
| 重复数 | 3 | 3（不变） |
| 预期样本 | 9 | **27** |
| 模型、端点、温度、thinking、重试 | — | **完全不变** |
| 三臂定义、工具集、导航、预算策略、提示词、阈值、每样本上限 | — | **完全不变** |
| 评测与审计路径 | — | 按任务分派（每任务用自己的实例目录与回归模块） |

**一处必须说明的非完全一致**：`django_referenced_window_wrapping` 的**研究文件列表**
v49 比 v47/v48 少一项 `tests/aggregation/models.py`（该任务在两版共用同一实例，
但可见研究文件集合并不完全相同）。因此**不能表述为"完全同协议、仅改变任务数量"**；
跨 v48/v49 比较同一任务时，这一差异必须一并考虑。复算依据：`.tooling/recount_v48_v49.py`。

**协议冻结的局限（如实记录）**：本批的 manifest 在 `--check` 时生成，而
`run_validation_v49.py` 与 `PILOT_PROTOCOL_V49.md` 在**付费运行之后**又被修改过，
导致首次审计哈希断言失败，需重新冻结 manifest 才通过。
**重新冻结只能证明"当前文件互相一致"，不能证明"协议在付费运行前已冻结"。**
下一轮起应额外保存一份**不可覆盖的预运行 manifest 快照 + 变更记录**。

## 3. 预算

每样本 ≤36 Agent 请求与 ≤16 摘要请求。

| 项 | 数值 |
|---|---|
| 单样本实测参考（v47/v48） | 约 20–31 次请求 / 0.14–0.58 M tokens |
| 27 样本预计 | **约 621 次请求 / 约 8.7 M tokens**（按每重复 69 次推算） |
| 硬上限 | **1404 次请求**（156 × 9） |
| 预计时长 | 3.5–4 小时 |

## 4. 统计口径

沿用 v48：`summary.json` 输出 `per_repeat_paired_savings`（逐重复逐任务）与 `dispersion`
（每臂每任务的逐次配对节省、均值、样本标准差、极值、双方成功次数）。

**主统计**：全部样本的配对节省率（含失败、未压缩与负收益）。
**跨任务报告**：逐任务分别给出，**不得合并成一个总节省率**；
如需综合，只能给描述性汇总并注明任务间异质性。
**子集**："双方成功且 API 正常"仅作敏感性说明，不得替换主指标。

## 5. 本轮能回答与不能回答

**能回答**
- 插件在 3 个不同实例、3 个不同 Django 世代上是否都保持正收益与低离散度；
- v48 单任务观察到的 +56.97% ± 7.72% 是否跨任务稳定；
- 原生摘要在 v48 表现出的高方差（26.99%）（含一次负值）是否跨任务重现。

**不能回答**
- 仍限于 Django 单项目、SWE-bench Verified 单数据集，不得外推为"通用有效"；
- 不得宣称跨 Agent 有效（本轮仍是 OpenHands 单宿主）；
- 峰值与开销必须单独报告：v48 峰值 −52.94% 达标，但 v47 仅 −17.7%，需逐任务复核。

## 6. 本批已修复的两个缺陷（否则批次会在首个请求前中止）

1. **多任务 TASKS 绑定**：`validation_tasks_v49` 原先把共享模块的 `TASKS` 在每次调用后还原成
   v45 的旧表，导致 `verification_tool_django_v49` 直接读 `TASKS` 时认为所有 v49 任务名未知，
   首个样本即 `AssertionError` 停批。现改为把 `TASKS` 永久绑定到 v49 全部任务。
2. **验证工具的任务来源**：新增 `verification_tool_django_v49.py`（仅把
   `validation_tasks_v45` 导入改为 `validation_tasks_v49`），因为原 v45 工具硬编码了 v45 的 TASKS。

两次失败样本已保留在批次目录外的诊断记录中，未被计入本批统计。

## 7. 环境前提

本批仍在 DSH 沙箱内运行，编辑器历史缓存写入失败（`AgentErrorEvent`）仍可能存在且**组间不对称**
（v45 0/2/2、v46 3/3/4、v47 2/2/1、v48 2/0/2）。审计会逐样本统计该事件数，
报告中将如实标注，且不与普通终端批次混算。
