# 两个项目的三组验证

计划 2 个评估者设计的功能任务 × 3 次 × 3 组；实际保存 18/18 样本。完整执行=True，不是上游漏洞样本。

| 方法 | 正常完成 | 最终代码通过 | 实际总输入 | Agent/摘要调用 | 压缩事件 | 失败请求 |
|---|---:|---:|---:|---:|---:|---:|
| none | 5/6 | 5/6 | 2,283,289 | 95/0 | 0 | 0 |
| native_summary | 6/6 | 6/6 | 1,540,154 | 95/9 | 9 | 0 |
| pruner_v11 | 5/6 | 5/6 | 1,285,154 | 77/0 | 9 | 0 |

| 方法 | 全配对均值 | API 正常配对均值 | 双方通过且 API 正常均值 |
|---|---:|---:|---:|
| native_summary | 21.48% (n=6) | 21.48% (n=6) | 32.91% (n=5) |
| pruner_v11 | 37.35% (n=6) | 37.35% (n=6) | 41.22% (n=4) |

## 全部配对

| 任务 | 次数 | 方法 | 减少率 | 双方通过 | API 正常 |
|---|---:|---|---:|---|---|
| click_catalog | 1 | native_summary | 59.89% | True | True |
| click_catalog | 2 | native_summary | 46.96% | True | True |
| click_catalog | 3 | native_summary | -35.65% | False | True |
| packaging_wheel_ranking | 1 | native_summary | 4.83% | True | True |
| packaging_wheel_ranking | 2 | native_summary | 22.29% | True | True |
| packaging_wheel_ranking | 3 | native_summary | 30.59% | True | True |
| click_catalog | 1 | pruner_v11 | 62.31% | True | True |
| click_catalog | 2 | pruner_v11 | 44.90% | False | True |
| click_catalog | 3 | pruner_v11 | 14.30% | False | True |
| packaging_wheel_ranking | 1 | pruner_v11 | 27.53% | True | True |
| packaging_wheel_ranking | 2 | pruner_v11 | 38.77% | True | True |
| packaging_wheel_ranking | 3 | pruner_v11 | 36.25% | True | True |

## 解释边界

供应商返回输入计数包含摘要开销；失败请求可能没有 usage，消耗未知。全配对统计不代表所有请求消耗完整可观测。
仅2个固定功能任务、每任务计划3次，不能证明广泛质量等价或对所有 Agent 都有效。未触发压缩的样本组间差异不能归因于压缩。
必须结合预算失败统计解释质量与输入差异。选定测试模块通过不等于完整上游测试通过。
本轮实际单次估计输入预算为 80,000，以冻结 manifest 为准。另有每阶段 SDK 200 步上限，压缩步骤也可能占用，不能等同于模型请求次数。
插件、任务与运行代码哈希匹配；编辑边界、测试不修改工作目录、出站工具结构和归档引用已核查。供应商美元金额未知。
