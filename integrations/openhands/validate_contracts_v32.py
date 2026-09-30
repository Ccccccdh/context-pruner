"""Offline baseline/reference gate for prospectively authored v32 tasks."""
import argparse
import json
from pathlib import Path
import subprocess

from validation_tasks_v32 import TASKS, SOURCES, prepare, evaluate
from reference_validation_v32 import apply_reference


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[2]
    out=Path(args.out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    upstream=root/'.tooling/upstream'
    for source in SOURCES.values():
        actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=upstream/source['directory'],text=True).strip()
        assert actual==source['commit'],source
    rows={}
    for task in TASKS:
        group=out/task
        base=group/'baseline/workspace'
        reference=group/'reference/workspace'
        if base.exists() or reference.exists():
            raise SystemExit(f'Existing task state preserved: {task}')
        prepare(upstream,base,task)
        prepare(upstream,reference,task)
        apply_reference(reference,task)
        regression=evaluate(base,task,group/'baseline/regression',regression_only=True)
        baseline=evaluate(base,task,group/'baseline/evaluation')
        expected=evaluate(reference,task,group/'reference/evaluation')
        rows[task]={'regression':regression,'baseline':baseline,'reference':expected}
        print(task,rows[task],flush=True)
    (out/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    assert all(r['regression']['passed'] and not r['baseline']['passed'] and r['reference']['passed'] for r in rows.values()),rows


if __name__=='__main__':main()
