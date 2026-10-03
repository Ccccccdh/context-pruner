# 三主机 token 阈值统一口径（标定报告）

冻结依据：本文件的比值来自**已有批次的实测**（零 API），是三个主机三臂预算的共同单位。

## 1. 问题

三臂预算（`none` / `native_summary` / `pruner_v1`）写在
`context_pruner.types.estimate_tokens` 单位里，而供应商按真实 token 计费。两者相差一个
**与宿主相关**的倍数：

| 主机 | provider/estimated 中位数 | 区间 | 估算器误差 |
|---|---:|---|---|
| OpenHands v52 | **0.900** | 0.854–0.989 | 高估约 10% |
| OpenAI Agents（v3arm） | **0.368** | 0.220–0.410 | **高估约 2.7 倍** |
| CrewAI（v133） | **0.389** | 0.368–0.428 | **高估约 2.6 倍** |

（合并口径：OpenHands 0.883、Agents 0.284、CrewAI 0.396。逐臂明细见
`.tooling/gates/token-calibration.json`。）

后果：同一个 `soft=1800` 在 OpenHands 约等于 1,600 真实 token，在 CrewAI 只有约 700。
因此"按本地记账触发"的臂在不同宿主上**物理负载不同**，跨宿主不可比。
但 CrewAI v133 原生摘要臂的 0 次成功摘要另有已证实的生产路径缺陷：未注入
`summary_client`，每次尝试以 `summary_transport_unavailable` 失败。不能把该批
0 次摘要归因于阈值；v2 已补客户端并录得 6 次摘要请求。

## 2. 为什么 OpenHands 更准

估算器是"中文按 1 字/token、其余按 0.7 字/token + 4"的启发式。
OpenHands 的载荷以源码与英文叙述为主，启发式恰好贴近；
Agents / CrewAI 的载荷含大量 JSON 结构（引号、括号、键名），供应商分词器对这类文本效率高得多，
启发式因此严重高估。

**结论：这不是"调大常数"能修的，必须逐宿主实测。**

## 3. 统一策略

1. **预算以 provider token 声明**（供应商计费单位，也是人能直接理解的单位）；
2. 运行时按实测比值转换为估算单位，转换后的两个数都写进 manifest
   （`provider_tokens` 与 `estimated_tokens`），冻结协议因此同时记录了**物理含义**；
3. 比值只能来自实测；没有实测的宿主**拒绝转换并报错**，不允许猜；
4. 提高阈值时必须显式给出倍数（消融），使"只改了一处"可被审阅。

实现：`experiments/runners/token_policy.py`
（`provider_to_estimated` / `estimated_to_provider` / `thresholds` / `scaled`）。

## 4. 三主机当前阈值换算

| 主机 | 配置值（估算单位） | 折算 provider token |
|---|---:|---:|
| OpenHands v52 | trigger 20,000 / target 16,000 / hard 28,000 | 18,000 / 14,400 / 25,200 |
| OpenAI Agents | soft 1,200 / target 900 / hard 1,800 | 442 / 331 / 662 |
| CrewAI | soft 1,800 / target 1,500 / hard 6,000 | 700 / 583 / 2,334 |

**这三个配额显然不是同一件事**：Agents 的硬上限折算后只有 662 真实 token，
而它的单次载荷实测就有 1,300–1,600 真实 token——即"硬上限"在物理上早就被越过，
只是本地记账看不出来。CrewAI 同理（硬上限 2,334 vs 单次载荷约 1,500）。

### 4.1 由此得到的两条行动结论

1. **新主机的预算必须重设**：以 provider token 声明（例如 soft 1,500 / hard 3,000 /
   target 1,200 真实 token），再换算成估算单位，否则原生臂与插件臂的触发条件都无意义；
2. **两臂必须共用同一触发条件**：目前 OpenHands 的插件臂在**每次模型调用**都渲染压缩视图
   （`compression_count` 计的是渲染次数），而原生臂只在越过软阈值时才摘要。
   在同一份报告里比较这两者时，这个不对称必须显式说明，或在下一版里统一。

## 5. 复现

```powershell
& .\.venv\Scripts\python.exe .tooling\calibrate_tokens.py
```

脚本只读已记录的批次（`report.json` / `samples.jsonl` / `results.jsonl`），不发任何请求。
更新比值时必须同时更新 `token_policy.CALIBRATION` 并在提交信息里写明来源批次。
