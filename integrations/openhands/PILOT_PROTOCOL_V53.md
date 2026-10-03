# v53 阈值提高消融批次（3 实例 × 3 组 × 3 重复 = 27 样本）

冻结说明。v53 相对 v52 **只改一处**：允许积累多少上下文才压缩。其余（任务、臂、工具、导航、
提示词、预算策略、每样本上限、v51 引用化压缩器、成功判定、审计脚本、重复次数）逐项不变。

## 1. 为什么单独做这一批

`V48_V49_DIAG_REVIEW.md` §3.4 要求：阈值应作为**单独消融**，不得与 v52 的"引用化"改动混评。
v52 只改了"摘要里是引用还是正文"，本批只改"什么时候触发压缩"。

## 2. 唯一改动

| 字段 | v52 | v53 | 折算 provider token（比值 0.900） |
|---|---:|---:|---:|
| `trigger_input_tokens` | 20,000 | **28,000** | 18,000 → **25,200** |
| `pruner_target_tokens` | 16,000 | **22,400** | 14,400 → **20,160** |
| `pruner_hard_tokens` | 28,000 | **39,200** | 25,200 → **35,280** |
| `native_max_tokens`（原生臂上下文额度） | 24,000 | **33,600** | — |

倍数统一为 **1.4**。原生臂的上下文额度按同一倍数放大，否则本批就变成"只给插件臂放宽阈值"，
而不是"放宽所有臂允许积累的上下文"。

实现方式：`ContextPrunerCondenserV51` 的 `trigger_tokens` / `target_tokens` / `hard_tokens`
**显式传入**（不再依赖类默认值），并在启动断言里核对协议值确实生效。

## 3. 与 v52 的差异清单（可逐条核对）

由 `.tooling/build_v53_from_v52.py` 从 v52 派生，**每个替换都断言"恰好出现一次"**，
意外改动会直接让生成脚本报错。差异仅 5 处：

1. `version` → `openhands-django-multitask-v53-threshold-ablation`；
2. 三个预算字段 + 原生臂 `max_tokens`；
3. 新增 `threshold_ablation` 说明块（含 v52 原值与 provider token 折算；只用字面量，
   manifest 不依赖任何导入）；
4. 源清单指向 `audit_validation_v53.py` / `run_validation_windows_v53.py` / `PILOT_PROTOCOL_V53.md`；
5. 阈值断言改为核对**显式传入**的值（而不是 v51 类默认值）。

## 4. 门控（付费前，零 API）

```powershell
DSH_RUNNER=integrations/openhands/run_validation_windows_v53.py
DSH_RUNNER_ARGS="--out runs/stage5-openhands/django-multitask-v53-pilot-01 --check"
```

`--check` 对三个任务各准备一次干净工作区并核对：
公开模块在未改动基线上全绿；宿主补丁副本在目标测试上失败（与 `.tooling/gates/v49-*.json` 的
冻结记录逐项一致）。通过后写 `baseline.json`，付费运行前还会再断言一次门控仍然成立。

## 5. 主统计量与口径

与 v52 完全一致：

- 主统计量 = 同任务同重复内，各臂相对 `none` 的**分数制 provider 输入节省**，**包含失败样本**；
- 同时报告"含该臂自身辅助开销的总 token 节省"；
- `both_success_api_ok` 子集只作敏感性分析；
- **v53 与 v52 不得合并**：本批是阈值消融，只回答"提高阈值是否改变结果"。
  允许的对比方式是逐任务、逐重复地并列 v52 与 v53 的同名样本。

## 6. 预期与判读规则

提前写死判读规则，避免事后挑选解释：

| 观察 | 结论 |
|---|---|
| v53 的压缩事件数明显少于 v52 | 阈值确实生效（必要条件） |
| v53 跨任务节省高于 v52 | 提高阈值有帮助 |
| v53 与 v52 节省接近（差异在重复间波动范围内） | 阈值不是主要因素，"何时触发"让位于"注入什么" |
| v53 更差 | 更早压缩（v52）更有利 |

## 7. 运行

```powershell
DSH_RUNNER=integrations/openhands/run_validation_windows_v53.py
DSH_RUNNER_ARGS="--out runs/stage5-openhands/django-multitask-v53-pilot-01 --run --resume --repeats 3"
```

结果目录：`runs/stage5-openhands/django-multitask-v53-pilot-01/`
审计：`integrations/openhands/audit_validation_v53.py --out <同上>`
