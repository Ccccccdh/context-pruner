# OpenAI Agents SDK 三臂结果（工具调用型宿主，合成载荷）

实验 ID：`deepseek-runner-3x3-v3arm`
产物：`runs/stage5-openai-agents-api/deepseek-runner-3x3-v3arm/`（`report.json`、`samples.jsonl`、`manifest.json`）
协议：`integrations/openai_agents/PILOT_PROTOCOL_3ARM_V1.md`（冻结）

## 1. 结论摘要

**三臂全部跑通，插件臂在输入上明显占优，原生摘要臂"毛节省高、含自身开销净亏"。**

| 项 | 结果 |
|---|---|
| 执行 | 3 场景 × 3 臂 = **9/9 成功**，无错误、无重试 |
| 请求用量 | 28 / 80（21 次模型调用 + 7 次摘要调用） |
| 插件臂 `pruner_v1` | 输入节省 **+57.93%**（95% CI +54.94%…+62.00%），含自身开销 **+55.51%**，成功率差 **0.00pp** |
| 原生摘要臂 `native_summary` | 输入节省 **+43.85%**（95% CI +41.10%…+46.99%），**含自身开销 −42.13%**，成功率差 **0.00pp** |
| 峰值输入 | 插件 −55.22%，原生 −43.09% |
| 结构安全 | 三臂 100%（调用/输出配对完整、无恢复失败、任务身份保留） |

净节省口径（与协议 §6 一致）：
- "输入节省"只比较**供应商计费的输入 token**；
- "含自身开销"把该臂自己发出的辅助请求（原生摘要的 7 次调用、7,535 输入 + 1,200 输出 token）也计入成本。

## 2. 分场景

| 场景 | 配对数 | 插件输入节省 | 插件含开销节省 | 原生输入节省 | 成功率差 |
|---|---:|---:|---:|---:|---:|
| single_tool | 1 | +62.00% | +59.04% | — | 0.00pp |
| parallel_tools | 1 | +56.85% | +54.70% | — | 0.00pp |
| multi_tool_chain | 1 | +54.94% | +52.78% | — | 0.00pp |

分样本（供应商计费输入 token）：

| 场景 | `none` | `pruner_v1` | `native_summary` | 原生摘要调用 |
|---|---:|---:|---:|---:|
| single_tool | 2,721 | 1,034 | 1,538 | 2 |
| parallel_tools | 2,920 | 1,260 | 1,548 | 2 |
| multi_tool_chain | 4,470 | 2,014 | 2,633 | 3 |

## 3. 机制观察

1. **两臂机制都真的触发了**：插件臂每回合渲染裁剪视图（`compressions` = 2/2/3），原生臂在软阈值之上各摘要 2/2/3 次；两者都不是"空转的对照臂"。
2. **原生臂的辅助开销是它净亏的全部原因**：7 次摘要调用花了 7,535 输入 token，而它只省下约 5,400 输入 token；毛节省 +43.85% 直接被自身开销吃掉变成 −42.13%。
3. **插件臂的毛节省与净节省几乎相同**（57.93% vs 55.51%）：插件压缩在宿主内完成，不额外发模型请求。
4. **成功率完全不动**（0.00pp，三臂 9/9）：本批任务在这两个臂下都仍能完成，压缩没有破坏任务身份或工具协议。

## 4. 本批的边界与不能外推的部分

- **载荷偏小**：本批量级是每次调用 1.3k–1.6k 真实 token，压缩压力远低于 OpenHands 的 Django 任务（几十万 token/样本）。因此这里的百分比是**机制正确性证据**，不是"生产环境能省 58%"的证据。
- **每次 3 臂只有 1 个重复**（`repeats=1`），无离散度估计；跨场景不得合并为单一效应量。
- **触发阈值是估算 token**，与供应商分词器有约 4.6 倍差距（估算 12,336 vs 实际 2,700），所以两臂的"触发点"并非同一物理量。
- 合成载荷、单一模型、单一供应商；费用金额未知（SDK 未映射价格），需与厂商账单核对。

## 5. 已修并在本批生效的缺陷（写进记录，避免重犯）

| 缺陷 | 症状 | 修复 |
|---|---|---|
| 摘要客户端跨事件循环 | `RuntimeError: Event loop is closed`，`native_summary` 全臂 `summary_call_failed` | 过滤器改为 **async**（SDK 会 await 可等待的过滤器），每次摘要请求在**当前循环**上新建客户端 |
| 摘要请求未关闭思考通道 | `completion_tokens=1024 / content 为空`，`finish_reason=length`，白付一次请求 | 摘要请求带上与主循环相同的 `extra_body={"thinking":{"type":"disabled"}}` |
| 载荷未增长时重复摘要 | 同一份内容每回合重复计费 | `regrowth_ratio=0.25` 再增长规则（两主机一致，有专门单测） |

## 6. 复现命令

```powershell
$env:PYTHONPATH='<repo>'
& .\.venv\Scripts\python.exe experiments\runners\run_openai_agents_api_experiment.py `
    --confirm-send-synthetic-data --methods none,pruner_v1,native_summary `
    --max-api-requests 80 --experiment-id deepseek-runner-3x3-v3arm
```

零 API 复核：`python .tooling\gate_openai_agents_3arm.py`（真实 SDK `Runner` + 桩提供方，
18 次执行全部通过，控制组证明"无可压缩历史时三臂一致"）。
