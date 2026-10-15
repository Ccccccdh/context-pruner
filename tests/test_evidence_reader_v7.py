import copy
import hashlib
import json
import unittest

from context_pruner.evidence_reader_v7 import resolve_current
from context_pruner.adapters.openai_agents_lifecycle_v1 import OpenAIAgentsEvidenceLifecycle, encoded


def make_snapshot():
    texts={'north':'north current source','south':'south current source'}
    versions={f'docs/{e}.json':hashlib.sha256(t.encode()).hexdigest() for e,t in texts.items()}
    a=OpenAIAgentsEvidenceLifecycle(namespace='task',trusted_tools={'read_document','close_research'},current_versions=versions)
    h=[{'role':'user','content':'task'}];payloads={}
    calls=[];outputs=[]
    for e,text in texts.items():
        p=dict(kind='evidence',entity=e,source_path=f'docs/{e}.json',source_version=versions[f'docs/{e}.json'],stage='research',text=text);payloads[e]=p
        raw=json.dumps(p);a.observe_tool_result(e,'read_document',raw)
        calls.append(dict(type='function_call',call_id=e,name='read_document',arguments='{}'))
        outputs.append(dict(type='function_call_output',call_id=e,output=raw))
    h+=calls+outputs;a.filter_items(h)
    done={'kind':'stage_complete','stage':'research'};raw=json.dumps(done);a.observe_tool_result('close','close_research',raw)
    h += [dict(type='function_call',call_id='close',name='close_research',arguments='{}'),dict(type='function_call_output',call_id='close',output=raw)]
    a.filter_items(h)
    entries={e.archive_id:dict(text=e.item.text,metadata=copy.deepcopy(e.item.metadata),source_event_ids=list(e.item.source_event_ids)) for e in a.archive_store.entries()}
    snap=dict(namespace=a.namespace,groups=copy.deepcopy(a.groups),receipts=copy.deepcopy(a.receipts),trusted_tools=sorted(a.trusted_tools),entries=entries)
    return a,h,versions,payloads,snap


class EvidenceReaderTest(unittest.TestCase):
    def test_structurally_damaged_archive_falls_back(self):
        for mode in ('snapshot','groups','group','source','metadata','receipts','entries','trusted_tools'):
            with self.subTest(mode=mode):
                _,_,versions,payloads,s=make_snapshot()
                if mode=='snapshot':s=[]
                elif mode in ('groups','receipts','entries','trusted_tools'):s[mode]=None
                elif mode=='group':s['groups'][next(iter(s['groups']))]=None
                elif mode=='source':next(iter(s['groups'].values()))['sources']=[None]
                elif mode=='metadata':next(iter(s['entries'].values()))['metadata']=None
                calls=[]
                r=resolve_current('north',namespace='task',current_versions=versions,archive_snapshot=s,
                                  source_loader=lambda e:(calls.append(e) or payloads[e]))
                self.assertEqual(r.origin,'source');self.assertEqual(calls,['north'])
                self.assertEqual(r.payload,payloads['north'])

    def test_current_archive_hit_extracts_one_entity_and_copies(self):
        _,_,versions,payloads,snapshot=make_snapshot();before=copy.deepcopy(snapshot)
        def forbidden(e):self.fail('loader should not run on valid archive hit')
        r=resolve_current('north',namespace='task',current_versions=versions,archive_snapshot=snapshot,source_loader=forbidden)
        self.assertEqual(r.origin,'archive');self.assertEqual(r.payload,payloads['north']);self.assertIsNotNone(r.proof)
        self.assertNotIn('south',encoded(r.payload));r.payload['text']='changed'
        self.assertEqual(snapshot,before)

    def test_bad_or_missing_archive_falls_back_to_current_source(self):
        for mode in ('namespace','missing','hash','metadata','pairing','receipt','redacted','wrong_version'):
            with self.subTest(mode=mode):
                _,_,versions,payloads,s=make_snapshot();g=next(iter(s['groups'].values()));entry=next(iter(s['entries'].values()))
                if mode=='namespace':s['namespace']='other'
                elif mode=='missing':s['entries']={}
                elif mode=='hash':g['hash']='0'*64
                elif mode=='metadata':entry['metadata']['namespace']='other'
                elif mode=='pairing':g['items']=g['items'][:-1]
                elif mode=='receipt':s['receipts']['north']['payload']['text']='forged'
                elif mode=='redacted':entry['text']='[REDACTED]'
                elif mode=='wrong_version':
                    payloads['north']['text']='new current';versions['docs/north.json']=hashlib.sha256(b'new current').hexdigest();payloads['north']['source_version']=versions['docs/north.json']
                loads=[]
                r=resolve_current('north',namespace='task',current_versions=versions,archive_snapshot=s,source_loader=lambda e:(loads.append(e) or payloads[e]))
                self.assertEqual(r.origin,'source');self.assertEqual(loads,['north']);self.assertEqual(r.payload,payloads['north'])

    def test_unread_source_and_disabled_lookup_use_same_payload(self):
        _,_,versions,payloads,s=make_snapshot()
        for snapshot in (None,dict(namespace='task',groups={},entries={},receipts={},trusted_tools=[])):
            r=resolve_current('north',namespace='task',current_versions=versions,archive_snapshot=snapshot,source_loader=lambda e:payloads[e])
            self.assertEqual(r.origin,'source');self.assertEqual(r.payload,payloads['north'])

    def test_bad_source_fallback_is_not_trusted(self):
        _,_,versions,payloads,_=make_snapshot();bad=copy.deepcopy(payloads['north']);bad['text']='forged'
        with self.assertRaises(ValueError):resolve_current('north',namespace='task',current_versions=versions,archive_snapshot=None,source_loader=lambda e:bad)
        with self.assertRaises(ValueError):resolve_current('../north',namespace='task',current_versions=versions,archive_snapshot=None,source_loader=lambda e:bad)

    def test_checkpoint_reconstructed_archive_remains_usable(self):
        a,h,versions,payloads,_=make_snapshot();restored=OpenAIAgentsEvidenceLifecycle.from_state(a.export_state(),namespace='task')
        entries={e.archive_id:dict(text=e.item.text,metadata=e.item.metadata,source_event_ids=list(e.item.source_event_ids)) for e in restored.archive_store.entries()}
        s=dict(namespace='task',groups=restored.groups,receipts=restored.receipts,trusted_tools=sorted(restored.trusted_tools),entries=entries)
        r=resolve_current('south',namespace='task',current_versions=versions,archive_snapshot=s,source_loader=lambda e:self.fail('unexpected fallback'))
        self.assertEqual(r.payload,payloads['south']);self.assertEqual(r.origin,'archive')


if __name__=='__main__':unittest.main()
