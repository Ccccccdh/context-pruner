# v56：预注册前缀筛选（余量 0、三档阶梯）+ 可独立验证的分叉

状态：**按冻结规则判定 “mechanism still not triggered”，未发任何分叉请求**（runner 退出码 3）。
付费支出只发生在前缀筛选阶段（582,493 完整总 token），**没有**压缩效果结论。

- 冻结协议见 `integrations/openhands/PILOT_PROTOCOL_V56.md`
- 冻结清单 `manifest.json`（其自身 SHA256
  `5618F4555B305FD8A56A8C4FAFE05D7BE2234B076E8B58A4FE56EAF4C5C5B8CB`）
- 本批**没有** `prefix-freeze.json`、`prefix-snapshot`、`branch-*`；
  `audit_validation_v56.py` 对本批不适用（没有可审计的前缀与分叉），仅保留 `select.json`
  与五次尝试的完整报告/账本/事件/宿主评测作为证据

## 1. 零 API `--check`（付费前）

| 任务 | 公开回归 | 宿主基线 | 工作区文件数 |
|---|---|---|---|
| `django_referenced_window_wrapping` | 124 通过 | 125 项 `errors=1` | 20,128 |
| `django_lookup_allowed_foreign_primary` | 162 通过 | 163 项 `failures=1` | 19,972 |
| `django_list_editable_atomicity` | 74 通过 | 75 项 `failures=1, skipped=7` | 19,939 |

分段计时：`gate_prepare_seconds` 810.75、`gate_hash_seconds` 49.58、
`host_test_seconds` 498.01。

## 2. 预注册筛选的付费结果（全部保留）

相对 v55 的两处预注册修订：实测重放闸门**去掉 2,000 token 余量**（改为机制自身的条件），
phase-1 阶梯扩到**三档 24/26/28**。

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | condenser 视图 token | 相对 28,000 | 真实 condenser 重放 | 完整总 token | 墙钟秒 |
|---:|---|---:|---|---:|---:|---:|---|---:|---:|
| 1 | `django_referenced_window_wrapping` | 0（24） | **通过** | 25 | — | — | 未预测（无需纠错） | 372,158 | 79.96 |
| 2 | `django_lookup_allowed_foreign_primary` | 0（24） | 未通过 | **6** | 16,543 | −11,457 | View（未触发） | 54,954 | 35.68 |
| 3 | `django_list_editable_atomicity` | 0（24） | **通过** | 10 | — | — | 未预测（无需纠错） | 99,371 | 51.10 |
| 4 | `django_lookup_allowed_foreign_primary` | 1（26） | 未通过 | **3** | 14,674 | −13,326 | View（未触发） | 28,005 | 32.35 |
| 5 | `django_lookup_allowed_foreign_primary` | 2（28） | 未通过 | **3** | 14,552 | −13,448 | View（未触发） | 28,005 | 31.08 |

- 规则指纹：`447fd51c37e76161…`（完整值见 `select.json`）
- 结果：五次尝试全部落在两类里——要么“宿主通过、不需纠错”，要么“宿主未通过但前缀太短、机制
  预测不触发”，因此 `select.json` 记录 `complete: false`，理由为
  `... mechanism still not triggered`。按协议不修改阈值、不重跑挑选、不发分叉请求。
- 尝试 4 与 5 的轨迹几乎相同（3 次请求、账本估算结束时同为 7,453、完整总 token 同为 28,005），
  说明**提高档位并不能让该 provider 多工作**：它关掉首阶段的时机由自己决定，而不是由上限决定。

## 3. 本批与 v55 批次 01 合起来给出的结论

两批共 **9 次** 付费的 phase-1 尝试（1,647,698 token）：

| 结局 | 次数 | 说明 |
|---|---:|---|
| 宿主目标测试通过（不需纠错） | 4 | 17084 两次、16100 两次 |
| 宿主未通过且视图 > 28,000（机制已触发） | **1** | v55 批次 01 第 2 次（16661，26 请求，28,775，重放 Condensation） |
| 宿主未通过但视图 < 28,000（机制未触发） | 4 | 16661 的四次短尝试（6/6/3/3 请求，14.5k–16.5k） |

- 该任务集合上，**“结束状态需要纠错”与“前缀长到 28,000”很少同时出现**：唯一同时满足的一次
  被 v55 自己的 2,000 token 余量挡下；v56 把余量降为 0 后重跑，同一任务的 phase-1 却缩短到
  3–6 次请求，仍然够不到阈值。
- 因此 v56 的这一批**只证明流程可运行**（预注册阶梯、宿主判定、真实 condenser 重放、
  中止路径都按协议执行），**不构成任何压缩效果结论**，`mechanism_triggered_plugin` 不适用。

## 4. 边界声明

- 本批只有 5 次筛选尝试，没有共同前缀、没有分叉，**没有**压缩效果结论；表中的 token 是
  各次尝试各计一次的实际支出。
- 三条分叉的逐分叉持久化、审计从自身字节重建与重跑、篡改拒绝等机制在本批代码与
  `tests/test_openhands_v56_formal_runner_gate.py`（9 项）中已实现并通过零 API 门控，
  但**本批没有真实数据样本**（没有分叉就没有分叉产物）。
- 本批不能估计插件在任务总体上的平均效果，也不能外推到其他宿主。

## 5. 由两批证据驱动的下一版（v57，独立付费批次）

`integrations/openhands/PILOT_PROTOCOL_V57.md`：把前缀用**同一语料臂的宿主反馈纠错轮**
延长（`prefix_host_feedback_rounds = 3`，前缀纠错窗口 `min(36, phase-1 请求 + 16)`），
使短首阶段的前缀也能在**冻结预算内**长到触发阈值；fork 点仍是“宿主目标测试仍未通过的
前缀”，分支自己不获得任何新额度，压缩机制阈值一字未改。
