"""Read frozen v32/v39/v40/v41 traces and emit bounded descriptive evidence."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from integrations.openhands.analyze_traces import sample_trace
from integrations.openhands.feedback_v42 import failure_index, format_correction_feedback

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'runs/stage5-openhands'
OUT = RUNS / 'v42-offline-diagnosis'
EXPERIMENTS = (
    'three-projects-v32-3x5-final',
    'django-16263-v39-feedback-retry-02',
    'django-16315-v40-pilot-retry-02',
    'django-16315-v41-development-preflight',
)


def selected_samples(experiment):
    samples = sorted((RUNS / experiment).glob('r*/report.json'))
    if 'v32' in experiment:
        samples = [p for p in samples if 'dotenv_value_origins-pruner_v11' in p.parent.name]
    return samples


def analyze_sample(report_path):
    sample = report_path.parent
    trace = sample_trace(sample)
    edits = [a for a in trace['actions'] if a['command'] in ('str_replace', 'insert', 'create', 'undo_edit')]
    noops = [a for a in edits if a['command'] == 'str_replace' and a['old_str'] == a['new_str']]
    first = sample / 'evaluation-first/test.txt'
    final = sample / 'evaluation-final/test.txt'
    first_log = first.read_text(encoding='utf-8', errors='replace') if first.exists() else ''
    final_log = final.read_text(encoding='utf-8', errors='replace') if final.exists() else ''
    feedback = format_correction_feedback(final_log, previous_log=first_log) if first_log and final_log else ''
    return {'experiment': sample.parent.name, 'sample': sample.name, 'arm': trace['arm'],
            'calls': len(trace['calls']), 'input_tokens': trace['agent_input'] + trace['summary_input'],
            'condensations': len(trace['condensations']), 'views': trace['view_count'],
            'already_covered_reads': trace['covered_reads'], 'exact_repeat_views': trace['repeated_views'],
            'phase2_covered_reads': sum(a['command'] == 'view' and a['phase'] == 2 and a['already_covered_read']
                                        for a in trace['actions']),
            'edit_attempts': len(edits), 'failed_edits': trace['failed_edits'],
            'zero_change_attempts': len(noops),
            'first_host_failures': len(failure_index(first_log)) if first_log else None,
            'final_host_failures': len(failure_index(final_log)) if final_log else None,
            'final_root_causes': Counter(cause for _, _, cause in failure_index(final_log)).most_common(4),
            'new_feedback_excerpt': feedback[:900]}


def main():
    records = [analyze_sample(path) for experiment in EXPERIMENTS
               for path in selected_samples(experiment)]
    OUT.mkdir(exist_ok=True)
    (OUT / 'trace-metrics.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    for record in records:
        print(record['sample'], 'calls', record['calls'], 'views', record['views'],
              'covered', record['already_covered_reads'], 'phase2_covered', record['phase2_covered_reads'],
              'edits', record['edit_attempts'], 'failed', record['failed_edits'],
              'noops', record['zero_change_attempts'], 'host',
              record['first_host_failures'], '->', record['final_host_failures'])


if __name__ == '__main__':
    main()
