"""Six natural development samples; observed receipt metadata is the only intervention."""
from contextlib import contextmanager
from copy import deepcopy
import argparse
import hashlib
import json
import math
import os
from pathlib import Path

from agents.models.chatcmpl_converter import Converter
from agents.models.openai_chatcompletions import _to_dump_compatible
from context_pruner.adapters.openai_agents_receipts_v6 import OpenAIAgentsReceiptLifecycle
from experiments.runners import run_agents_confirmation_v4 as base
from experiments.runners import run_agents_confirmation_v5 as v5
from experiments.runners.agents_registry_tasks_v4 import make_task, admission, dump, bytes_sha

ROOT = base.ROOT
OPERATION_CLAIM = ROOT / 'runs/agents-receipt-v6-20261015/paid-operation.claim'
SEEDS = (211, 223)
_VERIFY, _MATRIX = base.verify, base.matrix
PROTOCOL = {**deepcopy(base.PROTOCOL), 'candidate_version': 'v6-observed-receipt-manifest',
            'scope': 'two fresh synthetic development instances; descriptive only; recovery secondary',
            'samples': 6, 'seed_ids': list(SEEDS), 'repeats_per_arm': 1}
SOURCES = [*base.SOURCE_NAMES, 'experiments/runners/run_agents_confirmation_v5.py',
           'context_pruner/adapters/openai_agents_receipts_v6.py',
           'experiments/runners/run_agents_receipt_manifest_v6.py',
           'docs/superpowers/specs/2026-10-15-agents-v6-receipt-manifest-design.md',
           'docs/AGENTS_V6_DEVELOPMENT_PROTOCOL_20261015.md']


class ReceiptStudyAdapter(v5.HostPhaseAdapter, OpenAIAgentsReceiptLifecycle):
    def __call__(self, data):
        filtered = super().__call__(data)
        boundary = self.boundaries[-1]
        boundary.update(lifecycle_visible=deepcopy(self.lifecycle_visible),
                        receipt_projection=deepcopy(self.receipt_projection), instructions=filtered.instructions)
        base.write(self.out / 'model_boundaries.json', self.boundaries)
        return filtered


@contextmanager
def configured():
    saved = {key: getattr(base, key) for key in ('RegistryWorkspace', 'StudyAdapter', 'PROTOCOL', 'SOURCE_NAMES', 'verify', 'matrix')}
    base.RegistryWorkspace = v5.HostPhaseWorkspace
    base.StudyAdapter = ReceiptStudyAdapter
    base.PROTOCOL = PROTOCOL
    base.SOURCE_NAMES = SOURCES
    base.verify = verify
    base.matrix = lambda tasks, repeats: _MATRIX(tasks, 1)
    try:
        yield
    finally:
        for key, value in saved.items(): setattr(base, key, value)


def task_set():
    return [make_task(seed, f'receiptdev{seed}') for seed in SEEDS]


def prepare(out):
    out = Path(out)
    controls = out.parent / (out.name + '-controls')
    controls.mkdir(parents=True, exist_ok=False)
    tasks = task_set()
    admitted = [admission(task, controls / task['name']) for task in tasks]
    if not all(control['passed'] for control in admitted): raise ValueError('offline admission failed')
    with configured(): frozen = base.prepare(out, 'development', tasks, admitted)
    frozen.update(cap_usd=1., candidate_version='v6-observed-receipt-manifest', recovery_role='secondary')
    base.write(out / 'freeze.json', frozen)
    (out / 'freeze.sha256').write_text(base.file_sha(out / 'freeze.json'), encoding='utf-8')
    return verify(out)


def verify(out):
    out = Path(out)
    saved = base.PROTOCOL
    base.PROTOCOL = PROTOCOL
    try: frozen = _VERIFY(out)
    finally: base.PROTOCOL = saved
    tasks = json.loads((out / 'tasks.json').read_text(encoding='utf-8'))
    expected = dict(phase='development', samples=6, max_requests=96, cap_usd=1., max_body_bytes=24000,
                    model=base.MODEL, output=2048, temperature=0, thinking='disabled', prices=base.PRICES,
                    plan=_MATRIX(task_set(), 1), candidate_version='v6-observed-receipt-manifest', recovery_role='secondary')
    if tasks != task_set() or any(frozen.get(key) != value for key, value in expected.items()):
        raise ValueError('V6 fixed design/budget drift')
    if set(frozen['sources_sha256']) != set(SOURCES) or set(frozen['files_sha256']) != {'tasks.json', 'tools.json'}:
        raise ValueError('incomplete frozen manifest')
    if frozen['admission'] != [dict(task=t['name'], passed=True, reference_passed=True, empty_passed=False, historical_passed=False) for t in tasks]:
        raise ValueError('admission drift')
    if base.sha(json.loads((out / 'tools.json').read_text())) != frozen['tool_schema_sha256']:
        raise ValueError('tool hash drift')
    return frozen


def reserve_operation(freeze, out):
    frozen = verify(freeze)
    OPERATION_CLAIM.parent.mkdir(parents=True, exist_ok=True)
    with OPERATION_CLAIM.open('x', encoding='utf-8') as stream:
        json.dump(dict(freeze=str(Path(freeze).resolve()), out=str(Path(out).resolve()),
                       freeze_sha256=base.file_sha(Path(freeze) / 'freeze.json'),
                       max_requests=frozen['max_requests'], cap_usd=frozen['cap_usd']), stream)


def audit_output(freeze, out, *, mock):
    frozen, out = verify(freeze), Path(out)
    summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
    if summary['mock'] is not mock or summary['freeze_sha256'] != base.file_sha(Path(freeze) / 'freeze.json'):
        raise ValueError('output identity mismatch')
    claim = Path(freeze) / ('mock.claim' if mock else 'paid.claim')
    if Path(claim.read_text()).resolve() != out.resolve(): raise ValueError('output claim mismatch')
    if not mock:
        operation = json.loads(OPERATION_CLAIM.read_text())
        expected_operation = dict(freeze=str(Path(freeze).resolve()), out=str(out.resolve()),
                                  freeze_sha256=base.file_sha(Path(freeze)/'freeze.json'), max_requests=96, cap_usd=1.)
        if operation != expected_operation: raise ValueError('live operation claim mismatch')
    events = [json.loads(line) for line in (out / 'http/requests.jsonl').read_text().splitlines()]
    dispatch, completed = [e for e in events if e['event'] == 'dispatch'], [e for e in events if e['event'] == 'complete']
    if len(dispatch) != len(completed) or [{k:v for k,v in e.items() if k != 'event'} for e in completed] != summary['ledger']:
        raise ValueError('journal reconciliation failed')
    if len({e['index'] for e in completed}) != len(completed): raise ValueError('duplicate accounting')
    for index, (sent, got) in enumerate(zip(dispatch, completed), 1):
        if sent['index'] != index or got['index'] != index or any(sent[key] != got[key] for key in sent if key != 'event' and key not in {'status','usage'}):
            raise ValueError('dispatch mismatch')
        body = json.loads((out/'http'/sent['request_file']).read_text())
        wire = json.dumps(body,ensure_ascii=False,separators=(',', ':'),allow_nan=False).encode('utf-8')
        reserve = (len(wire)+4096)*base.PRICES['peak']['miss']/1e6 + 2048*base.PRICES['peak']['output']/1e6
        if len(wire) != sent['body_bytes'] or hashlib.sha256(wire).hexdigest() != sent['wire_body_sha256'] or not math.isclose(reserve,sent['reserved_peak_allmiss_usd'],abs_tol=1e-12):
            raise ValueError('wire/reservation reconstruction mismatch')
    if len(completed) > 96 or summary['reserved_peak_allmiss_usd'] > 1. or not math.isclose(
            sum(r['reserved_peak_allmiss_usd'] for r in completed), summary['reserved_peak_allmiss_usd'], abs_tol=1e-12):
        raise ValueError('budget reconciliation failed')
    results, manifest_boundaries, unsent, unknown_usage = [], 0, [], []
    if len(summary['samples']) > 6: raise ValueError('unexpected samples')
    for position, report in enumerate(summary['samples']):
        row = frozen['plan'][position]
        if any(report[key] != value for key, value in row.items()): raise ValueError('sample matrix mismatch')
        trial = out / row['sample_id']
        if json.loads((trial / 'report.json').read_text()) != report: raise ValueError('sample report mismatch')
        tools = json.loads((trial / 'tools.json').read_text())
        boundaries = json.loads((trial / 'model_boundaries.json').read_text()) if (trial/'model_boundaries.json').exists() else []
        records = [r for r in completed if r['sample_id'].split('/turn')[0] == row['sample_id']]
        if len(records) > len(boundaries) or len(records) != report['http_requests'] or len(records) > 16:
            raise ValueError('sample count mismatch')
        by_scope = {r['sample_id']:r for r in records}
        if len(by_scope) != len(records) or not set(by_scope).issubset({b['request_scope'] for b in boundaries}):
            raise ValueError('unbound dispatch')
        task = next(t for t in task_set() if t['name'] == row['task'])
        versions = {e: bytes_sha(dump(doc).encode()) for e, doc in task['documents'].items()}
        for key, suffix in (('documents',''),('old_documents','.old')):
            for entity,doc in task[key].items():
                if base.file_sha(trial/f'workspace/docs/{entity}{suffix}.json') != bytes_sha(dump(doc).encode()):
                    raise ValueError('workspace source drift')
        for boundary in boundaries:
            record = by_scope.get(boundary['request_scope'])
            if record is None:
                unsent.append(boundary['request_scope'])
                continue
            if record['sdk_input_sha256'] != base.sha(boundary['visible']) or not boundary['paired']:
                raise ValueError('boundary identity mismatch')
            body = json.loads((out / 'http' / record['request_file']).read_text())
            messages = Converter.items_to_messages(boundary['visible'], model=base.MODEL, base_url='https://api.deepseek.com', strict_feature_validation=True)
            messages.insert(0, {'role': 'system', 'content': boundary['instructions']})
            if (body['messages'] != _to_dump_compatible(messages) or base.sha(body) != record['body_sha256']
                    or base.sha(body['tools']) != frozen['tool_schema_sha256'] or record['body_bytes'] > 24000):
                raise ValueError('final converted input mismatch')
            u = record['usage']
            if record['transport_kind'] != ('mock' if mock else 'live'): raise ValueError('transport identity mismatch')
            if record['status'] == 'returned' and not record.get('usage_errors') and all(v is not None for v in u.values()):
                response = json.loads((out / 'http' / record['response_file']).read_text())
                if any(response['usage'].get(key) != value for key, value in u.items()) or u['prompt_tokens'] != u['prompt_cache_hit_tokens'] + u['prompt_cache_miss_tokens'] or u['total_tokens'] != u['prompt_tokens'] + u['completion_tokens']:
                    raise ValueError('raw usage mismatch')
                for period, prices in base.PRICES.items():
                    cost = (u['prompt_cache_hit_tokens']*prices['hit'] + u['prompt_cache_miss_tokens']*prices['miss'] + u['completion_tokens']*prices['output']) / 1e6
                    if not math.isclose(cost, record[period+'_usage_priced_usd'], abs_tol=1e-12): raise ValueError('price mismatch')
            else:
                unknown_usage.append(dict(request=record['sample_id'],status=record['status'],response_file=record.get('response_file')))
            prior = tools[:boundary['tool_count_before_model']]
            closed = any(t['payload'].get('kind') == 'stage_complete' for t in prior)
            projection = boundary['receipt_projection']
            if closed:
                observed = {t['payload']['entity']: t['payload'] for t in prior if t['name'] == 'read_document' and t.get('revision') == 'current'}
                expected_sources = [dict(entity=e, source_path=f'docs/{e}.json', source_version=versions[e]) for e in sorted(observed)]
                expected_manifest = dict(kind='host_source_receipts', schema='agents_receipts_v6', sources=expected_sources)
                if not projection or projection['manifest'] != expected_manifest or projection['bytes'] > 2048:
                    raise ValueError('manifest visibility/provenance mismatch')
                occurrences = [i for i in boundary['visible'] if i.get('role') == 'user' and i.get('content') == base.json.dumps(expected_manifest,ensure_ascii=False,sort_keys=True,separators=(',', ':'))]
                if len(occurrences) != 1: raise ValueError('manifest must occur exactly once')
                manifest_boundaries += 1
            elif projection is not None: raise ValueError('early manifest')
        for key, field in (('prompt_tokens', 'prompt_tokens'), ('completion_tokens', 'completion_tokens')):
            values = [r['usage'][field] for r in records]
            value = sum(values) if all(v is not None for v in values) else None
            if value != report[key]: raise ValueError('sample token mismatch')
        for period in ('offpeak', 'peak'):
            values = [r.get(period+'_usage_priced_usd') for r in records]
            cost = sum(values) if all(v is not None for v in values) else None
            reported = report['cost_'+period+'_usd']
            if (cost is None) != (reported is None) or (cost is not None and not math.isclose(cost,reported,abs_tol=1e-12)):
                raise ValueError('sample cost mismatch')
        actual_path = trial / 'workspace/artifact.json'
        actual = json.loads(actual_path.read_text()) if actual_path.exists() else None
        expected_rows = []
        for query in task['queries']:
            rec = next(r for r in task['documents'][query['entity']]['records'] if r['code'] == query['code'])
            expected_rows.append(dict(id=query['id'], value=query['reading']*rec['multiplier']+rec['bias'], label=rec['label'],
                                     source_version=versions[query['entity']], evidence_code=query['code']))
        if (actual == {'rows':expected_rows}) != report['independent']['passed']: raise ValueError('independent grade mismatch')
        results.append(report)
    if sum(r['http_requests'] for r in results) != len(completed): raise ValueError('unattributed requests')
    complete = len(results) == 6 and summary['samples_completed'] == 6 and summary['stopped'] is None and not unknown_usage and not unsent
    if summary['provider_requests'] != (0 if mock else len(completed)): raise ValueError('provider count mismatch')
    return dict(complete=complete, requests=len(completed), manifest_boundaries=manifest_boundaries,
                provider_requests=summary['provider_requests'], passed=sum(r['independent']['passed'] for r in results),
                reserved_usd=summary['reserved_peak_allmiss_usd'], scope=PROTOCOL['scope'],
                planned_samples=6, observed_samples=len(results), completed_samples=summary['samples_completed'],
                unattempted_samples=6-len(results), infrastructure_failed_samples=sum(r['status']=='infrastructure_error' for r in results),
                unsent_boundaries=unsent, unknown_usage_requests=len(unknown_usage), unknown_usage=unknown_usage)


def run_batch(freeze, out, key, *, mock=False):
    verify(freeze)
    if not mock:
        mock_out = Path((Path(freeze) / 'mock.claim').read_text())
        gate = audit_output(freeze, mock_out, mock=True)
        if not gate['complete'] or gate['passed'] != 6: raise ValueError('actual SDK mock gate failed')
        reserve_operation(freeze, out)
    with configured(): summary = base.run_batch(freeze, out, key, mock=mock)
    try:
        audit = audit_output(freeze, out, mock=mock)
    except Exception as exc:
        base.write(Path(out)/'audit.json',dict(complete=False,audit_error=type(exc).__name__,reason=str(exc),
                    planned_samples=6,observed_samples=len(summary['samples']),unattempted_samples=6-len(summary['samples']),
                    reserved_usd=summary['reserved_peak_allmiss_usd'],provider_requests=summary['provider_requests']))
        raise
    base.write(Path(out) / 'audit.json', audit)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'mock', 'run', 'audit'))
    parser.add_argument('--freeze', required=True, type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--mock-audit', action='store_true')
    args = parser.parse_args()
    if args.mode == 'prepare': result = prepare(args.freeze)
    elif args.mode == 'audit': result = audit_output(args.freeze, args.out, mock=args.mock_audit)
    else:
        key = 'offline' if args.mode == 'mock' else os.environ.get('DEEPSEEK_API_KEY')
        if not key: raise RuntimeError('DEEPSEEK_API_KEY unavailable; never print credentials')
        s = run_batch(args.freeze, args.out, key, mock=args.mode == 'mock')
        result = {k:s[k] for k in ('samples_completed','provider_requests','reserved_peak_allmiss_usd','stopped')}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
