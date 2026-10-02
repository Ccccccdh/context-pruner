# v48 多重复批次授权卡（等待用户明确放行）

状态：**离线准备已完成，零模型请求。** 未获明确授权前不得执行第 2 节命令。

本轮目的是 roadmap 的第一半：**多重复**（估计轨迹波动）。多任务留作下一轮，两者分开报告。

## 1. 事实摘要

| 项 | 内容 |
|---|---|
| 实例 | SWE-bench Verified `django__django-17084`（与 v47 同一实例） |
| 固定提交 | `f8c43aca467b7b0c4bb0a7fa41362f90b610b8df`（Django 5.0.0a1 世代） |
| 可编辑范围 | `django/db/models/sql/query.py`（唯一文件） |
| 宿主目标测试 | `aggregation.tests.AggregateAnnotationPruningTests.test_referenced_window_requires_wrapping` |
| 公开回归 | `aggregation` 模块 124 项（原版工作区全绿） |
| 门控结论 | 原版 + 宿主补丁 = 125 项 1 error；参考修复 = 125 项全绿；冻结于 `runs/stage5-openhands/django-17084-v47-gate-01/gate.json` |
| **与 v47 的唯一差异** | **重复次数 1 → 3**；任务、臂、模型、端点、工具、导航、预算、提示词、阈值全部不变 |
| 端点 / 模型 | `https://api.deepseek.com` / `openai/deepseek-v4-flash`，温度 0，thinking 关闭，0 重试 |
| 三组 | 无压缩 / OpenHands 原生摘要 / Context Pruner v42 |
| **样本数** | **9**（1 任务 × 3 组 × 3 重复） |
| 每样本上限 | ≤36 Agent ＋ ≤16 摘要 |
| **预计请求数** | **约 207**（按 v47 单次 69 次实测推算） |
| **硬上限** | **468**（156 × 3） |
| 预计时长 | 约 35–45 分钟 |
| 输出目录 | `runs/stage5-openhands/django-17084-v48-pilot-01`（**不覆盖** v45/v46/v47） |
| 冻结哈希 | manifest 登记 **90 个源码文件**，全部校验通过 |
| 停止规则 | 出现余额或连接异常立即停批，保留全部失败样本，不用成功补跑覆盖 |

出站内容：公开问题描述、公开源码、工具结果与压缩摘要。
**宿主测试补丁与参考修复只存在于本机隔离评分副本，不进入 Agent 工作区，也不进入任何模型请求。**

## 2. 付费运行命令（需授权后执行）

**推荐：普通 PowerShell 窗口**（避免沙箱 ACL 的组间不对称干扰）

```powershell
cd 'C:\Users\LENOVO\Desktop\AI Agent\code'
.\integrations\openhands\launch_normal_windows.ps1 -Mode Probe -Runner integrations/openhands/run_validation_windows_v48.py -Out runs/stage5-openhands/django-17084-v48-pilot-01
.\integrations\openhands\launch_normal_windows.ps1 -Mode Run -Runner integrations/openhands/run_validation_windows_v48.py -Out runs/stage5-openhands/django-17084-v48-pilot-01
```

或由 DSH 沙箱内的工具调用启动（我已实测过 v45–v47 的同样路径）：

```powershell
$env:DSH_RUNNER = 'integrations/openhands/run_validation_windows_v48.py'
$env:DSH_RUNNER_ARGS = '--out runs/stage5-openhands/django-17084-v48-pilot-01 --run --resume --repeats 3'
powershell -NoProfile -ExecutionPolicy Bypass -File .\.tooling\launch_with_key.ps1
```

## 3. 运行后的独立审计

```powershell
$env:DSH_RUNNER = 'integrations/openhands/audit_validation_v48.py'
$env:DSH_RUNNER_ARGS = '--out runs/stage5-openhands/django-17084-v48-pilot-01'
powershell -NoProfile -ExecutionPolicy Bypass -File .\.tooling\launch_with_key.ps1
```

审计会逐个复核 **9 个样本**的工作区、文件边界、调用上限、SDK 事件引用、请求状态与凭据泄漏，
并输出全样本、API 正常子集、**双方成功且 API 正常**三组配对统计。

## 4. 本轮能回答与不能回答的问题

**能回答**
- 同一任务重复 3 次，插件配对输入节省的**均值与样本标准差**是多少（v47 的单点是否稳定）；
- v47 观察到的"两臂相差 24 个百分点"是回合间波动还是臂间差异；
- 三组成功率在重复间是否稳定（v47 为 3/3 全通过）。

**不能回答**（留待多任务轮）
- 结论仍限于**这一个实例**，不得外推为跨任务普遍有效；
- 峰值上下文与压缩开销必须单独报告：v47 峰值仅 −17.7%，未达 README 的 ≥30% 目标；
- 不得与 v45/v46（不同实例）或 v43（不同协议）的数字合并成总节省率。

## 5. 已知残留限制

1. **沙箱 ACL 干扰**：v47 三组均出现编辑器历史缓存写入失败（错误事件 2/2/1），
   且临时目录重定向未生效（SDK 早于包装器解析临时目录）。改在普通终端运行可消除。
2. **可编辑文件只有一个**，跨模块复杂性弱于 v45/v46 用的 `django-15563`（2 文件）。
3. 单任务单宿主结果不得外推为"跨宿主有效"。
