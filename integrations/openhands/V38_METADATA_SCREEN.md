# v38 第一候选元数据和长度初筛（零模型请求）

按事先记录的 [v38 规则](V38_SELECTION_RULES.md)，排除已用 v36 实例与 v37 开发筛选实例，只取参考修复涉及 3～4 个源码文件、至少 1 个测试、难度 1–4 小时或以上的公开 SWE-bench Verified 问题，再按创建时间降序。首个候选是 `django__django-16263`（2022-11-06，难度 1–4 小时），固定基线 `321ecb40f4da842926e1bc07e11df4aabe53ca4b`。此筛选只读取元数据中的路径，没有查看参考修复正文。

本地已取得公开 Django 基线，`git status --short` 为空。实例问题是：`Book.objects.annotate(Count('chapters')).count()` 不应把未被过滤、其它注解或排序引用的注解带入计数 SQL。数据集列出 3 个 FAIL_TO_PASS 和 100 个 PASS_TO_PASS。参考修复的元数据路径与基线文件大小为：

| 公开基线源码 | 字节 |
|---|---:|
| `django/db/models/expressions.py` | 64,350 |
| `django/db/models/query_utils.py` | 15,255 |
| `django/db/models/sql/query.py` | 113,989 |
| `django/db/models/sql/where.py` | 12,571 |
| **合计** | **206,165** |

这超过事先规定的 50 KB 长度初筛线，但代码规模不保证 Agent 实际读取量或压缩触发。还未检查跨模块调用链、Python 环境、原版失败/参考通过正负门控、宿主补丁隔离或完整测试。该候选尚未冻结为付费三组任务，模型 API 请求 0。下一步先只读评估实际调用关系与本机依赖，再决定是否搭建零 API 功能门控。

后续只读调用链审查及主 Agent 独立检查完成；隔离 Python 3.12 环境下原版公开 aggregation 模块 116/116 通过。宿主应用目标测试后原版 120 项中 3 项失败，参考修复 120 项全过，源码测试前后哈希不变。详见 [v38 门控复盘](V38_DJANGO_GATE_REVIEW.md)。此实例可进入三组协议准备，但尚无付费样本或插件触发证据。
