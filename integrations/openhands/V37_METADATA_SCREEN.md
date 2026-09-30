# v37 公开任务元数据初筛（零模型请求）

筛选规则先写入 [V37_TASK_SELECTION_RULES.md](V37_TASK_SELECTION_RULES.md)，随后只读取本机已下载 SWE-bench Verified Parquet 的问题 ID、仓库、时间、难度、参考修复**文件路径**、测试补丁**文件路径**、测试计数和问题陈述长度；没有读取参考修复正文。按 2～4 个源码路径、至少 1 个测试路径、难度 `1-4 hours` 或 `>4 hours`、排除已用实例 `pytest-dev__pytest-10356`，得到 24 个元数据候选。先按创建时间从新到旧进入本地可行性门控；此顺序没有使用模型结果。

| 顺序 | 实例 | 日期 | 源码路径数 | 测试路径数 | FAIL_TO_PASS | 当前状态 |
|---:|---|---|---:|---:|---:|---|
| 1 | `pylint-dev__pylint-8898` | 2023-07-29 | 3 | 1 | 1 | 本地正负门控已通过，长上下文适配待审 |
| 2 | `django__django-16631` | 2023-03-06 | 2 | 1 | 1 | 后备 |
| 3 | `django__django-16560` | 2023-02-16 | 2 | 2 | 8 | 后备 |
| 4 | `astropy__astropy-14369` | 2023-02-06 | 2 | 1 | 3 | 后备 |
| 5 | `scikit-learn__scikit-learn-25102` | 2022-12-02 | 2 | 2 | 2 | 后备 |

首个候选公开元数据：基线提交 `1f8c4d9eb185c16a2c1d881c054f015e1c2eb334`；源码路径 `pylint/config/argument.py`、`pylint/utils/__init__.py`、`pylint/utils/utils.py`，测试路径 `tests/config/test_config.py`。问题是 `bad-names-rgxs` 把正则量词中的逗号当作列表分隔符；具体合同尚未冻结。不能仅凭 3 个源码路径推断 Agent 一定触发 20k 压缩阈值。

已将公开 `pylint` 基线固定在上述提交，并在隔离 Python 3.12 环境安装该版本及最小测试依赖。原版 `tests/config/test_config.py` 为 16 passed；在宿主副本加入目标测试后为 **2 failed、18 passed**，两个失败均涉及带逗号的正则量词；参考修复同一选定模块为 **20 passed**。两侧测试前后源码哈希不变。测试补丁与参考补丁保存在 `.tooling/swebench-verified/pylint-8898/`，不放入将来的 Agent 工作区。门控脚本和原始输出见 [validate_candidate_v37.py](validate_candidate_v37.py)及 `runs/stage5-openhands/pylint-8898-v37-gate-01/`。只运行选定模块，不等于完整上游测试或官方 SWE-bench harness。

三处修复路径的源码合计 28,405 字节（15,304 + 1,341 + 11,760），文件数虽多于 v36，仍不能保证轨迹会越过 20k 输入触发阈值。下一步做只读源码关系与自然读取需求审查，再决定是否把它冻结为三组候选；不得灌入无关文本强迫压缩。若不满足预先记录的自然长上下文目标，记录原因并按元数据顺序检查后备任务。尚未发起新任务付费实验。

后续只读审查及主 Agent 复核确认多文件链路，但认为任务过短风险明显；由于原筛选规则写了首个功能门控通过者进入冻结协议，跳选会偏离该规则。故 v37 不称独立验证、不运行该付费小试，作为开发筛选记录保留。详见 [v37 长上下文适配复核](V37_CONTEXT_FIT_REVIEW.md)；后续另起 [v38 规则](V38_SELECTION_RULES.md)。
