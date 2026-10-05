# OpenAI Agents v6：指针式行范围保留（Django 小试）

## 目的与背景

v5 把资格判据换成「先查已注册字面约束集」，严格质量从 v4 的 **0/3** 恢复到 **3/3**，四个已注册字面在最终 `model_input` 中逐字保留；但配对完整总 token **−79.79 %**（正 0/3），本批不是有效节省。

零 API 归因诊断（`integrations/openai_agents/V5_COST_REGRESSION_DIAGNOSIS.md`，脚本 `.tooling/diagnose_v5_cost_regression.py`）给出可重建的分解（provider input token，残差 ≈ 9e-13）：

| 项 | 数值 | 占输入回退 |
|---|---:|---:|
| (a) 多走的一次模型调用 | **+2,335.8** | **+99.85 %** |
| (a2) 其中被组基线假阳性挡掉的压缩 | +140.2 | +5.99 % |
| (b) 固定文本造成的载荷膨胀（同一边界比较） | **−136.7** | −5.84 % |

即：**主导项是调用轮次，不是重传字节**；v5 插件第 2 次调用（2,075）比基线第 2 次调用（2,212）反而便宜 137 token。修好假阳性只能把插件输入从 5,213 降到约 5,073，方向不变。

v6 因此只改机制本身，目标是让**证据安全保留比基线更便宜，或者证明它做不到**：

1. 不再「整份重传承载约束的输出」，改成**行范围省略**：注册字面所在行逐字保留，其余行区间被确定性指针替换；
2. 指针写明 `repo/path`、**被省略的确切行区间**、保留行号、省略字符数与重新取回所需的工具名；保留行与被省略区间必须**恰好划分**注册范围；
3. 修掉 v5 的组基线假阳性（**不回写 v5 冻结源码**）：基线只从未被机制改写的观测中取，且按公共前缀比较；
4. 新增零 API 门控必须在付费前证明「插件输入至少在一个样本上低于基线」，并给出付费批次的投影节省。

## 机制（`experiments/runners/openai_agents_span_retention_v6.py`）

注册表扩展 `experiments/runners/openai_agents_literal_registry_v6.py`：**冻结的 v5 注册表逐字不改**，v6 在其上加一层可复算的**字面行区间表**（`literal_lines` 由公开基线文件逐行匹配注册字面得出，`verify()` 校验它落在注册范围内）。

过滤资格按固定顺序判定：

1. 属于**最近一次工具组** → 逐字保留（当前轮正在推理的证据永不被省略）；
2. 长度低于最小可压缩阈值 → 保留；
3. 该文本在本次运行中**尚未逐字送达** → 保留（必须至少完整送达一次）；
4. 该工具**没有注册源范围**，或送达文本的路径与注册路径不一致 → 保留（注释无法给出可审计的指针）；
5. 否则按行范围缩减：
   - 该文本**承载已注册字面** → 保留承载字面的行（`CONTEXT_LINES = 0`，冻常常数）+ 头部行，其余行区间换成指针；
   - 该文本**不承载已注册字面** → 只保留头部行，编号正文整段省略（与 v5 行为一致）；
6. 替换后若不严格变短 → 保留原样。

**逐次不变式（每次模型调用都跑）**：

- 受保护单元 / 字面单元必须全部存在；
- **字面生存守卫**：每个已注册字面在过滤后载荷中的出现次数不得少于观测输入，任一净损失即整份回退；
- 结构性检查：条目数、条目身份、非受保护单元多重集、`call_id` 集合不变；唯一允许变化的工具输出文本必须是本模块的指针注释且严格变短；
- 受保护 Responses 工具组（`type`/`call_id`/`name`/`arguments`/`role`/`id` 指纹）与基线一致；
- 过滤后字节数必须严格小于观测输入，且不超过硬字节上限 65,216。

**回退语义**：受保护项缺失、字面净损失、工具组指纹变化、结构性不一致 → `task_anchor_restore_failures` 与 `task_restore_fallbacks` 各 +1，该次调用**整份回退**；硬字节上限或「没有严格变小」→ `budget_fallbacks` +1，同样整份回退。任何情况下都不会静默丢掉承载约束的行，也不会声称该次压缩成功。

**v5 假阳性修复**：v5 的 `_group_hashes` 在**每一次**调用（含过滤后的观测）用 `setdefault` 写基线，于是被自己写的注释污染，下一次未过滤载荷与之比较必然「不一致」。v6 只从**未改写的观测**取基线，并按条目公共前缀比较（每轮平台会追加一对 call/output，那是增长不是变化）。

## 任务、冻结矩阵与预算

- 任务：`django_count_annotations`（公开 SWE-bench 问题 `django__django-16263` 与固定公开基线源码范围）。只发送公开问题陈述、公开基线源码片段、SDK 工具结果与确定性指针；不发送参考修复、宿主测试补丁、密钥或本地私有文件。
- 矩阵：1 任务 × 3 重复 × 3 臂（`none` / `pruner_v1` / `native_summary`）= **9 样本**。
- 模型：DeepSeek `deepseek-v4-flash`，endpoint `https://api.deepseek.com`，temperature 0，隐藏 thinking 关闭。
- 预算（provider token，标定比 0.368）：soft/target/hard = **2000 / 1500 / 6000**，单次输出上限 1024；过滤硬字节上限 **65,216**；全局请求尝试上限 **100**。
- 三臂按 `(repeat + scenario_index) mod 3` 轮换；逐样本落盘，支持 `--resume`；`data_class` 为诚实的 `public_source_diagnostic`。

## 判定口径

- 主质量：与 v1–v5 相同的冻结严格答案合同（前缀 `RESULT `、单行、≤160 字符、答案正则与必要词、工具集合与顺序、`constraint_preserved` / `pairing_integrity` / `structure_safe`）。
- 约束保留：逐样本、逐模型边界核对四个已注册字面的布尔与出现次数；**审计以同一边界上基线臂的出现次数为下界逐字面比对**，而不是只与插件自己的观测输入比较。
- 资格判据：逐样本、逐边界的 `output_manifest` 与 `evidence_span_elision_records` 记录每个输出的 `call_id`、文本 SHA256、长度、命中字面、是否被缩减、保留行号、省略行区间、省略字符数、指针是否可解析且是否指向注册范围；审计断言「保留行与省略区间恰好划分」「被省略的行区间不含任何承载注册字面的行」「最近一次工具组全文保留」。
- 成本：实际输入 token 与**完整总 token**（含每一次摘要辅助请求与失败尝试）配对节省，全部样本、全部失败照实报告。
- 预注册验收线：**插件严格质量 ≥ 基线 且 完整总 token 配对均值为正**，两者同时成立才可称本批为有效节省；单任务 3 重复不外推多任务，更不外推真实软件修复。

## 前置零 API 门控

`tests.test_openai_agents_evidence_safe_retention_v6`（真实 SDK + 本地假模型，无网络）：

1. 跑 v6 runner 自身入口的完整 Django 栅格（3 重复 × 3 臂），进程内移除两个 provider 密钥；
2. 逐样本断言四个字面（含只存在于工具输出中的 `existing_annotations`）出现在**每一次**与**最后一次** `model_input`，且跨过滤边界出现次数不下降；`aggregation_decision` 在携带注册源码的边界上必须存在；
3. 断言受保护工具组 id / 输出配对 / 组哈希在过滤前后一致；唯一允许变化的工具输出文本是本模块的指针注释；
4. 断言**插件输入至少在一个样本上低于基线**，并输出付费批次的投影节省；
5. 断言 `task_anchor_restore_failures == 0`（快乐路径）、`budget_fallbacks == 0`、`pointer_coverage` 与 `pointer_completeness` 全 True，所有被省略输出的注释都带可解析指针；
6. 断言保留行与省略区间**恰好划分**缩减范围，且**没有任何承载注册字面的行被省略**；
7. 注入故障：强制字面生存守卫报告丢失（含一次真实 SDK 边界的子进程端到端注入），必须被计数、整份回退、不声称压缩；
8. 断言 v5 的组基线假阳性在 v6 下归零（同一载荷序列，v5 现场版回退 1 次，v6 回退 0 次）；
9. 审计器自检：抹掉最终边界的字面、伪造指针、伪造保留行、篡改质量、篡改恢复失败数、把组基线换回 v5 写法，都必须被判为不通过。

独立审计 `experiments/audits/audit_openai_agents_repo_diagnostic_v6.py` 只读冻结哈希与持久化行：冻结集**包含审计自身源码与门控脚本**；独立重算 v5 与 v6 两层注册表指纹、按公开源码重算行区间表、质量、用量、请求账本、工具边界、逐字面（以基线为下界）保留、行区间划分、恢复失败计数与配对均值，并输出预注册验收线的判定（`valid-saving` / `not-yet-valid`）。

## 运行

```powershell
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v6 --plan --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v6-pointer-retention-pilot-01
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v6 --confirm-send-public-source --scenarios django_count_annotations --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-api-requests 100 --experiment-id openai-repo-diagnostic-v6-pointer-retention-pilot-01
& .\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_repo_diagnostic_v6 runs/stage5-openai-agents-api/openai-repo-diagnostic-v6-pointer-retention-pilot-01 --freeze integrations/openai_agents/REPO_DIAGNOSTIC_V6_POINTER_RETENTION_FREEZE.json
```

## 边界

- 本批是机制开发小试：同一 Django 任务在 v1–v5 已参与调参，**不是**未参与调参的盲测确认批；单任务 3 重复不外推，也禁止与 CrewAI / OpenHands 的百分比合并。
- 指针只给结构性信息（路径、省略行区间、保留行号、省略字符数），不保留被省略行的原文；模型是否愿意重新调用工具取回原文不由本机制保证，也不在本批测量。
- 全量省略只在**不承载任何已注册字面**的输出上使用；承载字面的行受 `literal_guard` 与「省略行不得含承载行」两条独立检查保护。
- 证据只有哈希、长度、布尔、计数与行区间边界，不保存原文；审计通过只说明冻结哈希、账本、资格判据、质量口径、字面保留与行区间划分一致，不等于语义等价或成本有效的结论。
- v1–v5 源码、协议、冻结与结果目录保持不变；v6 使用新文件、新协议、新冻结与新批次 ID。
