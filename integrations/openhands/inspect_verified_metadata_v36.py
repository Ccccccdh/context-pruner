"""Inspect public SWE-bench Verified metadata without printing gold patch bodies."""
from __future__ import annotations

import argparse
import json
import re

import duckdb


def paths(patch: str) -> list[str]:
    return re.findall(r'^diff --git a/(.*?) b/', patch or '', flags=re.MULTILINE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    relation = duckdb.read_parquet(args.dataset)
    rows = relation.filter("repo = '" + args.repo.replace("'", "''") + "'").fetchall()
    columns = relation.columns
    summaries = []
    for raw in rows:
        row = dict(zip(columns, raw))
        summaries.append({'id': row['instance_id'], 'created_at': row['created_at'],
                          'difficulty': row['difficulty'], 'base_commit': row['base_commit'],
                          'source_paths': paths(row['patch']), 'test_paths': paths(row['test_patch']),
                          'fail_to_pass': len(row['FAIL_TO_PASS'] or []),
                          'pass_to_pass': len(row['PASS_TO_PASS'] or []),
                          'problem_chars': len(row['problem_statement'] or ''),
                          'problem_intro': (row['problem_statement'] or '')[:180].replace('\n', ' ')})
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
