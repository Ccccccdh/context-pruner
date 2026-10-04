"""v55 per-branch artifact persistence: every branch stays verifiable on its own.

v54's gap, stated in ``RESULTS.md`` §6: the three branches ran strictly
sequentially at **one absolute workspace path**, so when the batch ended that
path held only the last branch's bytes.  The audit could therefore re-run the
host target test only for branches that happened to end byte-identical to the
frozen prefix; the plugin arm's verdict could not be re-scored from an
independent byte source at all.

v55 closes that gap without changing where the branches run:

* after a branch ends, its **final cache-free file map** (``final-hashes.json``),
  the **bytes of every file whose content differs from the frozen prefix**
  (``final-files/``), its **event stream** (``events.json``) and an
  ``artifacts.json`` index with a SHA-256 per persisted file are written inside
  that branch's own directory;
* :func:`reconstruct_branch_workspace` rebuilds a branch's exact final workspace
  from the frozen read-only snapshot plus **that branch's own persisted bytes**,
  and refuses to continue unless the rebuilt full-file hash map equals the
  branch's recorded one;
* a batch-level index (``branch-artifact-index.json``) pins the SHA-256 of every
  per-branch artifact file, so a tampered byte has to be made consistent in
  three independent places before it can survive :func:`verify_batch_index`.

Everything here is pure filesystem work: no model request, no provider client.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from same_prefix_fork_v54 import restore_workspace

#: Bumped whenever the on-disk shape changes; the audit refuses other versions.
ARTIFACT_VERSION = 'openhands-v55-branch-artifacts-1'
ARTIFACT_INDEX = 'artifacts.json'
ARTIFACT_FILES_DIR = 'final-files'
ARTIFACT_FINAL_HASHES = 'final-hashes.json'
ARTIFACT_EVENTS = 'events.json'
BRANCH_INDEX_FILE = 'branch-artifact-index.json'
#: Artifact files whose own SHA-256 the batch index pins.
INDEXED_ARTIFACTS = (ARTIFACT_INDEX, ARTIFACT_FINAL_HASHES, ARTIFACT_EVENTS,
                     'ledger.json', 'report.json')


class BranchArtifactError(RuntimeError):
    """A persisted branch artifact is missing, inconsistent or tampered with."""


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_map(mapping: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()


def digest_ids(ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest()


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Every path whose content (or existence) differs between two file maps."""
    return sorted(name for name in set(before) | set(after)
                  if before.get(name) != after.get(name))


def persist_branch_artifacts(sample_dir: Path, workspace: Path, *,
                             frozen_source: dict[str, str], allowed: list[str],
                             task: str, arm: str, position: int,
                             prefix_event_count: int, conversation_id: str,
                             events: list[dict], extra: dict | None = None) -> dict:
    """Persist one branch's final bytes, hashes, event stream and index.

    ``allowed`` files are always persisted, even when the branch left them
    untouched, so the audit can verify the "nothing changed" claim byte for byte
    instead of trusting it.  Any *other* file the branch changed is persisted too
    (and still counted as a boundary violation): a violation must not also become
    an unreconstructable state.
    """
    sample_dir = Path(sample_dir)
    workspace = Path(workspace)
    from validation_tasks_v49 import hashes

    final_source = hashes(workspace)
    changed = changed_files(frozen_source, final_source)
    files_dir = sample_dir / ARTIFACT_FILES_DIR
    if files_dir.exists():
        shutil.rmtree(files_dir)
    persisted: dict[str, str | None] = {}
    for name in sorted(set(allowed) | set(changed)):
        source = workspace / name
        target = files_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copy2(source, target)
            persisted[name] = sha256_file(target)
        else:
            # A deleted allowed file is a real (recorded) state, not a missing
            # artifact: `None` means "this branch's final state has no such file".
            persisted[name] = None

    events_payload = {
        'conversation_id': conversation_id,
        'prefix_event_count': prefix_event_count,
        'events': events,
        'branch_event_ids': [row['id'] for row in events
                             if row.get('id') is not None],
        'branch_event_digest': digest_ids([row['id'] for row in events
                                           if row.get('id') is not None]),
        'observed_event_types': sorted({row['type'] for row in events}),
    }
    save(sample_dir / ARTIFACT_EVENTS, events_payload)
    save(sample_dir / ARTIFACT_FINAL_HASHES, final_source)

    record = {
        'artifact_version': ARTIFACT_VERSION,
        'sample': sample_dir.name, 'task': task, 'arm': arm, 'position': position,
        'allowed': list(allowed),
        'changed_files': changed,
        'file_boundary_ok': set(changed) <= set(allowed),
        'workspace_file_count': len(final_source),
        'final_source_digest': digest_map(final_source),
        'final_files_dir': ARTIFACT_FILES_DIR,
        'final_files': persisted,
        'final_files_digest': hashlib.sha256(
            json.dumps(persisted, sort_keys=True).encode()).hexdigest(),
        'final_hashes_file': ARTIFACT_FINAL_HASHES,
        'events_file': ARTIFACT_EVENTS,
        'events_digest': digest_ids(events_payload['branch_event_ids']),
        'branch_event_digest': events_payload['branch_event_digest'],
        'conversation_id': conversation_id,
        'prefix_event_count': prefix_event_count,
        'reconstruction_rule': (
            'restore the frozen read-only snapshot, overlay this branch\'s persisted '
            'final files, then compare the cache-free full-file hash map with '
            'final-hashes.json; the host target test is re-run on those bytes'),
    }
    record.update(extra or {})
    save(sample_dir / ARTIFACT_INDEX, record)
    return record


def load_branch_artifacts(sample_dir: Path) -> tuple[dict, dict[str, str]]:
    sample_dir = Path(sample_dir)
    if not (sample_dir / ARTIFACT_INDEX).is_file():
        raise BranchArtifactError(f'{sample_dir.name}: {ARTIFACT_INDEX} missing')
    record = load(sample_dir / ARTIFACT_INDEX)
    if record.get('artifact_version') != ARTIFACT_VERSION:
        raise BranchArtifactError(
            f'{sample_dir.name}: unexpected artifact version '
            f'{record.get("artifact_version")!r}')
    if not (sample_dir / ARTIFACT_FINAL_HASHES).is_file():
        raise BranchArtifactError(f'{sample_dir.name}: {ARTIFACT_FINAL_HASHES} missing')
    final_source = load(sample_dir / ARTIFACT_FINAL_HASHES)
    return record, final_source


def verify_branch_artifacts(sample_dir: Path, frozen_source: dict[str, str], *,
                            allowed: list[str], index_entry: dict | None = None,
                            report: dict | None = None) -> dict:
    """Re-derive every claim the branch's own artifacts make about its final state.

    Raises :class:`BranchArtifactError` on the first inconsistency.  ``index_entry``
    (from the batch index) pins the SHA-256 of the artifact files themselves, and
    ``report`` (the branch report, when available) must carry a byte-identical
    copy of the artifact record.
    """
    sample_dir = Path(sample_dir)
    record, final_source = load_branch_artifacts(sample_dir)
    if index_entry is not None:
        for name, expected in (index_entry.get('files') or {}).items():
            target = sample_dir / name
            if not target.is_file():
                raise BranchArtifactError(f'{sample_dir.name}: artifact file removed: {name}')
            if sha256_file(target) != expected:
                raise BranchArtifactError(f'{sample_dir.name}: artifact file changed: {name}')
        if index_entry.get('final_source_digest') != digest_map(final_source):
            raise BranchArtifactError(f'{sample_dir.name}: final hash map changed')
    if report is not None and 'branch_artifacts' in report:
        if report['branch_artifacts'] != record:
            raise BranchArtifactError(
                f'{sample_dir.name}: report artifact record differs from artifacts.json')

    if digest_map(final_source) != record['final_source_digest']:
        raise BranchArtifactError(f'{sample_dir.name}: final-hashes.json was modified')
    if len(final_source) != record['workspace_file_count']:
        raise BranchArtifactError(f'{sample_dir.name}: recorded file count disagrees')

    persisted = record['final_files']
    for name, expected in persisted.items():
        target = sample_dir / ARTIFACT_FILES_DIR / name
        if expected is None:
            if target.exists():
                raise BranchArtifactError(
                    f'{sample_dir.name}: {name} is recorded as deleted but was restored')
            continue
        if not target.is_file():
            raise BranchArtifactError(f'{sample_dir.name}: persisted file missing: {name}')
        if sha256_file(target) != expected:
            raise BranchArtifactError(f'{sample_dir.name}: persisted file changed: {name}')
    if hashlib.sha256(json.dumps(persisted, sort_keys=True).encode()).hexdigest() != \
            record['final_files_digest']:
        raise BranchArtifactError(f'{sample_dir.name}: persisted file index was modified')

    # Every file that differs from the frozen prefix must have its bytes persisted,
    # otherwise the branch's final state is not reconstructable at all.
    changed = changed_files(frozen_source, final_source)
    if changed != sorted(record['changed_files']):
        raise BranchArtifactError(
            f'{sample_dir.name}: recorded change set disagrees with the frozen prefix')
    missing = [name for name in changed if name not in persisted]
    if missing:
        raise BranchArtifactError(
            f'{sample_dir.name}: changed files were not persisted: {missing[:5]}')
    # The allowed-file bytes on disk must be exactly the ones the final hash map
    # claims: this is what makes the persisted bytes usable as a byte source.
    for name in allowed:
        if persisted.get(name) != final_source.get(name):
            raise BranchArtifactError(
                f'{sample_dir.name}: allowed file {name} disagrees with the final hash map')
    if record['file_boundary_ok'] != (set(changed) <= set(allowed)):
        raise BranchArtifactError(f'{sample_dir.name}: boundary flag disagrees')

    events_path = sample_dir / ARTIFACT_EVENTS
    if not events_path.is_file():
        raise BranchArtifactError(f'{sample_dir.name}: {ARTIFACT_EVENTS} missing')
    events = load(events_path)
    if events.get('branch_event_digest') != digest_ids(events.get('branch_event_ids') or []):
        raise BranchArtifactError(f'{sample_dir.name}: event stream was modified')
    return {'record': record, 'final_source': final_source, 'events': events}


def reconstruct_branch_workspace(target: Path, snapshot: Path, *,
                                 frozen_tree: dict[str, str], frozen_source: dict[str, str],
                                 artifacts_dir: Path, experiment_root: Path,
                                 allowed: list[str],
                                 index_entry: dict | None = None,
                                 report: dict | None = None) -> dict:
    """Rebuild a branch's own final workspace from the snapshot + its bytes."""
    target = Path(target)
    artifacts_dir = Path(artifacts_dir)
    verified = verify_branch_artifacts(artifacts_dir, frozen_source, allowed=allowed,
                                       index_entry=index_entry, report=report)
    restore_workspace(target, snapshot, experiment_root=experiment_root,
                      expected_hashes=frozen_tree)
    for name, expected in sorted(verified['record']['final_files'].items()):
        destination = target / name
        if expected is None:
            if destination.exists():
                destination.unlink()
            continue
        source = artifacts_dir / ARTIFACT_FILES_DIR / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256_file(destination) != expected:
            raise BranchArtifactError(
                f'{artifacts_dir.name}: copy of {name} does not match its recorded digest')
    from validation_tasks_v49 import hashes
    observed = hashes(target)
    if observed != verified['final_source']:
        raise BranchArtifactError(
            f'{artifacts_dir.name}: reconstruction does not reproduce the branch\'s final '
            f'file map ({len(observed)} vs {len(verified["final_source"])} files)')
    return verified


def write_batch_index(out: Path, sample_dirs: list[Path]) -> dict:
    """Pin the SHA-256 of every per-branch artifact file (written once, at run time)."""
    out = Path(out)
    branches = {}
    for sample_dir in sample_dirs:
        entry_files = {}
        for name in INDEXED_ARTIFACTS:
            target = sample_dir / name
            if target.is_file():
                entry_files[name] = sha256_file(target)
        record, final_source = load_branch_artifacts(sample_dir)
        branches[sample_dir.name] = {
            'arm': record['arm'], 'position': record['position'], 'task': record['task'],
            'allowed': list(record['allowed']),
            'files': entry_files,
            'final_source_digest': digest_map(final_source),
            'final_files_digest': record['final_files_digest'],
            'changed_files': record['changed_files'],
        }
    index = {'artifact_version': ARTIFACT_VERSION, 'branches': branches}
    save(out / BRANCH_INDEX_FILE, index)
    return index


def load_batch_index(out: Path) -> dict:
    path = Path(out) / BRANCH_INDEX_FILE
    if not path.is_file():
        raise BranchArtifactError(f'{BRANCH_INDEX_FILE} missing: per-branch persistence '
                                  f'cannot be verified')
    index = load(path)
    if index.get('artifact_version') != ARTIFACT_VERSION:
        raise BranchArtifactError('batch artifact index has an unexpected version')
    return index


def verify_batch_index(out: Path, sample_dirs: list[Path],
                       frozen_source: dict[str, str]) -> dict:
    """Every branch in the batch must have a consistent, pinned artifact set.

    ``frozen_source`` is the frozen prefix's cache-free file map: the same map
    the branches were diffed against when they were persisted.
    """
    index = load_batch_index(out)
    verified = {}
    for sample_dir in sample_dirs:
        entry = (index.get('branches') or {}).get(sample_dir.name)
        if entry is None:
            raise BranchArtifactError(
                f'{sample_dir.name}: branch is absent from {BRANCH_INDEX_FILE}')
        record = load_branch_artifacts(sample_dir)[0]
        report_path = sample_dir / 'report.json'
        report = load(report_path) if report_path.is_file() else None
        verified[sample_dir.name] = verify_branch_artifacts(
            sample_dir, frozen_source, allowed=list(record.get('allowed') or []),
            index_entry=entry, report=report)
    return verified
