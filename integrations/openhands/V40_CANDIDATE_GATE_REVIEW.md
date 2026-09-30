# v40 候选筛选与零 API 门控

筛选规则先写于 `V40_SELECTION_RULES.md`，再按本地 Verified 元数据排序。
排序结果在 `runs/stage5-openhands/v40-metadata-screen.json`。首位
`django__django-16938` 的两个目标源码共 24,906 字节，低于预定
50 KB 弱代理门槛，故跳过。随后两个 Matplotlib 候选分别涉及
108,038 和 80,656 字节目标源码，但本机缺少其固定提交所需的
原生扩展构建链，未能完成公开基线功能门控，标记为**环境未验证**。
它们不是功能失败，也没有用于调整预期模型收益。

下一候选 `django__django-16315` 的两个目标源码共 189,994 字节。
公开问题要求修复 `bulk_create(update_conflicts=True)` 在 `db_column`
与字段名不同（尤其混合大小写）时的冲突列 SQL。公开源码路径为
`QuerySet.bulk_create()` → `_batched_insert()` → `SQLInsertCompiler.as_sql()`
→ 后端 `on_conflict_suffix_sql()`；SQLite、PostgreSQL、MySQL 后端
都会处理字段名与实际列名。定向检索能较快定位，因此源码大小
**不能保证**超过 20,000 token 压缩阈值。目标元数据含 1 项
FAIL_TO_PASS、42 项 PASS_TO_PASS。

本地 Python 3.12 环境下，固定提交公开 `bulk_create` 测试 50 项
通过（7 项跳过）。宿主隔离门控使用原版与参考版两份复制目录，
均注入同一测试补丁；原版运行 51 项，目标报错 1 项，参考版运行
51 项全部通过。两边测试前后源码 SHA256 映射完全一致。参考补丁
只在宿主复制目录，未放入 Agent 基线；Agent 的编辑范围限定为
`django/db/models/query.py` 和 `django/db/models/sql/compiler.py`。
详细结果与数据集、补丁哈希在
`runs/stage5-openhands/django-16315-v40-gate-01/gate.json`。

这仅证明任务和测试环境可用，不证明 Agent 能修复任务、插件会触发
或节省 token。下一步冻结三组运行清单并进行 `--check`；付费小试
须取得针对本任务上下文及调用范围的独立授权。
