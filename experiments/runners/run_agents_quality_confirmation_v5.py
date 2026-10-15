"""User-selected quality/cost confirmation; original recovery gate stays FAIL."""
from contextlib import contextmanager
import argparse
import json
import os
import tempfile
from pathlib import Path

from experiments.runners import run_agents_confirmation_v4 as base
from experiments.runners import run_agents_confirmation_v5 as v5
from context_pruner.adapters.openai_agents_lifecycle_v1 import atomic_spans,digest

_VERIFY=v5._VERIFY
POLICY_FILE='experiments/runners/run_agents_quality_confirmation_v5.py'
PROTOCOL_FILE='docs/AGENTS_FORMAL_QUALITY_COST_V5_PROTOCOL_20261014.md'


def quality_cost_gate(s):
    original=base.development_gate(s)
    rows=s.get('samples',[])
    passed=not s.get('mock',True) and s.get('phase')=='development' and original['complete_matrix'] and original['baseline_stable'] and original['integrity']
    passed=passed and original['quality_counts']['A2']>=original['quality_counts']['A0']
    passed=passed and all(x.get('all_tool_groups_paired') is True for x in rows)
    return {'passed':bool(passed),'scope':'whole-task quality and cost confirmation','recovery_role':'secondary',
        'user_selected_scope':'formal quality/cost; recovery secondary','original_recovery_gate':original,
        'samples':len(rows),'candidate_behavior':'v5-host-phase-only'}


def development_binding(dev_freeze,summary):
    df=v5.verify(dev_freeze);p=Path(summary).resolve();s=json.loads(p.read_text(encoding='utf-8'));g=quality_cost_gate(s)
    if not g['passed']:raise ValueError('quality/cost development admission failed')
    if s.get('candidate_version')!='v5-host-phase-only' or s['freeze_sha256']!=base.file_sha(Path(dev_freeze)/'freeze.json'):raise ValueError('candidate/development identity mismatch')
    expected=[{k:r[k] for k in ('sample_id','task','arm','repeat')} for r in df['plan']]
    actual=[{k:r[k] for k in ('sample_id','task','arm','repeat')} for r in s['samples']]
    if expected!=actual:raise ValueError('exact development matrix mismatch')
    if Path((Path(dev_freeze)/'paid.claim').read_text()).resolve()!=p.parent:raise ValueError('development paid claim mismatch')
    allocated=json.loads(Path(df['operation_claim']).read_text(encoding='utf-8'))
    if allocated['freeze']!=str(Path(dev_freeze).resolve()) or allocated['out']!=str(p.parent) or allocated['freeze_sha256']!=base.file_sha(Path(dev_freeze)/'freeze.json'):raise ValueError('development operation claim mismatch')
    journal=p.parent/'http/requests.jsonl';events=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    dispatch=[e for e in events if e['event']=='dispatch'];done=[{k:v for k,v in e.items() if k!='event'} for e in events if e['event']=='complete']
    if not dispatch or done!=s['ledger'] or len(done)!=len(dispatch) or [r['index'] for r in dispatch]!=list(range(1,len(dispatch)+1)):raise ValueError('development durable ledger mismatch')
    for r in done:
        if r.get('transport_kind')!='live' or r['status']!='returned' or r.get('usage_errors') or any(r['usage'][k] is None for k in ('prompt_tokens','completion_tokens')):raise ValueError('development HTTP accounting invalid')
        body=json.loads((p.parent/'http'/r['request_file']).read_text(encoding='utf-8'))
        response=json.loads((p.parent/'http'/r['response_file']).read_text(encoding='utf-8'))
        if base.sha(body)!=r['body_sha256'] or response.get('usage')!=r['raw_usage']:raise ValueError('development raw HTTP drift')
    reserved=sum(r['reserved_peak_allmiss_usd'] for r in dispatch);b=df['predecessor']
    if len(dispatch)+b['requests']>288 or reserved+b['reserved_usd']>3.25 or abs(reserved-s['reserved_peak_allmiss_usd'])>1e-12:raise ValueError('shared development budget invalid')
    tasks=json.loads((Path(dev_freeze)/'tasks.json').read_text(encoding='utf-8'))
    for row in s['samples']:
        trial=p.parent/row['sample_id'];report=json.loads((trial/'report.json').read_text(encoding='utf-8'))
        if row!=report:raise ValueError('development sample report drift')
        records=[r for r in done if r['sample_id'].startswith(row['sample_id']+'/turn')]
        if not 1<=len(records)<=16 or row['http_requests']!=len(records):raise ValueError('sample HTTP count mismatch')
        for key in ('prompt_tokens','completion_tokens'):
            if row[key]!=sum(r['usage'][key] for r in records):raise ValueError('sample usage count mismatch')
        for period in ('offpeak','peak'):
            values=[r.get(period+'_usage_priced_usd') for r in records];expected=sum(values) if all(v is not None for v in values) else None
            got=row.get('cost_'+period+'_usd')
            if (expected is None)!=(got is None) or (expected is not None and abs(got-expected)>1e-12):raise ValueError('sample price accounting mismatch')
        boundaries=json.loads((trial/'model_boundaries.json').read_text(encoding='utf-8'));indexed={x['request_scope']:x for x in boundaries}
        if len(indexed)!=len(boundaries) or set(indexed)!={r['sample_id'] for r in records}:raise ValueError('sample model/HTTP boundary mismatch')
        for r in records:
            boundary=indexed[r['sample_id']]
            if boundary.get('paired') is not True or any(not span['complete'] for span in atomic_spans(boundary['visible']) if span['calls']):raise ValueError('sample tool pairing violated')
            if digest(boundary['visible'])!=r['sdk_input_sha256'] or boundary['input_sha256']!=r['sdk_input_sha256']:raise ValueError('sample SDK input binding mismatch')
        with tempfile.TemporaryDirectory() as tmp:
            host=base.RegistryWorkspace(next(t for t in tasks if t['name']==row['task']),Path(tmp));host.closed=True;host.ordered=True
            artifact=trial/'workspace/artifact.json'
            if artifact.exists():host.write_artifact(artifact.read_text(encoding='utf-8'))
            if host.evaluate()!=row['independent']:raise ValueError('independent development artifact validation drift')
    return {'freeze_path':str(Path(dev_freeze).resolve()),'freeze_sha256':base.file_sha(Path(dev_freeze)/'freeze.json'),
        'summary':str(p),'summary_sha256':base.file_sha(p),'journal_sha256':base.file_sha(journal),
        'quality_cost_gate':g,'original_recovery_gate':g['original_recovery_gate']}


def analyze(s):
    result=base.analyze(s);rows=s['samples']
    complete=s.get('samples_completed')==45 and len(rows)==45 and not s.get('stopped')
    result.update(study_incomplete=not complete,planned_samples=45,observed_samples=len(rows),
        completed_samples=s.get('samples_completed',0),unattempted_samples=45-len(rows))
    for arm in ('A0','A1','A2'):
        observed=[x for x in rows if x['arm']==arm];a=result['arms'][arm]
        a.update(planned_n=15,observed_n=len(observed),completed_n=sum(x['status'] in {'completed','max_turns'} for x in observed),
            ordinary_failed_n=sum(x['status'] in {'completed','max_turns'} and x['independent'].get('passed') is False for x in observed),
            infrastructure_failed_n=sum(x['status']=='infrastructure_error' for x in observed),
            unattempted_n=15-len(observed),unknown_quality_n=sum(x['independent'].get('passed') is None for x in observed),
            quality_rate=a['passed']/15 if complete else None)
    return result


@contextmanager
def configured():
    with v5.configured():
        base.PROTOCOL={**base.PROTOCOL,'study_version':'v5-quality-cost-confirmation',
            'formal_admission':'stable whole-task baseline/quality plus integrity; positive recovery event not required by user-selected scope',
            'recovery_role':'secondary','primary_outcomes':'whole artifact quality and full task cost'}
        base.SOURCE_NAMES=[*base.SOURCE_NAMES,POLICY_FILE,PROTOCOL_FILE]
        def checked(path):
            f=_VERIFY(path)
            if f['phase']!='formal':raise ValueError('quality/cost entry is formal only')
            b=f['development_binding'];actual=development_binding(b['freeze_path'],b['summary'])
            if actual!=b:raise ValueError('stable development evidence drift')
            if f['operation_claim']!=str((Path(b['freeze_path'])/'formal-confirmation.claim').resolve()):raise ValueError('formal operation claim path drift')
            return f
        base.verify=checked
        yield


def verify(out):
    with configured():return base.verify(out)


def prepare_formal(out,dev_freeze,summary):
    binding=development_binding(dev_freeze,summary);df=v5.verify(dev_freeze)
    # First generation of held-out values occurs only after valid stable QC gate.
    with configured():
        tasks=[base.make_task(n,'confirm'+str(n)) for n in base.PROTOCOL['formal_seeds']]
        controls_dir=Path(out).parent/(Path(out).name+'-controls');controls_dir.mkdir(exist_ok=False)
        controls=[base.admission(t,controls_dir/t['name']) for t in tasks]
        if not all(c['passed'] for c in controls):raise ValueError('held-out controls failed; no replacement')
        f=base.prepare(out,'formal',tasks,controls,binding)
    if f['initial_body_sha256']!=df['initial_body_sha256']:raise ValueError('candidate sent-input behavior changed')
    f['operation_claim']=str((Path(dev_freeze)/'formal-confirmation.claim').resolve())
    f['scientific_scope']='whole-task quality/cost; recovery secondary; original recovery gate unaltered'
    return v5.seal(out,f)


def run_batch(freeze_dir,out,key,mock=False):
    f=verify(freeze_dir)
    if not mock:v5.reserve_successor(f,freeze_dir,out)
    with configured():s=base.run_batch(freeze_dir,out,key,mock=mock)
    s.update(study_version='v5-quality-cost-confirmation',candidate_behavior='v5-host-phase-only',recovery_role='secondary',
        original_recovery_gate_passed=f['development_binding']['original_recovery_gate']['passed'])
    base.write(Path(out)/'summary.json',s)
    with configured():base.write(Path(out)/'analysis.json',analyze(s))
    return s


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','mock','run'));p.add_argument('--out',required=True,type=Path)
    p.add_argument('--freeze',type=Path);p.add_argument('--development-summary',type=Path);a=p.parse_args()
    if a.mode=='prepare':f=prepare_formal(a.out,a.freeze,a.development_summary);print({k:f[k] for k in ('samples','max_requests','cap_usd','scientific_scope')});return
    key='offline' if a.mode=='mock' else os.environ.pop('DEEPSEEK_API_KEY','');os.environ.pop('OPENAI_API_KEY',None)
    if not key or a.freeze is None:raise ValueError('key/freeze required')
    s=run_batch(a.freeze,a.out,key,mock=a.mode=='mock');print({k:s[k] for k in ('samples_completed','provider_requests','stopped')})


if __name__=='__main__':main()
