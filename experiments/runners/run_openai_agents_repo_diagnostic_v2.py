"""Public-source v2 diagnosis with hashed model-input evidence.

This entrypoint leaves the frozen v1 runner and shared SDK runner untouched.
The shared runner is sequential, so one recorder belongs to exactly one arm.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners.openai_agents_input_evidence_v2 import (
    EvidenceCaptureFilter, EvidenceModelProxy, InputEvidenceRecorder,
)


@contextmanager
def evidence_wiring(output: Path):
    """Attach evidence at both SDK boundaries for the duration of one run."""
    original_filter = base._build_filter
    original_model = base.RecordingRetryModel
    original_case = base.run_case
    active: dict[str, Any] = {}

    def build_filter(method: str, *, case, **kwargs):
        recorder = InputEvidenceRecorder(case.scenario)
        active['key'] = (case.scenario, case.repeat, method)
        active['recorder'] = recorder
        inner = original_filter(method, case=case, **kwargs)
        return EvidenceCaptureFilter(inner, recorder) if inner is not None else None

    def recording_model(*args, **kwargs):
        return EvidenceModelProxy(original_model(*args, **kwargs), active['recorder'])

    async def run_case(case, *, method: str, gate_model=None, **kwargs):
        key = (case.scenario, case.repeat, method)
        if active.get('key') != key:
            raise RuntimeError('input evidence recorder does not match sample')
        recorder = active['recorder']
        if gate_model is not None:
            gate_model = EvidenceModelProxy(gate_model, recorder)
        try:
            result = await original_case(case, method=method, gate_model=gate_model, **kwargs)
        finally:
            evidence = output / 'input-evidence' / f'{case.scenario}-{case.repeat}-{method}.jsonl'
            recorder.save(evidence)
        result['input_evidence_file'] = str(evidence.relative_to(output)).replace('\\', '/')
        result['input_evidence_model_calls'] = recorder.model_calls
        result['input_evidence_filter_calls'] = recorder.filter_calls
        return result

    base._build_filter = build_filter
    base.RecordingRetryModel = recording_model
    base.run_case = run_case
    try:
        yield
    finally:
        base._build_filter = original_filter
        base.RecordingRetryModel = original_model
        base.run_case = original_case


def main(argv=None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    parsed = base.build_parser().parse_args([
        '--confirm-send-synthetic-data' if item == '--confirm-send-public-source' else item
        for item in args
    ])
    output = Path(parsed.out) / parsed.experiment_id
    with evidence_wiring(output):
        return v1.main(args)


if __name__ == '__main__':
    raise SystemExit(main())
