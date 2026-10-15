"""Versioned synthetic registries with independent late-work-order validation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random


def dump(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,indent=2)
def bytes_sha(b):return hashlib.sha256(b).hexdigest()


def make_task(seed,name):
    r=random.Random(seed);docs={};old={};queries=[]
    for e in ('north','south'):
        records=[{'code':e[0].upper()+f'-{i:02d}','multiplier':r.randint(2,9),'bias':r.randint(-20,20),
            'label':r.randbytes(12).hex()} for i in range(24)]
        docs[e]={'revision':'current-v2','rule':'value = reading * multiplier + bias; copy the exact opaque label from the selected record.','records':records}
        old[e]=deepcopy(docs[e]);old[e]['revision']='obsolete-v1'
        for row in old[e]['records']:row.update(multiplier=row['multiplier']+1,bias=row['bias']-3,label='old-'+row['label'])
        for i in r.sample(range(24),3):queries.append({'id':f'Q{len(queries)+1}','entity':e,'code':records[i]['code'],'reading':r.randint(-9,19)})
    return {'name':name,'seed':seed,'documents':docs,'old_documents':old,'queries':queries}


class RegistryWorkspace:
    def __init__(self,task,out):
        self.task=deepcopy(task);self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True);(self.out/'docs').mkdir(exist_ok=True)
        self.entities=list(task['documents']);self.versions={};self.old_versions={};self.material_hashes={}
        self.tools=[];self.reads=set();self.closed=False;self.ordered=False;self.rereads=0
        for e in self.entities:
            for old,version_map,key in ((False,self.versions,'documents'),(True,self.old_versions,'old_documents')):
                p=f'docs/{e}{".old" if old else ""}.json';raw=dump(task[key][e]);(self.out/p).write_text(raw,encoding='utf-8',newline='\n')
                h=bytes_sha(raw.encode());version_map[e]=h;self.material_hashes[p]=h

    def verify_sources(self):
        if any(bytes_sha((self.out/p).read_bytes())!=h for p,h in self.material_hashes.items()):raise ValueError('immutable registry drift')

    def record(self,name,payload,**meta):
        self.tools.append({'name':name,'payload':deepcopy(payload),**meta});return payload

    def read_document(self,entity,revision):
        self.verify_sources()
        if entity not in self.entities or revision not in {'current','historical'}:return self.record('read_document',{'kind':'error','error':'known entities north/south; revisions current/historical'})
        if revision=='current':
            self.reads.add(entity)
            if self.closed:self.rereads+=1
        return self.record('read_document',{'kind':'evidence','source_path':f'docs/{entity}.json',
            'source_version':(self.versions if revision=='current' else self.old_versions)[entity],
            'entity':entity,'stage':'research','text':dump(self.task['documents' if revision=='current' else 'old_documents'][entity])},
            revision=revision,reread=self.closed)

    def close_research(self,summary):
        self.verify_sources()
        if self.closed or self.reads!=set(self.entities) or not isinstance(summary,str) or not summary.strip():
            return self.record('close_research',{'kind':'error','error':'investigate both current sources, preserve useful nonempty summary, close once'})
        self.closed=True;(self.out/'research.txt').write_text(summary,encoding='utf-8')
        return self.record('close_research',{'kind':'stage_complete','stage':'research','summary':summary})

    def work_order(self):
        if not self.closed:return self.record('work_order',{'kind':'error','error':'close research first'})
        self.ordered=True
        return self.record('work_order',{'kind':'work_order','queries':self.task['queries'],
            'artifact_schema':{'rows':'in query order, exactly one per query','row_fields':['id','value','label','source_version','evidence_code']},
            'note':'Use current record multiplier,bias and exact opaque label; source_version is current receipt SHA, evidence_code equals requested code. All numeric values are integers.'})

    def retrieve_evidence(self,entity):
        if entity not in {*self.entities,'unrelated'}:return self.record('retrieve_evidence',{'kind':'error','error':'known entities north,south,unrelated'})
        return self.record('retrieve_evidence',{'kind':'missing_evidence','entity':entity,'origin':'model'})

    def write_artifact(self,content):
        if not self.ordered:return self.record('write_artifact',{'kind':'error','error':'obtain work order first'})
        if not isinstance(content,str) or len(content.encode())>12000:return self.record('write_artifact',{'kind':'error','error':'invalid or oversized JSON string'})
        try:obj=json.loads(content)
        except ValueError:return self.record('write_artifact',{'kind':'error','error':'valid JSON required'})
        if not isinstance(obj,dict):return self.record('write_artifact',{'kind':'error','error':'JSON object required'})
        (self.out/'artifact.json').write_text(dump(obj),encoding='utf-8');return self.record('write_artifact',{'kind':'written','path':'artifact.json'})

    def artifact(self):
        p=self.out/'artifact.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None

    def schema_valid(self,obj):
        if not isinstance(obj,dict) or set(obj)!= {'rows'} or not isinstance(obj['rows'],list) or len(obj['rows'])!=len(self.task['queries']):return False
        keys={'id','value','label','source_version','evidence_code'}
        return all(isinstance(row,dict) and set(row)==keys and isinstance(row['value'],int) and not isinstance(row['value'],bool)
            and all(isinstance(row[k],str) and bool(row[k]) for k in keys-{'value'}) for row in obj['rows'])

    def check_artifact(self):
        self.verify_sources();obj=self.artifact()
        if not self.schema_valid(obj):return self.record('check_artifact',{'kind':'public_check','schema_valid':False,'error':'row schema/length/types invalid'})
        for row,q in zip(obj['rows'],self.task['queries']):
            if row['source_version']!=self.versions[q['entity']]:return self.record('check_artifact',{'kind':'missing_evidence','entity':q['entity'],'origin':'host','error':'current source provenance missing or obsolete'})
        return self.record('check_artifact',{'kind':'public_check','schema_valid':True,'note':'Final independent value and opaque-label validation after model stops.'})

    def reference(self,historical=False):
        rows=[]
        for q in self.task['queries']:
            source=self.task['old_documents' if historical else 'documents'][q['entity']]
            rec=next(x for x in source['records'] if x['code']==q['code'])
            rows.append({'id':q['id'],'value':q['reading']*rec['multiplier']+rec['bias'],'label':rec['label'],
                'source_version':(self.old_versions if historical else self.versions)[q['entity']],'evidence_code':q['code']})
        return {'rows':rows}

    def evaluate(self):
        self.verify_sources();obj=self.artifact();valid=self.schema_valid(obj)
        return {'passed':valid and obj==self.reference(),'schema_valid':valid,
            'reason':'passed' if valid and obj==self.reference() else 'artifact_missing_or_wrong'}


def admission(task,out):
    w=RegistryWorkspace(task,out)
    for e in w.entities:w.read_document(e,'current')
    w.close_research('offline control');w.work_order();empty=w.evaluate()
    w.write_artifact(json.dumps(w.reference()));reference=w.evaluate()
    w.write_artifact(json.dumps(w.reference(True)));historical=w.evaluate()
    c={'task':task['name'],'passed':reference['passed'] and not empty['passed'] and not historical['passed'],
        'reference_passed':reference['passed'],'empty_passed':empty['passed'],'historical_passed':historical['passed']}
    (Path(out)/'admission.json').write_text(dump(c),encoding='utf-8');return c
