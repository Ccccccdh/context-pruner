# v13 Stage C：留出任务确认（`psf__requests-1766`，1 任务 × 3 重复 × 3 臂）

批次 `openai-repo-diagnostic-v13-requests1766-confirm-01`；冻结文件
`integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json`（SHA256
`e1f5bb3a…3d694`，manifest 记录且审计重算一致）；配套修订
`V13_REQUESTS1766_FREEZE_AMENDMENT_20261005.json`（SHA256 `c700ed22…cdf7b`）。

机制：**冻结的 v12 逐字重复输出规则**（`experiments/runners/openai_agents_exact_duplicate_replay_v12.py`，
哈希未变），由新文件 `openai_agents_requests_duplicate_filter_v13.py` 把它接到本任务的注册上；
归约、结构不变量、字面守卫、指针检查与整份回退全部按恒等继承，零 API 门用 `is` 断言。

取源（Stage 0）：**允许的只读取源**在 `.tooling/scratch/psf-requests-1766` 检出固定 commit
（`git init`/`remote add`/`fetch --depth 1`/`checkout FETCH_HEAD`，HEAD 校验等于 base_commit；
未对本仓库 `add/commit/push`、未写上游、未走 `raw.githubusercontent.com`）。四个基线文件与批次实际使用的
副本逐字节一致，冻结与 manifest 记录的哈希因此是被验证而非被替换；登记见
`integrations/openai_agents/V13_REQUESTS1766_STAGE0_REGISTRATION_20261005.json`
（"基线源码＝机制输入" 与 "判据元数据＝只登记哈希" 分开登记）。

## 1. 三臂结果（9 样本，全部计入，无失败、无触顶）

| 臂 | Track A（严格，验收主轨） | Track B（语义，诊断副轨） | 逐重复完整总 token | 配对节省 |
|---|---|---:|---:|---:|
| `none` | **3/3** | 3/3 | 21,295 / 21,295 / 21,295 | — |
| `pruner_v1` | **3/3** | 3/3 | 17,567 / 17,565 / 17,570 | **+17.51 % / +17.52 % / +17.49 %（正 3/3，均值 +17.50 %）** |
| `native_summary` | 3/3 | 3/3 | 22,118 / 22,182 / 22,184 | −3.86 % / −4.17 % / −4.17 %（正 0/3，均值 −4.07 %） |

- 请求数：**66 / 66**（冻结上限 66；基线 21 + 插件 21 + 原生 21+3 次摘要）。
- 插件答复长度 139 字符、基线 145 字符；**三条并列的严格条件（前缀、单行、≤160）与冻结正则整串匹配全部通过**。
- 逐重复配对节省三对全为正，离散度极小（17.49 %–17.52 %）。
- 插件实际缩减：`selective_retention_saved_bytes_total = 13,558`（每重复一致）；审计从**已记录载荷**重算出的替换次数 = 6（跨边界；判定边界上 3 次逐字重复替换）。

**验收线（预注册）**：Track A 插件 ≥ 基线 **且** 配对完整总 token 节省 ≥ 3 % → **两条都满足**。
这是本宿主第一次在**未参与调参的留出任务**上通过预注册验收线。

## 2. 独立审计

`experiments/audits/audit_openai_agents_requests_confirm_v13.py` → `audit.json`：
`complete=true`、`errors=[]`、`acceptance.met=true`、`rows=9`、`api_request_attempts=66`。
硬字段：冻结哈希重算、矩阵完整性、上限、**两轨质量由审计自行按冻结合同计算**（不读样本自报标志）、
逐重复配对节省重算、逐样本边界结构、插件项**指针文本重算**（必须恰好等于
`[Exact duplicate output; full source is at call_id=<最新副本>; sha256=<源码 SHA256>]`）、
保留项与原文逐字节相同、被替换位置集合与重算一致、字面 presence 台账（按边界作用域）、
配对与未配对调用、恢复/整份回退记账。

## 3. 与机制安全性有关的两点如实记录

1. **守卫非空**：留出任务的其他注册字面都位于工具视图里，而守卫保护的是**陈述单元**；因此修订 A1
   补注册了陈述侧字面 `qop-options`，守卫保护集合由空变为 **2 个单元**（与 v12 同形）。方向是**更严**。
2. **未持久化的机制计数器**：插件行持久化了 `selective_retention_saved_bytes_total`，但没有
   `exact_duplicate_replacements` / `trigger_gate_*` / `narrow_guard_protected_unit_count`，因为本 runner
   没有扩展 `PERSISTED_RETENTION_FIELDS`。替换次数因此由审计从记录载荷**重算**（6 次跨边界、判定边界 3 次），
   指针文本与记录逐字节一致；后续批次应补上持久化字段列表。

## 4. 请求上限与原生臂的边界（修订 A3）

harness 自身的最坏情况下界必须塞进冻结上限：3 重复 × 3 臂 × 7 次调用 = 63，加上
3 重复 × `max_summary_calls`。默认 16 会使下界变成 111 > 66，因此把原生臂的摘预算设为**每样本 1 次**，
下界正好等于 66（未抬高上限）。**后果**：原生臂本批的实测成本是其无界成本的**下界**；验收线
（Track A：插件 vs 基线）不涉及原生臂。

## 5. manifest 修正（必须留痕）

批次全部产物写完后，runner 自己的 manifest 修正在一个相对路径基准上抛 `ValueError`，因此冻结键由
`.tooling/amend_v13_stage_c_manifest.py` 复核后补写，并把"为什么"记录在 `manifest.manifest_amendments` 里。
冻结文件本身**未被编辑**；`citable_as_saving=false`、`citable_as_quality_equivalence=false` 实际写入 manifest。

## 6. 边界声明（不得越读）

- **1 任务 × 3 重复不构成多任务稳定性**：收益只在 `psf__requests-1766` 上测得，跨任务离散度未知。
- **Track B 通过 ≠ 质量不降**：Track B 只是措辞诊断；本批 Track A 已通过，故此点在本批不构成额外主张。
- **不得与其他宿主百分比合并**；`requests-1766` 自此为**已用掉的留出任务**：若失败也不得在该任务上
  调参重试，重试须换机制并另选新候选任务。
- 旧批（v1–v12）的冻结、评分、答案未改动、未重打分、未放宽合同。
