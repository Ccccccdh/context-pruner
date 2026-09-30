# 两个项目的三组验证

计划 3 个评估者设计的功能任务 × 5 次 × 3 组；实际保存 45/45 样本。完整执行=True，不是上游漏洞样本。

| 方法 | 正常完成 | 最终代码通过 | 实际总输入 | Agent/摘要调用 | 压缩事件 | 失败请求 |
|---|---:|---:|---:|---:|---:|---:|
| none | 12/15 | 12/15 | 1,500,186 | 142/0 | 0 | 0 |
| native_summary | 10/15 | 10/15 | 2,504,260 | 192/2 | 2 | 0 |
| pruner_v11 | 10/15 | 10/15 | 3,135,042 | 214/0 | 6 | 0 |

| 方法 | 全配对均值 | API 正常配对均值 | 双方通过且 API 正常均值 |
|---|---:|---:|---:|
| native_summary | -71.24% (n=15) | -71.24% (n=15) | -13.78% (n=8) |
| pruner_v11 | -88.99% (n=15) | -88.99% (n=15) | -15.25% (n=10) |

## 全部配对

| 任务 | 次数 | 方法 | 减少率 | 双方通过 | API 正常 |
|---|---:|---|---:|---|---|
| click_path_resolution | 1 | native_summary | 9.00% | False | True |
| click_path_resolution | 2 | native_summary | -89.28% | True | True |
| click_path_resolution | 3 | native_summary | 19.10% | True | True |
| click_path_resolution | 4 | native_summary | -65.58% | True | True |
| click_path_resolution | 5 | native_summary | -24.11% | False | True |
| packaging_partition | 1 | native_summary | 6.07% | True | True |
| packaging_partition | 2 | native_summary | -20.82% | True | True |
| packaging_partition | 3 | native_summary | 11.69% | True | True |
| packaging_partition | 4 | native_summary | 18.83% | True | True |
| packaging_partition | 5 | native_summary | 9.75% | True | True |
| dotenv_value_origins | 1 | native_summary | -135.53% | False | True |
| dotenv_value_origins | 2 | native_summary | -238.29% | False | True |
| dotenv_value_origins | 3 | native_summary | 27.28% | False | True |
| dotenv_value_origins | 4 | native_summary | 15.34% | False | True |
| dotenv_value_origins | 5 | native_summary | -612.07% | False | True |
| click_path_resolution | 1 | pruner_v11 | 7.56% | False | True |
| click_path_resolution | 2 | pruner_v11 | -209.38% | True | True |
| click_path_resolution | 3 | pruner_v11 | 62.25% | True | True |
| click_path_resolution | 4 | pruner_v11 | -115.33% | False | True |
| click_path_resolution | 5 | pruner_v11 | -5.32% | True | True |
| packaging_partition | 1 | pruner_v11 | 0.51% | True | True |
| packaging_partition | 2 | pruner_v11 | -6.00% | True | True |
| packaging_partition | 3 | pruner_v11 | 12.28% | True | True |
| packaging_partition | 4 | pruner_v11 | 5.96% | True | True |
| packaging_partition | 5 | pruner_v11 | 35.61% | True | True |
| dotenv_value_origins | 1 | pruner_v11 | 6.43% | True | True |
| dotenv_value_origins | 2 | pruner_v11 | -471.60% | False | True |
| dotenv_value_origins | 3 | pruner_v11 | -254.04% | False | True |
| dotenv_value_origins | 4 | pruner_v11 | -348.91% | False | True |
| dotenv_value_origins | 5 | pruner_v11 | -54.84% | True | True |

## 解释边界

供应商返回输入计数包含摘要开销；失败请求可能没有 usage，消耗未知。全配对统计不代表所有请求消耗完整可观测。
仅3个固定功能任务、每任务计划5次，不能证明广泛质量等价或对所有 Agent 都有效。未触发压缩的样本组间差异不能归因于压缩。
必须结合预算失败统计解释质量与输入差异。选定测试模块通过不等于完整上游测试通过。
本轮实际单次估计输入预算为 80,000，以冻结 manifest 为准。另有每阶段 SDK 200 步上限，压缩步骤也可能占用，不能等同于模型请求次数。
插件、任务与运行代码哈希匹配；编辑边界、测试不修改工作目录、出站工具结构和归档引用已核查。供应商美元金额未知。
