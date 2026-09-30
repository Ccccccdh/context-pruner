"""Zero-API base/reference gate for the first v37 public issue candidate."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / '.tooling/upstream/pylint-8898'
INSTANCE = ROOT / '.tooling/swebench-verified/pylint-8898'
OUTPUT = ROOT / 'runs/stage5-openhands/pylint-8898-v37-gate-01'
PYTHON = ROOT / '.tooling/venv-pylint-8898/Scripts/python.exe'
COMMIT = '1f8c4d9eb185c16a2c1d881c054f015e1c2eb334'


def hashes(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()
            and not {'__pycache__', '.pytest_cache', '.git'}.intersection(p.parts)
            and p.suffix != '.pyc'}


def apply_patch(target: Path, patch: Path) -> None:
    directory = target.relative_to(ROOT).as_posix()
    for extra in (['--check'], []):
        result = subprocess.run(['git', 'apply', *extra, f'--directory={directory}', str(patch)],
                                cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    reverse = subprocess.run(['git', 'apply', '--reverse', '--check',
                              f'--directory={directory}', str(patch)],
                             cwd=ROOT, capture_output=True, text=True)
    assert reverse.returncode == 0, reverse.stderr


def evaluate(workspace: Path, label: str) -> dict:
    destination = OUTPUT / label
    destination.mkdir()
    before = hashes(workspace)
    env = {key: val for key, val in os.environ.items() if key.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
    env.update(PYTHONPATH=str(workspace), PYTHONDONTWRITEBYTECODE='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    command = [str(PYTHON), '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '-W', 'ignore::DeprecationWarning', '--basetemp', str(destination / 'tmp'),
               'tests/config/test_config.py']
    result = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                            text=True, timeout=180)
    output = result.stdout + result.stderr
    (destination / 'pytest.txt').write_text(output, encoding='utf-8')
    summary = [line for line in output.splitlines() if re.search(r'\b(passed|failed|error|errors)\b', line)]
    after = hashes(workspace)
    return {'passed': result.returncode == 0, 'returncode': result.returncode,
            'summary': summary[-1] if summary else output[-500:],
            'source_unchanged_by_test': before == after}


def main() -> None:
    assert not OUTPUT.exists() and PYTHON.is_file()
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=UPSTREAM, text=True).strip()
    assert actual == COMMIT
    OUTPUT.mkdir(parents=True)
    base, reference = OUTPUT / 'base', OUTPUT / 'reference'
    ignore = shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', '*.pyc')
    shutil.copytree(UPSTREAM, base, ignore=ignore)
    shutil.copytree(UPSTREAM, reference, ignore=ignore)
    apply_patch(base, INSTANCE / 'host-tests.patch')
    apply_patch(reference, INSTANCE / 'host-tests.patch')
    apply_patch(reference, INSTANCE / 'reference.patch')
    result = {'instance': 'pylint-dev__pylint-8898', 'base_commit': COMMIT,
              'dataset_sha256': hashlib.sha256((INSTANCE.parent / 'test.parquet').read_bytes()).hexdigest(),
              'host_test_patch_sha256': hashlib.sha256((INSTANCE / 'host-tests.patch').read_bytes()).hexdigest(),
              'reference_patch_sha256': hashlib.sha256((INSTANCE / 'reference.patch').read_bytes()).hexdigest(),
              'base_host': evaluate(base, 'base-host'),
              'reference_host': evaluate(reference, 'reference-host')}
    (OUTPUT / 'gate.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert not result['base_host']['passed'] and result['reference_host']['passed']
    assert result['base_host']['source_unchanged_by_test']
    assert result['reference_host']['source_unchanged_by_test']


if __name__ == '__main__':
    main()
