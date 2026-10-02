# Django 15563 v45 三组开发小试

计划 1 个公开真实问题 × 3 次 × 3 组；实际保存 9/9 样本。完整执行=True；验收为本机适配的选定测试。

| 方法 | 正常完成 | 最终代码通过 | 实际总输入 | Agent/摘要调用 | 压缩事件 | 失败请求 |
|---|---:|---:|---:|---:|---:|---:|
| none | 2/3 | 2/3 | 1,336,747 | 74/0 | 0 | 0 |
| native_summary | 3/3 | 3/3 | 1,184,116 | 78/2 | 2 | 0 |
| pruner_v42 | 3/3 | 3/3 | 550,173 | 48/0 | 0 | 0 |

| 方法 | 全配对均值 | API 正常配对均值 | 双方通过且 API 正常均值 |
|---|---:|---:|---:|
| native_summary | 4.98% (n=3) | 4.98% (n=3) | -8.27% (n=2) |
| pruner_v42 | 56.97% (n=3) | 56.97% (n=3) | 53.87% (n=2) |

## 全部配对

| 任务 | 次数 | 方法 | 减少率 | 双方通过 | API 正常 |
|---|---:|---|---:|---|---|
| django_referenced_window_wrapping | 1 | native_summary | 31.46% | False | True |
| django_referenced_window_wrapping | 2 | native_summary | -22.49% | True | True |
| django_referenced_window_wrapping | 3 | native_summary | 5.96% | True | True |
| django_referenced_window_wrapping | 1 | pruner_v42 | 63.16% | False | True |
| django_referenced_window_wrapping | 2 | pruner_v42 | 48.32% | True | True |
| django_referenced_window_wrapping | 3 | pruner_v42 | 59.42% | True | True |

## 解释边界

供应商返回输入计数包含摘要开销；失败请求可能没有 usage，消耗未知。全配对统计不代表所有请求消耗完整可观测。
仅1个固定功能任务、每任务计划3次，不能证明广泛质量等价或对所有 Agent 都有效。未触发压缩的样本组间差异不能归因于压缩。
必须结合预算失败统计解释质量与输入差异。选定测试模块通过不等于完整上游测试通过。
本轮实际单次估计输入预算为 80,000，以冻结 manifest 为准。另有每阶段 SDK 200 步上限，压缩步骤也可能占用，不能等同于模型请求次数。
插件、任务与运行代码哈希匹配；编辑边界、测试不修改工作目录、出站工具结构和归档引用已核查。供应商美元金额未知。
