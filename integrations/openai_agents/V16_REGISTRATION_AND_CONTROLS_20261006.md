# OpenAI Agents v16：开发任务注册、零 API 负控制、缓存计量与美元成本（2026-10-06）

**本轮全零 API**：未发任何请求、未冻结任何文件、未启动三臂、未改动 v12–v15 任何冻结/评分/答案。
机器输出：`V16_SOURCE_REGISTRATION_20261006.json`、`V16_NEGATIVE_CONTROL_20261006.json`、
`V16_CACHE_METERING_AND_COST_20261006.json`。

## 1. 开发任务注册（2/3，1 个按门拒绝）

来源读取方式：对本地 partial clone 做**只读 git 对象读取**（`git rev-parse <commit>:<path>` 取 blob 哈希、
`git show <commit>:<path>` 取内容），不 fetch、不写、不 add/commit。

| 任务 ID | 实例 | base_commit | 视图（文件/行/blob） | 合同（issue / 冻结正则） | 缺陷在基线中 |
|---|---|---|---|---|---|
| `django_sqlite_version_floor` | `django__django-13821` | `e64c1d8055…` | `sqlite3/base.py` 60–75 `ab4ea704`、`features.py` 30–50 `3348256c`、`features.py` 60–95 `3348256c` | `django-13821` / `cause=.*check_sqlite_version.* fix=.*3.9.0.*` | **present**（基线 `(3, 8, 3)`） |
| `django_orderedset_reversed` | `django__django-14089` | `d01709aae2…` | `datastructures.py` 5–37 / 42–75 / 265–335，blob `871b0167` | `django-14089` / `cause=.*OrderedSet.* fix=.*__reversed__.*` | **absent**（基线无 `__reversed__`，即缺陷=缺失） |

每个任务另登记：`problem_statement_sha256`（与预选清单记录值逐位一致）、三视图的**文件级与视图级 SHA256**、
全臂共用的结构化合同（JSON `issue/cause/fix/facts` + 确定性 `RESULT` 渲染）、必需事实清单
（`13821`: `check_sqlite_version`/`3.8.3`/`3.9.0`；`14089`: `OrderedSet`/`__iter__`/`__reversed__`）、
读取协议（三视图各读两次＝6 次读 + 1 次作答＝7 次模型调用）。

**被拒绝的候选（未递补）**：`django__django-13410`。其公开问题描述的是 posix `lock()` 返回
`fcntl.lockf()` 的结果（成功返回 None ⇒ 恒假），但固定 base 提交的 `locks.py` **根本没有 `lockf` 调用**：
posix 分支已经是 `fcntl.flock(_fd(f), flags)` + `return ret == 0`，回退分支按设计返回 False/True
（115 行、blob `c46b00b9…`、`lockf` 出现 0 次、`flock` 2 次）。缺陷不在基线 → 无法从这些视图登记可辩护的
cause/fix 合同 → **记为拒绝**，且**未**从预选清单递补、**未**动用封存确认候选、**未**动用 v15 未花费的 `13512`。
因此开发任务为 **2/3**，需用户裁决后再登记第三个（新候选＝新决定）。

## 2. 零 API 负控制（全部通过）

**结构化输出 SDK 接线**：`build_agent()` 用冻结的 pydantic `output_type`（字段 `issue/cause/fix/facts`）构造
真实 SDK `Agent`（`agent_has_output_type=true`，无请求）；`run_structured()` 走共享 renderer + `settle()`。
现场路径 `sdk_runner()` 已接线但**未接通供应商**（本轮不付费），这一点在记录里显式标注。

| 控制 | 结果 |
|---|---|
| 首答合格 | 接受，**1 次**调用 |
| 缺字段 → 一次重试修复 | 接受，**2 次**调用（token 两次合计） |
| 多字段 → 修复 | 接受，2 次 |
| 类型错（`facts` 非列表）→ 修复 | 接受，2 次 |
| **超长两次**（179→仍超长） | **关门**：拒绝，理由 `retry_rejected:over_160_chars`，**2 次**调用，无第三次 |
| 重试抛未知异常 | 拒绝且**账本不完整**（`accounting_complete=false`），2 次调用 |

**观测到的最大供应商调用次数 = 2**（即"至多一次计费重试"），全部 6 项 `ok=true`。

**独立审计负控制**：`clean_record` 通过；四类坏记录**全部被拒**——改视图哈希（`view_hash_mismatch`）、
删合同字段（`contract_field_missing:required_facts` + `pinned_required_facts_empty`）、篡改固定值
（`pinned_max_answer_chars_changed`）、伪造 `complete`（`forged_complete_with_errors` 等 5 条）。全部 `ok=true`。

## 3. 缓存计量落地（字段 + 时间戳）

`extract_cache_usage()` 逐调用记录 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` /
`call_timestamp_utc` / `off_peak`；`attach_cache_to_manifest()` 汇总进 manifest。**fail-closed**：供应商未给字段时
记为 `cache_fields_present=false`、`cache_usage_unknown=true`，合计保持 **null 而非 0**（控制已验证：
缺失字段的调用不会被算成 0 命中）。非高峰判定按 UTC 周一至周五 01:00–04:00 与 06:00–10:00 以外
（控制：peak 判定 false、off-peak 判定 true）。v14/v15 批**早于**该计量，故其缓存拆分不可得（见 §4 双界）。

## 4. v14 / v15 美元成本表（官方价：miss $0.15、hit $0.003、output $0.60 / 1M；非高峰五折）

| 批次 | 样本/臂 | 输入 token | 输出 token | 全 miss | 假想全 hit | 差额 |
|---|---:|---:|---:|---:|---:|---:|
| v14（3 任务×3 重复×3 臂） | 9 | 489,841 | 8,992 | **$0.0789** | **$0.0069** | 输入侧 **~11×**（hit 比 miss 便宜 50×，输出比 hit 贵 **200×**） |
| v15（3 获取任务，单臂） | 3 | 40,324 | 586 | **$0.0064** | **$0.0005** | 同上 |

- v15 逐批：`field_error_messages_copy` 7 请求 / 11,753 token / $0.0018；`method_decorator_partial` 7 / 13,577 / $0.0021；
  `textchoices_string_value` 8 / 15,580 / $0.0024（与 v15 结果文档逐项一致）。
- **v14 插件臂 vs 基线臂的输出差额**：9 对合计 **+15 输出 token = +$0.000009**；其中两条**超长样本**
  （`xarray_copy_dtype` r0/r1，179 字符）各 **+10 输出 token**。
- **两点必须同时说**：①单位价上输出确是缓存命中输入的 **200 倍**，"答复变长"因此在**单位价**意义上同时是成本事件；
  ②在本轮实际规模上该差额是 **+15 token ≈ $0.000009**，**不构成成本驱动**——v14 的成本主体是输入侧
  （$0.079 全 miss）。用户裁决 `≤160` 时，两者都成立，本文件不替用户下结论。

## 5. 现在还缺什么才能跑三臂

1. **第三个开发任务**：`13410` 被源码缺陷门拒绝后开发集为 2/3；需用户裁决是否登记新候选（新 ID、新登记、
   预选清单不递补）。
2. **合同可得性证明**：现有证据只能说明"模型在 v16 结构化 JSON 合同下能否稳定给出合格短答"**尚未证明**
   （v15 已付费的 11964 显示首答 166 字符、且重试逐字相同）；三臂前需要一次**有界获取批**取得真实载荷，
   并由零 API replay 门与负控制复核。
3. **冻结**：新 ID 的冻结须写死视图/blob 哈希、事实清单、合同、请求与摘要上限、失败线与"忠实报告"条款；
   本文件**不是**冻结。
4. **缓存字段进入批次 manifest**：计量模块已就位，但必须由未来批次的运行器实际写入并接受审计重算。
5. 三臂的准入线（严格轨 ≥ 基线 ∧ 配对 ≥3%）与 v13/v14 一致；**成本正数必须与严格质量缺口同时报告**。
