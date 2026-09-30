# 三个项目的三组开发复验

计划 3 个评估者设计的功能任务 × 5 次 × 3 组；实际保存 45/45 样本。完整执行=True，不是上游漏洞样本。

| 方法 | 正常完成 | 最终代码通过 | 实际总输入 | Agent/摘要调用 | 压缩事件 | 失败请求 |
|---|---:|---:|---:|---:|---:|---:|
| none | 12/15 | 12/15 | 1,478,027 | 139/0 | 0 | 0 |
| native_summary | 11/15 | 11/15 | 1,871,087 | 155/1 | 1 | 0 |
| pruner_v33 | 12/15 | 12/15 | 1,369,480 | 136/0 | 2 | 0 |

| 方法 | 全配对均值 | API 正常配对均值 | 双方通过且 API 正常均值 |
|---|---:|---:|---:|
| native_summary | -28.36% (n=15) | -28.36% (n=15) | 18.38% (n=10) |
| pruner_v33 | 5.32% (n=15) | 5.32% (n=15) | 12.49% (n=10) |

## 全部配对

| 任务 | 次数 | 方法 | 减少率 | 双方通过 | API 正常 |
|---|---:|---|---:|---|---|
| click_path_resolution | 1 | native_summary | -0.52% | True | True |
| click_path_resolution | 2 | native_summary | 29.71% | True | True |
| click_path_resolution | 3 | native_summary | 3.83% | False | True |
| click_path_resolution | 4 | native_summary | -1.78% | False | True |
| click_path_resolution | 5 | native_summary | 17.42% | True | True |
| packaging_partition | 1 | native_summary | -2.87% | True | True |
| packaging_partition | 2 | native_summary | 13.94% | True | True |
| packaging_partition | 3 | native_summary | 15.67% | True | True |
| packaging_partition | 4 | native_summary | 17.56% | True | True |
| packaging_partition | 5 | native_summary | 0.59% | True | True |
| dotenv_value_origins | 1 | native_summary | 26.40% | True | True |
| dotenv_value_origins | 2 | native_summary | -3.73% | False | True |
| dotenv_value_origins | 3 | native_summary | 65.93% | True | True |
| dotenv_value_origins | 4 | native_summary | -51.17% | False | True |
| dotenv_value_origins | 5 | native_summary | -556.44% | False | True |
| click_path_resolution | 1 | pruner_v33 | -22.44% | True | True |
| click_path_resolution | 2 | pruner_v33 | 0.15% | True | True |
| click_path_resolution | 3 | pruner_v33 | 20.15% | True | True |
| click_path_resolution | 4 | pruner_v33 | -15.54% | False | True |
| click_path_resolution | 5 | pruner_v33 | 49.34% | True | True |
| packaging_partition | 1 | pruner_v33 | -0.16% | True | True |
| packaging_partition | 2 | pruner_v33 | 18.74% | True | True |
| packaging_partition | 3 | pruner_v33 | 15.50% | True | True |
| packaging_partition | 4 | pruner_v33 | 16.02% | True | True |
| packaging_partition | 5 | pruner_v33 | -18.06% | True | True |
| dotenv_value_origins | 1 | pruner_v33 | 45.66% | True | True |
| dotenv_value_origins | 2 | pruner_v33 | 10.21% | False | True |
| dotenv_value_origins | 3 | pruner_v33 | 12.76% | False | True |
| dotenv_value_origins | 4 | pruner_v33 | -30.91% | False | True |
| dotenv_value_origins | 5 | pruner_v33 | -21.55% | False | True |

## 解释边界

供应商返回输入计数包含摘要开销；失败请求可能没有 usage，消耗未知。全配对统计不代表所有请求消耗完整可观测。
仅3个固定功能任务、每任务计划5次，不能证明广泛质量等价或对所有 Agent 都有效。未触发压缩的样本组间差异不能归因于压缩。
必须结合预算失败统计解释质量与输入差异。选定测试模块通过不等于完整上游测试通过。
本轮实际单次估计输入预算为 80,000，以冻结 manifest 为准。另有每阶段 SDK 200 步上限，压缩步骤也可能占用，不能等同于模型请求次数。
插件、任务与运行代码哈希匹配；编辑边界、测试不修改工作目录、出站工具结构和归档引用已核查。供应商美元金额未知。
