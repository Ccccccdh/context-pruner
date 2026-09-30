"""Fixed Django multiple-inheritance update; target tests stay host-side.

Boundaries (deliberate, mirrored from validation_tasks_v36.py):

* The Agent workspace is a copy of the public upstream baseline at the pinned
  commit. It contains NO reference patch and NO target tests.
* Public regression runs only the pre-existing `model_inheritance_regress` module.
* Host acceptance copies the workspace to a scoring copy, applies
  `host-tests.patch` to that copy only, and re-runs the same module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTANCE_DIR = ROOT / '.tooling/swebench-verified/django-15563'
INSTANCE = json.loads((INSTANCE_DIR / 'instance.json').read_text(encoding='utf-8'))
SOURCES = {'django': {'directory': 'django-15563', 'commit': INSTANCE['base_commit']}}
COMMIT = {'django': INSTANCE['base_commit']}

# Candidate edit scope is fixed before reading the reference patch.
ALLOWED = [
    'django/db/models/sql/compiler.py',
    'django/db/models/sql/subqueries.py',
]

# Public files the Agent may read to understand update routing and inheritance.
RESEARCH_FILES = ALLOWED + [
    'django/db/models/sql/query.py',
    'django/db/models/query.py',
    'tests/model_inheritance_regress/tests.py',
    'tests/model_inheritance_regress/models.py',
    'tests/runtests.py',
]

def _test_ids(labels, source: str) -> tuple[list[str], list[str]]:
    """Normalize dataset labels to Django dotted test ids, dropping non-ids.

    The dataset stores target tests as ``test_name (module.tests.Case)``;
    the runner accepts ``module.tests.Case.test_name``.

    Unusable metadata labels, if any, are recorded separately. The public
    regression always runs the entire module.
    """
    if isinstance(labels, str):
        labels = [labels]
    ids: list[str] = []
    unusable: list[str] = []
    for label in labels:
        text = label.strip()
        match = re.match(r'^(\w+)\s*(?:\(([\w.]+)\))?$', text)
        if match and match.group(2):
            ids.append(f'{match.group(2)}.{match.group(1)}')
        elif match and match.group(1).startswith('test'):
            ids.append(match.group(1))
        else:
            unusable.append(text)
    return ids, unusable


_FAIL_TO_PASS, _FTP_UNUSABLE = _test_ids(INSTANCE['FAIL_TO_PASS'], 'FAIL_TO_PASS')
_PASS_TO_PASS, _PTP_UNUSABLE = _test_ids(INSTANCE['PASS_TO_PASS'], 'PASS_TO_PASS')
assert _FTP_UNUSABLE == [], _FTP_UNUSABLE

TASKS = {
    'django_multiple_inheritance_update': {
        'project': 'django',
        'allowed': ALLOWED,
        'research_files': RESEARCH_FILES,
        # Pre-existing public module; no target test is added to the workspace.
        'regression': ['model_inheritance_regress'],
        'regression_module': 'model_inheritance_regress',
        'target_tests': _FAIL_TO_PASS,
        'pass_to_pass': _PASS_TO_PASS,
        'pass_to_pass_unusable_labels': _PTP_UNUSABLE,
        'problem': INSTANCE['problem_statement'],
    }
}

TEST_PYTHON = ROOT / '.tooling/venv-django-16263/Scripts/python.exe'

# The whole `model_inheritance_regress` module is bounded; Django setup still dominates.
# Keep a bounded ceiling rather than an unbounded wait.
REGRESSION_TIMEOUT = 300

_SKIP_DIRS = {'__pycache__', '.pytest_cache', '.git'}
_SKIP_SUFFIXES = {'.pyc', '.pyo'}
# `Django.egg-info` is a gitignored local build artifact of `.tooling/upstream/`.
# It is excluded from the Agent workspace so the workspace payload contains the
# public baseline and TASK.md only, matching the frozen gate copies.
_COPY_IGNORE = shutil.ignore_patterns(
    '.git', '__pycache__', '.pytest_cache', '*.pyc', '*.egg-info')
_ENV_ALLOWLIST = {
    'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE',
}


def hashes(workspace: Path) -> dict[str, str]:
    """SHA-256 of every file in the workspace, excluding caches."""
    workspace = Path(workspace)
    return {
        p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in workspace.rglob('*')
        if p.is_file()
        and not _SKIP_DIRS.intersection(p.parts)
        and p.suffix not in _SKIP_SUFFIXES
    }


def code_revision(workspace: Path, task: str) -> str:
    """Revision of the *editable source only*.

    Deliberately scoped to the two allowed files: this is the value compared
    for the tool's "unchanged code" short-circuit, so unrelated workspace
    churn (logs, output dirs) cannot invalidate a cached result, and no full
    Django tree hash is needed per model request.
    """
    allowed = TASKS[task]['allowed']
    workspace = Path(workspace)
    payload = {name: (hashlib.sha256((workspace / name).read_bytes()).hexdigest()
                      if (workspace / name).is_file() else 'missing') for name in allowed}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def prepare(upstream_root: Path, workspace: Path, task: str):
    """Create the Agent workspace from the public baseline only."""
    spec = TASKS[task]
    upstream = Path(upstream_root) / SOURCES[spec['project']]['directory']
    workspace = Path(workspace)
    assert not workspace.exists(), f'workspace already exists: {workspace}'
    shutil.copytree(upstream, workspace, ignore=_COPY_IGNORE)
    (workspace / 'TASK.md').write_text(
        spec['problem'].rstrip()
        + '\n\nAllowed source files (edit only these):\n'
        + ''.join(f'- {name}\n' for name in spec['allowed']),
        encoding='utf-8',
    )
    return hashes(workspace)


def _host_patch(host_workspace: Path) -> None:
    """Apply host-tests.patch to a scoring copy only."""
    host_workspace = Path(host_workspace).resolve()
    assert host_workspace.is_relative_to(ROOT), (
        f'scoring copy must live under {ROOT} for `git apply --directory`: {host_workspace}')
    relative = host_workspace.relative_to(ROOT).as_posix()
    patch = INSTANCE_DIR / 'host-tests.patch'
    for flags in (['--check'], []):
        result = subprocess.run(
            ['git', 'apply', *flags, f'--directory={relative}', str(patch)],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    reverse = subprocess.run(
        ['git', 'apply', '--reverse', '--check', f'--directory={relative}', str(patch)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert reverse.returncode == 0, reverse.stderr


def _test_environment(test_workspace: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if key.upper() in _ENV_ALLOWLIST}
    env.update(
        PYTHONPATH=os.pathsep.join((str(test_workspace), str(test_workspace / 'tests'))),
        PYTHONDONTWRITEBYTECODE='1',
    )
    return env


def _summaries(output: str) -> list[str]:
    markers = re.compile(r'^(Ran \d+ tests|FAILED|OK$|Testing against Django|ERROR:)')
    return [line.strip() for line in output.splitlines() if markers.search(line.strip())]


def evaluate(workspace: Path, task: str, destination: Path, regression_only: bool = False,
             target_tests_only: bool = False):
    """Run the fixed model inheritance regress module.

    regression_only=True  -> run the candidate workspace as-is (public tests).
    regression_only=False -> copy to a scoring copy, apply host tests there,
                             then run.
    target_tests_only     -> restrict to the dataset FAIL_TO_PASS labels
                             (only meaningful together with host tests).
    """
    spec = TASKS[task]
    # Resolve both paths up front: `_host_patch` has to express the scoring copy
    # relative to ROOT, which fails on a relative destination.
    workspace = Path(workspace).resolve()
    destination = Path(destination).resolve()
    assert task in TASKS and TEST_PYTHON.is_file(), f'missing test python: {TEST_PYTHON}'
    assert workspace.is_dir(), f'no such workspace: {workspace}'
    assert not (target_tests_only and regression_only), 'target tests are host-side only'
    assert not destination.exists(), f'destination already exists: {destination}'
    destination.mkdir(parents=True, exist_ok=False)

    try:
        if regression_only:
            test_workspace = workspace
            candidate_before = hashes(workspace)
        else:
            test_workspace = destination / 'host-workspace'
            shutil.copytree(workspace, test_workspace, ignore=_COPY_IGNORE)
            _host_patch(test_workspace)
            candidate_before = hashes(workspace)

        selection = [spec['regression_module']]
        if target_tests_only:
            selection = list(spec['target_tests'])
        command = [str(TEST_PYTHON), '-B', 'tests/runtests.py', *selection, '--noinput',
                   '--parallel=1', '--verbosity=1']
        started = time.monotonic()
        result = subprocess.run(
            command, cwd=test_workspace, env=_test_environment(test_workspace),
            capture_output=True, text=True, timeout=REGRESSION_TIMEOUT,
        )
        elapsed = round(time.monotonic() - started, 2)
        output = result.stdout + result.stderr
        (destination / 'test.txt').write_text(output, encoding='utf-8')
        candidate_after = hashes(workspace)
        ran = re.search(r'^Ran (\d+) tests', output, re.MULTILINE)
        failures = re.search(r'FAILED \((.*)\)', output)
        return {
            'passed': result.returncode == 0,
            'returncode': result.returncode,
            'ran': int(ran.group(1)) if ran else None,
            'failures': failures.group(1) if failures else None,
            'summary': _summaries(output),
            'elapsed_seconds': elapsed,
            'host_tests_applied': not regression_only,
            'selection': selection,
            'workspace_unchanged_by_test': candidate_before == candidate_after,
        }
    finally:
        # The scoring copy must not survive as a second Django tree.
        if not regression_only:
            shutil.rmtree(destination / 'host-workspace', ignore_errors=True)

