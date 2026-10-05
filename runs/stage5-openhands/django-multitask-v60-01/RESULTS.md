# v60：预注册“读数更细”的前缀延长 + 更宽的分叉窗口 —— 门控通过，但本批**没有出现合格前缀**

状态：**三硬标志一个都没有取得**（无法取得，见 §3）。批次在预注册选择的阶梯上走完三次尝试，
按冻结规则如实报告 `mechanism still not triggered`，**未发任何分叉请求**，退出码 3。
本批按约定**不修改任何压缩机制阈值**，也不把成本数字当作质量结论。

- 协议：`integrations/openhands/PILOT_PROTOCOL_V60.md`（本文件 §1 的算术与提示词改动在付费前冻结）
- 冻结清单：`manifest.json`，SHA256 **`584A89CDC6BA5FFB5D369068D75244F271A9381AF41B44C2627053BBB344A052`**，
  111 个源文件，包含审计器与门控本身；`--check` 之后与付费运行期间实测**零漂移**
- 独立审计：本批**无法运行**（见 §4），如实记录，不以运行记录冒充审计结论

## 1. 本批预注册的内容（相对 v59，只此六项，机制阈值全部未动）

```
共享上限 132 = 前缀预算上限 42 + 每臂保留额度 30 × 3 臂   （v59：84 = 42 + 14 × 3）
prefix_correction_window = min(132 − 30, 42, work_used + 36)  ← 仍以 42 为唯一前缀上限
分叉纠错窗口 = min(132, 前缀请求 + 30)                      ← 由 10 提到 30
prefix_host_feedback_rounds: 3 → 6                          ← 读数更细
max_total_estimated_input_per_sample: 4,670,000 → 7,330,000  ← 按 132/36 同倍
冻结提示词新增只读调查前置条件（"read-only investigation" / "premature and will be reverted"）
```

未改：触发 28,000 / 目标 22,400 / 硬 39,200 / 原生摘要 33,600、阶梯 `[24, 26, 28]`、
每档 phase-1 上限 `[26, 28, 30]`、`initial_work_requests = 24`、
`prefix_correction_call_limit = 36`、`minimum_prefix_requests_to_trigger = 30`、
工具边界、模型/温度、任务集合与顺序、逐分叉持久化与审计方式。

## 2. 零 API 门控（付费前，全部通过）

```powershell
cd 'C:\Users\LENOVO\Desktop\AI Agent\code'
New-Item -ItemType Directory -Force -Path .tooling\tmp | Out-Null
$env:TEMP='C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp'; $env:TMP=$env:TEMP
& .\.venv-openhands\Scripts\python.exe -m pytest `
    tests/test_openhands_same_prefix_fork_v54.py tests/test_openhands_v54_branch_loopback.py `
    tests/test_openhands_v54_formal_runner_gate.py tests/test_openhands_v55_formal_runner_gate.py `
    tests/test_openhands_v56_formal_runner_gate.py tests/test_openhands_v57_formal_runner_gate.py `
    tests/test_openhands_v58_formal_runner_gate.py tests/test_openhands_v59_formal_runner_gate.py `
    tests/test_openhands_v60_formal_runner_gate.py -q `
    --basetemp="C:\Users\LENOVO\Desktop\AI Agent\code\.tooling\tmp\pt60"
```

**85 passed**（既有 51 + v59 门控 18 + v60 门控 16）。v60 门控新增：上限恒等式
`132 = 42 + 30×3`、`branch_request_reserve ≥ host_feedback_correction_reserve`（30 ≥ 30）、
前缀越 42 或跌破保留即抛错、v60 与 v59 的四个机制阈值/阶梯/闸门余量逐项相等、
`prefix_prompt` 必须含只读调查两句话、规则指纹与 v59 逐位相同、三标志门在缺宿主判定或
压缩 0 次时必须 `quality_verdict_obtained: false`。

真实 Django 树 `--check` 也通过：124+125（17084，`errors=1`）、162+163（16661，`failures=1`）、
74+75（16100，`failures=1, skipped=7`），工作区 20,128 / 19,972 / 19,939 文件。

## 3. 付费段实测：三次尝试全部**通过**宿主目标测试，因此没有候选前缀

| 尝试 | 任务 | 档 | 宿主目标测试 | Agent 请求 | 纠错轮 | 触发预测 | 该次完整总 token |
|---|---|---:|---|---:|---:|---|---:|
| 1 | `django_referenced_window_wrapping` | 0 | **通过** | 15 | 1（第 1 轮以 budge 守卫结束） | 未预测（无需纠错） | 197,954 |
| 2 | `django_lookup_allowed_foreign_primary` | 0 | **通过** | 20 | 3 | 未预测 | 459,430 |
| 3 | `django_list_editable_atomicity` | 0 | **通过** | 8 | 1 | 未预测 | 67,380 |

- 三次尝试合计 **724,764** 完整总 token（各计一次）；阶梯按冻结顺序走完第 0 档后，
  三个任务都已“通过”，按规则**不再在更高档重跑**，因此选择判定为
  `no attempt in the frozen ladder both failed the host target test and satisfied the frozen
  trigger prediction: mechanism still not triggered`。
- **v60 的额度算术本身完全成立**（这是本批可复用的正面结论）：三次尝试的
  `prefix_reserve_at_end` 分别为 `used=15 / each=39`、`used=20 / each=37`、`used=8 / each=41`，
  `reserve_satisfied: true`；`132 − 42 = 90 ≥ 30×3` 的保留额度从未被触碰，
  前缀纠错窗口实测为 `min(102, 42, 6+36) = 38`，与预注册一致。
- **本批的负面结论与 v59 04/05 同源、且更明显**：v60 把分叉窗口从 10 提到 30、
  把前缀反馈轮从 3 提到 6、并加了只读调查前置条件之后，三次尝试分别在
  **15 / 20 / 8** 次请求就把该任务修对（`host_passed: true`），比 v59 的
  9–41 次更快，**根本没有进入“宿主未通过且前缀够长”的状态**。
  也就是说：这个 provider 在**更宽松的上限下反而更容易交出正确修复**，
  而冻结规则要求 fork 点必须“未通过”，于是候选前缀消失。
- 该批**没有产生 `prefix-freeze.json`、没有前缀快照、没有分叉请求**，
  因此 §1 的三硬标志（`branch_host_verdicts_measured`、插件压缩 ≥ 1、
  `quality_verdict_obtained`）在本批**不适用/未取得**：本批不存在任何分叉。

## 4. 审计：本批无法运行，如实记录

`audit_validation_v60.py` 的预注册前提是“本批存在冻结前缀”（它要核对
`prefix-freeze.json`、`frozen-hashes.json`、`prefix-snapshot` 与三条分叉的持久化字节）。
本批按规则**没有冻结前缀**，因此审计不可运行——这不是失败被掩盖，而是**本批没有可分叉的证据**。
`frozen_experiment_sources_valid` 在本批只能以“运行期零漂移”这个较弱的形式陈述
（`manifest.json` 的 111 个源在 `--check` 后、付费前、付费后逐字节一致，实测漂移 0），
**不能**写成审计器的判定值。

## 5. 分段计时（秒，实测）

| 分段 | 数值 |
|---|---:|
| `prepare_copy_hash_seconds`（三次任务的复制与哈希） | 558.16 |
| `prepare_reuse_verify_seconds` | 18.86 |
| 模型等待（前缀）`prefix_model_wait_seconds` | 346.04 |
| 宿主测试 `host_test_seconds` | 1,053.92 |
| 触发预测 `trigger_prediction_seconds` | 0.04（三次都无需预测） |

墙钟：付费段约 30 分钟（09:16→09:44）；`--check` 段约 30 分钟。磁盘由 4.56 GB 降到 4.36 GB。

## 6. 下一步的两个可选方向（本批不开，需先报告再决定）

1. **v61：换更硬的实例**（用户允许的另一条预注册手段）——选一个 `reference.patch` 触及
   更多文件或契约更长的 Django 实例，使模型在 42 次内**难以**修对，从而稳定产出
   “宿主未通过 ∧ 前缀越过 28,000”。需要新增数据集门控与 (`--check`) 基线，成本较高但方向明确。
2. **v61：把只读调查变成硬前置**——现在的提示词要求“先完成只读调查”，但模型仍可在
   第 3 次请求就进入 work 阶段并编辑。若把“必须先提交阶段性只读报告再编辑”写成
   **预算阶段**（而不是提示词请求），需要改预算策略（属于新机制版本，需单独预注册）。

两条路都不构成本批的结论，本批只能报告：**v60 的算术与门控可用，但在这三个任务上
“宿主未通过”这一 fork 前提没有出现，因此三硬标志批仍未完成。**

## 7. 边界声明

- 本批 **0 个前缀 × 0 条分叉**，不能估计插件在任务总体上的平均效果，也不能外推到其他宿主。
- 三次尝试的 token 只说明“本批花了多少”，不构成任何压缩收益或质量结论。
- 与 v59 一样：**不得**把成本数字当作质量等价的节省；也**不得**把“宿主通过”解释为
  插件或机制的效果（本批根本没有插件臂参与）。
