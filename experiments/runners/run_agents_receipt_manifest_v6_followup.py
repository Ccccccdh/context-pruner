"""Independent bounded cohort; original V6 behavior and failed cohort stay frozen."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path

from experiments.runners import run_agents_receipt_manifest_v6 as v6

ROOT = v6.ROOT
PRIOR = ROOT / 'runs/agents-receipt-v6-20261015'
OPERATION_CLAIM = ROOT / 'runs/agents-receipt-v6-followup-20261015/paid-operation.claim'
PRIOR_MANIFEST_SHA256 = '35612d60a4cbc6e97b2237c83bdebc10daed73ace63428f0c5f1d22b799ca9a4'
_ACTIVE = False


def predecessor_binding():
    required = ('completion_manifest.json', 'paid/summary.json', 'frozen/freeze.json', 'paid-operation.claim')
    if not all((PRIOR/name).is_file() for name in required):
        raise ValueError('unbound predecessor')
    if v6.base.file_sha(PRIOR/'completion_manifest.json') != PRIOR_MANIFEST_SHA256:
        raise ValueError('predecessor manifest identity drift')
    v6.verify(PRIOR/'frozen')
    manifest = json.loads((PRIOR/'completion_manifest.json').read_text(encoding='utf-8-sig'))
    for name, expected in manifest.items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file() or v6.base.file_sha(path) != expected:
            raise ValueError('predecessor evidence drift: '+name)
    summary = json.loads((PRIOR/'paid/summary.json').read_text())
    claim = json.loads((PRIOR/'paid-operation.claim').read_text())
    expected_claim = dict(freeze=str((PRIOR/'frozen').resolve()),out=str((PRIOR/'paid').resolve()),
                          freeze_sha256=v6.base.file_sha(PRIOR/'frozen/freeze.json'),max_requests=96,cap_usd=1.)
    last = summary['ledger'][-1]
    if (claim != expected_claim or summary.get('mock') is not False or summary['samples_completed'] != 5
            or summary['provider_requests'] != 35 or last.get('status_code') != 503
            or last['sample_id'] != 'receiptdev223-A0-r1/turn1' or last.get('raw_usage') is not None):
        raise ValueError('predecessor identity/outcome mismatch')
    return dict(run=str(PRIOR.resolve()),freeze_sha256=expected_claim['freeze_sha256'],
                summary_sha256=v6.base.file_sha(PRIOR/'paid/summary.json'),
                completion_manifest_sha256=v6.base.file_sha(PRIOR/'completion_manifest.json'),
                http_requests=35,completed_samples=5,unknown_usage_requests=1)


@contextmanager
def cohort():
    global _ACTIVE
    if _ACTIVE or v6.SEEDS != (211,223): raise RuntimeError('serial nonnested cohort required')
    binding = predecessor_binding()
    saved = {key:getattr(v6,key) for key in ('SEEDS','OPERATION_CLAIM','PROTOCOL','SOURCES')}
    _ACTIVE = True
    v6.SEEDS = (227,229)
    v6.OPERATION_CLAIM = OPERATION_CLAIM
    v6.PROTOCOL = {**deepcopy(saved['PROTOCOL']), 'seed_ids':[227,229],
                   'cohort_id':'v6-independent-followup-20261015','prior_run_binding':binding}
    v6.SOURCES = [*saved['SOURCES'],'experiments/runners/run_agents_receipt_manifest_v6_followup.py',
                  'docs/AGENTS_V6_FOLLOWUP_PROTOCOL_20261015.md']
    try:
        yield
    finally:
        for key,value in saved.items():setattr(v6,key,value)
        _ACTIVE = False


def prepare(out):
    with cohort():return v6.prepare(out)


def verify(out):
    with cohort():return v6.verify(out)


def run_batch(freeze,out,key,*,mock=False):
    with cohort():return v6.run_batch(freeze,out,key,mock=mock)


def audit_output(freeze,out,*,mock):
    with cohort():return v6.audit_output(freeze,out,mock=mock)


def reserve_operation(freeze,out):
    with cohort():return v6.reserve_operation(freeze,out)


if __name__ == '__main__':
    with cohort():v6.main()
