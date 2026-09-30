"""Offline positive/negative gate for the new packaging contract."""
import argparse
import json
from pathlib import Path
from validation_tasks_v34 import TASKS, SOURCES, prepare, evaluate
from reference_validation_v34 import apply_reference

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    assert not out.exists()
    out.mkdir(parents=True)
    task = next(iter(TASKS))
    upstream = ROOT / '.tooling/upstream'
    before = out / 'original'
    prepare(upstream, before, task)
    base_regression = evaluate(before, task, out / 'original-regression', regression_only=True)
    base_contract = evaluate(before, task, out / 'original-contract')
    after = out / 'reference'
    prepare(upstream, after, task)
    apply_reference(after)
    reference = evaluate(after, task, out / 'reference-evaluation')
    result = {'task': task, 'original_regression': base_regression,
              'original_contract': base_contract, 'reference': reference}
    (out / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    assert base_regression['passed'] and not base_contract['passed'] and reference['passed']


if __name__ == '__main__':
    main()
