# v45 Django 15563 三组开发小试冻结说明

任务为 SWE-bench Verified `django__django-15563`，公开基线提交 `9ffd4eae2ce7a7100c98f681e2b6ab818df384a4`。按 [V45_NEW_TASK_SELECTION.md](V45_NEW_TASK_SELECTION.md) 中记录的顺序选择，未阅读参考修复正文。零 API 门控：原版含宿主目标的 `model_inheritance_regress` 32 项有 2 项失败、1 项预期失败；参考版 32 项通过；两者源码测试前后未改变。

无压缩、OpenHands 原生摘要、Context Pruner v42 各运行 1 次，三个样本使用相同 v44 符号工具（接受 `view` 和 `search`）、公开源码阅读与编辑工具、公开回归工具、预算策略和失败反馈；只比较压缩机制。Agent 看到公开问题描述、公开基线、公开测试、工作区路径、工具结果和必要的压缩摘要。可编辑范围固定为 `django/db/models/sql/compiler.py` 与 `django/db/models/sql/subqueries.py`。宿主目标测试补丁与参考修复只存在于本机隔离评分副本，不进入 Agent 工作区或模型请求。

端点 `https://api.deepseek.com`，现有环境密钥，模型 `openai/deepseek-v4-flash`，温度 0，关闭 thinking，不重试模型请求。每样本最多 36 次 Agent 请求及 16 次摘要请求；三组合计理论上限 108＋48 次。首阶段最多 26 次，宿主纠错预留 10 次，最后预留公开验证与 Finish。所有调用和供应商 token 逐样本记录；失败请求消耗可能未知。

先用 `--check` 确认公开回归、宿主负门控和源码哈希，再在新目录运行付费小试。出现余额或连接问题立即停批，保留全部原始结果。最终独立审计宿主测试、文件边界、调用上限、SDK 事件引用、请求状态和密钥泄露。优先报告各组通过率及双方通过且请求正常的配对数；单次任务只能作为开发信号。若插件未触发或质量失败，不把组间输入差异归因于压缩效果。
