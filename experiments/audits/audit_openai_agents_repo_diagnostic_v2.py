"""Independent frozen-result and content-free input-evidence audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from experiments.audits.audit_openai_agents_repo_diagnostic_v1 import audit as audit_v1


STAGES = {'filter_before', 'filter_after', 'model_input'}
SCHEMA = 'openai_input_evidence_v2'
FIELDS = {
    'schema', 'task', 'stage', 'index', 'input_sha256', 'item_count',
    'input_bytes', 'constraint_present',
    'constraint_present_in_unprotected_messages',
    'constraint_present_in_protected_groups', 'protected_groups',
}
GROUP_FIELDS = {
    'group_id', 'group_sha256', 'item_count', 'tool_output_count',
    'tool_output_sha256',
}
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def audit_evidence(batch: Path, rows: list[dict]) -> list[str]:
    issues: list[str] = []
    expected_files: set[Path] = set()
    for row in rows:
        key = f"{row.get('scenario')}-{row.get('repeat')}-{row.get('method')}"
        relative = row.get('input_evidence_file')
        if relative != f'input-evidence/{key}.jsonl':
            issues.append(f'{key}: evidence path mismatch')
            continue
        path = batch / relative
        expected_files.add(path)
        if not path.is_file():
            issues.append(f'{key}: evidence file missing')
            continue
        try:
            evidence = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
        except (UnicodeError, json.JSONDecodeError):
            issues.append(f'{key}: evidence JSON invalid')
            continue
        for item in evidence:
            if set(item) != FIELDS or item.get('schema') != SCHEMA or item.get('task') != row.get('scenario') or item.get('stage') not in STAGES:
                issues.append(f'{key}: evidence schema mismatch')
                break
            if not HEX64.fullmatch(str(item.get('input_sha256'))):
                issues.append(f'{key}: evidence digest invalid')
            for group in item.get('protected_groups', []):
                if set(group) != GROUP_FIELDS or not HEX64.fullmatch(str(group.get('group_sha256'))):
                    issues.append(f'{key}: protected group schema mismatch')
                    break
                if any(not HEX64.fullmatch(str(digest)) for digest in group.get('tool_output_sha256', [])):
                    issues.append(f'{key}: tool output digest invalid')
        before = [item for item in evidence if item.get('stage') == 'filter_before']
        after = [item for item in evidence if item.get('stage') == 'filter_after']
        model = [item for item in evidence if item.get('stage') == 'model_input']
        if len(model) != row.get('model_calls') or len(model) != row.get('input_evidence_model_calls'):
            issues.append(f'{key}: model call evidence mismatch')
        if len(before) != len(after) or len(before) != row.get('input_evidence_filter_calls'):
            issues.append(f'{key}: filter call evidence mismatch')
        if row.get('method') == 'none' and before:
            issues.append(f'{key}: baseline unexpectedly filtered')
        if [item.get('index') for item in model] != list(range(len(model))):
            issues.append(f'{key}: model indices mismatch')
        if [item.get('index') for item in before] != list(range(len(before))) or [item.get('index') for item in after] != list(range(len(after))):
            issues.append(f'{key}: filter indices mismatch')
        model_hashes = {item.get('input_sha256') for item in model}
        for first, second in zip(before, after):
            if first.get('protected_groups') != second.get('protected_groups'):
                issues.append(f'{key}: protected groups changed')
            if second.get('input_sha256') not in model_hashes:
                issues.append(f'{key}: filter output not observed at model boundary')
        content = path.read_text(encoding='utf-8')
        if re.search(r'sk-[A-Za-z0-9]{24,}', content):
            issues.append(f'{key}: credential-like text in evidence')
    actual_files = set((batch / 'input-evidence').glob('*.jsonl'))
    if actual_files != expected_files:
        issues.append('evidence file grid mismatch')
    return issues


def audit(batch: Path, freeze: Path) -> dict:
    result = audit_v1(batch, freeze)
    rows = [json.loads(line) for line in (batch / 'samples.jsonl').read_text(encoding='utf-8').splitlines() if line]
    issues = list(result['issues']) + audit_evidence(batch, rows)
    return {**result, 'complete': not issues, 'issues': issues, 'evidence_checked': True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('batch', type=Path)
    parser.add_argument('--freeze', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.batch, args.freeze)
    (args.batch / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
