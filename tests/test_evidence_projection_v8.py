import copy
import hashlib
import json
import unittest
from context_pruner.evidence_projection_v8 import project_records
from experiments.runners.agents_registry_tasks_v4 import make_task, dump


def source():
    text=dump(make_task(401,'test')['documents']['north'])
    return dict(kind='evidence',entity='north',stage='research',source_path='docs/north.json',
                source_version=hashlib.sha256(text.encode()).hexdigest(),text=text)


class ProjectionTest(unittest.TestCase):
    def test_all_records_order_and_hashes(self):
        p=source();before=copy.deepcopy(p);doc=json.loads(p['text'])
        for rec in doc['records']:
            r=project_records(p,'north',[rec['code']],'current',True)
            self.assertEqual(json.loads(r['text']),dict(revision=doc['revision'],rule=doc['rule'],records=[rec]))
            self.assertEqual(r['source_version'],p['source_version'])
            self.assertEqual(r['payload_sha256'],hashlib.sha256(r['text'].encode()).hexdigest())
            self.assertNotEqual(r['source_version'],r['payload_sha256'])
            self.assertEqual(r['kind'],'registry_record_evidence')
        codes=['N-23','N-00','N-11']
        r=project_records(p,'north',codes,'current',True)
        self.assertEqual([x['code'] for x in json.loads(r['text'])['records']],codes)
        self.assertEqual(r['requested_codes'],codes);self.assertEqual(p,before)
        full=project_records(p,'north',codes,'current',True,full=True)
        self.assertEqual(full['text'],p['text']);self.assertEqual(full['representation'],'full')

    def test_invalid_requests_and_source_fail_closed(self):
        p=source()
        for entity,codes,revision,closed in [('other',['N-00'],'current',True),('north',[],'current',True),
                ('north',['N-00','N-00'],'current',True),('north',['missing'],'current',True),
                ('north','N-00','current',True),('north',[1],'current',True),('north',[''],'current',True),
                ('north',['N-00'],'historical',True),('north',['N-00'],'current',False),
                ('north',[f'N-{i:02d}' for i in range(25)],'current',True)]:
            with self.subTest(codes=codes,entity=entity,revision=revision,closed=closed):
                with self.assertRaises(ValueError):project_records(p,entity,codes,revision,closed)
        for field,value in [('text','forged'),('source_version','0'*64),('entity','south'),('source_path','../north.json')]:
            bad={**p,field:value}
            with self.assertRaises(ValueError):project_records(bad,'north',['N-00'],'current',True)

    def test_selected_evidence_sufficient_without_answer_fields(self):
        task=make_task(401,'test')
        for entity in ('north','south'):
            text=dump(task['documents'][entity]);p=dict(kind='evidence',entity=entity,stage='research',source_path=f'docs/{entity}.json',source_version=hashlib.sha256(text.encode()).hexdigest(),text=text)
            queries=[q for q in task['queries'] if q['entity']==entity]
            selected=json.loads(project_records(p,entity,[q['code'] for q in queries],'current',True)['text'])
            for q in queries:
                rec=next(x for x in selected['records'] if x['code']==q['code'])
                original=next(x for x in task['documents'][entity]['records'] if x['code']==q['code'])
                self.assertEqual(q['reading']*rec['multiplier']+rec['bias'],q['reading']*original['multiplier']+original['bias'])
                self.assertEqual(rec['label'],original['label'])
            self.assertEqual(set(selected),{'revision','rule','records'})


if __name__=='__main__':unittest.main()
