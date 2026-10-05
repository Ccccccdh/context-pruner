# OpenAI Agents v11：省略比例 × 质量的安全分界（消融）

## 本轮要回答的问题

v10 用"保护最近 1 次工具调用"得到 **+27.68 %** 配对完整总节省，但插件严格质量 **0/3**。本仓库此前从未测出"质量与节省同时成立"的点。v11 把 **M = 保护最近几次工具调用** 作为**唯一变量**，问一个问题：

> 是否存在一个更小的省略比例，能在**保住质量**的同时仍然省下 token？

交叉证据（支持"优先保质量"的设计）：同一任务上**原生摘要臂也大幅缩减输入却同样掉质量**（v10 批次原生 1/3），说明该任务对"模型能看到什么"很敏感。

## 唯一变量的定义

```
elidable(call) := 载荷中更新的工具调用数量 >= M          M ∈ {6, 4, 2}
```

- 一个"工具调用"= 一次 `function_call` 与应答它的 `function_call_output`（同一 `call_id`）；
- **M = 6** 最保守：该任务的记录结构只有 6 次工具调用，因此**一个都不省**；
- 机制、注册表、守卫、回退语义、阈值与预算**一律不动**（沿用 v10 的"工具调用"口径、v9 的收窄守卫分类、v6 的行范围省略与全部守卫；门控以对象同一性断言）；
- `M` 由环境变量 `DSH_V11_RECENT_CALLS_KEPT` 选择（只在预注册阶梯内），因此消融不需要改动任何文件。

## 预注册阶梯的离线投影（零 API，replay 夹具）

夹具沿用 v10 的 `openai_agents_real_payload_replay_v10`：边界由公开确定输入重建，并对 v9 付费批次的 `input-evidence` 逐边界断言结构一致（含工具项连续性与结构指纹）。产物 `integrations/openai_agents/V11_ELISION_LADDER_PROJECTION.json`。

| M | 末边界可省略索引 | 可省略调用数 | 省略源 | 省略行 | 载荷字节节省 | 恢复失败 | 字面回退 |
|---:|---|---:|---:|---:|---:|---:|---:|
| 6 | `[]` | 0 | 0 | 0 | **0.00 %** | 0 | 无 |
| **4** | `[3,4,5,6]` | 2 | **3** | **130** | **+8.04 %** | 0 | 无 |
| 2 | `[3,4,5,6,7,8,9,10]` | 4 | 10 | 401 | **+23.96 %** | 0 | 无 |

**选择规则（预注册）**：取**投影为正的 M 中最大的那个**（最保守）。→ **M\* = 4**，允许付费。

## 冻结与不可变性

`integrations/openai_agents/REPO_DIAGNOSTIC_V11_ELISION_RATIO_FREEZE.json` **一经写定不再修改**；需要修订时另建 `*_FREEZE_AMENDMENT_<日期>.json` 记录"改了哪个字段、原因、新旧 SHA256"。本批次 manifest 另外记录**冻结文件自身的 SHA256**，因此事后改动可被检出。此前 v7/v9/v10 的原地哈希刷新已在 `REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json` 中补记。

## 验收线与三种合格结局（预注册）

**插件严格质量 ≥ 基线 且 配对完整总 token 为正**，两条都要。

- **质量保住且为正** → 本宿主出现第一个有效节省候选，给出 M\*；
- **质量仍掉** → 收口：该任务上任何省略比例都掉质量（与原生摘要臂同现象互证）；
- **投影全零** → 不付费，直接收口。

## 矩阵与预算

- 任务 `django_long_investigation`（与 Django 同一公开问题、同三条公开源码范围、同一条只读调查协议消息）；矩阵 1 任务 × 3 重复 × 3 臂 = **9 样本**；批次 ID `openai-repo-diagnostic-v11-elision-ratio-boundary`；
- 模型 `deepseek-v4-flash`、endpoint `https://api.deepseek.com`、temperature 0、禁隐藏 thinking；`--max-output-tokens 1024`、`--max-turns 10`、`--max-api-requests 200`；provider soft/target/hard 2000/1500/6000；三臂轮换、逐样本落盘、`data_class = public_source_diagnostic`。

## 运行

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_openai_agents_elision_ratio_v11 -v
& .\.venv\Scripts\python.exe -m experiments.runners.openai_agents_elision_ladder_projection_v11
$env:DSH_V11_RECENT_CALLS_KEPT='4'
& .\.venv\Scripts\python.exe -m experiments.runners.run_openai_agents_repo_diagnostic_v11 --plan --scenarios django_long_investigation --methods none,pruner_v1,native_summary --repeats 3 --provider-soft 2000 --provider-target 1500 --provider-hard 6000 --max-output-tokens 1024 --max-turns 10 --max-api-requests 200 --experiment-id openai-repo-diagnostic-v11-elision-ratio-boundary
& .\.venv\Scripts\python.exe -m experiments.audits.audit_openai_agents_repo_diagnostic_v11 runs/stage5-openai-agents-api/openai-repo-diagnostic-v11-elision-ratio-boundary --freeze integrations/openai_agents/REPO_DIAGNOSTIC_V11_ELISION_RATIO_FREEZE.json --batch-name openai-repo-diagnostic-v11-elision-ratio-boundary
```

## 边界

- 只有 1 个任务、3 个重复、9 个样本；任务输入与 v8–v10 相同，不是未参与调参的盲测确认批；禁止与其他宿主百分比合并。
- 阶梯只有 {6,4,2} 三点；**不为了凑正数再调 M 或换任务**（预注册纪律），三点之外的 M 未测。
- replay 用的是已记录的真实载荷**结构**（公开输入的确定性重建）；字节与记录不同（付费走 chat-completions 传输）已在夹具与协议明示。
- 按迭代上限，本轮只跑一个付费批次。
