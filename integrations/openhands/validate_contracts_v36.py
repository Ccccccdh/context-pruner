"""Reproduce the base-fail/reference-pass gate for the frozen external issue."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from prepare_verified_v36 import apply_patch
from validation_tasks_v36 import INSTANCE, INSTANCE_DIR, TASKS, evaluate, hashes, prepare, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    assert not out.exists()
    out.mkdir(parents=True)
    task = next(iter(TASKS))
    source = ROOT / '.tooling/upstream'
    base, reference = out / 'base', out / 'reference'
    original_hashes = prepare(source, base, task)
    prepare(source, reference, task)
    apply_patch(reference, INSTANCE_DIR / 'reference.patch')
    results = {
        'instance_id': INSTANCE['instance_id'], 'base_commit': INSTANCE['base_commit'],
        'dataset_sha256': INSTANCE['dataset_sha256'],
        'reference_patch_sha256': hashlib.sha256((INSTANCE_DIR / 'reference.patch').read_bytes()).hexdigest(),
        'host_test_patch_sha256': hashlib.sha256((INSTANCE_DIR / 'host-tests.patch').read_bytes()).hexdigest(),
        'original_source_hashes_sha256': hashlib.sha256(json.dumps(original_hashes, sort_keys=True).encode()).hexdigest(),
        'reference_source_hashes_sha256': hashlib.sha256(json.dumps(hashes(reference), sort_keys=True).encode()).hexdigest(),
        'original_regression': evaluate(base, task, out / 'original-regression', regression_only=True),
        'original_host': evaluate(base, task, out / 'original-host'),
        'reference_regression': evaluate(reference, task, out / 'reference-regression', regression_only=True),
        'reference_host': evaluate(reference, task, out / 'reference-host'),
    }
    assert results['original_regression']['passed']
    assert not results['original_host']['passed']
    assert results['reference_regression']['passed']
    assert results['reference_host']['passed']
    (out / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
