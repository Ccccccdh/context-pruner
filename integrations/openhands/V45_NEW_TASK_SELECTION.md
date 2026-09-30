# v45 new task selection (frozen before gate and paid runs)

The v40 candidate order was followed after `django__django-16032`. Decisions below use only metadata and public baseline code. No reference fix or host test patch was read.

| Candidate | Fixed source bytes | Decision | Baseline evidence |
|---|---:|---|---|
| `sphinx-doc__sphinx-10673` | 43,553 | Skip | Three target modules total less than the predeclared 50 KB proxy. |
| `pylint-dev__pylint-6386` | 72,404 | Skip | The `-v` defect is already localized to `pylint/config/utils.py`: preprocessing handles `--verbose`, and a branch bypasses all arguments without `--`. This is a short parsing path despite total size. |
| `django__django-15563` | 86,172 | Enter zero-API gate | `SQLUpdateCompiler.pre_sql_setup()` selects the child model's PK values and stores them as `related_ids`; `UpdateQuery.get_related_updates()` applies those values to ancestor models, whose PK may be a different parent link under multiple inheritance. The reasoning spans compiler, subquery construction, and model inheritance. |

The Django instance has two FAIL_TO_PASS labels and 29 PASS_TO_PASS labels in `model_inheritance_regress`. The fixed baseline commit is `9ffd4eae2ce7a7100c98f681e2b6ab818df384a4`. File size is only a weak proxy: a successful gate would justify a small pilot, not prove the compression threshold will be reached.
