"""Re-verify each v54 branch's final state and re-run the host target test.

The three branches deliberately ran sequentially at **one absolute path**, so
after the batch that path holds only the last branch's bytes.  One final
workspace therefore cannot be the evidence for three different arms.

What this script can and cannot do with a frozen batch, stated plainly:

* It **always** re-establishes each branch's start state: restore from the
  frozen read-only snapshot and check the full-file hash map equals the frozen
  prefix.
* It **always** checks the branch's recorded final state against its recorded
  change set: a branch that changed nothing must end byte-identical to the
  frozen prefix; a branch that changed an allowed file must have a recorded
  final digest for exactly those files.
* It re-runs the host target test on the bytes it can establish.  For a branch
  whose final state equals the frozen prefix, that is this branch's own final
  state.  For a branch that ended with a different allowed-file version, the
  batch must have persisted that branch's bytes (``branch_files.json``); the
  frozen runner used for batch 01 did not, so this script reports the limitation
  instead of scoring a different branch's code.

Usage:
    python verify_v54_branch_workspaces.py --out runs/stage5-openhands/<batch>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from same_prefix_fork_v54 import restore_workspace, tree_hashes
from validation_tasks_v49 import TASKS, evaluate

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def redirect_temp(out: Path) -> None:
    import validation_tasks_v49 as frozen
    temp = out / '.audit-tmp'
    temp.mkdir(parents=True, exist_ok=True)
    for name in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[name] = str(temp)
    original = frozen._test_environment

    def redirected(test_workspace):
        environment = original(test_workspace)
        scratch = temp / Path(test_workspace).name
        scratch.mkdir(parents=True, exist_ok=True)
        for name in ('TEMP', 'TMP', 'TMPDIR'):
            environment[name] = str(scratch)
        return environment

    frozen._test_environment = redirected


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    redirect_temp(out)

    manifest = load(out / 'manifest.json')
    task = manifest['tasks'][0]
    allowed = TASKS[task]['allowed']
    freeze = load(out / 'prefix-freeze.json')
    frozen = load(out / 'frozen-hashes.json')
    snapshot = Path(freeze['snapshot'])
    workspace = out / 'workspaces' / 'prefix'
    reports = [load(path / 'report.json') for path in sorted(out.glob('branch-*/'))
               if (path / 'report.json').is_file()]
    assert reports, 'no branch reports to verify'
    archive_root = out / 'branch-final-files'
    archive_root.mkdir(exist_ok=True)

    verified, unreconstructable, timings = [], [], {}
    for report in reports:
        sample_dir = out / report['sample']
        sample = sample_dir.name
        assert report['restore_hash_equal'] is True, sample
        started = time.perf_counter()
        restore_workspace(workspace, snapshot, experiment_root=out, expected_hashes=frozen)
        assert tree_hashes(workspace) == frozen, (
            f'{sample}: restored workspace is not the frozen prefix')
        timings[f'{sample}_restore_seconds'] = round(time.perf_counter() - started, 2)

        changed = sorted(report['changed_files'])
        assert set(changed) <= set(allowed), (sample, changed)
        persisted = sample_dir / 'branch_files.json'
        if changed and not persisted.is_file():
            # Keep this branch's bytes anyway: they are the bytes still on the
            # shared path right now, so the audit can at least confirm byte-level
            # identity, even though the frozen batch has no second source for
            # them and they therefore cannot be re-scored from scratch.
            archive = archive_root / sample
            archive.mkdir(exist_ok=True)
            final_files = {}
            for name in allowed:
                target = archive / Path(name).name
                target.write_bytes((workspace / name).read_bytes())
                final_files[name] = digest(target)
            unreconstructable.append({
                'sample': sample, 'arm': report['arm'], 'changed_files': changed,
                'archive': str(archive.relative_to(out)),
                'final_files': final_files,
                'reason': ('the frozen runner did not persist this branch\'s final bytes; they '
                           'are recoverable only while this branch is the one on the shared '
                           'path, so its host verdict is the recorded run verdict and cannot '
                           'be re-run from an independent byte source'),
            })
            continue

        if persisted.is_file():
            recorded_files = load(persisted)['final_files']
            for name, expected in recorded_files.items():
                target = workspace / name
                assert target.is_file() and digest(target) == expected, (
                    f'{sample}: {name} does not match the branch\'s own recorded bytes')
        # archive the branch's own final editable files, read fresh from the
        # restored workspace, so the audit can re-verify them later
        archive = archive_root / sample
        archive.mkdir(exist_ok=True)
        final_files = {}
        for name in allowed:
            source = workspace / name
            target = archive / Path(name).name
            target.write_bytes(source.read_bytes())
            final_files[name] = digest(target)

        started = time.perf_counter()
        # A previous interrupted verification may have left its scoring copy
        # behind; keep it (renamed) instead of overwriting evidence.
        audit_dir = sample_dir / 'evaluation-audit'
        if audit_dir.exists():
            audit_dir.rename(sample_dir / f'evaluation-audit-stale-{int(time.time())}')
        result = evaluate(workspace, task, audit_dir, regression_only=False)
        timings[f'{sample}_host_test_seconds'] = round(time.perf_counter() - started, 2)
        recorded = (report.get('host_rounds') or [{}])[-1].get('host', {})
        assert result['passed'] == recorded.get('passed'), (
            f'{sample}: independent host verdict differs from the recorded verdict')
        payload = {
            'sample': sample, 'arm': report['arm'],
            'restore_hash_equal': True,
            'workspace_file_count': len(frozen),
            'changed_files': changed,
            'final_files': final_files,
            'archive': str(archive.relative_to(out)),
            'host_evaluation': result,
            'recorded_host_passed': recorded.get('passed'),
            'host_evaluation_scope': (
                'branch ended byte-identical to the frozen prefix'
                if not changed else 'persisted branch bytes restored and verified'),
        }
        (sample_dir / 'evaluation-audit').mkdir(parents=True, exist_ok=True)
        (sample_dir / 'evaluation-audit' / 'verification.json').write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        verified.append(payload)
        shutil.rmtree(workspace, ignore_errors=True)

    (out / 'branch-verification.json').write_text(json.dumps(
        {'verified': verified, 'unreconstructable': unreconstructable,
         'timings_seconds': timings}, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'verified': [v['sample'] for v in verified],
                      'unreconstructable': [u['sample'] for u in unreconstructable],
                      'timings': timings}, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
