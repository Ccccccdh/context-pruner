# v38 Django 首候选只读审查与零 API 正负门控

候选 `django__django-16263` 按 [事先规则](V38_SELECTION_RULES.md)从公开 SWE-bench Verified 元数据进入初筛；固定基线 `321ecb40f4da842926e1bc07e11df4aabe53ca4b`。4 个元数据相关源码文件合计 206,165 字节，满足 50 KB 弱代理。DeepSeek 的只读审查保存在 `runs/DEEPSEEK_V38_DJANGO_FIT_ANSWER.md`。主 Agent 复核 `QuerySet.count()`、`Query.get_count()`、`get_aggregation()`、`annotation_select` 与 SQL 编译器的函数位置，并确认上游基线 `git status --short` 为空。

DeepSeek 提到可能需要自定义 `DJANGO_SETTINGS_MODULE` 和 SQLite 设置，主 Agent 查阅 `tests/runtests.py:733–736` 后修正：未指定时运行器自动选择 `test_sqlite`。在隔离 Python 3.12 环境安装该基线声明的 asgiref、sqlparse 和 Windows tzdata 后，直接运行 `tests/runtests.py aggregation --noinput --parallel=1`，原版公开模块 **116/116 通过**。因此不需要私有数据库配置。基线自报 Django 4.2 alpha；本机可运行这一选定模块，不代表完整上游兼容性。

随后通过脚本 [validate_candidate_v38.py](validate_candidate_v38.py) 在两个**新复制的宿主副本**应用数据集目标测试，并且只在参考副本应用参考修复。原始结果见 `runs/stage5-openhands/django-16263-v38-gate-01/gate.json` 与各自的 `test.txt`：

| 宿主副本 | 选定 aggregation 模块 | 源码被测试修改 |
|---|---|---|
| 固定原版 + 目标测试 | 120 项，3 项失败 | 否 |
| 固定原版 + 参考修复 + 目标测试 | 120 项全过 | 否 |

原版三个失败名称与数据集 FAIL_TO_PASS 一致：`test_non_aggregate_annotation_pruned`、`test_unreferenced_aggregate_annotation_pruned`、`test_unused_aliased_aggregate_pruned`。两副本测试运行时均从自身目录导入 Django；目标与参考补丁 SHA-256 已记入 `gate.json`。该门控只覆盖选定 `aggregation` 模块，不等同完整上游套件或官方 SWE-bench Docker harness。参考修复和目标测试保存在 `.tooling/swebench-verified/django-16263/` 宿主侧，不进入后续 Agent 工作区。

**决定**：该实例通过 v38 长度初筛、公开源码关系审查和本地正负门控，可进入三组协议准备。206 KB 源码及正负门控都不能证明真实 Agent 会超过 20k 输入或插件会压缩。下一步先为 Django 编写独立任务/只读工具/固定测试适配，修复 v36 运行器在每次请求全树哈希及最终比较重复扫描的问题，完成零 API 预检和代码哈希冻结，再讨论新的付费 1×1×3 小试。v36 的三样本授权不覆盖本轮 Django 上下文；当前 v38 模型请求为 0。
