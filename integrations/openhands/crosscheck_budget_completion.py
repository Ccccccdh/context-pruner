"""Crosscheck recorded outcome against saved SDK terminal status; no API calls."""
import argparse
import json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--out',required=True)
a=p.parse_args();out=Path(a.out).resolve()
rows=[]
for rp in sorted(out.glob('r*/report.json')):
    report=json.loads(rp.read_text(encoding='utf-8'))
    states=list(rp.parent.glob('conversation/**/base_state.json'))
    assert len(states)==1,(rp,len(states))
    state=json.loads(states[0].read_text(encoding='utf-8'))
    actual=state['execution_status']=='finished' and report.get('artifact_success',False) and report.get('workflow_verification_ok',False)
    rows.append({'sample':rp.parent.name,'saved_sdk_status':state['execution_status'],'reported_sdk_status':report.get('sdk_status'),'artifact_success':report.get('artifact_success'),'reported_success':report['success'],'expected_success':actual})
    assert actual==report['success'],rows[-1]
result={'samples':len(rows),'mismatches':0,'rows':rows}
(out/'completion-state-crosscheck.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'samples':len(rows),'mismatches':0}))
