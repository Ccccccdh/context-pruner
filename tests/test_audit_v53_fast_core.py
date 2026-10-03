"""Tiny zero-API boundary and resume checks; no Django tree is copied."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from integrations.openhands.audit_v53_fast_core import (
    AtomicCheckpoint, credential_scan, file_map, full_boundary, map_digest,
)


class FastAuditCoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'django').mkdir()
        (self.root / 'django' / 'allowed.py').write_text('old', encoding='utf-8')
        (self.root / 'django' / 'fixed.py').write_text('fixed', encoding='utf-8')
        (self.root / 'TASK.md').write_text('task', encoding='utf-8')
        self.original = file_map(self.root)

    def test_allowed_change_passes_full_file_boundary(self):
        (self.root / 'django' / 'allowed.py').write_text('new', encoding='utf-8')
        changed, outside = full_boundary(self.original, file_map(self.root), ['django/allowed.py'])
        self.assertEqual(['django/allowed.py'], changed)
        self.assertEqual([], outside)

    def test_noneditable_change_and_new_file_are_caught(self):
        (self.root / 'django' / 'fixed.py').write_text('tampered', encoding='utf-8')
        (self.root / 'extra.py').write_text('new', encoding='utf-8')
        changed, outside = full_boundary(self.original, file_map(self.root), ['django/allowed.py'])
        self.assertEqual(['django/fixed.py', 'extra.py'], changed)
        self.assertEqual(changed, outside)

    def test_parallel_file_map_matches_independent_serial_reference(self):
        cache = self.root / '__pycache__'
        cache.mkdir()
        (cache / 'ignored.py').write_text('ignored', encoding='utf-8')
        (self.root / 'ignored.pyc').write_bytes(b'ignored')
        for index in range(280):
            (self.root / 'django' / f'file_{index:03d}.py').write_bytes(
                f'content-{index}'.encode())
        reference = {}
        for path in self.root.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in {'.pyc', '.pyo'}:
                reference[path.relative_to(self.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        serial = file_map(self.root, max_workers=1)
        parallel = file_map(self.root, max_workers=8)
        self.assertEqual(reference, serial)
        self.assertEqual(reference, parallel)
        self.assertEqual(map_digest(serial), map_digest(parallel))

    def test_checkpoint_validity_requires_all_fingerprints_and_log(self):
        log = self.root / 'test.txt'
        log.write_text('Ran 1 tests\nOK\n', encoding='utf-8')
        path = self.root / 'checkpoint.json'
        row = {'sample': 'r1-task-none', 'artifact_success': True}
        arguments = dict(manifest_sha='m', code_sha='c', inputs_sha='i',
                         workspace_sha=map_digest(self.original), log=log)
        checkpoint = AtomicCheckpoint(path)
        checkpoint.record('r1-task-none', row, **arguments)
        self.assertEqual(row, AtomicCheckpoint(path).valid_row('r1-task-none', **arguments))
        for changed_key in ('manifest_sha', 'code_sha', 'inputs_sha', 'workspace_sha'):
            changed = {**arguments, changed_key: 'stale'}
            self.assertIsNone(AtomicCheckpoint(path).valid_row('r1-task-none', **changed))
        log.write_text('different', encoding='utf-8')
        self.assertIsNone(AtomicCheckpoint(path).valid_row('r1-task-none', **arguments))
        log.unlink()
        self.assertIsNone(AtomicCheckpoint(path).valid_row('r1-task-none', **arguments))

    def test_nested_workspace_named_generated_dirs_are_scanned(self):
        key = 'SYNTHETIC-CREDENTIAL-FOR-TEST'
        verified = self.root / 'workspaces' / 'w000'
        verified.mkdir(parents=True)
        (verified / 'public.py').write_text('public', encoding='utf-8')
        nested_a = self.root / 'r1-task-none' / 'conversation' / 'workspaces' / 'secret.txt'
        nested_b = self.root / 'r1-task-none' / 'evaluation-audit-fast' / 'host-workspace' / 'secret.txt'
        for path in (nested_a, nested_b):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(key, encoding='utf-8')
        leaks, stats = credential_scan(self.root, key, [], {verified})
        self.assertEqual({str(nested_a.relative_to(self.root)), str(nested_b.relative_to(self.root))}, set(leaks))
        self.assertEqual(['workspaces/w000'], stats['verified_workspace_dirs_pruned'])


if __name__ == '__main__':
    unittest.main()
