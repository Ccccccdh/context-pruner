# v53 加速审计器：审计实施变更记录

这是对冻结 `audit_validation_v53.py` 的**独立复核实现**，不是 v53 付费 runner 或原审计器的热修。入口 `audit_validation_v53_fast.py`；辅助模块 `audit_v53_fast_core.py`。二者均为新文件。运行时依然检查冻结 manifest 中 **105 项**源码 SHA256 以及 Windows shim 哈希，并在 `audit-fast.json` 单独记录这两个新审计源码文件的 SHA256。不会改写旧 `audit.json`、`REPORT.md` 或样本 `evaluation-final`。

## 相同的验收语义

逐任务仍由 `validation_tasks_v49._bind(task)` 绑定 v45 的目标；对候选 workspace 做独立 `copytree`，在评分副本调用原有 `_host_patch`，使用同一个 `TEST_PYTHON`、`_test_environment`、`REGRESSION_TIMEOUT`，执行同一 `tests/runtests.py [regression_module] --noinput --parallel=1 --verbosity=1`。`passed` 仍严格取 subprocess `returncode == 0`；审计行中的 `artifact_success` 仍为 `passed and file_boundary_ok`。Agent 请求、摘要、事件、引用完整性、配对统计沿用冻结审计器的检查。

## 有意改变的 I/O 与边界证明

v45 `evaluate` 对候选原工作区在测试前后各做一次全树 `hashes`。新评分函数不做这两次哈希，仍在独立评分副本运行测试。审计器在评分后对**候选工作区所有非缓存文件**独立算一次路径→SHA256 映射，与运行时保留的 `original_hashes.json` 比较全部新增、删除和内容变化；任何白名单以外差异都使审计失败。哈希计算使用最多 8 个线程、每批最多 256 个排队文件，仍对每个文件读取完整字节并算 SHA256，无缓存和元数据替代；`map_digest` 对路径排序以保持串行/并行结果一致。允许编辑文件在评分前后另用内容 digest 比较，确保本机评分未改变候选编辑结果。与原审计器只读取白名单文件并信任 runner `changed_files` 的方式相比，这一项更严格；依旧与 runner 的 `changed_files` 做交叉核对。它证明评分后的完整最终状态和允许文件不变；不声称观察到测试期间已经恢复的临时变动。

恢复用单独 `.audit-fast-checkpoint.json`。每项保存完整审计行，以及 manifest、新审计代码、样本报告/会话输入、完整候选文件映射、测试日志的哈希；写临时文件并 `os.replace`。恢复时重新算一次全树 SHA256，只有所有指纹及日志匹配才跳过宿主测试并还原审计行。失配或上次中断留下的 `evaluation-audit-fast/` 被改名保留，然后重做验收。凭据扫描仅在 batch 根目录 `workspaces/` 下剪枝**已由全树 SHA256 边界验证**的工作区目录；其它任何同名 `workspaces`/`host-workspace` 目录仍扫描，未验证的工作区也不跳过。扫描另覆盖每个已验工作区的 `TASK.md`、实际编辑及新增文件。没有环境密钥时明确记 `credential_scan_performed=false`、匹配数 null，不宣称零泄漏。

## 启用门槛

先跑小型零 API 单元测试：`python -m unittest -q tests.test_audit_v53_fast_core`，覆盖白名单允许变化、白名单外内容变化/新文件，以及 checkpoint 对 manifest/审计代码/样本输入/工作区/日志失配的失效。再在历史 v52 批次中选择至少一个宿主测试通过与一个失败的样本，用 `--allow-incomplete --sample <name>` 对照冻结审计：`passed`、`ran`、`summary`、完整边界、输入 token 和 artifact_success 必须一致。历史样本仅产出 `audit-fast-smoke.json`、`REPORT-fast-smoke.md` 与各样本 `evaluation-audit-fast/`，不覆盖旧审计。**等价对照通过前，不把本审计器用于 v53 最终结论。**

示例：

```powershell
.\.venv-openhands\Scripts\python.exe integrations\openhands\audit_validation_v53_fast.py `
  --out runs\stage5-openhands\django-multitask-v52-pilot-01 `
  --allow-incomplete --sample <通过样本目录> --sample <失败样本目录>
```

历史等价门控成功后，完整运行 v53 使用新审计器同一入口、`--out runs\stage5-openhands\django-multitask-v53-pilot-03`，无 `--sample`；`audit-fast.json` 与冻结运行报告的逐样本验收及 `summary.json` 配对结论交叉比较。历史 v52 的两个样本另与其已完成的冻结 `audit.json` 对照。本地 I/O 加速不触发模型 API。
