# v13 Stage A：留出任务载荷获取（`psf__requests-1766`）

批次 `openai-repo-diagnostic-v13-requests1766-acquisition-01`；冻结文件
`integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json`，manifest 记录其 SHA256
`e1f5bb3ad3b03a13bc7048c7d0e1204df884e5ab0cf0e9c1ed753be4ee93d694`（审计重算一致）。

**本批只作载荷获取，不是节省证据，也不是质量等价证据**：manifest 里实际写入
`purpose`、`citable_as_saving=false`、`citable_as_quality_equivalence=false`（见下"manifest 修正"）。

## 1. 任务与冻结（Stage 0）

| 项 | 值 |
|---|---|
| instance / repo | `psf__requests-1766` / `psf/requests` |
| base_commit | `847735553aeda6e6633f2b32e14ba14ba86887a4` |
| difficulty / PASS_TO_PASS | `<15 min fix` / 79 |
| 取源方式 | **允许的只读取源**：`.tooling/scratch/psf-requests-1766`（在 `.gitignore` 内）中执行 `git init` → `git remote add origin https://github.com/psf/requests.git` → `git fetch --depth 1 origin <base_commit>` → `git checkout FETCH_HEAD`（5.7 秒，HEAD 校验等于 base_commit）。**未对本仓库执行任何 `add/commit/push`，未对上游做任何写操作，未使用 `raw.githubusercontent.com`** |
| 视图 V1/V2/V3 | `requests/auth.py` 58–149、`requests/models.py` 451–472、`requests/sessions.py` 232–270 |

已取源文件 SHA256（**从检出工作树读取**；与批次实际使用的副本逐字节一致）：

| 文件 | 字节 | SHA256 |
|---|---:|---|
| `requests/auth.py` | 6063 | `e9ac12db8dfb81a4abe59e5e3b1764e01fe1f04dbdc5f57dc7deb7a47695087c` |
| `requests/models.py` | 24715 | `d67cf75ca48670f07fbebfb53637f338111603fd41f58de87aaa4da2ff4c3d2e` |
| `requests/sessions.py` | 18844 | `7519189c53263cb28b294dc79e42e6a0a9ce9290fc4c538b544e640867866b0e` |
| `test_requests.py`（基线版） | 31704 | `2c482f0ad0b1c5e7465a95bcf19cab5834c3dca3c4c8b755c2dd990bdaac34cd` |
| `.tooling/swebench-verified/psf-requests-1766/instance.json` | — | `daabed7ba113157cdd2beda19de482b94dc690ceb582efa012d9c722cbfe28fd` |

判据元数据（**只登记哈希，不作机制输入、不发送、不用于评分**）：`problem_statement` 897 B、`test_patch` 571 B
（SHA256 `8104cc2c…`）、`reference_patch` 621 B（仅哈希，从未打开）、`FAIL_TO_PASS` 6 项、`PASS_TO_PASS` 79 项；
落盘位置 `.tooling/swebench-verified/psf-requests-1766-harness/`（沿用仓库既有 `make_instance_dir.py` 布局）。
完整登记见 `integrations/openai_agents/V13_REQUESTS1766_STAGE0_REGISTRATION_20261005.json`。

**FTP 与 base 树（更正此前表述）**：`test_DIGESTAUTH_QUOTES_QOP_VALUE` 不在 base 树，是因为
**评测 test patch 把它加进来**——这是 SWE-bench 的正常机制，**不是**候选或取源缺陷。base 失败态由源码
本身确立：`requests/auth.py:147` 以**未加引号**的形式发出 qop 值
（`base += ', qop=auth, nc=%s, cnonce="%s"'`），且基线树中不含修复后的 `qop="auth"`。

## 2. 实测（9 项中的 1 项：单臂 none、1 任务 × 1 重复）

| 项 | 值 |
|---|---|
| 样本 | 1（`success=True`、`final_format_correct=True`、`answer_correct=True`） |
| 模型调用 / 边界 | 7 / 7 |
| API 请求尝试 | **7**（上限 12） |
| 失败样本 | 0 |
| 完整总 provider token | 21,295 |
| 逐边界项数 | 3/5/7/9/11/13/15；`turn_count=1`（工具项连续） |
| 六个输出的 SHA256/字符数 | `a78edd05`/3514、`c6dd0e79`/886、`f178e23d`/1716、`a78edd05`/3514、`c6dd0e79`/886、`f178e23d`/1716（即两轮读取产生 3 组逐字重复） |

## 3. manifest 修正（必须留痕）

批次的样本、证据、report、csv 全部写完之后，共享 report 步骤在单臂批次上抛
`KeyError: paired_n`（它假定存在配对比较），因此 runner 自己的 manifest 修正没有执行。
`.tooling/amend_v13_stage_a_manifest.py` 先复核批次（1 行样本、7 个已记录边界、请求数在冻结上限内），
再写入冻结要求的键，并把这次修正连同"为什么"记录在 `manifest.manifest_amendments` 里。

**独立审计**：`experiments/audits/audit_openai_agents_requests_acquisition_v13.py` →
`audit.json`：`complete=true`、`errors=[]`、7 次请求、冻结哈希一致、三个引用标志均为 false。
（审计的字面 presence 检查按边界作用域：第 k 个边界只可能已经读过 0..k-1 号视图。）

## 4. 边界声明

- 这是**一个任务的一次重复**，只证明"该留出任务有可重放的真实载荷"，不构成任何节省或质量结论。
- 该批次不得与 Stage C 的百分比合并；Stage A 只用于 replay 门的前置证据。
