# OpenHands v54：同前缀分叉正式协议（冻结）

状态：**已冻结**，由 `PILOT_PROTOCOL_V54_DRAFT.md` 的零 API 门控升级为正式协议。
冻结内容 = `integrations/openhands/run_validation_v54.py` 里的 `PROTOCOL` 字典 +
`manifest.json` 中全部源文件 SHA256。任何字段改动都会让 runner 的
`Frozen protocol changed` 断言失败；改动必须新建版本与新批次目录。

批次目录：`runs/stage5-openhands/django-multitask-v54-prefix-fork-01`

## 1. 这一批解决的问题

v53（`django-multitask-v53-pilot-03`）插件完整总 token 配对 **−35.80%**，但最大的负号
发生在**第一次压缩之前**：三臂各自独立起跑，导航与纠错路径不同，成本差异无法归因于压缩。
v54 因此不再让三臂独立起跑，而是从**同一条真实事件前缀**分叉。

## 2. 实验设计（1 任务 × 1 前缀 × 3 臂）

| 阶段 | 做什么 | 是否发模型请求 |
|---|---|---|
| `--stage check` | 建一次干净工作区、核对编辑范围等于数据集 patch、公开回归模块在未改动基线上全绿、宿主补丁副本在目标测试上按冻结计数失败 | 否 |
| `--stage prefix` | 用**语料臂 `none`** 在真实 Django 任务上跑第一段，结束后由宿主目标测试判定，并把事件 ID 序列、工作区完整哈希、只读快照、请求账本与预算状态冻结为共同前缀 | 是 |
| `--stage branches` | 从 SDK 持久化重建**同一条**前缀会话（核对事件 ID 摘要与完整哈希），再顺序分叉 `none` / `native_summary` / `pruner_v1` | 是 |
| `--stage run` | 先 prefix 再 branches（与分两次执行等价，同一批次目录） | 是 |

固定任务：`django_referenced_window_wrapping`（`django__django-17084`，Django 5.0.0a1）。
选择规则**写死在协议里**：语料臂固定为 `none`，任务固定，前缀不做任何事后挑选；前缀是那次
运行产生的结果，宿主测试通过与否都照实记录。

## 3. 分叉顺序与资源约束

- 分支顺序冻结为 `none → native_summary → pruner_v1`；`--rotate` 会把顺序整体前移一位，
  用于另一批的轮换，同一批次内顺序在 manifest 里记录。
- **同一绝对路径顺序续跑**：三个分支共用 `<batch>/workspaces/prefix`，每条分支开始前
  从 `<batch>/prefix-snapshot` 恢复，并比较全文件 SHA256；分支之间绝不并发。
- 前缀账本的**已消耗请求数与估算输入**通过 `budget_policy_v54.PrefixCarryingBudgetPolicy`
  带入每条分支：策略在 fork 点固定纠错边界 `min(36, 前缀请求 + 10)`，累计计数达到共享
  上限 36 时引擎直接抛 `Frozen experiment call/token limit`。**没有任何分支获得新的 36 次额度。**
- 分支账本只记录**分支自己**的请求（前缀的行不复制进来，否则会与携带计数重复计费），
  `shared_prefix_call_count` / `shared_prefix_estimated_input` 单独记录携带量。
- 前缀预算状态（`passed` / `verified_revision` / `last_attempt_revision` / `in_correction`）
  只作为 fork 点证据冻结，不套回分支策略：分叉点就是分支自己阶段记账的起点。

## 4. 工具允许范围与文件边界（三臂完全相同）

| 工具 | 允许范围 |
|---|---|
| `scoped_editor` | 只能**编辑** `django/db/models/sql/query.py`（等于数据集 `reference.patch` 触及的文件，`--check` 会核对）；越界或工作区外路径在 executor 层直接拒绝 |
| `scoped_symbols` | 只读，只能在冻结的研究文件集合内按 AST 查询定义 |
| `scoped_tests` | 只能跑固定的公开回归模块（`aggregation`），最多 3 次、代码未变时返回缓存；不接受命令、路径或测试 ID |
| `finish` | 由预算策略在 `finish` 阶段单独放行 |

没有注册任何 shell。编辑范围与读范围对前缀和三臂逐字相同。
（v54 曾出现一个真实缺陷：工具类名派生出 `scoped_editor_tool_v54`，与预算白名单
`scoped_editor` 不匹配，导致工作阶段静默失去编辑器；已在 `fork_tools_v54.py` 显式设置
工具名，并由零 API 门控断言三个工具名与 `PROTOCOL['tools']` 完全一致。）

## 5. 宿主测试反馈（`scoped_tests` 路径）

- 第一段结束后，宿主在**评分副本**上打 `host-tests.patch` 并运行目标测试；工作区本身
  不被宿主测试修改（`workspace_unchanged_by_test`）。
- 失败文本经 `feedback_v42.format_correction_feedback` 转成只读反馈，**逐字冻结**在
  `prefix-freeze.json` 的 `correction_feedback` 里，三臂收到同一份字节。
- 分支从 fork 点直接进入宿主机纠错阶段：`begin_correction` 在分叉后的第一个请求前调用，
  纠错窗口上限为 `min(36, 前缀请求 + 10)`；每次宿主判定后若仍未通过且还有预算，可再走
  一轮宿主反馈（上限 `host_feedback_rounds_per_branch = 2`），否则记录停止原因。

## 6. 预算与请求计量

与 v53 相同的冻结值：模型 `openai/deepseek-v4-flash`、温度 0、禁用 thinking、`num_retries=0`、
单次最大输出 3072、单次估算输入上限 80,000、每样本估算输入上限 2,000,000、
每样本 Agent 请求上限 36、原生摘要请求上限 16、首阶段工作请求 24、纠错保留 10、收尾保留 2。
每个请求（含失败请求与原生摘要请求）落盘到分支自己的 `ledger.json`。

## 7. 主统计量与判读

- **主统计量**：分叉后分支自身的完整总 token（provider 输入+输出，含所有摘要/原生请求）；
  失败与触顶分支全部保留。
- **次要统计量**：把共同前缀**各计一次**的全程完整总 token。
- 插件分支分叉后若一次都没有压缩，本批只报告“**机制未触发**”，不声称任何压缩效果。
- 质量以宿主目标测试 + 未改动上游回归模块 + 文件边界为准。
- 只有当三个宿主各自完成多任务多重复、质量不劣、成本稳定正向时，才能谈结项；本批只有
  1 前缀，**不能**估计插件在任务总体上的平均效果。

## 8. 门控（付费前，零 API）

```powershell
# 零 API 门控（合成工作区、真实 SDK 会话/fork/工具/压缩器路径）
& .\.venv-openhands\Scripts\python.exe -m pytest tests/test_openhands_v54_formal_runner_gate.py `
    tests/test_openhands_same_prefix_fork_v54.py tests/test_openhands_v54_branch_loopback.py -q

# 真实 Django 树门控（复制约 20,129 文件，20-40 分钟）
DSH_RUNNER=integrations/openhands/run_validation_windows_v54.py
DSH_RUNNER_ARGS="--out runs/stage5-openhands/django-multitask-v54-prefix-fork-01 --stage check"
```

## 9. 运行与审计

```powershell
# 付费：前缀 + 三分叉（同一批次目录）
DSH_RUNNER_ARGS="--out runs/stage5-openhands/django-multitask-v54-prefix-fork-01 --stage run"

& .\.venv-openhands\Scripts\python.exe integrations/openhands/audit_validation_v54.py `
    --out runs/stage5-openhands/django-multitask-v54-prefix-fork-01
```

审计独立复核：全部源哈希、冻结快照完整哈希、**每个分叉**从快照恢复后的完整哈希与文件
边界、独立重跑宿主目标测试（与运行记录逐项比较）、携带请求数与共享 36 次上限、压缩次数、
以及凭据扫描。

## 10. 与 v53 的差异清单

1. 比较设计：三臂独立起跑 → 同一真实前缀分叉；
2. 工作区：每样本独立目录 → 同一绝对路径顺序续跑 + 只读快照全哈希恢复；
3. 预算：每臂各自 36 次 → 前缀已消耗请求带入每臂，共享 36 次上限；
4. 计时：新增 `prepare` 复制/哈希、快照、恢复、模型等待、宿主测试、审计分段计时；
5. 插件必须真实压缩，否则只报告机制未触发。
其余（任务、模型、工具、导航、提示词、阈值、成功判定、压缩器实现）与 v53 逐项相同。
