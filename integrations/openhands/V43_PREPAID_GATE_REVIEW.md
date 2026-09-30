# v43 付费运行前门控与审批状态

新实例 `django__django-16032` 已按 `V42_NEW_TASK_SELECTION.md` 选出。固定公开提交为 `0c3981eb5094419fe200eb46c71b5376a2266166`；允许编辑 `django/db/models/fields/related_lookups.py` 与 `django/db/models/sql/query.py`。参考修复和宿主测试补丁只在宿主目录，Agent 基线没有这两份补丁。

零 API 正负门控：公开 `annotations` 模块原版 80 项通过；宿主应用新增目标测试后，原版 82 项中 2 错误，参考版 82 项通过。测试前后源码映射未变化。正式目录 `runs/stage5-openhands/django-16032-v43-pilot-01/` 的 `--check` 通过，83 个清单源码哈希与当前文件一致；相关候选机制测试 7 项通过，运行器与审计器编译通过。

计划实验为无压缩、原生摘要、v42 Context Pruner 各 1 次；每样本最多 36 次 Agent 请求、16 次摘要请求，三组合计最多 108＋48 次。首次阶段最多 26 次，宿主反馈后最多 10 次修正，含最后验证和 Finish。出站目的地是 `https://api.deepseek.com`，使用现有环境密钥；会发送公开问题陈述与公开基线源码、任务范围、Agent 工具结果、压缩摘要和本机工作区路径。不会发送宿主目标测试补丁或参考修复。

**审批与运行记录：**首次启动命令被自动审批拒绝，理由是当时的明确授权只覆盖 v41 的 `django-16315`。拒绝后未尝试其它路径执行。用户随后明确授权本轮 `django-16032` 的载荷、目的地和 108＋48 次总上限；重新提交后获准执行。三组结果、独立复测与审计见 `runs/stage5-openhands/django-16032-v43-pilot-01/` 和 `V43_PILOT_REVIEW.md`。
