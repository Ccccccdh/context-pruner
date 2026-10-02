# v52 参考化压缩批次（3 实例 × 3 组 × 3 重复 = 27 样本）

冻结说明。v52 只改**一处机制**：插件臂用 `ContextPrunerCondenserV51`（证据引用化）替代 v42（内嵌源码正文）。

## 1. 为什么改（依据 `V49_COMPRESSION_DIAGNOSTIC.md`，全部零 API）

v49 的 27 个样本揭示：**净输入由"是否触发压缩"决定，而非任务难度**。

| 样本 | 压缩次数 | 摘要字符 | 插件 tokens | 基线 tokens | 净节省 |
|---|---:|---:|---:|---:|---:|
| atomicity r1/r2/r3 | **0** | 0 | 49.8k | 49.8k | **+0.1% / +2.4% / −0.0%** |
| lookup r1 | **4** | 77,351 | 555,259 | 231,468 | **−139.9%** |
| lookup r2 | 2 | 48,340 | 523,019 | 756,746 | +30.9% |
| lookup r3 | **0** | 0 | 249,739 | 180,716 | −38.2% |
| referenced r1 | 2 | 58,466 | 546,707 | 263,468 | **−107.5%** |
| referenced r2 | 1 | 29,331 | 334,449 | 139,160 | **−140.3%** |
| referenced r3 | **0** | 0 | 141,541 | 364,104 | **+61.1%** |

- **未触发压缩 → 与基线持平**；**触发压缩 → 普遍大幅变负**；
- 原生摘要臂**同样模式**（−207.1% / −71.1%）→ 不是 v42 独有；
- 摘要分节占比：`SELECTED OBSERVED CODE` 占 **66%–86%**（如 referenced r1 = 50,500/58,466）。

**结论**：保留性没问题（五项必需标记 v42 全保留），问题是**为了不丢信息，把源码正文又抄了一遍**——
而这些正文本可用 `scoped_editor view` 按摘要里已有的 `path + view_range` 重新读取。

## 2. v52 的唯一改动

`context_pruner/adapters/openhands_v51.py`：**被选证据单元以引用列出，而非粘贴正文**。

保持不变（与 v42 逐项一致）：
- trigger / target / hard 预算（20k / 16k / 28k）与视图裁剪；
- 受保护最近编辑窗口、证据排序（含"已编辑符号"加权）；
- 受保护契约 + 当前状态注入；
- 结构校验（`enforce_properties`）与"拒绝截断必需上下文"；
- 全部审计字段，另加 `reference_only_chars` / `body_chars_avoided`。

**未包含"提高阈值"**——按 `V48_V49_DIAG_REVIEW.md` 第 3.4 条，阈值应作为**单独消融**，不与本次改动混评。

## 3. 离线回放验证（付费前已完成，零 API）

`integrations/openhands/replay_v51_reference_only.py` 对 v49 的 9 条插件轨迹逐条回放：

| 样本 | 适配器 | 压缩次数 | 摘要字符 | 被遗忘事件 | 峰值 tokens | 必需标记 |
|---|---|---:|---:|---:|---:|---|
| referenced r1 | v42 | 2 | **58,084** | 67 | 19,734 | 全保留 |
| referenced r1 | **v51** | **1** | **4,637** | 40 | **18,955** | **全保留** |
| lookup r2 | v42 | 2 | 48,512 | 77 | 19,803 | 全保留 |
| lookup r2 | **v51** | **1** | **9,018** | 60 | 19,803 | **全保留** |
| referenced r2 | v42 | 1 | 29,014 | 36 | 19,487 | 全保留 |
| referenced r2 | **v51** | 1 | **8,194** | 36 | 19,487 | **全保留** |

- 保留性零损失；注入体积降至 **1/6 ~ 1/3**；峰值下降；**压缩次数 2→1**（摘要变小后不再迅速重新越阈值）。
- **回放只证明"保留不变、注入更小、峰值更低"，不证明整任务输入下降或质量提升**——后者必须由本付费批次验证。

## 4. 任务与门控

三个实例与门控记录完全沿用 v49，付费前已复验：

| 任务键 | 实例 | Django | 可编辑文件 | 回归模块 | 门控（原版 → 参考） |
|---|---|---|---|---|---|
| `django_referenced_window_wrapping` | django-17084 | 5.0.0a1 | `db/models/sql/query.py` | `aggregation` | 125 项 1 error → 全绿 |
| `django_lookup_allowed_foreign_primary` | django-16661 | 5.0.0a1 | `contrib/admin/options.py` | `modeladmin` | 163 项 1 failure → 全绿 |
| `django_list_editable_atomicity` | django-16100 | 4.2.0a1 | `contrib/admin/options.py` | `admin_changelist` | 75 项 1 failure (7 skipped) → 全绿 |

门控记录 `.tooling/gates/v49-*.json`（已哈希入 manifest）。v52 的 `--check --repeats 3` 已复验通过，
manifest **105 项哈希零不匹配**（含 `openhands_v51.py`）。

## 5. 规模与预算

| 项 | 数值 |
|---|---|
| 样本数 | **27**（3 任务 × 3 组 × 3 重复） |
| 每样本上限 | ≤36 Agent ＋ ≤16 摘要 |
| 预计请求 | 约 **500–620** 次（v49 实测 494 次 ledger 调用） |
| 硬上限 | **1404** 次 |
| 输出目录 | `runs/stage5-openhands/django-multitask-v52-pilot-01`（不覆盖任何旧结果） |

端点 `https://api.deepseek.com`，模型 `openai/deepseek-v4-flash`，温度 0，thinking 关闭，0 重试。
出站内容仅公开问题描述、公开源码、工具结果与压缩摘要；宿主补丁与参考修复不进入请求。

## 6. 判定口径

沿用 v49：逐任务逐重复配对节省、均值、样本标准差、极值、双方成功次数；
主统计含全部失败样本；"双方成功且 API 正常"只作敏感性说明。
**跨任务不得合并成总节省率。**

**本轮要回答的唯一问题**：把正文换成引用后，
（a）触发压缩的样本是否仍明显劣于自身基线；
（b）压缩次数与摘要体积是否如回放所示下降；
（c）成功率（v49 插件 6/9）是否改善。

**不能回答**：提高阈值的效果（未包含）、跨宿主有效性、恢复工具效果。

## 7. 预注册的停止与解读规则

- 余额或连接异常**立即停批**，保留失败样本，不用成功补跑覆盖；
- 若插件仍全部失败但压缩体积如预期下降，结论是"**摘要瘦身不足**"，下一步再单独测阈值消融；
- 若触发的样本不再显著变负，则"**内嵌正文是主因**"得到支持，可冻结 v53 做多重复确认；
- 不得把本批与 v45–v49 的数字合并。

## 8. 环境前提

本批在 DSH 沙箱内运行（与前几批一致），编辑器历史缓存写入失败可能存在且组间不对称；
审计会逐样本统计 `AgentErrorEvent` 并在报告中标注。若要与 v26–v43 普通终端批次比较，
需改在普通 PowerShell 窗口运行。

## 9. 本批运行中的一次中断与修复（如实记录）

首次启动后，运行在完成 **2 个样本**（`r1-referenced_window` 的 `none` 与 `native_summary`）后中止，
`EXIT: 1`，异常为：

```
File "integrations/openhands/run_validation_v52.py", line 375, in main
    if arm == 'native_summary' else ContextPrunerCondenserV42(
NameError: name 'ContextPrunerCondenserV42' is not defined. Did you mean: 'ContextPrunerCondenserV51'?
```

**原因是我自己的改造失误**：把 v49 复制为 v52 时，我用批量替换把 `pruner_v42` → `pruner_v51`，
但**类名实例化处 `ContextPrunerCondenserV42(` 不在匹配范围内**（后面是左括号而非引号），
而 import 已被替换为 `ContextPrunerCondenserV51`，于是插件臂一构造就抛 `NameError`。

**处理**：修正该行 → 重新冻结 manifest（运行器源码哈希已变）→ 以 `--resume` 继续。
已完成的 2 个样本**不含插件臂**（只有 `none` 与 `native_summary`），
因此**其结果有效并保留**，插件臂的 9 个样本随后正常生成。

**教训（写入下一轮检查清单）**：版本改名必须**编译后运行一次 `--check`**，
并确认三臂都能实例化；仅靠 `py_compile` 无法发现运行期 `NameError`，因为该分支只在构造插件臂时执行。

