"""Task binding for the v47 instance: django__django-17084.

The generic machinery (workspace preparation, hashing, host-patch isolation,
test execution and result parsing) is NOT copied here. It lives once in
`validation_tasks_v45` and operates on module-level globals. This module rebinds
those globals for the v47 instance and re-exports the same function objects, so
there is exactly one implementation and it cannot drift between versions.

Instance summary (from the frozen dataset metadata and the zero-API gate):

* base commit `f8c43aca467b7b0c4bb0a7fa41362f90b610b8df`, Django 5.0.0a1 era
* reference fix edits `django/db/models/sql/query.py`
* host test patch adds one test to `tests/aggregation/tests.py`
* gate result: base -> target test ERROR, 125-test `aggregation` module has 1
  error; reference -> target test OK and 125/125 OK; scoring copies leave the
  Agent workspace byte-identical.
"""
from __future__ import annotations

import json
from pathlib import Path

import validation_tasks_v45 as _shared

ROOT = Path(__file__).resolve().parents[2]
INSTANCE_DIR = ROOT / '.tooling/swebench-verified/django-17084'
INSTANCE = json.loads((INSTANCE_DIR / 'instance.json').read_text(encoding='utf-8'))
SOURCES = {'django': {'directory': 'django-17084', 'commit': INSTANCE['base_commit']}}
COMMIT = {'django': INSTANCE['base_commit']}

# Edit scope frozen before any model request. It equals the file set the
# reference fix touches, which was taken from the dataset patch *file list* only.
ALLOWED = [
    'django/db/models/sql/query.py',
]

RESEARCH_FILES = ALLOWED + [
    'django/db/models/aggregates.py',
    'django/db/models/expressions.py',
    'django/db/models/sql/compiler.py',
    'django/db/models/sql/subqueries.py',
    'tests/aggregation/tests.py',
    'tests/aggregation/models.py',
    'tests/runtests.py',
]

def _test_ids(labels, source: str) -> tuple[list[str], list[str]]:
    """Normalize dataset labels to Django dotted test ids, dropping non-ids.

    This dataset writes this instance's target label as
    ``test_x (module.tests.Case.test_x)`` -- the method name is repeated inside
    the parentheses. The shared parser appends ``Class.method`` as if the
    parenthesised part were only a class path, producing
    ``module.tests.Case.test_x.test_x``. Django then reports
    ``AttributeError: type object 'Case' has no attribute 'test_x'``.

    Normalization is applied here rather than inside the shared parser, because
    editing that parser would change the frozen v45/v46 source hashes and
    invalidate their manifests.
    """
    ids, unusable = _shared._test_ids(labels, source)
    normalized = []
    for test_id in ids:
        head, _, tail = test_id.rpartition('.')
        if tail and head.endswith('.' + tail):
            test_id = head
        normalized.append(test_id)
    return normalized, unusable


_FAIL_TO_PASS, _FTP_UNUSABLE = _test_ids(INSTANCE['FAIL_TO_PASS'], 'FAIL_TO_PASS')
_PASS_TO_PASS, _PTP_UNUSABLE = _test_ids(INSTANCE['PASS_TO_PASS'], 'PASS_TO_PASS')
assert _FTP_UNUSABLE == [], _FTP_UNUSABLE

TASKS = {
    'django_referenced_window_wrapping': {
        'project': 'django',
        'allowed': ALLOWED,
        'research_files': RESEARCH_FILES,
        # Pre-existing public module; no target test is added to the workspace.
        'regression': ['aggregation'],
        'regression_module': 'aggregation',
        'target_tests': _FAIL_TO_PASS,
        'pass_to_pass': _PASS_TO_PASS,
        'pass_to_pass_unusable_labels': _PTP_UNUSABLE,
        'problem': INSTANCE['problem_statement'],
    }
}

# Rebind the shared module's globals so its functions resolve the v47 instance.
_shared.ROOT = ROOT
_shared.INSTANCE_DIR = INSTANCE_DIR
_shared.INSTANCE = INSTANCE
_shared.SOURCES = SOURCES
_shared.COMMIT = COMMIT
_shared.ALLOWED = ALLOWED
_shared.RESEARCH_FILES = RESEARCH_FILES
_shared.TASKS = TASKS

# Re-export the shared function objects, now bound to the v47 globals.
hashes = _shared.hashes
code_revision = _shared.code_revision
prepare = _shared.prepare
evaluate = _shared.evaluate
_summaries = _shared._summaries
_host_patch = _shared._host_patch
_test_environment = _shared._test_environment

TEST_PYTHON = _shared.TEST_PYTHON
REGRESSION_TIMEOUT = _shared.REGRESSION_TIMEOUT
TASK_NAME = next(iter(TASKS))
