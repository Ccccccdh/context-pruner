# v14 多任务确认（3 个留出任务 × 3 重复 × 3 臂）——**验收线未通过：质量在 1/3 任务上掉**

批次 `openai-repo-diagnostic-v14-multitask-confirm-01`；冻结
`integrations/openai_agents/V14_MULTITASK_FREEZE_20261005.json`（SHA256 `04425ae3…`，写一次未改），
修订 `V14_MULTITASK_FREEZE_AMENDMENT_20261005.json`（A1：任务文件的 read_calls 计数更正，**在任何 v14 请求之前**）；
准入记录 `V14_ADMISSION_20261005.json`（规则在请求前冻结，只做适用）。

## 1. 任务选择与准入（4 个候选 → 3 个合格）

四个候选都取自本地 SWE-bench Verified parquet、且**三宿主此前均未引用**（仓库文本扫描 0 命中、无已提取目录）：
`psf__requests-5414`、`psf__requests-2931`、`pallets__flask-5014`、`pydata__xarray-3095`。
只读取源（`git init/remote add/fetch --depth 1/checkout FETCH_HEAD`，未对仓库 add/commit/push、未写上游）在
`.tooling/scratch/<short>` 检出，逐个登记文件级 SHA256。任务形状与 v13 相同：三只读视图、两轮读取共 6 次调用、单行答案。

| 任务 | 实例 | 仓库 | 获取请求 | 逐调用投影 | 守卫后安全候选 | 零 API 门 | 基线 Track A | 准入 |
|---|---|---|---:|---:|---:|---|---|---|
| requests_bad_host_unicode | psf__requests-5414 | psf/requests | 7/12 | 15.59 % | 3 | clean | **FAIL** | ✗ |
| requests_binary_payload | psf__requests-2931 | psf/requests | 7/12 | 14.04 % | 3 | clean | pass | ✓ |
| flask_empty_blueprint_name | pallets__flask-5014 | pallets/flask | 7/12 | 17.62 % | 3 | clean | pass | ✓ |
| xarray_copy_dtype | pydata__xarray-3095 | pydata/xarray | 7/12 | 18.96 % | 3 | clean | pass | ✓ |

被拒任务的原因如实记录：其基线答案 **事实正确**（`cause=_get_idna_encoded_host raises UnicodeError fix=raise InvalidURL`），
但冻结契约要求 `cause=` 内出现 `prepare_url`（发起方）而模型写的是抛出方，因此 `frozen_regex_fullmatch`/`required_terms`
不成立。**这是预注册令牌选择的结果，不是错误答案**；按纪律**不在看到答案后调契约**，故该任务记为"契约不可达、无合格空间"。

## 2. 小试结果（3 任务 × 3 重复 × 3 臂 = 27 样本；207/300 请求，0 触顶）

| 任务 | Track A 基线 | Track A 插件 | Track A 原生 | 插件逐重复节省 | 插件均值 | 原生均值 |
|---|---:|---:|---:|---|---:|---:|
| requests_binary_payload | 3/3 | **3/3** | 3/3 | 8.03 / 8.10 / 8.15 % | **+8.09 %** | −5.17 % |
| flask_empty_blueprint_name | 3/3 | **3/3** | 3/3 | 12.98 / 13.00 / 12.97 % | **+12.98 %** | −9.00 % |
| xarray_copy_dtype | 3/3 | **1/3** | 3/3 | 16.84 / 16.87 / 16.88 % | **+16.87 %** | −13.21 % |
| **合计（9 配对）** | 9/9 | **7/9** | 9/9 | 最小 8.03 %、最大 16.88 % | **+12.65 %（正 9/9）** | −9.13 %（正 0/9） |

- **任务间离散度**：逐任务均值 8.09 % / 12.98 % / 16.87 %，极差 **8.77 个百分点**，三个任务均为正且都高于 3 % 门槛。
- **摘要调用**：每样本上限 3 次（预注册参数并写进冻结），实测共 18 次（原生臂 1/2/3 次），最坏情况下界 216 ≤ 300，未事后截断。
- **Track B**：27 条样本全部通过（诊断副轨），本批不需要语义轨补救。

## 3. 验收线与判定

预注册验收线：**Track A 插件 ≥ 基线（逐任务与合计）且 配对完整总节省 ≥3 %**。
- 成本侧 **满足**（+12.65 %，9/9 为正，逐任务全正）。
- 质量侧 **不满足**：`xarray_copy_dtype` 的插件臂 1/3 vs 基线 3/3。
→ **`acceptance.met = false`；"多任务确认"未达成**，四项结项判据里这一项仍然缺。

## 4. 两处失败的机械定性（模型侧，不是机制侧）

两条失败样本（`xarray_copy_dtype` 的插件 r0/r1）**只违反长度条件**（179 字符 > 160），**必需字面齐备**
（`IndexVariable`、`dtype` 都在），答案内容正确且更细（`cause=IndexVariable.copy passes data through
as_compatible_data, which coerces unicode to object fix=preserve IndexVariable._data …`）。审计的逐边界 presence 台账
**没有任何字面丢失**，不变量五列全绿，指针可逐字节复算 → 与 v12 同一现象：**机制没有把证据搬出可见范围，失败是模型把答案写长了**。

## 5. 独立审计

`experiments/audits/audit_openai_agents_multitask_v14.py`：
- 四个获取批：各 `complete=true`、`errors=[]`、7/12 请求、manifest 一次写全（`manifest_written_in_one_pass=true`，无 `manifest_amendments`）。
- 小试批：**`complete=true`、`errors=[]`、rows=27、207/300 请求、18 次摘要调用**；两轨质量由审计按冻结合同自行计算，
  逐样本边界结构、指针文本重算（允许"未触发即保留原文"或"恰好等于冻结指针"两种，任何第三种改动即报错）、
  presence 台账、配对与回退记账全部重算；**插件行已可直接读到 `exact_duplicate_replacements`（5/6/6）**、
  `exact_duplicate_saved_bytes`、`trigger_gate_triggered_calls`（2/3/6）、`narrow_guard_protected_unit_count`。
- 验收判定由审计独立重算：`track_A_all_tasks=false`、`pooled_saving_at_least_3_percent=true`、`met=false`。

## 6. 本轮修好的 runner 缺陷（付费前）

1. **单臂批次不再崩**：`build_report` 由共享链在运行时打补丁（冻结哈希的共享模块未改），单臂批次不会再抛 `KeyError: paired_n`。
2. **manifest 一次写全**：四个获取批与小试批的 `purpose`、`citable_as_saving=false`、`citable_as_quality_equivalence=false`
   与冻结哈希全部由 runner 自己写入，**没有使用任何事后修订工具**。
3. **持久化字段补齐**：冻结计数器构造器运行时扩展，插件行现在直接带 `exact_duplicate_replacements` /
   `trigger_gate_*` / `narrow_guard_protected_unit_count`，审计可直接断言行内字段。

## 7. 如实记录的局限

- **守卫在这三个任务上为空**：`narrow_guard_protected_unit_count=0`——它们的注册字面都在**工具视图**里，而守卫保护的是**陈述单元**。
  字面保全因此由机制自身不变量 + 审计 presence 台账证明，而不是由守卫证明；这一点不得含糊。
- **每任务重复数只有 3**，任务数只有 3（第 4 个被预注册可达性筛掉），因此本批只能给"3 任务范围内"的证据。
- **不得与其他宿主百分比合并**；`requests_binary_payload`、`flask_empty_blueprint_name`、`xarray_copy_dtype`、
  `requests_bad_host_unicode` 四个实例**自本轮起即为已用**：即使结果不利也不得在其上调参重试，重试须换机制并另选新实例。
- v12/v13 及更早批次的冻结、评分、答案未动、未重打分、未放宽合同。
