# v47 选型记录（第二轮重选，含 v40 规则的一处显式偏离）

记录时间 2026-09-29。本文档替代先前的 `V47_NEW_TASK_SELECTION.md` 结论；先前选中
`django__django-13212` 的依据已作废，原因见
[V47_CHECKOUT_DEFECT_AND_RESELECTION.md](V47_CHECKOUT_DEFECT_AND_RESELECTION.md)。

## 1. 本轮发现的两个硬约束

### 1.1 检出必须是整树导出，不能复制工作树

先前用"复制 `django-15563` 工作树 + 覆盖若干文件"的方式构建 base 检出，
结果把 **Django 4.1.0a1 的框架代码**与**目标提交的模块**混在一起，
造成回归模块忙循环（子进程 CPU 累计 1754 秒）。由此得到的门控结论**全部不可信**，
包括 `django__django-15561` 的 FAIL 判定。

**已修复**：新增 `.tooling/export_tree.py`，用 GitHub codeload 端点
（`https://codeload.github.com/django/django/tar.gz/<commit>`）下载**整棵树**并解包，
不经 git 远端（git 在本沙箱因 `schannel SEC_E_NO_CREDENTIALS` 无法联网）。
已验证：`django-13212` 整树导出 6,286 个文件，`django/__init__.py` 显示
`VERSION = (3, 2, 0, 'alpha', 0)` —— 即先前混装检出确实错得离谱。

### 1.2 实例的 Django 版本必须能被固定测试解释器导入

固定解释器为 `.tooling/venv-django-16263`（**Python 3.12.14**）。实测：

```
Django 3.2.0a1 -> django/utils/version.py: from distutils.version import LooseVersion
                  ModuleNotFoundError: No module named 'distutils'
```

Python 3.12 移除了 `distutils`，**Django 早于 ~4.1 的实例无法运行**。
本轮选择对每个候选**实测其 base commit 的 `django/__init__.py` 版本**，只保留 ≥ 4.1。

## 2. 对 v40 规则的一处显式偏离（需在结项材料中标注）

**问题**：在"版本 ≥ 4.1"之外再要求"参考修复涉及 **2–4 个** Python 文件"，
合格集为**空**——剩余 Django 候选中绝大多数只有 **1 个** Python 文件
（固定工具为每个实例选定单一模块）。实测结果：

| 过滤组合 | 合格候选数 |
|---|---:|
| 版本 ≥ 4.1 + 2–4 文件 + ≥50 KB + 标签可用 | **0** |
| 版本 ≥ 4.1 + 1–4 文件 + ≥50 KB + 标签可用 | **16** |

**处理**：将文件数下界由 2 放宽为 1，**保留 50 KB 弱代理作为主要上下文压力信号**
（v45/v46 使用的 `django-15563` 为 86,172 字节、2 个文件，故 50 KB／1–3 文件属同一量级）。
其余 v40 规则（难度档位、`FAIL_TO_PASS ≥ 1`、`PASS_TO_PASS ≤ 150`、组内从新到旧、
不读参考修复正文与问题陈述）**全部保持**。偏离理由与计数写入本节，以便审计。

## 3. 重选结果（16 个合格候选，从新到旧）

| # | 实例 | created_at | 目标版本 | 文件 | 字节 | ftp | ptp |
|---:|---|---|---|---|---:|---:|---:|
| 1 | **`django__django-17084`** | 2023-07-17 | 5.0 开发线 | `db/models/sql/query.py` | 114,109 | 1 | 107 |
| 2 | `django__django-16661` | 2023-03-18 | 5.0 开发线 | `contrib/admin/options.py` | 98,406 | 1 | 36 |
| 3 | `django__django-16100` | 2022-09-24 | 4.2 | `contrib/admin/options.py` | 97,954 | 1 | 59 |
| 4 | `django__django-15957` | 2022-08-13 | 4.2 | `db/models/fields/related_descriptors.py` | 59,091 | 4 | 89 |
| 5 | `django__django-15930` | 2022-08-07 | 4.2 | `db/models/expressions.py` | 62,628 | 1 | 88 |
| 6 | `django__django-15916` | 2022-08-04 | 4.2 | `forms/models.py` | 60,344 | 2 | 149 |
| 7 | `django__django-15814` | 2022-07-03 | 4.2 | `db/models/sql/query.py` | 114,568 | 1 | 29 |
| 8 | `django__django-15732` | 2022-05-24 | 4.2 | `db/backends/base/schema.py` | 68,998 | 1 | 125 |
| 9 | `django__django-15554` | 2022-03-29 | 4.1 | `db/models/sql/query.py` | 113,654 | 1 | 41 |
| 10 | `django__django-15467` | 2022-02-27 | 4.1 | `contrib/admin/options.py` | 97,886 | 1 | 62 |
| 11 | `django__django-15380` | 2022-01-31 | 4.1 | `db/migrations/autodetector.py` | 67,547 | 1 | 133 |
| 12 | `django__django-15368` | 2022-01-27 | 4.1 | `db/models/query.py` | 90,888 | 1 | 29 |
| 13 | `django__django-15315` | 2022-01-13 | 4.1 | `db/models/fields/__init__.py` | 91,296 | 1 | 33 |
| 14 | `django__django-15104` | 2021-11-19 | 4.1 | `db/migrations/autodetector.py` | 67,541 | 1 | 131 |
| 15 | `django__django-14915` | 2021-09-29 | 4.1 | `forms/models.py` | 58,614 | 1 | 23 |
| 16 | `django__django-14725` | 2021-08-01 | 4.1 | `forms/models.py` | 58,671 | 3 | 64 |

完整数据（含被跳过原因）在 `.tooling/reselection_final.json`。

## 4. 下一候选：`django__django-17084`

- 依据：在合格池中 `created_at` 最新（组内从新到旧）。
- 参考修复仅涉及 `django/db/models/sql/query.py`（114,109 字节，属最大的一档）。
- 需在门控中先确认：整树导出后 `VERSION` 为 5.0 开发线，且能被 Python 3.12 导入
  （5.0 开发线的 `django/utils/version.py` 已不依赖 `distutils`，预期可导入）。
- 若导入或门控失败：**记录失败并继续**，按第 3 节顺序取 `django__django-16661`，
  不再用混装检出，也不据预期收益调换顺序。

## 5. 下一轮的执行清单

1. `python .tooling/export_tree.py django__django-17084 .tooling/upstream/django-17084`
2. 校验整树：`VERSION` 行、`tests/runtests.py` 存在、被改文件与 git blob SHA-1 一致
   （git blob 校验只在本地已有对象时可用；否则以 codeload 整树为准并记录）
3. 生成 `.tooling/swebench-verified/django-17084/` 下的 `instance.json`、`host-tests.patch`、
   `reference.patch`（从 parquet 的 `test_patch` / `patch` 字段导出）
4. 跑门控：base 目标测试必须失败，加参考修复必须通过，评分副本不影响 Agent 工作区
5. 门控通过后才写 `validation_tasks_v47.py` 与 v47 三组运行器、冻结 manifest、备批次目录
6. 最后向用户申请该新 payload 的付费授权；**未获授权不发起任何模型请求**
