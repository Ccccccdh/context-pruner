"""Independent r4 multi-task freeze and quality audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.audits.audit_crewai_handoff_v4 import audit as audit_quality


ROOT = Path(__file__).resolve().parents[2]
FROZEN_PATHS = {
    'experiments/runners/run_crewai_handoff_v4_multitask.py',
    'experiments/runners/run_crewai_handoff_v4.py',
    'experiments/runners/crewai_handoff_quality_v4.py',
    'experiments/runners/run_crewai_experiment.py',
    'experiments/audits/audit_crewai_handoff_v4.py',
    'experiments/audits/audit_crewai_handoff_v4_multitask.py',
    'tasks/stage5_autogen/natural_tasks.json',
    'integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R4_DRAFT.md',
    'integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R4_MULTITASK_DEV.md',
}
MANIFEST_FIELDS = {
    'protocol', 'runner_sha256', 'task_sha256', 'tasks', 'methods', 'repeats',
    'model', 'mode', 'base_url', 'budget', 'limits', 'max_api_requests',
    'failure_policy',
}


def audit(directory: Path, freeze_path: Path, *, dry_run_mock: bool = False) -> dict:
    result = audit_quality(directory)
    errors = list(result['errors'])
    freeze = json.loads(freeze_path.read_text(encoding='utf-8'))
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not dry_run_mock and freeze.get('batch') != directory.name:
        errors.append('freeze batch mismatch')
    paths = freeze.get('source_sha256', {})
    if set(paths) != FROZEN_PATHS:
        errors.append('freeze source path set mismatch')
    for name in sorted(FROZEN_PATHS):
        path = ROOT / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != paths.get(name):
            errors.append(f'frozen source mismatch: {name}')
    expected = freeze.get('manifest', {})
    if set(expected) != MANIFEST_FIELDS or set(manifest) != MANIFEST_FIELDS:
        errors.append('manifest field set mismatch')
    for field in sorted(MANIFEST_FIELDS):
        if dry_run_mock and field == 'mode':
            if expected.get('mode') != 'api' or manifest.get('mode') != 'mock':
                errors.append('mock mode mismatch')
        elif expected.get(field) != manifest.get(field):
            errors.append(f'manifest mismatch: {field}')
    if freeze.get('samples') != result['expected_rows']:
        errors.append('freeze sample count mismatch')
    result.update(errors=errors, freeze_checked=True, dry_run_mock=dry_run_mock)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--dry-run-mock', action='store_true')
    args = parser.parse_args()
    result = audit(args.directory, args.freeze, dry_run_mock=args.dry_run_mock)
    (args.directory / 'audit.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['complete'] and not result['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
