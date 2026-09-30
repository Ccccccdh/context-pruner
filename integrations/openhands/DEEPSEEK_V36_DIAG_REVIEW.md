# DeepSeek 辅助诊断复核：v36 pytest 标记 MRO

DeepSeek 的交接答案保存在 `runs/DEEPSEEK_V36_OFFLINE_DIAG_ANSWER.md`；它新增的三个探针留在 `runs/`。主 Agent 读取脚本后，另外编写并运行了 [独立复核脚本](review_deepseek_v36_diagnosis.py)。复核只在从基线复制的新开发工作区中修改 `src/_pytest/mark/structures.py`，没有改动 v36 的冻结三组工作区、manifest 或原始结果，没有模型 API 请求。结果原件见 `runs/stage5-openhands/pytest-mark-mro-v36-offline-review-01/review.json`。

## 核对结果

三组最终 `structures.py` 的 SHA-256 分别以 `88F4018F`、`9E9A785C`、`0104196D` 开头，与 DeepSeek 答案一致。源码也支持核心诊断：`store_mark` 在装饰 `C(A, B)` 时通过 `getattr(C, "pytestmark", [])` 读到 A 的继承值，随后把 `[a, c]` 写成 C 的自有标记。w000 与 w002 再按 MRO 收集，因而会重复得到 A 的标记；w001 还按 `mark.name` 去重，丢失同名但参数不同的 B 标记，并且缺少 `consider_mro` 关键字参数。

主 Agent 没有直接采纳 DeepSeek 的补丁文本，而是在新副本中独立实现相同语义：类写入只读取 `obj.__dict__` 中的自有标记；读取时才按 MRO 收集；非类对象保持普通读取。源文件唯一变更为 `src/_pytest/mark/structures.py`。在本机隔离 Python 3.13 环境及相同警告设置下，公开选定回归为 **88 passed、1 xfailed**；加入宿主目标测试后为 **89 passed、1 xfailed**。两次测试前后源码 SHA-256 一致。此结果支持该诊断及候选修复在选定测试范围内成立，未运行完整上游测试或官方 SWE-bench Docker harness。

DeepSeek 答案最后把 -8.71% 归因于“无压缩组异常偏高与单次采样波动”，超出了证据。当前只能说三组均未压缩/摘要、轨迹和请求数不同，**无法确定输入差异的因果来源**。保留 v36 报告的全配对 -8.71% 描述值，不将其解释为压缩收益、压缩损失或质量等价。DeepSeek 的“候选修复在三组模块重放两条断言通过”是其本地探针结果；主 Agent 的独立证据是新副本上的完整选定测试模块通过。

## 下一步

此 pytest 实例已用于开发分析，不能再充当新验证任务。插件 v36 本轮没有触发，根因是任务轨迹没有达到 20k 触发阈值；修好 pytest 功能实现本身不能证明 Context-Pruner 有效。下一轮应先离线冻结另一个未接触、自然需要较长上下文的公开任务，并预检正负门控、编辑边界和运行器性能，再决定是否做三组小试。新的付费任务与外发上下文不在 v36 的三样本授权内。
