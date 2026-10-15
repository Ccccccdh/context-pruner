"""Deterministic record selection from a verified immutable whole source."""
from copy import deepcopy
import hashlib
import json
from .evidence_reader_v7 import _check_payload


def validate_request(entity,codes,revision,closed):
    if (entity not in {'north','south'} or revision!='current' or closed is not True
            or not isinstance(codes,list) or not 1<=len(codes)<=24
            or any(not isinstance(c,str) or not c.strip() for c in codes)
            or len(set(codes))!=len(codes)):
        raise ValueError('closed research, known entity, current revision and 1..24 distinct codes required')


def project_records(payload,entity,codes,revision,closed,*,full=False):
    validate_request(entity,codes,revision,closed)
    if not isinstance(payload,dict):raise ValueError('whole source required')
    _check_payload(payload,entity,f'docs/{entity}.json',payload.get('source_version'))
    try:
        doc=json.loads(payload['text']);records=doc['records']
        if (not isinstance(doc['revision'],str) or not isinstance(doc['rule'],str)
                or not isinstance(records,list) or any(not isinstance(r,dict) or not isinstance(r.get('code'),str) for r in records)):
            raise ValueError('invalid registry source')
        bycode={r['code']:r for r in records}
        if len(bycode)!=len(records) or any(c not in bycode for c in codes):raise ValueError('unknown or ambiguous code')
        selected=dict(revision=doc['revision'],rule=doc['rule'],records=[bycode[c] for c in codes])
        text=payload['text'] if full else json.dumps(selected,ensure_ascii=False,sort_keys=True,indent=2)
    except (KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('invalid registry source') from exc
    return deepcopy(dict(kind='registry_record_evidence',entity=entity,stage='implementation',
                         source_path=payload['source_path'],source_version=payload['source_version'],
                         requested_codes=codes,representation='full' if full else 'selected',text=text,
                         payload_sha256=hashlib.sha256(text.encode()).hexdigest()))
