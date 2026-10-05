# 集成者独立抽查记录（批次结束后追加，20261004）

本文件由**集成者**在 v61-01 批次结束后写入，用于记录一次**不依赖该批 runner 与审计器**的独立复算。
它只新增证据，不修改本批任何原有产物；本批的冻结源清单（111 个文件）与 `RESULTS.md` 均未被触碰。

## 1. 逐分叉落盘字节复算：15/15 一致

用 `branch-artifact-index.json` 里钉住的路径与 SHA256，对三条分叉各 5 个文件
（`artifacts.json` / `final-hashes.json` / `events.json` / `ledger.json` / `report.json`，
共 15 个）逐个重算：

```
index hashes verified: 15  mismatches: 0
```

即：批次根索引对分叉产物自身的承诺成立，分叉数字不是只在汇总 JSON 里存在。

## 2. 三臂硬字段复算（直接读 `report.json`，不读 `comparison.json`）

| 分叉 | `host_verdict_measured` | `host_rounds_completed` | `final_host_verdict` | `condenser` | `condensation_events` | `restore_hash_equal` | `source_prefix_unchanged` | `trigger_prediction_matches_freeze` | `file_boundary_ok` |
|---|---|---:|---|---|---:|---|---|---|---|
| `branch-0-none` | true | 2 | failed | `NoOpCondenser` | 0 | true | true | true | true |
| `branch-1-native_summary` | true | 2 | failed | `LLMSummarizingCondenser` | 2 | true | true | true | true |
| `branch-2-pruner_v1` | true | 2 | failed | `ContextPrunerCondenserV51` | 2 | true | true | true | true |

压缩机身份与压缩次数、三臂的失败判定、前缀不变性与哈希还原，均与 `comparison.json` 的汇总一致；
`branch_verdict_reason` 三臂相同（「分叉在自己的最终工作区上至少完成了一轮宿主目标测试」），
说明 `failed` 是**实测**判定而不是缺测。

## 3. 一处汇总未展开的硬字段：`workflow_verification_ok` = true / true / **false**

三条分叉的 `workflow_verification_ok` 依次为 `true`、`true`、`false`。该字段的语义
（`integrations/openhands/budget_policy_v30.py:34`）是

```python
@property
def is_verified(self):
    return self.passed and self.verified_revision == self.current_revision()
```

即**分支内最后一次 `scoped_tests` 工具输出报告 `passed: true`，且此后没有新的编辑**。
它统计的是**分支自测**，**不是**宿主判定；本批三臂的宿主判定另有其字段（上表第 4 列，全部 `failed`）。

因此这组取值只说明：`none` 与 `native_summary` 的分支内自测曾报「通过」而宿主判定为 `failed`
（自测与宿主测试不一致，与 v59/v60 观察到的同类现象一致），`pruner_v1` 连分支内自测都未落在
「通过且未再编辑」的状态上。**它不改变本批任何结论**（三臂宿主判定全部 failed、成本侧降幅
`+44.73%` / `+16.36%` 与 `+25.84%` / `+9.45%` 均不变），但它是一条应当随数字一起引用的边界：
`native_summary` 臂另有一个 `round2_burst_error`（`ConversationRunError`：冻结纠错请求上限），
其 `sdk_status` 为 `error`；`pruner_v1` 的 `sdk_status` 为 `finished`。

## 4. 本文件不替代审计

`RESULTS.md` §5 记录的「独立审计结论本批记为未取得」依旧成立：审计器在冻结后被修正过一次，
且修正在哈希级被复现证明只差那处阶梯顺序检查。本节的三项复算可由任何人事后重复，但它是
**集成者抽查**，不是冻结版审计器的判定，两者不可互相冒充。
