"""Freeze one public benchmark instance for host-only offline evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--instance', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    dataset, out = Path(args.dataset).resolve(), Path(args.out).resolve()
    assert dataset.is_file() and not out.exists()
    relation = duckdb.read_parquet(str(dataset))
    selected = relation.filter("instance_id = '" + args.instance.replace("'", "''") + "'").fetchall()
    assert len(selected) == 1
    row = dict(zip(relation.columns, selected[0]))
    out.mkdir(parents=True)
    public = {key: row[key] for key in ('instance_id', 'repo', 'base_commit', 'created_at',
                                          'difficulty', 'problem_statement', 'FAIL_TO_PASS', 'PASS_TO_PASS')}
    public['dataset_sha256'] = hashlib.sha256(dataset.read_bytes()).hexdigest()
    (out / 'instance.json').write_text(json.dumps(public, indent=2, ensure_ascii=False), encoding='utf-8')
    (out / 'reference.patch').write_bytes(row['patch'].encode('utf-8'))
    (out / 'host-tests.patch').write_bytes(row['test_patch'].encode('utf-8'))
    print(json.dumps({'instance': row['instance_id'], 'base_commit': row['base_commit'],
                      'source_patch_bytes': len(row['patch']), 'test_patch_bytes': len(row['test_patch']),
                      'dataset_sha256': public['dataset_sha256']}))


if __name__ == '__main__':
    main()
