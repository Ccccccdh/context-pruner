# v36：真实 pytest 问题长上下文候选小试协议

## 任务与冻结点

选择公开 [SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) 的 `pytest-dev__pytest-10356`。项目 `pytest-dev/pytest` 固定在基线提交 `3c1534944cbd34e8a41bc9e76818018fadefc9a1`；数据集文件 SHA-256 和参考/测试补丁哈希见 `.tooling/validation-v36-reference-frozen/results.json`。这是此前未用于本项目调参的真实仓库问题，但公开基准可能被模型训练接触，不能称模型盲测。任务在 v36 阈值候选确定后选择，不根据本任务的模型结果改候选。

合同采用数据集的原始 `problem_statement`：多继承类应按 MRO 取得全部标记。允许读取任务相关的六个公开源码文件，仅允许修改 `src/_pytest/mark/structures.py`；Agent 工作区不包含官方修复补丁或新测试补丁。`TASK.md` 和初始消息传达完整问题与范围。三组统一使用 v35 只读符号工具兼容修复、v33 编辑护栏、预算提示与验证/Finish 预留。插件组固定 `ContextPrunerCondenserV36`，即 20k 触发、16k 目标、28k 硬上限；无压缩和 SDK 原生摘要配置沿用既有运行器。模型为 DeepSeek v4 Flash，温度 0，每样本最多 36 次 Agent 请求、16 次摘要请求、单次估计输入 80k、总估计输入 2M；失败请求不自动重试。

隔离的本机 Python 3.13 环境只安装该 pytest 版本声明的测试依赖，并生成正常构建会创建的 `_pytest/_version.py`。为了兼容 Python 3.13 对 2022 年源码的 `ast.Str` 弃用提示，三组及正负门控都固定使用 `-W ignore::DeprecationWarning`；这只影响警告处理，不改变目标断言。宿主验收在工作区副本应用数据集 `test_patch`，Agent 的 `scoped_tests` 只运行原项目 `testing/test_mark.py`，不得选择任意命令或读取宿主补丁。每次运行后对工作区源哈希复核，确保测试工具未修改候选源码。

已完成零 API 正负门控：原版公开回归 88 passed、1 xfailed，加入目标测试后 1 failed、88 passed、1 xfailed；官方参考补丁公开回归 88 passed、1 xfailed，加入目标测试后 89 passed、1 xfailed。门控日志、隔离副本及哈希在 `.tooling/validation-v36-reference-frozen/`；上游完整测试套件和官方 Docker harness 未运行，本机结果只覆盖选定测试。

## 首轮与停止规则

先运行 **1 任务 × 1 次 × 3 组 = 3 个付费样本**，每组独立工作区、轮换次序；所有正常/失败/未压缩结果保留。主输入口径为供应商返回的完整总输入（含摘要），插件相对无压缩的逐对节省率。并列记录正常完成、宿主代码通过、请求数、压缩事件、视图前后、估计峰值、工具错误、文件边界、预算与凭据审计。真实美元费用以供应商账单为准。

若插件未触发压缩，停止扩大本任务；任务上下文不足以验证 v36 长上下文目标。若插件触发但质量或工具审计失败，停止扩大并离线诊断。若压缩触发且三组质量与审计均正常，也仅认定运行可行；单次配对无论正负都不足以判定 30% 稳定目标，下一轮需另冻结多个未使用问题和样本数。不得把回放的 36.46% 视图估计当作本轮真实输入收益，不覆盖 v30–v35 数据，不自动提交 Git。
