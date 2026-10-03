"""Offline primitives for an independent, resumable v53 host audit.

This file does not alter the frozen v53 runner or evaluator. The acceptance copy
still receives the same host patch and runs the same selected Django module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKIP_DIRS = {'__pycache__', '.pytest_cache', '.git'}
SKIP_SUFFIXES = {'.pyc', '.pyo'}
TEXT_SUFFIXES = {'.py', '.json', '.md', '.txt', '.log', '.csv'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_map(workspace: Path, *, max_workers: int = 8) -> dict[str, str]:
    """Hash every non-cache file's complete bytes with bounded parallel I/O.

    Batches limit queued futures to 256 and workers to max_workers. This changes
    only scheduling: no metadata shortcut, cache, file omission, or digest change.
    """
    if max_workers < 1:
        raise ValueError('max_workers must be positive')
    result: dict[str, str] = {}
    paths: list[Path] = []
    for directory, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(directory) / name
            if path.suffix in SKIP_SUFFIXES:
                continue
            paths.append(path)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for start in range(0, len(paths), 256):
            batch = paths[start:start + 256]
            for path, digest in zip(batch, pool.map(sha, batch)):
                result[path.relative_to(workspace).as_posix()] = digest
    return result


def full_boundary(original: dict[str, str], final: dict[str, str], allowed) -> tuple[list[str], list[str]]:
    changed = sorted(name for name in original.keys() | final.keys() if original.get(name) != final.get(name))
    outside = sorted(set(changed) - {Path(name).as_posix() for name in allowed})
    return changed, outside


def map_digest(mapping: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sample_inputs_digest(sample: Path) -> str:
    """Fingerprint every generated input used for row reconstruction."""
    paths = [sample / name for name in ('report.json', 'original_hashes.json', 'pruner-state.json', 'ledger.json')]
    conversation = sample / 'conversation'
    if conversation.exists():
        paths.extend(p for p in conversation.rglob('*') if p.is_file())
    payload = {p.relative_to(sample).as_posix(): sha(p) for p in paths if p.is_file()}
    return map_digest(payload)


class AtomicCheckpoint:
    def __init__(self, path: Path):
        self.path = path
        self.done = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if not isinstance(self.done, dict):
            raise ValueError('invalid checkpoint root')

    def valid_row(self, sample: str, *, manifest_sha: str, code_sha: str,
                  inputs_sha: str, workspace_sha: str, log: Path):
        item = self.done.get(sample)
        if not isinstance(item, dict) or not log.is_file():
            return None
        expected = {'manifest_sha': manifest_sha, 'code_sha': code_sha,
                    'inputs_sha': inputs_sha, 'workspace_sha': workspace_sha,
                    'log_sha': sha(log)}
        if any(item.get(key) != value for key, value in expected.items()):
            return None
        row = item.get('row')
        return row if isinstance(row, dict) and row.get('sample') == sample else None

    def record(self, sample: str, row: dict, *, manifest_sha: str, code_sha: str,
               inputs_sha: str, workspace_sha: str, log: Path):
        if not log.is_file():
            raise FileNotFoundError(log)
        self.done[sample] = {'row': row, 'manifest_sha': manifest_sha,
                             'code_sha': code_sha, 'inputs_sha': inputs_sha,
                             'workspace_sha': workspace_sha, 'log_sha': sha(log)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                         prefix=self.path.name + '.', suffix='.tmp', delete=False) as handle:
            tmp = Path(handle.name)
            json.dump(self.done, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)


def evaluate_fast(workspace: Path, task: str, destination: Path) -> dict:
    """Same v45 host patch, command, environment and success criterion; no repeated
    full-tree hashes. Caller independently validates the entire final workspace.
    """
    import validation_tasks_v49 as v49
    v49._bind(task)
    shared = v49._shared
    spec = v49.TASKS[task]
    workspace = workspace.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if not v49.TEST_PYTHON.is_file():
        raise FileNotFoundError(v49.TEST_PYTHON)
    destination.mkdir(parents=True)
    test_workspace = destination / 'host-workspace'
    try:
        shutil.copytree(workspace, test_workspace, ignore=shared._COPY_IGNORE)
        shared._host_patch(test_workspace)
        selection = [spec['regression_module']]
        command = [str(v49.TEST_PYTHON), '-B', 'tests/runtests.py', *selection,
                   '--noinput', '--parallel=1', '--verbosity=1']
        started = time.monotonic()
        result = subprocess.run(command, cwd=test_workspace,
                                env=v49._test_environment(test_workspace),
                                capture_output=True, text=True,
                                timeout=v49.REGRESSION_TIMEOUT)
        elapsed = round(time.monotonic() - started, 2)
        output = result.stdout + result.stderr
        (destination / 'test.txt').write_text(output, encoding='utf-8')
        ran = re.search(r'^Ran (\d+) tests', output, re.MULTILINE)
        failures = re.search(r'FAILED \((.*)\)', output)
        return {'passed': result.returncode == 0, 'returncode': result.returncode,
                'ran': int(ran.group(1)) if ran else None,
                'failures': failures.group(1) if failures else None,
                'summary': v49._summaries(output), 'elapsed_seconds': elapsed,
                'host_tests_applied': True, 'selection': selection,
                'workspace_unchanged_by_test': True}
    finally:
        shutil.rmtree(test_workspace, ignore_errors=True)


def fresh_destination(sample: Path) -> Path:
    """Keep an interrupted prior audit log without overwriting it."""
    destination = sample / 'evaluation-audit-fast'
    if destination.exists():
        destination.rename(sample / f'evaluation-audit-fast-stale-{uuid.uuid4().hex[:8]}')
    return destination


def credential_scan(batch: Path, key: str, workspace_files: list[Path],
                    verified_workspaces: set[Path]) -> tuple[list[str], dict]:
    """One pruned traversal, plus every candidate-authored source and TASK.md.

    Only direct children of batch/workspaces whose full-file boundary has been
    verified are pruned. A generated directory named workspaces or host-workspace
    anywhere else is scanned normally. Changed source and TASK.md are explicit.
    """
    leaks: list[str] = []
    scanned: set[Path] = set()
    workspace_root = (batch / 'workspaces').resolve()
    verified = {path.resolve() for path in verified_workspaces}
    pruned: list[str] = []
    for directory, dirs, files in os.walk(batch):
        current = Path(directory).resolve()
        if current == workspace_root:
            kept = []
            for name in dirs:
                candidate = (current / name).resolve()
                if candidate in verified:
                    pruned.append(candidate.relative_to(batch.resolve()).as_posix())
                else:
                    kept.append(name)
            dirs[:] = kept
        for name in files:
            path = Path(directory) / name
            if path.suffix in TEXT_SUFFIXES:
                scanned.add(path)
    scanned.update(p for p in workspace_files if p.is_file() and p.suffix in TEXT_SUFFIXES)
    for path in sorted(scanned):
        if key in path.read_text(encoding='utf-8', errors='replace'):
            leaks.append(str(path.relative_to(batch)))
    return leaks, {'mode': 'pruned_with_changed_workspace_files',
                   'files_scanned': len(scanned), 'workspace_files_scanned': len(workspace_files),
                   'verified_workspace_dirs_pruned': sorted(pruned)}
