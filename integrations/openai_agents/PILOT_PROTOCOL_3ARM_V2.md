# 三主机联合改动 v2：统一阈值口径 + 对称触发（新主机协议）

在 `PILOT_PROTOCOL_3ARM_V1.md` 基础上，v2 把**同一套预算与触发规则**同时落到两个新宿主。
V1 的结果仍然有效（对应 `deepseek-runner-3x3-v3arm` 与 `crewai-natural-3arm-v133`），
v2 是新的实验 ID，两者不得合并统计。

## 1. 为什么要 v2

V1 复盘暴露两个跨宿主不可比的地方：

| 问题 | V1 的实际情况 |
|---|---|
| 预算单位不一致 | 预算写在本地估算器单位，而估算器误差随宿主变化：OpenHands 0.900、Agents 0.368、CrewAI 0.389（provider/estimated）。同一个 `soft=1800` 在 OpenHands 约 1,600 真实 token，在 CrewAI 只有约 700。 |
| 触发条件不对称 | 插件臂在**每次模型调用**都渲染裁剪视图，而原生摘要臂只在越过软阈值时才摘要。两臂比的是"两种触发条件 + 两种机制"的混合。 |

## 2. 联合改动的两条内容

### 2.1 预算以 provider token 声明

```powershell
--provider-soft 1200 --provider-hard 3000 --provider-target 900
```

运行时按实测比值换算成估算器单位，两个单位都写进 manifest：

| 主机 | 比值 | provider（声明） | estimated（实际使用） |
|---|---:|---|---|
| OpenAI Agents | 0.368 | 1200 / 3000 / 900 | 3261 / 8152 / 2446 |
| CrewAI | 0.389 | 1200 / 3000 / 900 | 3085 / 7712 / 2314 |

`--soft/--hard/--target`（Agents）与 `--soft-limit/--hard-limit/--target`（CrewAI）
保留为**估算器单位覆盖**，只为复现 V1；默认 0 表示走校准路径。

实现：`experiments/runners/token_policy.py`（`CALIBRATION` 表 + 换算 + manifest 记录）。

### 2.2 对称触发（`--trigger-policy symmetric_budget`，默认）

- Agents：`BudgetTriggeredFilter` 包住插件过滤器，载荷低于软阈值时**原样放行**；
- CrewAI：`TriggeredCrewAIContextAdapter` 在低于软阈值时不改写 `context.messages`
  （但仍观察对话，状态保持热启动）。

可测性质（门控断言）：**低于阈值时该臂的载荷与 `none` 逐字节相同**。
`--trigger-policy always` 可精确复现 V1 行为。

## 3. 门控（付费前，零 API）

| 门控 | 结果 |
|---|---|
| `.tooling/gate_openai_agents_3arm.py` | PASSED：三臂 × 3 场景 × 2 重复，18 次执行；控制组"无可压缩历史时三臂一致"通过；插件输入节省 0.8656、原生 0.8188（含开销 −0.1221） |
| `.tooling/gate_crewai_3arm.py` | PASSED：真实 CrewAI ReAct 循环；对照 11,579 → 插件 2,450 / 原生 2,767（含开销 5,678）；控制组一致 |

门控同时抓到一个新缺陷并已修：**包装器对同步与异步过滤器必须区别对待**
（`TypeError: object ModelInputData can't be used in 'await' expression`）——
插件适配器的 `__call__` 是同步的，原生摘要器是异步的。

## 4. 与三主机的关系

| 主机 | 预算声明单位 | 触发规则 | 本批 |
|---|---|---|---|
| OpenHands | provider token（v53 消融批次记录折算值） | 插件：阈值触发（`trigger_input_tokens`）；原生：阈值触发 | v53 阈值消融 |
| OpenAI Agents | provider token（v2） | 两臂均为 `symmetric_budget` | v2 批次 |
| CrewAI | provider token（v2） | 两臂均为 `symmetric_budget` | v2 批次 |

**注意**：三个宿主的**数值**预算不同（Django 任务与合成任务的量级差两个数量级），
统一的不是数值，而是**单位、换算方式、记录方式与触发规则**。

## 5. 运行

```powershell
# OpenAI Agents v2
$env:PYTHONPATH='<repo>'
& .\.venv\Scripts\python.exe experiments\runners\run_openai_agents_api_experiment.py `
    --confirm-send-synthetic-data --methods none,pruner_v1,native_summary `
    --max-api-requests 80 --experiment-id deepseek-runner-3x3-v2cal

# CrewAI v2
& .\.venv-crewai\Scripts\python.exe experiments\runners\run_crewai_experiment.py `
    --mode api --confirm-send-synthetic-data --methods none,pruner_v1,native_summary `
    --max-api-requests 90 --experiment-id crewai-natural-3arm-v2cal
```

## 6. 判读规则（事前写死）

| 观察 | 结论 |
|---|---|
| 原生臂在 v2 下**仍然**不触发 | 校准后的阈值对该任务集仍偏高，应下调 provider 预算后再跑 |
| 原生臂触发但净节省仍为负 | 与 V1 的 Agents 结论一致（毛节省被自身开销吃掉），属机制性质而非阈值性质 |
| 插件臂在 v2 下的节省低于 V1 | 说明 V1 的部分收益来自"低于阈值也压缩"的不对称触发；这是本批最重要的对照结论 |
