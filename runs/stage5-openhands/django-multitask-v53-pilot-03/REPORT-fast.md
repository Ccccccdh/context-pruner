# OpenHands v53 独立加速审计

计划 3 个公开真实问题 × 3 次 × 3 组；实际保存 27/27 样本。完整执行=True；验收为本机适配的选定测试。

| 方法 | 正常完成 | 最终代码通过 | 实际总输入 | Agent/摘要调用 | 压缩事件 | 失败请求 |
|---|---:|---:|---:|---:|---:|---:|
| none | 6/9 | 6/9 | 2,099,421 | 151/0 | 0 | 0 |
| native_summary | 8/9 | 8/9 | 1,718,676 | 131/0 | 0 | 0 |
| pruner_v51 | 8/9 | 8/9 | 1,987,269 | 140/0 | 2 | 0 |

| 方法 | 全配对均值 | API 正常配对均值 | 双方通过且 API 正常均值 |
|---|---:|---:|---:|
| native_summary | 12.06% (n=9) | 12.06% (n=9) | 5.43% (n=6) |
| pruner_v51 | -36.25% (n=9) | -36.25% (n=9) | -82.11% (n=6) |

## 全部配对

| 任务 | 次数 | 方法 | 减少率 | 双方通过 | API 正常 |
|---|---:|---|---:|---|---|
| django_referenced_window_wrapping | 1 | native_summary | 30.81% | True | True |
| django_referenced_window_wrapping | 2 | native_summary | 26.06% | True | True |
| django_referenced_window_wrapping | 3 | native_summary | -20.24% | True | True |
| django_lookup_allowed_foreign_primary | 1 | native_summary | 0.25% | False | True |
| django_lookup_allowed_foreign_primary | 2 | native_summary | 37.13% | False | True |
| django_lookup_allowed_foreign_primary | 3 | native_summary | 38.61% | False | True |
| django_list_editable_atomicity | 1 | native_summary | -0.00% | True | True |
| django_list_editable_atomicity | 2 | native_summary | -3.69% | True | True |
| django_list_editable_atomicity | 3 | native_summary | -0.37% | True | True |
| django_referenced_window_wrapping | 1 | pruner_v51 | -97.88% | True | True |
| django_referenced_window_wrapping | 2 | pruner_v51 | -17.59% | True | True |
| django_referenced_window_wrapping | 3 | pruner_v51 | -376.85% | True | True |
| django_lookup_allowed_foreign_primary | 1 | pruner_v51 | 76.95% | False | True |
| django_lookup_allowed_foreign_primary | 2 | pruner_v51 | 21.24% | False | True |
| django_lookup_allowed_foreign_primary | 3 | pruner_v51 | 68.21% | False | True |
| django_list_editable_atomicity | 1 | pruner_v51 | 0.01% | True | True |
| django_list_editable_atomicity | 2 | pruner_v51 | 0.00% | True | True |
| django_list_editable_atomicity | 3 | pruner_v51 | -0.34% | True | True |

## 解释边界

供应商返回输入计数包含摘要开销；失败请求可能没有 usage，消耗未知。全配对统计不代表所有请求消耗完整可观测。
仅3个固定功能任务、每任务计划3次，不能证明广泛质量等价或对所有 Agent 都有效。未触发压缩的样本组间差异不能归因于压缩。
必须结合预算失败统计解释质量与输入差异。选定测试模块通过不等于完整上游测试通过。
本轮实际单次估计输入预算为 80,000，以冻结 manifest 为准。另有每阶段 SDK 200 步上限，压缩步骤也可能占用，不能等同于模型请求次数。
插件、任务与运行代码哈希匹配；编辑边界、测试不修改工作目录、出站工具结构和归档引用已核查。供应商美元金额未知。
