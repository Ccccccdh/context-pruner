# CrewAI 三臂结果（多 Agent 交接型宿主，合成载荷）

实验 ID：`crewai-natural-3arm-v133`
产物：`runs/stage5-crewai/crewai-natural-3arm-v133/`（`summary.json`、`results.jsonl`、`run_manifest.json`、`report.md`）
协议：`integrations/openai_agents/PILOT_PROTOCOL_3ARM_V1.md`（冻结）

## 1. 结论摘要

**三臂全部跑通；插件臂有正向但较小且高方差的节省；原生摘要臂在本批完全没有触发。**

| 项 | 结果 |
|---|---|
| 执行 | 3 任务 × 3 臂 = **9/9 成功**，无错误 |
| 插件臂 `pruner_v1` | 输入节省 **+12.92%**（95% CI +2.29%…+33.59%），含自身开销 **+11.69%**，成功率差 **0.00pp**，压缩事件 9 次 |
| 原生摘要臂 `native_summary` | 输入节省 **+0.00%**，**摘要调用 0 次**（本批从未越过软阈值） |
| 结构安全 | 三臂 100%（工具多重集正确、任务身份保留、状态往返成功） |

## 2. 分任务

| 任务 | `none` 输入 | `pruner_v1` 输入 | 插件节省 | 插件压缩事件 | `native_summary` 输入 | 原生摘要调用 |
|---|---:|---:|---:|---:|---:|---:|
| incident_triage | 4,499 | 2,988 | **+33.6%** | 3 | 4,499 | 0 |
| customer_migration | 4,445 | 4,317 | +2.9% | 3 | 4,445 | 0 |
| release_readiness | 4,506 | 4,403 | +2.3% | 3 | 4,506 | 0 |

按任务方差极大（+2.3% 到 +33.6%），95% CI 下界仅 +2.29%——**本批不足以证明插件在 CrewAI 上有稳定正收益**。

## 3. 机制观察

1. **插件臂每次都触发**（3 任务 × 3 次渲染），但收益取决于任务里的可丢弃历史占比：`incident_triage` 的历史交接噪声最多，收益最大；另两个任务几乎无可丢弃内容。
2. **原生摘要臂完全没触发**：CrewAI 每次调用的载荷约 1,500 真实 token，而软阈值是 1,800 **估算** token。
   估算函数与供应商分词器的比例约 4.6 倍，导致同一"1,800"在两臂里不是同一个物理量——
   这既是本批原生臂空转的原因，也是后续必须统一的阈值口径问题（见 §4）。
3. 由于原生臂未触发，本批的"三臂"实际是**两臂对照 + 一个恒等臂**；恒等臂与 `none` 逐字节相同，
   这本身就验证了再增长规则与"未触发即不动载荷"的不变量。

## 4. 已知问题与下一步

| 问题 | 说明 | 处置 |
|---|---|---|
| 阈值口径不统一 | 软/硬预算是 `estimate_tokens`（中英混合按 0.7 字/token），与供应商真实计数差约 4.6 倍 | 后续：为每个宿主编排一次"阈值标定"，或在报告中同时给出两套触发点 |
| 原生臂未触发 | 本批载荷低于触发条件 | 后续：提高可丢弃历史占比（更长交接历史 / 更多重复），使原生臂真正工作后再比较 |
| 单重复 | `repeats=1`，无离散度 | 后续：`repeats>=3` 并报告样本标准差 |
| 合成载荷 | 不代表真实生产任务 | 结论仅限本任务集、本模型、本预算 |

## 5. 门控（付费前零 API）

`..\.venv-crewai\Scripts\python.exe .tooling\gate_crewai_3arm.py`：真实 CrewAI ReAct 循环 + 回环提供方。

| 臂 | 发送输入 tokens | 摘要调用 | 控制组一致性 |
|---|---:|---:|---|
| `none` | 11,579 | 0 | 基准 |
| `pruner_v1` | 2,450 | 0 | 无可压缩历史时与 `none` 相同（1,838） |
| `native_summary` | 2,767 | 1 | 同上 |

门控同时暴露并修掉了三个真实缺陷：`ContextPluginConfig(method="native_summary")` 直接抛错、
Agent 客户端与摘要客户端混用导致 `'coroutine' object has no attribute 'choices'`、
以及载荷未增长时的重复摘要。

## 6. 复现命令

```powershell
$env:PYTHONPATH='<repo>'
& .\.venv-crewai\Scripts\python.exe experiments\runners\run_crewai_experiment.py `
    --mode api --confirm-send-synthetic-data --methods none,pruner_v1,native_summary `
    --max-api-requests 90 --experiment-id crewai-natural-3arm-v133
```

## 7. 批次沿革（避免误读旧目录）

| 目录 | 状态 |
|---|---|
| `crewai-natural-smoke-v130` | 两臂烟雾测试 |
| `crewai-natural-mock-3x3-v13x-*` | 三臂 mock 框架验证 |
| `crewai-natural-3arm-v132` | 被会话中断，仅 8/9 样本，**不作为结果** |
| **`crewai-natural-3arm-v133`** | **本报告使用的完整 9/9 批次** |
