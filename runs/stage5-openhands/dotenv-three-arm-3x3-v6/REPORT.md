# OpenHands 三组对照实验报告

协议：`openhands-dotenv-three-arm-v6`。3 个真实开源缺陷 × 3 次重复 × 3 组，共 27 个样本。

## 结果

| 组别 | 文件任务通过 | 正常完整完成 | Agent 输入 | 摘要输入 | 总输出 | 模型调用 | 摘要调用 |
|---|---:|---:|---:|---:|---:|---:|---:|
| none | 8/9 | 7/9 | 905,775 | 0 | 29,621 | 78 | 0 |
| native_summary | 7/9 | 7/9 | 782,498 | 21,773 | 40,271 | 91 | 9 |
| pruner_v1 | 9/9 | 8/9 | 823,492 | 0 | 33,118 | 78 | 0 |

文件任务通过：全部 27 个最终工作区统一进行只读行为测试与文件边界审计。正常完整完成：原报告记载的 Agent 三阶段均结束、测试通过且遵守调查阶段不编辑。达到调用上限后已有正确代码的样本，其文件任务可以通过，但不算正常完整完成；原始报告不改写。

输入与输出均为提供商回报值；摘要开销已计入。SDK 缺少该模型价格映射，0 美元不能解释为免费。

| 相对完整历史 | 配对平均输入节省 | 配对中位数 | 输入节省胜率 | 两组同时成功 |
|---|---:|---:|---:|---:|
| native_summary | 3.19% | 0.45% | 5/9 | 7/9 |
| pruner_v1 | 0.87% | 14.80% | 5/9 | 8/9 |

## 任务与判分

固定源码：python-dotenv v1.2.1，提交 `eaf2a9129ccec6febda0f741eb3bb852c3f947bd`（MIT）。

- [quoted_roundtrip](https://github.com/theskumar/python-dotenv/issues/661)：set_key with quote_mode='always' or 'auto' must round-trip literal backslashes, including consecutive backslashes and apostrophes. Reading the written file with dotenv_values(interpolate=False) must reproduce the exact value. Preserve existing quote modes and export behavior.
- [empty_inline_comment](https://github.com/theskumar/python-dotenv/pull/663)：An empty unquoted value followed by whitespace and an inline comment, e.g. KEY= # explanation, must parse as the empty string. Hash characters without preceding whitespace, e.g. KEY=#literal and KEY=a#b, remain literal. Preserve quoting, exports, and ordinary comments.
- [crlf_error_recovery](https://github.com/theskumar/python-dotenv/pull/669)：Parser recovery after an invalid binding must consume a CRLF as one complete newline. For an invalid first line followed by GOOD=ok, the invalid binding's original.string must include the whole CRLF and the valid binding must start at line 2 with original.string exactly GOOD=ok plus its newline. Preserve LF and CR behavior and repeated invalid lines.

原版负对照：转义 12 项失败、注释 4 项失败、CRLF 3 项失败。每项实验还运行原版 parser/variables 回归用例；仅允许修改指定实现文件，测试不允许改动。

三个阶段均固定：先调查且不编辑、实施修复、接收宿主测试反馈并完成检查或修正。模型只能用受限文件编辑器和 FinishTool，未启用终端。各组独立工作区、独立会话，顺序按任务和重复轮换。

## 集成与审计

Context-Pruner 通过官方 CondenserBase 扩展点接入，以安全工具批次边界压缩旧事件前缀；保留系统、初始任务和最近工具批次，使用 Condensation 派生视图，原事件仍在 SDK 持久化日志中。没有额外摘要模型调用。

27 个样本文件边界、模型请求工具结构、压缩事件引用已全部通过审计。实际多调用批次共 31 组。凭据扫描命中 0 项。

插件压缩次数：7；结构校验回退：0；压缩后超过 16000 token 的派生视图：0。

12000 token 为 SDK 计数口径下的压缩触发阈值，插件目标 10000、硬目标 16000。受保护部分自身超限时无法保证满足硬目标；不能把目标称为 API 的硬上下文限制。各组请求均有总调用和估算输入上限。

## 范围与限制

配对输入指标包含全部九对，不剔除失败。文件任务通过数为统一只读补充审计结果；预设运行器的成功数保留为正常完整完成列。补充审计不修改原报告，也不重试模型。

本次是单库、三个小缺陷的受控代码修复实验，不能推出所有 Agent、模型或长任务都获得相同收益，也不能仅凭 9 次重复证明质量等价。这里的工作流包含固定调查阶段和宿主测试反馈。Windows 未运行依赖 POSIX sh 的上游 test_main.py；通过所选回归测试不代表通过整个上游测试集。

开发记录单独保留：installation-smoke-v1 的 LiteLLM 兼容故障发生在一次模型响应后；smoke-v2 成功。对照 v1/v2/v4 为请求前工具工厂兼容故障；v3 因全文件阅读耗尽预算停止；v5 暴露 pytest 默认临时目录权限冲突及低阈值下的摘要循环，已统一设置每轮独立临时目录并调整三组压缩参数后冻结 v6。这些开发调用不计入 v6 的效果统计，但存在额外 API 用量；中断调用的计费情况无法从本地完整确认。

## 复现与证据

`manifest.json` 冻结任务、版本、参数和源文件哈希；每样本保留 `report.json`、`ledger.json`、工作区、两轮 pytest 日志和 SDK 事件；插件组另有 `pruner-state.json`。`summary.json` 是组汇总，`audit.json` 是只读审计及 9 对逐对比较。

```powershell
.\.venv-openhands\Scripts\python.exe -B integrations/openhands/run_comparison.py --run --out runs/stage5-openhands/new-experiment-id
.\.venv-openhands\Scripts\python.exe -B integrations/openhands/audit_comparison.py --out runs/stage5-openhands/new-experiment-id
```

已有完整样本可用 `--resume` 继续，源文件哈希必须匹配；未写最终报告的中断样本需要先审计，运行器拒绝静默覆盖。
