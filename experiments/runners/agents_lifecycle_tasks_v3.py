"""Synthetic versioned workspace tasks; hidden evaluator never exposed as a tool."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path

PATH='docs/policy.json'


def text(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2)
def sha_bytes(x):return hashlib.sha256(x).hexdigest()


def candidate(name,rule,policies,requests,old_policies):
    return {'name':name,'contract':{'revision':'current-v2','rule':rule,'policies':policies,
        'background':['Operational context paragraph %03d: retain source provenance and use current revision for later work orders; archived audits are historical only.'%i for i in range(32)]},
        'old_contract':{'revision':'obsolete-v1','rule':rule,'policies':old_policies},'requests':requests}


CANDIDATES=[
    candidate('dispatch_routes','Compute fee = base_fee + units * unit_fee; route by region.',
        {'north':{'route':'NORTH7','base_fee':17,'unit_fee':3},'south':{'route':'SOUTH4','base_fee':11,'unit_fee':5}},
        [{'id':'S1','region':'north','units':8},{'id':'S2','region':'south','units':3},{'id':'S3','region':'north','units':0}],
        {'north':{'route':'NORTH2','base_fee':13,'unit_fee':2},'south':{'route':'SOUTH1','base_fee':9,'unit_fee':4}}),
    candidate('sensor_windows','Compute corrected = raw * scale + offset; use the configured window_seconds.',
        {'temperature':{'scale':3,'offset':-7,'window_seconds':45},'pressure':{'scale':2,'offset':13,'window_seconds':90}},
        [{'id':'R1','sensor':'temperature','raw':9},{'id':'R2','sensor':'pressure','raw':11},{'id':'R3','sensor':'temperature','raw':-2}],
        {'temperature':{'scale':2,'offset':-3,'window_seconds':30},'pressure':{'scale':4,'offset':5,'window_seconds':60}}),
    candidate('retention_policy','Compute delete_day = created_day + retention_days; include replicas for each class.',
        {'audit':{'retention_days':31,'replicas':3},'cache':{'retention_days':7,'replicas':1}},
        [{'id':'F1','class':'audit','created_day':12},{'id':'F2','class':'cache','created_day':25}],
        {'audit':{'retention_days':14,'replicas':2},'cache':{'retention_days':3,'replicas':1}}),
]


def expected_rows(task,*,historical=False):
    # Only admission/evaluator call this; tool API does not expose it.
    policies=task['old_contract' if historical else 'contract']['policies'];rows=[]
    for req in task['requests']:
        if task['name']=='dispatch_routes':
            p=policies[req['region']];row={'id':req['id'],'route':p['route'],'fee':p['base_fee']+req['units']*p['unit_fee']}
        elif task['name']=='sensor_windows':
            p=policies[req['sensor']];row={'id':req['id'],'corrected':req['raw']*p['scale']+p['offset'],'window_seconds':p['window_seconds']}
        elif task['name']=='retention_policy':
            p=policies[req['class']];row={'id':req['id'],'delete_day':req['created_day']+p['retention_days'],'replicas':p['replicas']}
        else:raise ValueError('unregistered task')
        rows.append(row)
    return rows


class TaskWorkspace:
    def __init__(self,task,out):
        self.task=deepcopy(task);self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True)
        self.current=text(task['contract']);self.historical=text(task['old_contract'])
        self.version=sha_bytes(self.current.encode());self.old_version=sha_bytes(self.historical.encode())
        self.read_current=0;self.rereads=0;self.closed=False;self.tools=[]
        (self.out/'docs').mkdir(exist_ok=True)
        (self.out/PATH).write_text(self.current,encoding='utf-8')
        (self.out/'docs/policy.old.json').write_text(self.historical,encoding='utf-8')
        self.material_hashes={p:sha_bytes((self.out/p).read_bytes()) for p in (PATH,'docs/policy.old.json')}

    def verify_sources(self):
        if any(sha_bytes((self.out/p).read_bytes())!=v for p,v in self.material_hashes.items()):
            raise ValueError('immutable task source drift')

    def record(self,name,payload,**meta):
        self.tools.append({'name':name,'payload':deepcopy(payload),**meta});return payload

    def read_policy(self,revision):
        self.verify_sources()
        if revision not in {'current','historical'}:return self.record('read_policy',{'kind':'error','error':'revision must be current or historical'})
        if revision=='current':
            if self.closed:self.rereads+=1
            self.read_current+=1
        return self.record('read_policy',{'kind':'evidence','source_path':PATH,
            'source_version':self.version if revision=='current' else self.old_version,'entity':'policy','stage':'research',
            'text':self.current if revision=='current' else self.historical},revision=revision,reread=self.closed)

    def close_research(self,summary):
        self.verify_sources()
        if not self.read_current or not isinstance(summary,str) or not summary.strip() or self.closed:
            return self.record('close_research',{'kind':'error','error':'read current policy and supply a nonempty summary once'})
        self.closed=True
        (self.out/'research.txt').write_text(summary,encoding='utf-8')
        return self.record('close_research',{'kind':'stage_complete','stage':'research','summary':summary})

    def work_order(self):
        if not self.closed:return self.record('work_order',{'kind':'error','error':'complete research first'})
        schemas={'dispatch_routes':['id','route','fee'],'sensor_windows':['id','corrected','window_seconds'],'retention_policy':['id','delete_day','replicas']}
        return self.record('work_order',{'kind':'work_order','requests':self.task['requests'],
            'row_fields':schemas[self.task['name']],'artifact_schema':{'rows':'list in request order','evidence':{'source_path':PATH,'source_version':'SHA from current read receipt','quote':'exact rule string from current policy'}},
            'note':'Produce artifact.json using current rules. Historical policy must not govern current work.'})

    def write_artifact(self,content):
        if not self.closed:return self.record('write_artifact',{'kind':'error','error':'complete research first'})
        if not isinstance(content,str) or len(content.encode())>12000:return self.record('write_artifact',{'kind':'error','error':'artifact too large or invalid'})
        try:obj=json.loads(content)
        except ValueError:return self.record('write_artifact',{'kind':'error','error':'valid JSON required'})
        if not isinstance(obj,dict):return self.record('write_artifact',{'kind':'error','error':'JSON object required'})
        (self.out/'artifact.json').write_text(text(obj),encoding='utf-8')
        return self.record('write_artifact',{'kind':'written','path':'artifact.json'})

    def provenance_ok(self,obj):
        e=obj.get('evidence',{})
        return isinstance(e,dict) and e.get('source_path')==PATH and e.get('source_version')==self.version and e.get('quote')==self.task['contract']['rule']

    def check_artifact(self):
        self.verify_sources();path=self.out/'artifact.json'
        if not path.exists():return self.record('check_artifact',{'kind':'error','error':'artifact missing'})
        obj=json.loads(path.read_text(encoding='utf-8'))
        if not self.provenance_ok(obj):return self.record('check_artifact',{'kind':'missing_evidence','entity':'policy','origin':'host','error':'current source provenance missing or wrong; use current source version and exact rule quote'})
        return self.record('check_artifact',{'kind':'public_check','schema_valid':isinstance(obj.get('rows'),list),'note':'Independent numeric/route validation runs after the model stops.'})

    def report_missing(self,entity):
        if entity not in {'policy','unrelated'}:return self.record('report_missing',{'kind':'error','error':'known entities: policy, unrelated'})
        return self.record('report_missing',{'kind':'missing_evidence','entity':entity,'origin':'model'})

    def reference(self,*,historical=False):
        return {'rows':expected_rows(self.task,historical=historical),'evidence':{'source_path':PATH,'source_version':self.old_version if historical else self.version,'quote':self.task['contract']['rule']}}

    def evaluate(self):
        self.verify_sources();path=self.out/'artifact.json'
        if not path.exists():return {'passed':False,'reason':'artifact_missing'}
        try:obj=json.loads(path.read_text(encoding='utf-8'))
        except ValueError:return {'passed':False,'reason':'invalid_json'}
        rows_ok=obj.get('rows')==expected_rows(self.task);provenance=self.provenance_ok(obj)
        return {'passed':rows_ok and provenance,'rows_correct':rows_ok,'current_provenance':provenance,
            'reason':'passed' if rows_ok and provenance else 'rows_or_provenance_wrong'}


def admit(task,out=None):
    if out is None:
        with tempfile.TemporaryDirectory() as tmp:return admit(task,Path(tmp))
    w=TaskWorkspace(task,out);w.read_policy('current');w.close_research('offline admission')
    empty=w.evaluate()
    w.write_artifact(json.dumps(w.reference()));reference=w.evaluate()
    w.write_artifact(json.dumps(w.reference(historical=True)));old=w.evaluate()
    result={'task':task['name'],'reference_passed':reference['passed'],'empty_passed':empty['passed'],
        'old_version_passed':old['passed'],'passed':reference['passed'] and not empty['passed'] and not old['passed'],
        'reference':reference,'empty':empty,'historical':old}
    (Path(out)/'admission.json').write_text(text(result),encoding='utf-8');return result
