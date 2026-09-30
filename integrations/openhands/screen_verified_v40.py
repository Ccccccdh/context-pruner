"""Rank SWE-bench Verified candidates from metadata paths, never patch bodies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import duckdb

EXCLUDED = {
    'pytest-dev__pytest-10356',
    'pylint-dev__pylint-8898',
    'django__django-16263',
}
DIFFICULTY_ORDER = {'15 min - 1 hour': 0, '1-4 hours': 1}


def changed_paths(patch: str) -> list[str]:
    return re.findall(r'^diff --git a/(.*?) b/', patch or '', flags=re.MULTILINE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    relation = duckdb.read_parquet(str(args.dataset))
    columns = relation.columns
    selected = []
    for raw in relation.fetchall():
        item = dict(zip(columns, raw))
        if item['instance_id'] in EXCLUDED or item['difficulty'] not in DIFFICULTY_ORDER:
            continue
        source = changed_paths(item['patch'])
        tests = changed_paths(item['test_patch'])
        if not 2 <= len(source) <= 4 or not all(path.endswith('.py') for path in source):
            continue
        if not tests or not item['FAIL_TO_PASS']:
            continue
        pass_count = len(item['PASS_TO_PASS'] or [])
        if pass_count > 150:
            continue
        selected.append({
            'instance_id': item['instance_id'],
            'repo': item['repo'],
            'base_commit': item['base_commit'],
            'created_at': str(item['created_at']),
            'difficulty': item['difficulty'],
            'source_paths': source,
            'target_test_paths': tests,
            'fail_to_pass_count': len(item['FAIL_TO_PASS']),
            'pass_to_pass_count': pass_count,
            'problem_chars': len(item['problem_statement'] or ''),
        })
    selected.sort(key=lambda x: x['created_at'], reverse=True)
    selected.sort(key=lambda x: DIFFICULTY_ORDER[x['difficulty']])
    payload = {'rule': 'V40_SELECTION_RULES.md', 'candidate_count': len(selected),
               'candidates': selected}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'candidate_count': len(selected), 'first_five': selected[:5]},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
