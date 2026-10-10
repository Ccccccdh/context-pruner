# v15 获取批停止记录（2026-10-06）

## 门控与冻结

三个新 Django Verified 实例按与答案长度无关的规则预选；题面、精确基线 commit、三段只读源码及 SHA256、六读协议和严格 `≤160` 字符答案合同已注册。零 API 源码/合同门三任务均通过：重复源码的合成七边界中，工具配对、唯一全文副本、指针 SHA256、字面 presence 均保全；破坏唯一源与源码哈希的负控制被抓到。这是**受控重复源码诊断工作量**的结构门，不是自然 Agent 行为或供应商 token 节省证据。

获取批已用新 ID 和 `V15_ACQUISITION_FREEZE_20261006.json` 冻结：每任务 `none` 一臂 × 一重复、最多 36 次 API 尝试、至多一次全臂共用格式重试、传输重试最多两次、摘要 0；`citable_as_saving=false`、`citable_as_quality_equivalence=false`。独立获取审计在合成完整七边界记录上通过正例，并抓到 provider token 漏计及工具输出哈希漂移负控制。

## 实际运行

只启动第一个任务 `django_field_error_messages_copy` 的获取批 `openai-repo-diagnostic-v15-django_field_error_messages_copy-acquisition-01`。底层请求尝试 **3/36**，三次均因 `APIConnectionError: Connection error.` 未得到模型响应；记录 `model_calls=0`、provider token 输入/输出均为 0。独立审计 `complete=false`，原因是没有记录边界 1–6；真实载荷 replay 无法成立。此批不能用于节省、质量或后续三臂准入引用。

停止在这里：未启动另两个获取批，也未启动三臂小试或多任务确认。未在同一 ID 上续跑，因为当前 runner 的 36 次硬上限按单次进程计，续跑前必须先实现并审计跨续跑累计上限，且恢复 API 连通性。冻结源和旧 v12–v14 结果均未改；没有 Git 暂存或提交。
