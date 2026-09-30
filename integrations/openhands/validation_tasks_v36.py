"""Fixed external pytest issue, with gold tests and patch kept host-side."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTANCE_DIR = ROOT / '.tooling/swebench-verified/pytest-10356'
INSTANCE = json.loads((INSTANCE_DIR / 'instance.json').read_text(encoding='utf-8'))
SOURCES = {'pytest': {'directory': 'pytest-10356', 'commit': INSTANCE['base_commit']}}
COMMIT = {'pytest': INSTANCE['base_commit']}
TASKS = {
    'pytest_mark_mro': {
        'project': 'pytest',
        'allowed': ['src/_pytest/mark/structures.py'],
        'research_files': ['src/_pytest/mark/structures.py', 'src/_pytest/mark/__init__.py',
                           'src/_pytest/python.py', 'src/_pytest/nodes.py',
                           'src/_pytest/fixtures.py', 'src/_pytest/config/__init__.py'],
        'regression': ['testing/test_mark.py'],
        'problem': INSTANCE['problem_statement'],
    }
}
TEST_PYTHON = ROOT / '.tooling/venv-pytest-10356/Scripts/python.exe'


def hashes(workspace: Path):
    return {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in workspace.rglob('*') if p.is_file() and '__pycache__' not in p.parts
            and '.pytest_cache' not in p.parts}


def prepare(upstream_root: Path, workspace: Path, task: str):
    spec = TASKS[task]
    upstream = upstream_root / SOURCES[spec['project']]['directory']
    assert not workspace.exists()
    shutil.copytree(upstream, workspace, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', '*.pyc'))
    (workspace / 'src/_pytest/_version.py').write_bytes(
        b"version = '7.2.0.dev0'\nversion_tuple = (7, 2, 0, 'dev', 0)\n")
    (workspace / 'TASK.md').write_text(spec['problem'] + '\nAllowed source file: '
                                       + spec['allowed'][0] + '\n', encoding='utf-8')
    return hashes(workspace)


def _host_patch(host_workspace: Path):
    relative = host_workspace.relative_to(ROOT).as_posix()
    patch = INSTANCE_DIR / 'host-tests.patch'
    for flags in (['--check'], []):
        result = subprocess.run(['git', 'apply', *flags, f'--directory={relative}', str(patch)],
                                cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    reverse = subprocess.run(['git', 'apply', '--reverse', '--check', f'--directory={relative}', str(patch)],
                             cwd=ROOT, capture_output=True, text=True)
    assert reverse.returncode == 0, reverse.stderr


def evaluate(workspace: Path, task: str, destination: Path, regression_only: bool = False):
    assert task in TASKS and TEST_PYTHON.is_file()
    destination.mkdir(parents=True, exist_ok=False)
    if regression_only:
        test_workspace = workspace
    else:
        test_workspace = destination / 'host-workspace'
        shutil.copytree(workspace, test_workspace,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', '*.pyc'))
        _host_patch(test_workspace)
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    env.update(PYTHONPATH=str(test_workspace / 'src'), PYTHONDONTWRITEBYTECODE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    command = [str(TEST_PYTHON), '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '-W', 'ignore::DeprecationWarning', '--basetemp', str(destination / 'tmp'),
               'testing/test_mark.py']
    result = subprocess.run(command, cwd=test_workspace, env=env,
                            capture_output=True, text=True, timeout=180)
    output = result.stdout + result.stderr
    (destination / 'pytest.txt').write_text(output, encoding='utf-8')
    last = output.strip().splitlines()[-1] if output.strip() else 'no pytest output'
    return {'passed': result.returncode == 0, 'returncode': result.returncode,
            'summary': last, 'host_tests_applied': not regression_only}
