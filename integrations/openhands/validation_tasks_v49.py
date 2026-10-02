"""Task bindings for the v49 multi-task batch: three Django instances, one module each.

Instances (all gated with a positive/negative host check before any model request):

| task key | instance | Django | target test | regression module | gate |
|---|---|---|---|---|---|
| django_referenced_window_wrapping | django__django-17084 | 5.0.0a1 | aggregation.tests.AggregateAnnotationPruningTests.test_referenced_window_requires_wrapping | aggregation | 125 tests, 1 error -> 0 |
| django_lookup_allowed_foreign_primary | django__django-16661 | 5.0.0a1 | modeladmin.tests.ModelAdminTests.test_lookup_allowed_foreign_primary | modeladmin | 163 tests, 1 failure -> 0 |
| django_list_editable_atomicity | django__django-16100 | 4.2.0a1 | admin_changelist.tests.ChangeListTests.test_list_editable_atomicity | admin_changelist | 75 tests, 1 failure -> 0 |

As in v47, the generic machinery (workspace preparation, hashing, host-patch
isolation, test execution, result parsing) is NOT copied. It lives once in
`validation_tasks_v45` and operates on module-level globals; this module rebinds
those globals per task and re-exports the same function objects, so there is a
single implementation that cannot drift.

Per-task rebinding requires swapping the shared globals around each call, because
`prepare` and `evaluate` read `TASKS` / `SOURCES` / `INSTANCE_DIR` from their own
module. The wrappers below do that switch, so callers keep using the normal
per-task signature.
"""
from __future__ import annotations

import json
from pathlib import Path

import validation_tasks_v45 as _shared

ROOT = Path(__file__).resolve().parents[2]

# Instance directories created from the frozen dataset metadata.
INSTANCE_DIRS = {
    'django_referenced_window_wrapping': ROOT / '.tooling/swebench-verified/django-17084',
    'django_lookup_allowed_foreign_primary': ROOT / '.tooling/swebench-verified/django-16661',
    'django_list_editable_atomicity': ROOT / '.tooling/swebench-verified/django-16100',
}
UPSTREAM_DIRS = {
    'django_referenced_window_wrapping': 'django-17084',
    'django_lookup_allowed_foreign_primary': 'django-16661',
    'django_list_editable_atomicity': 'django-16100',
}

INSTANCES = {
    task: json.loads((directory / 'instance.json').read_text(encoding='utf-8'))
    for task, directory in INSTANCE_DIRS.items()
}


def _test_ids(labels, source: str) -> tuple[list[str], list[str]]:
    """Normalize dataset labels, stripping a method name repeated inside the parens.

    Some instances write the label as ``test_x (module.Case.test_x)``; the shared
    parser then appends ``Case.test_x`` to ``test_x`` and Django reports
    "type object 'Case' has no attribute 'test_x'". Normalization happens here
    rather than in the shared parser so the frozen v45-v48 source hashes stay valid.
    """
    ids, unusable = _shared._test_ids(labels, source)
    normalized = []
    for test_id in ids:
        head, _, tail = test_id.rpartition('.')
        if tail and head.endswith('.' + tail):
            test_id = head
        normalized.append(test_id)
    return normalized, unusable


TASKS = {}
for _task, _instance in INSTANCES.items():
    _ftp, _ftp_unusable = _test_ids(_instance['FAIL_TO_PASS'], 'FAIL_TO_PASS')
    _ptp, _ptp_unusable = _test_ids(_instance['PASS_TO_PASS'], 'PASS_TO_PASS')
    assert _ftp_unusable == [], (_task, _ftp_unusable)
    _regression = _instance['FAIL_TO_PASS'][0].split(' (')[1].split('.')[0]
    TASKS[_task] = {
        'project': 'django',
        'allowed': [],
        'research_files': [],
        'regression': [_regression],
        'regression_module': _regression,
        'target_tests': _ftp,
        'pass_to_pass': _ptp,
        'pass_to_pass_unusable_labels': _ptp_unusable,
        'problem': _instance['problem_statement'],
    }

# Edit scope and read set are frozen before any model request. Each equals the
# single file the reference fix touches, taken from the dataset patch file list.
ALLOWED = {
    'django_referenced_window_wrapping': ['django/db/models/sql/query.py'],
    'django_lookup_allowed_foreign_primary': ['django/contrib/admin/options.py'],
    'django_list_editable_atomicity': ['django/contrib/admin/options.py'],
}
RESEARCH_FILES = {
    'django_referenced_window_wrapping': [
        'django/db/models/aggregates.py',
        'django/db/models/expressions.py',
        'django/db/models/sql/compiler.py',
        'django/db/models/sql/subqueries.py',
        'tests/aggregation/tests.py',
        'tests/runtests.py',
    ],
    'django_lookup_allowed_foreign_primary': [
        'django/contrib/admin/checks.py',
        'django/contrib/admin/utils.py',
        'django/db/models/fields/related.py',
        'tests/modeladmin/tests.py',
        'tests/runtests.py',
    ],
    'django_list_editable_atomicity': [
        'django/contrib/admin/checks.py',
        'django/contrib/admin/utils.py',
        'django/db/models/sql/query.py',
        'tests/admin_changelist/tests.py',
        'tests/runtests.py',
    ],
}
for _task in TASKS:
    TASKS[_task]['allowed'] = ALLOWED[_task]
    TASKS[_task]['research_files'] = ALLOWED[_task] + RESEARCH_FILES[_task]

COMMIT = {task: instance['base_commit'] for task, instance in INSTANCES.items()}
SOURCES = {task: {'directory': UPSTREAM_DIRS[task], 'commit': COMMIT[task]} for task in TASKS}

# The shared module reads TASKS / SOURCES / INSTANCE_DIR / INSTANCE from its own
# globals. Two different consumers need them:
#   * the shared functions themselves (prepare / evaluate / code_revision), which
#     are wrapped below so they bind per call; and
#   * tools that import TASKS directly to validate a task name, such as
#     verification_tool_django_v49.py. Those read the global OUTSIDE any bind
#     call, so TASKS must stay bound to the v49 table permanently - restoring the
#     v45 table after each call made every v49 task name look unknown and aborted
#     the batch before its first model request.
_shared.TASKS = TASKS
_PREVIOUS = {
    'SOURCES': _shared.SOURCES,
    'INSTANCE_DIR': _shared.INSTANCE_DIR,
    'INSTANCE': _shared.INSTANCE,
}


def _bind(task: str) -> None:
    _shared.TASKS = TASKS
    _shared.SOURCES = {'django': SOURCES[task]}
    _shared.INSTANCE_DIR = INSTANCE_DIRS[task]
    _shared.INSTANCE = INSTANCES[task]


def prepare(upstream_root, workspace, task):
    _bind(task)
    return _shared.prepare(upstream_root, workspace, task)


def evaluate(workspace, task, destination, regression_only=False, target_tests_only=False):
    _bind(task)
    return _shared.evaluate(workspace, task, destination,
                            regression_only=regression_only,
                            target_tests_only=target_tests_only)


hashes = _shared.hashes
code_revision = _shared.code_revision
_summaries = _shared._summaries
_test_environment = _shared._test_environment
TEST_PYTHON = _shared.TEST_PYTHON
REGRESSION_TIMEOUT = _shared.REGRESSION_TIMEOUT

# Provenance is per task, so upstream verification is exposed as a helper.
_EXPORT_REPORTS = {
    'django_referenced_window_wrapping': ROOT / '.tooling/export_django_django-17084.json',
    'django_lookup_allowed_foreign_primary': ROOT / '.tooling/export_django_django-16661.json',
    'django_list_editable_atomicity': ROOT / '.tooling/export_django_django-16100.json',
}


def verify_upstream(task: str) -> str:
    """Return the upstream directory name after checking its provenance record.

    A git checkout is accepted only when HEAD equals the pinned commit; otherwise
    the recorded codeload export must name that exact commit and directory.
    """
    import subprocess
    directory = SOURCES[task]['directory']
    checkout = ROOT / '.tooling/upstream' / directory
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                          capture_output=True, text=True)
    if head.returncode == 0 and head.stdout.strip() == COMMIT[task]:
        return directory
    provenance_path = _EXPORT_REPORTS[task]
    assert provenance_path.is_file(), f'no provenance record for {checkout}'
    provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
    assert provenance['base_commit'] == COMMIT[task], (provenance_path, provenance['base_commit'])
    assert Path(provenance['dest']).name == directory, provenance['dest']
    assert 'codeload.github.com/django/django/tar.gz/' in provenance['url'], provenance['url']
    return directory
