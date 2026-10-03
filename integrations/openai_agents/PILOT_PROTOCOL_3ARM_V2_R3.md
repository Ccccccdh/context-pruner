# OpenAI Agents v2 校准协议：三次重复稳定性小试

## 问题与边界

`deepseek-runner-3x3-v2cal` 每场景只有一次执行。本批 `deepseek-runner-3x3-v2cal-r3-stability` 在同一合成任务集、同一三臂和同一阈值下，对每个场景独立执行三次，以检查节省和任务正确性是否稳定。新批的 repeat 0、1、2 使用不同 codename 与逐渐增加的历史长度；v2cal 的旧 repeat 0 保留为历史参照，不并入新批统计，也不称为随机化因果估计。

三臂：`none`、`pruner_v1`、`native_summary`；场景：`single_tool`、`parallel_tools`、`multi_tool_chain`。模型 `deepseek-v4-flash`，端点 `https://api.deepseek.com`。只发送生成的合成历史、工具参数和合成工具结果；不读取工作区源码或宿主真实任务。`temperature=0`，但模型服务仍可能存在执行波动。

## 冻结条件

- provider 声明预算 soft/hard/target = 1200/3000/900 token；按 0.368 比值换算为本地估算单位 3261/8152/2446。两个主动臂均使用 `symmetric_budget`，其余 runner 默认参数与 v2cal 相同。
- 每个 Agent 样本最多 6 轮模型调用；每个模型调用最多 2 次重试，原生摘要最多 16 次调用。因此单样本 API 尝试理论上限为 34 次（18 个 Agent 尝试 + 16 个摘要尝试）。整批 27 样本的全局硬上限 `--max-api-requests 240`；runner 超额即停止。
- 输出长度上限：Agent 每次 256 token，原生摘要每次 1024 token。本地估算 `hard=8152` 不是供应商强制 token 截断；按 34 次调用 × (8152 输入 + 1024 输出) 得到每样本约 312,000 token、整批约 8.42M token 的**保守规划警戒线**，并非代码保证或价格承诺。原始输入与辅助请求均按供应商 `usage` 单独计费与审计。样本超过规划线须在结果中说明，不能隐去。
- 固定臂顺序轮换由 runner 的 `(repeat + scenario_index) mod 3` 决定。运行期间不改 runner、预算模块、触发器、原生摘要器和评估口径。源文件 SHA256：runner `CBB47DF340603BEAF6F44D5FB7CC8345041293F82E15EC196F551D82A7F69BFC`；token policy `E1D3C0DE34C1BA55A3400A24CA71EA514363B7C9CDCF0CA4E8449382A58474CB`；trigger gate `931FD869733029F2F47604F6D99FDAEF5DA542840C6CB8C806360870CB3C5B23`；native summary `2B05DB2560AB80D8ECA9AAEE5828209F9439D0682D200CF8E6A7F01AB5F7FCCD`。

## 零 API 门控与运行

零 API：运行 `--plan`，现有 `.tooling/gate_openai_agents_3arm.py`，及 `tests.test_stage5_openai_agents_api_experiment`。确保新输出目录为空，密钥只检查存在性、不打印。

```powershell
$env:PYTHONPATH = (Get-Location).Path
& .\.venv\Scripts\python.exe experiments\runners\run_openai_agents_api_experiment.py `
  --plan --methods none,pruner_v1,native_summary --repeats 3 `
  --max-api-requests 240 --experiment-id deepseek-runner-3x3-v2cal-r3-stability

& .\.venv\Scripts\python.exe experiments\runners\run_openai_agents_api_experiment.py `
  --confirm-send-synthetic-data --methods none,pruner_v1,native_summary `
  --repeats 3 --max-api-requests 240 `
  --experiment-id deepseek-runner-3x3-v2cal-r3-stability
```

付费输出目录：`runs/stage5-openai-agents-api/deepseek-runner-3x3-v2cal-r3-stability/`。不覆盖旧目录。运行结束后独立地从 `samples.jsonl` 重算样本数、质量率、逐配对输入与含辅助开销总 token、请求账本及源哈希，再与 `report.json` 交叉核对。

## 预先判读

主要指标：9 个场景×重复配对中插件成功率与无压缩相同、结构安全全通过，且至少 7/9 配对的含辅助开销总 token 节省为正。逐场景均值、整体均值和峰值是描述性指标；失败、重试与截断均计入全样本，不只筛成功轨迹。若样本少于 27、结构破损、请求触顶或哈希变化，批次不作稳定性结论。原生摘要作为主动基线单独报告，不以它失败衬托插件。此实验只检验合成 OpenAI Agents 宿主，不能外推到真实软件任务或其它 Agent。
