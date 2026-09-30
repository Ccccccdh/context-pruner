"""Build immutable base and host-only gold-test/reference preflight copies."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def apply_patch(target: Path, patch: Path):
    directory = target.relative_to(ROOT).as_posix()
    check = subprocess.run(['git', 'apply', '--check', f'--directory={directory}', str(patch)], cwd=ROOT,
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    applied = subprocess.run(['git', 'apply', f'--directory={directory}', str(patch)], cwd=ROOT,
                             capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    reverse = subprocess.run(['git', 'apply', '--reverse', '--check', f'--directory={directory}', str(patch)], cwd=ROOT,
                             capture_output=True, text=True)
    assert reverse.returncode == 0, reverse.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--instance-dir', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    source, task, out = (Path(value).resolve() for value in
                         (args.source, args.instance_dir, args.out))
    instance = json.loads((task / 'instance.json').read_text(encoding='utf-8'))
    assert source.is_dir() and not out.exists()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    assert commit == instance['base_commit']
    out.mkdir(parents=True)
    for label in ('base', 'reference'):
        target = out / label
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
        # setuptools-scm normally generates this during an installed build.
        (target / 'src/_pytest/_version.py').write_text(
            "version = '7.2.0.dev0'\nversion_tuple = (7, 2, 0, 'dev', 0)\n", encoding='utf-8')
        apply_patch(target, task / 'host-tests.patch')
        if label == 'reference':
            apply_patch(target, task / 'reference.patch')
    (out / 'manifest.json').write_text(json.dumps({'instance_id': instance['instance_id'],
        'base_commit': commit, 'dataset_sha256': instance['dataset_sha256'],
        'host_test_patch': str(task / 'host-tests.patch'),
        'reference_patch': str(task / 'reference.patch')}, indent=2), encoding='utf-8')
    print(json.dumps({'base_commit': commit, 'copies': ['base', 'reference']}))


if __name__ == '__main__':
    main()
