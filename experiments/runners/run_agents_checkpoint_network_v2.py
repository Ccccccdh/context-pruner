"""New infrastructure-successor batch, subtracting a failed sandbox attempt.

No historical runner or consumed freeze is edited. The original operation's
request and conservative money ceilings cover both epochs together.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil

from . import run_agents_checkpoint_natural_v2 as runner


def remaining_budget(freeze,previous):
    if previous.get('samples_completed')!=0 or previous.get('stopped',{}).get('reason')!='APIConnectionError':
        raise ValueError('only the no-completed-sample infrastructure failure may create successor')
    records=previous['ledger']
    if not records or any(r['status']!='transport_error' for r in records):
        raise ValueError('provider responses cannot be retried as infrastructure failures')
    count=freeze['max_requests']-len(records)
    money=freeze['cap_usd']-sum(r['reserved_peak_allmiss_usd'] for r in records)
    if count<=0 or money<=0:raise ValueError('original operation budget exhausted')
    return {'max_requests':count,'cap_usd':money,'prior_requests':len(records),
            'prior_reserved_usd':freeze['cap_usd']-money}


def prepare_successor(frozen,previous,out):
    frozen,previous,out=Path(frozen).resolve(),Path(previous).resolve(),Path(out).resolve()
    config=runner.verify_freeze(frozen)
    summary=json.loads(previous.read_text(encoding='utf-8'))
    budget=remaining_budget(config,summary)
    out.mkdir(parents=True,exist_ok=False)
    for name in config['files_sha256']:
        target=out/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(frozen/name,target)
    config['experiment_id']+='-network02'
    config['operation_budget_after_predecessor']=budget
    config['predecessor_summary']=previous.relative_to(runner.ROOT).as_posix()
    config['predecessor_summary_sha256']=runner.file_sha(previous)
    config['sources_sha256']['experiments/runners/run_agents_checkpoint_network_v2.py']=runner.file_sha(Path(__file__))
    (out/'freeze.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'freeze.sha256').write_text(runner.file_sha(out/'freeze.json'),encoding='ascii')
    return config


async def run_successor(frozen,out,key):
    config=runner.verify_freeze(frozen)
    previous=runner.ROOT/config['predecessor_summary']
    if runner.file_sha(previous)!=config['predecessor_summary_sha256']:raise ValueError('predecessor evidence drift')
    budget=remaining_budget(config,json.loads(previous.read_text(encoding='utf-8')))
    if budget!=config['operation_budget_after_predecessor']:raise ValueError('remaining-budget binding mismatch')
    original=runner.RequestLedger

    class RemainingLedger(original):
        def __init__(self,directory,**kwargs):
            kwargs['max_requests']=min(kwargs['max_requests'],budget['max_requests'])
            kwargs['cap_usd']=min(kwargs['cap_usd'],budget['cap_usd'])
            super().__init__(directory,**kwargs)

    runner.RequestLedger=RemainingLedger
    try:summary=await runner.run_batch(frozen,out,key)
    finally:runner.RequestLedger=original
    summary['operation_total_attempts']=budget['prior_requests']+summary['http_requests']
    summary['operation_total_reserved_usd']=budget['prior_reserved_usd']+summary['reserved_peak_allmiss_usd']
    summary['predecessor_summary_sha256']=config['predecessor_summary_sha256']
    assert summary['operation_total_attempts']<=config['max_requests']
    assert summary['operation_total_reserved_usd']<=config['cap_usd']+1e-12
    (Path(out)/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('prepare','run'))
    p.add_argument('--freeze',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--previous',type=Path);args=p.parse_args()
    if args.mode=='prepare':
        if args.previous is None:raise ValueError('--previous required')
        config=prepare_successor(args.freeze,args.previous,args.out)
        print(config['operation_budget_after_predecessor'])
    else:
        key=os.environ.pop('DEEPSEEK_API_KEY','');os.environ.pop('OPENAI_API_KEY',None)
        if not key:raise ValueError('provider credential unavailable')
        result=asyncio.run(run_successor(args.freeze,args.out,key))
        print({k:result[k] for k in ('samples_completed','operation_total_attempts','operation_total_reserved_usd','stopped')})
