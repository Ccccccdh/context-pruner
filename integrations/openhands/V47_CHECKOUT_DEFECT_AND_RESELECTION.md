# v47 检出方案缺陷与新实例重选（记录于任何付费请求之前）

## 1. 决定性缺陷：检出是版本混装，不是真正的 base 检出

上一轮为 `django__django-15561` 与本轮为 `django__django-13212` 构建的"base 检出"，
做法是**复制 `django-15563` 的工作树（Django 4.1.0a1）**，再用 HTTPS 覆盖若干文件到目标提交的版本。

实测证据：

| 检查 | 结果 |
|---|---|
| `django-13212/django/__init__.py` | `VERSION = (4, 1, 0, "alpha", 0)` —— 仍是 **4.1.0a1** |
| 被覆盖的 `django/core/validators.py` | 19,532 B，blob SHA-1 与 `f4e93919…` 一致（**Django 3.1 时代**代码） |
| 被覆盖的 `django/forms/fields.py` | 46,925 B，blob SHA-1 与 `f4e93919…` 一致（**Django 3.1 时代**代码） |
| 门控回归模块 `forms_tests` | 子进程 CPU 时间累计 **1754 秒**后仍未结束 → 忙循环，不是性能问题 |

即：**解释器里 4.1 的框架代码与 3.1 的两个模块混在一起**，回归模块因此陷入忙循环。
`validation_tasks_v45.py` 的 `_test_environment` 把工作区放在 `PYTHONPATH` 最前，
所以导入的就是这个混装树。**该检出不能用于门控，也不能用于实验。**

**由此必须撤回的结论**：上一轮 `django__django-15561` 的门控结果（"原版目标测试即通过 →
无区分度"）是在同一个混装检出上得到的，**同样不可信**。
（`django__django-15563` 的 v45/v46 门控**不受影响**：它的检出是真克隆在工作树中的完整检出，
不是复制品，且当时断言 `ran=32` 等与预期一致。）

## 2. 为什么当时退回复制方案

`git` 在本沙箱内无法联网：`schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`，
因此 `git clone` 与 partial clone 的惰性 blob 拉取全部失败；目标是加入一个未克隆过的实例。

## 3. 修复方案（待验证）

不再复制工作树，改为**真正的整树导出**，逐文件做 blob 校验：

1. 在已存在的完整对象库中定位目标提交（`git cat-file -e <commit>` 已确认可达）；
2. 用 `git archive <commit> | tar -x` 或 `git checkout-index` 导出**整棵树**，
   避免任何工作树复制造成的版本混装；
   - 若惰性 blob 拉取再次失败，则改由 HTTPS 逐文件下载整棵树中 `*.py` 等实际引用到的文件，
     并对**每一个**文件做 git blob SHA-1 校验（当前只校验了 3 个文件）；
3. 导出后断言 `django/__init__.py` 的 `VERSION` 与目标提交一致，
   且 `git status`/全量哈希与提交内容一致；
4. 只有在整树校验通过后，才允许重跑门控。

## 4. 决定性发现：候选实例与测试解释器版本不兼容

整树导出（`codeload`）后得到 `django-13212` 在 `f4e93919…` 的**真实版本**：

```
VERSION = (3, 2, 0, 'alpha', 0)
```

而 `validation_tasks_v45.py` 的 `TEST_PYTHON` 是 `.tooling/venv-django-16263`，**Python 3.12.14**。
实测：

```
django/utils/version.py line 6: from distutils.version import LooseVersion
ModuleNotFoundError: No module named 'distutils'
```

**Python 3.12 已移除 `distutils`，Django 3.2.0a1 连导入都失败。**
因此 `django-13212` 在当前测试解释器下**根本无法运行**，回归模块的"1754 秒 CPU 忙循环"
正是这一不兼容的表现。之前基于混装检出的门控结论（含 `15561` 的 FAIL 判定）**全部不可信**。

### 选型必须新增的过滤条件

在 v40 元数据规则之外，实例还须满足：**其 Django 版本能在现有测试解释器下导入**。
Python 3.12 可用的 Django 版本下限约为 **4.1**（更早版本依赖已移除的 `distutils`／旧 API）。

按此条件复核 tier-2 的合格候选（需先用整树导出确认各自版本的 `VERSION` 行）：

| 候选 | created_at | 预期 Django 世代 | 版本兼容性 |
|---|---|---|---|
| `django__django-15629` | 2022-04 | 4.1 时代 | 兼容，但 FAIL 标签是自然语言，不可用 |
| `django__django-13212` | 2020-07 | **3.2.0a1（实测）** | **不兼容** |
| `django__django-11400` | 2021-04 | 3.2 时代 | 需实测，可能不兼容 |
| `django__django-11138` | 2020-04 | 3.1 时代 | 需实测，很可能不兼容 |
| `django__django-10554` | 2019-10 | 2.2/3.0 时代 | 需实测，很可能不兼容 |

**结论**：tier-2 里时间较老的候选大概率都不可用，选择面会收敛到 Django 4.1 时代的实例
（即 2022 年及以后）。这也解释了为何 v45/v46 用的 `15563`（2022-04）能正常工作。

### 两条可行路线（择一，均零 API）

1. **按版本兼容性重新选实例**：只选 Django ≥ 4.1 世代的 Verified 实例，
   用整树导出确认 `VERSION` 后再门控。改动最小，且与 v45/v46 环境可比。
2. **为老实例另建解释器**：新建 Python ≤ 3.11 的 venv（含 `setuptools` 以提供 `distutils`），
   把解释器路径写入实例元数据。成本高，且与 v45/v46 环境不可比。

推荐路线 1。它需要放宽“难度 15 分钟~1 小时 + 2~4 文件 + ≥50 KB 代理”三者同时满足的限制——
当前 tier-1 已耗尽、tier-2 又多为老版本，因此下一步应回到 v40 元数据池，
**以“Django ≥ 4.1 世代”为一等过滤条件**再排序，并在选择文档中记录这一新增前提。

## 5. 重新评估候选顺序（待整树检出可用后执行）

原先排除/选中的依据必须在正确检出上重做：

| 候选 | 先前结论 | 需要重做 |
|---|---|---|
| `django__django-15561` | 门控 FAIL（原版即通过） | **重做**：结论来自混装检出，不可信 |
| `django__django-13212` | 选中 | **作废**：Django 3.2.0a1 与 Python 3.12 不兼容 |

按 v40 冻结顺序，其后依次为 `15103`（41,091 B，低于 50 KB 代理 → 仍跳过）、
`14170`（49,589 B → 仍跳过）、`12155`（24,816 B）、`11333`（32,611 B），
其后 tier-1 再无 2–4 文件候选；tier-2 合格顺序为
`15629`（FAIL 标签不可用）、`13212`（版本不兼容）、`11400`、`11138`、`10554`。

## 6. 另一处需要复查的依据

`validation_tasks_v45.py` 内的 `TEST_PYTHON` 指向 `.tooling/venv-django-16263`（Python 3.12）。
对 Django 4.1 时代实例没问题；选中更老的实例时必须按第 4 节处理。

