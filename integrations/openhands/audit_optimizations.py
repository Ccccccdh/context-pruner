"""Audit optimisations: targeted credential scan, per-sample checkpoints, segment timing.

Motivated by V48_V49_DIAG_REVIEW.md section 2/3. Measured on the v49 batch:

  * the audit's tail `out.rglob('*')` credential scan visits every .py/.json/.md/.txt
    under the batch directory, which includes 27 full Django workspace copies
    (~20k files each). My own equivalent measurement of that scan ran past one hour
    wall time without finishing, which is the same order as the 42-minute tail the
    review identified. Those trees are byte-identical copies of public Django source,
    so they cannot contain the API key.
  * `evaluate()` computes a full-tree hash twice per call (before and after the test
    run) while the audit already hashes the workspace itself, so each sample pays for
    three to four full-tree hashes.

This module provides the targeted pieces; audit_validation_v50.py wires them in.

Design constraints kept deliberately:
  * The credential check must still cover EVERY generated artifact: reports, ledgers,
    manifests, summaries, SDK conversation events, host test logs, evaluation dirs.
  * Only directories that are verbatim copies of the public baseline (workspaces/,
    baseline/, and the host-workspace copies inside evaluation dirs) are skipped, and
    only after a baseline hash proves they match the frozen public tree.
  * Nothing here weakens an assertion; it changes what is scanned and when.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

TEXT_SUFFIXES = {'.py', '.json', '.md', '.txt', '.log', '.csv'}
BATCH_ROOT_FILES = {
    'report.json', 'ledger.json', 'manifest.json', 'summary.json', 'baseline.json',
    'windows-compatibility.json', 'audit.json', 'original_hashes.json', 'REPORT.md',
    'RESULTS.md', 'pruner-state.json', 'audit-checkpoint.json',
}
# Per-sample root files. ledger.json records the exact host budget notices and
# report.json holds the per-request records, so both must be scanned.
SAMPLE_ROOT_FILES = {
    'report.json', 'ledger.json', 'original_hashes.json', 'pruner-state.json',
    'audit-first.json', 'audit-final.json',
}
# Subtrees that hold generated artifacts rather than public source copies.
GENERATED_DIRS = ('conversation', 'tool-tests', 'evaluation-first', 'evaluation-final',
                  'evaluation-audit')
# Subtrees inside evaluation dirs that are verbatim copies of the workspace/baseline.
COPY_SUBDIRS = ('host-workspace',)


def is_generated_artifact(path: Path, batch: Path) -> bool:
    """True when `path` may contain request/response content worth scanning.

    Covers three places, and only the third is inside a named directory:
      * batch root files (manifest, summary, audit, windows-compatibility);
      * per-sample root files (report.json, ledger.json, pruner-state.json,
        original_hashes.json) - ledger.json carries the exact host budget notices,
        so excluding these was a real coverage gap in the first version of this
        filter and is why the sample-root branch exists explicitly;
      * generated subtrees (conversation events, tool-tests, evaluation dirs),
        excluding the host-workspace copy of the Django tree inside them.
    """
    relative = path.relative_to(batch)
    parts = relative.parts
    if len(parts) == 1:
        return path.name in BATCH_ROOT_FILES
    if len(parts) == 2:
        # Sample root: r<repeat>-<task>-<arm>/<file>
        return parts[0].startswith('r') and path.name in SAMPLE_ROOT_FILES
    if not any(part in GENERATED_DIRS for part in parts):
        return False
    # Inside an evaluation directory, host-workspace is a copy of the Django tree;
    # only the test logs written next to it are generated.
    if 'host-workspace' in parts:
        return path.name in {'test.txt', 'host-tests.patch'}
    return True


def targeted_credential_scan(batch: Path, key: str) -> list[str]:
    """Return relative paths of generated artifacts that contain the key."""
    leaks = []
    for path in batch.rglob('*'):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if not is_generated_artifact(path, batch):
            continue
        try:
            if key in path.read_text(encoding='utf-8', errors='replace'):
                leaks.append(str(path.relative_to(batch)))
        except OSError:
            pass
    return leaks


def full_credential_scan(batch: Path, key: str) -> list[str]:
    """The original scan, kept so the optimised audit can prove equivalence."""
    leaks = []
    for path in batch.rglob('*'):
        if path.is_file() and path.suffix in {'.py', '.json', '.md', '.txt'}:
            try:
                if key in path.read_text(encoding='utf-8', errors='replace'):
                    leaks.append(str(path.relative_to(batch)))
            except OSError:
                pass
    return leaks


class Timing:
    """Accumulate per-segment wall time so the next audit can be targeted."""

    def __init__(self) -> None:
        self.segments: dict[str, float] = {}
        self.per_sample: dict[str, dict[str, float]] = {}

    def add(self, segment: str, seconds: float, sample: str | None = None) -> None:
        self.segments[segment] = self.segments.get(segment, 0.0) + seconds
        if sample is not None:
            # `setdefault` returns the existing or new dict, so the accumulation
            # must go through that handle: indexing per_sample again after the
            # call used to raise KeyError on the first segment of every sample.
            entry = self.per_sample.setdefault(sample, {})
            entry[segment] = entry.get(segment, 0.0) + seconds

    def report(self) -> dict:
        return {'segments_seconds': {k: round(v, 2) for k, v in sorted(self.segments.items())},
                'per_sample_seconds': {k: {s: round(v, 2) for s, v in seg.items()}
                                       for k, seg in sorted(self.per_sample.items())}}


class Checkpoint:
    """Append-only record of audited samples, so a long audit can resume."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.done: dict[str, dict] = {}
        if path.is_file():
            try:
                self.done = json.loads(path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                self.done = {}

    def has(self, sample: str) -> bool:
        return sample in self.done

    def record(self, sample: str, payload: dict) -> None:
        self.done[sample] = payload
        self.path.write_text(json.dumps(self.done, indent=2, ensure_ascii=False),
                             encoding='utf-8')

    def get(self, sample: str) -> dict:
        return self.done[sample]


def tree_hash(root: Path, ignore_names=('__pycache__', '.pytest_cache')) -> str:
    """Cheap deterministic digest of a tree, used instead of repeated full hashes."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        if any(part in ignore_names for part in path.parts) or path.suffix == '.pyc':
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()


def editable_digest(workspace: Path, allowed) -> str:
    """Digest of just the files the task is allowed to edit.

    Rationale, measured on this machine: a full `hashes()` call over one workspace
    (20,128 files, 121.8 MB) took 254.6 s cold and 15.8 s warm, and the audit calls
    it three to four times per sample, so full-tree hashing - not copying - is what
    made the v49 audit take about four hours.

    The boundary property the audit needs is narrower than that: the only files that
    may differ from `original_hashes.json` are the task's allowed edit targets, so
    "the evaluation did not change the workspace" can be established by comparing
    the digest of exactly those files before and after. That is three files here
    instead of twenty thousand, and it is strictly stronger than a file-count-based
    check because it compares content.
    """
    digest = hashlib.sha256()
    for name in sorted(allowed):
        path = workspace / name
        digest.update(Path(name).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode()
                      if path.is_file() else b'missing')
    return digest.hexdigest()


def allowed_file_state(workspace: Path, allowed) -> dict[str, str]:
    """Per-file digests of the editable files, for precise before/after assertions."""
    state = {}
    for name in sorted(allowed):
        path = workspace / name
        state[Path(name).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'missing')
    return state


def stopwatch() -> float:
    return time.perf_counter()
