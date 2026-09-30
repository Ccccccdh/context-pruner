# v40 付费小试前预检

2026-09-29 完成零 API 准备，模型调用为 0。

- 固定实例：`django__django-16315`，公开基线提交
  `7d5329852f19c6ae78c6f6f3d3e41835377bf295`。
- 宿主门控：原版加入目标测试后 51 项中 1 项报错；参考版 51 项
  通过；两份目录在测试前后源码哈希一致。
- Agent 公开工作区：固定 `bulk_create` 回归 50 项通过（7 跳过）；
  宿主加入目标测试后仍按预期 1 项报错；Agent 工作区未包含目标
  用例名称、测试补丁或参考补丁。
- 失败反馈：真实宿主日志被压缩为 750 字符，保留目标用例名称和
  `OperationalError: no such column: EXCLUDED.name`。
- 策略/反馈单元测试：4 passed。运行器、工作区适配器、测试工具、
  Windows 包装器与审计器均通过 Python 语法编译。
- 冻结 manifest、baseline 在
  `runs/stage5-openhands/django-16315-v40-pilot-preflight/`。

尚未运行三组模型样本，因此没有本任务的压缩次数、token 节省或
修复质量结论。新任务源码、问题陈述、工具结果、摘要与路径会发送至
DeepSeek；按协议最多每组 36 次 Agent 请求和 16 次摘要请求，三组
合计上限 108 次 Agent 请求和 48 次摘要请求。须就此取得明确授权。
