"""Verified archive-first resolution for immutable registry sources, no model calls."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from .adapters.openai_agents_lifecycle_v1 import OpenAIAgentsEvidenceLifecycle, encoded, source_path


@dataclass(frozen=True)
class ReadResolution:
    payload: dict
    origin: str
    archive_group_id: str | None = None
    reason: str = ''
    proof: dict | None = None


def _check_payload(payload, entity, path, version):
    if (not isinstance(payload,dict) or payload.get('kind')!='evidence' or payload.get('entity')!=entity
            or source_path(payload.get('source_path',''))!=path or payload.get('source_version')!=version
            or not isinstance(payload.get('text'),str)
            or hashlib.sha256(payload['text'].encode()).hexdigest()!=version):
        raise ValueError('current evidence payload/provenance mismatch')


def resolve_current(entity, *, namespace, current_versions, archive_snapshot, source_loader):
    """Snapshot is trusted host state, never model-supplied metadata.

    A cache rejection falls back to a separately verified source result. Cached
    call IDs stay in host proof only; payload is used by a new ordinary tool call.
    """
    if entity not in {'north','south'} or not isinstance(namespace,str) or not namespace:
        raise ValueError('known registry entity and task namespace required')
    path=f'docs/{entity}.json';version=current_versions.get(path)
    if not isinstance(version,str) or len(version)!=64:raise ValueError('current source version required')
    reason='lookup_disabled' if archive_snapshot is None else 'no_current_archive'
    if archive_snapshot is not None:
        snapshot=deepcopy(archive_snapshot)
        if (not isinstance(snapshot,dict)
                or any(not isinstance(snapshot.get(k),dict) for k in ('groups','receipts','entries'))
                or not isinstance(snapshot.get('trusted_tools'),(list,tuple,set))
                or any(not isinstance(t,str) for t in snapshot['trusted_tools'])):
            reason='archive_rejected'
        elif snapshot.get('namespace')!=namespace:
            reason='namespace_mismatch'
        else:
            validator=OpenAIAgentsEvidenceLifecycle(namespace=namespace,
                trusted_tools=set(snapshot.get('trusted_tools',[])),current_versions=current_versions)
            validator.receipts=deepcopy(snapshot.get('receipts',{}))
            for gid,group in sorted(snapshot.get('groups',{}).items()):
                try:
                    if not isinstance(group,dict):raise ValueError('invalid archive group')
                    if group.get('status')!='archived':continue
                    sources=group.get('sources')
                    if not isinstance(sources,list) or any(not isinstance(s,dict) for s in sources):
                        raise ValueError('invalid archive sources')
                    if not any(s.get('entity')==entity for s in sources):continue
                    validator._validate_group(gid,group)
                    if any(current_versions.get(source_path(s['source_path']))!=s['source_version'] for s in group['sources']):
                        raise ValueError('stale archive source')
                    entry=snapshot.get('entries',{}).get(group.get('archive_id'))
                    if (not entry or entry['text']!=encoded(group['items'])
                            or entry['source_event_ids']!=[gid]
                            or entry['metadata'].get('namespace')!=namespace
                            or entry['metadata'].get('group_hash')!=group['hash']
                            or entry['metadata'].get('sources')!=group['sources']):
                        raise ValueError('archive entry identity mismatch')
                    matches=[]
                    for item in group['items']:
                        if item.get('type')!='function_call_output':continue
                        receipt=validator.receipts[item['call_id']]
                        p=receipt['payload']
                        if receipt['tool_name']=='read_document' and p.get('kind')=='evidence' and p.get('entity')==entity:
                            _check_payload(p,entity,path,version);matches.append(p)
                    if not matches or any(p!=matches[0] for p in matches):raise ValueError('ambiguous source payload')
                    ids={i['call_id'] for i in group['items'] if i.get('type')=='function_call'}
                    proof=dict(namespace=namespace,groups={gid:deepcopy(group)},
                               entries={group['archive_id']:deepcopy(entry)},
                               receipts={cid:deepcopy(validator.receipts[cid]) for cid in ids},
                               trusted_tools=sorted(validator.trusted_tools))
                    return ReadResolution(deepcopy(matches[0]),'archive',gid,'verified_current_archive',proof)
                except (ValueError,KeyError,TypeError,IndexError,AttributeError):
                    reason='archive_rejected'
    payload=source_loader(entity)
    _check_payload(payload,entity,path,version)
    return ReadResolution(deepcopy(payload),'source',reason=reason)
