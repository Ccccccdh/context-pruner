# v42 新实例选择（模型调用前记录）

继续沿用 v40 已冻结的 Verified 元数据排序与 50 KB 弱代理门槛，排除已开发的 `django__django-16315`。下一候选 `django__django-16256` 的两个目标源码在固定提交合计 87,561 字节，但公开问题陈述直接给出 related manager 的 `acreate()` 实现示例；待处理逻辑集中在三个 manager factory 的相邻 `create/get_or_create/update_or_create` 方法，六类情形高度同构。按 v40 “调用链局限短函数则跳过”的规则，判为不适合长上下文压缩收益实验，未做模型请求，也不据预期结果挑选。

再下一候选是 `django__django-16032`，固定提交 `0c3981eb5094419fe200eb46c71b5376a2266166`。元数据仅给出两个目标源码路径：`django/db/models/fields/related_lookups.py` 与 `django/db/models/sql/query.py`；本机公开基线文件分别 8,204 与 113,526 字节，合计 121,730 字节。问题涉及 `RelatedIn` 对 RHS Query 的预处理、`Query.has_select_fields`、`annotation_select_mask` 和 `clear_select_clause()`，跨两个模块且涉及 SELECT 状态，因此进入零 API 功能门控。目标测试 2 项、PASS_TO_PASS 77 项。字节数仍不保证插件会触发；门控或环境失败就记录失败，不用模型结果回选任务。

只读基线源码和公开问题陈述可给 Agent；`reference.patch` 与 `host-tests.patch` 只在宿主评估复制目录使用，不放进 Agent 工作区。新实例 1×1×3 是开发小试，不能和已用实例混称为大规模独立确认。费用与模型输入、请求数按 v42 协议单独审计。
