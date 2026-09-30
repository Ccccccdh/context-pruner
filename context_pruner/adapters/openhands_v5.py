"""Versioned file evidence rebuilt from immutable SDK events, without nested memory."""
from __future__ import annotations

import re
from typing import Any
from pydantic import PrivateAttr
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM

from .openhands_v4 import ContextPrunerCondenserV4, compact_path, semantic_text
from ..middleware import ContextPrunerMiddleware
from ..plugin import ContextPluginConfig
from ..types import ContextBudget


class ContextPrunerCondenserV5(ContextPrunerCondenserV4):
    """Keep SDK-safe boundaries; compact duplicate and superseded file evidence.

    A live condenser records the original events it has observed. If it encounters
    an unknown summary after restoration, it preserves that view rather than
    pretending it can recover missing originals. No filesystem reads or API calls
    are performed here.
    """
    _original_events: dict[str, Any] = PrivateAttr(default_factory=dict)
    _archived_ids: set[str] = PrivateAttr(default_factory=set)
    _summary_ids: set[str] = PrivateAttr(default_factory=set)

    def condense(self, view: View, agent_llm: LLM | None = None):
        for event in view.events[2:]:
            if type(event).__name__ != 'CondensationSummaryEvent':
                self._original_events.setdefault(event.id, event)
        if agent_llm is None or len(view.events) < 5:
            return view
        before = get_total_token_count(view.events, agent_llm)
        if before <= self.trigger_tokens:
            return view
        if any(type(e).__name__ == 'CondensationSummaryEvent' and e.id not in self._summary_ids for e in view.events):
            self._audit.append({'before': before, 'skipped': 'unknown_summary_preserved'})
            return view
        safe = view.manipulation_indices
        ends = [i for i in safe if 2 < i < len(view.events) - 1]
        if 2 not in safe or not ends:
            return view
        end = max(ends)
        # User requirements remain as their original events, even when complete
        # tool batches after them are compacted. Validate the resulting SDK view
        # below; never remove a partial tool batch.
        protected_users = [e for e in view.events[2:end]
                           if type(e).__name__ == 'MessageEvent' and e.to_llm_message().role == 'user']
        protected_ids = {e.id for e in protected_users}
        forgotten = [e for e in view.events[2:end] if e.id not in protected_ids]
        if not forgotten:
            return view
        if all(type(e).__name__ == 'CondensationSummaryEvent' for e in forgotten):
            self._audit.append({'before': before, 'skipped': 'no_new_history'})
            return view
        retained = view.events[:2] + protected_users + view.events[end:]
        candidate_ids = self._archived_ids | {e.id for e in forgotten if e.id in self._original_events}
        originals = [e for key, e in self._original_events.items() if key in candidate_ids]
        prepared, evidence_stats = project_current_evidence(originals)
        task_state = '\n'.join(semantic_text(e.to_llm_message()) for e in view.events
                               if type(e).__name__ == 'MessageEvent' and e.to_llm_message().role == 'user')
        from litellm import token_counter
        # Rebuild once from source events. A previous derived memory is never fed
        # back into the middleware; immutable SDK logs own the full archive.
        middleware = ContextPrunerMiddleware(
            ContextPluginConfig(budget=ContextBudget(self.trigger_tokens, self.hard_tokens, self.target_tokens)),
            token_counter=lambda text: token_counter(model='gpt-4o', text=text))
        hook = middleware.before_model(prepared, task_state=task_state,
                                       reserved_tokens=get_total_token_count(retained, agent_llm))
        memory = ('Archived evidence rebuilt from original events. File versions refer to the archived prefix; '
                  'newer retained tool results supersede them. Agent statements remain unverified.\n' +
                  '\n'.join(str(m.get('content', '')) for m in hook.messages))
        result = Condensation(forgotten_event_ids={e.id for e in forgotten}, summary=memory,
                              summary_offset=2, llm_response_id='context-pruner-local-v5')
        projected = result.apply(view.events)
        checked = View(events=list(projected))
        checked.enforce_properties(view.events)
        if [e.id for e in checked.events] != [e.id for e in projected]:
            self._audit.append({'before': before, 'skipped': 'structural_validation_failed'})
            return view
        after = get_total_token_count(projected, agent_llm)
        if after >= before:
            self._audit.append({'before': before, 'after': after, 'skipped': 'no_token_reduction'})
            return view
        self._middleware = middleware
        self._archived_ids = candidate_ids
        self._summary_ids.add(result.summary_event.id)
        self._audit.append({'before': before, 'after': after, 'forgotten_ids': sorted(result.forgotten_event_ids),
                            'retained_ids': [e.id for e in retained], 'safe_start': 2, 'safe_end': end,
                            'hard_budget_exceeded': after > self.hard_tokens, **evidence_stats})
        return result

    def export_state(self):
        return {'adapter': 'openhands-condenser-v5', 'audit': self._audit,
                'archived_sdk_source_ids': sorted(self._archived_ids),
                'middleware': self._middleware.export_state() if self._middleware else None}


def project_current_evidence(events):
    """Union numbered reads per version; successful mutation replaces old code.

    Full new_content is supplied by the editor observation, never by opening a
    file or inspecting hidden acceptance tests. Failed edits cannot update it.
    """
    actions = {e.id: e.action for e in events if hasattr(e, 'action')}
    files, other = {}, []
    superseded = duplicates = 0
    for event in events:
        if hasattr(event, 'observation'):
            obs = event.observation
            action = actions.get(event.action_id)
            path = compact_path(getattr(action, 'path', None) or getattr(obs, 'path', '') or '')
            command = getattr(action, 'command', getattr(obs, 'command', ''))
            text = semantic_text(event.to_llm_message())
            if obs.is_error:
                other.append({'role': 'assistant', 'content': f'Tool failed; no confirmed change: {command} {path}\n{text[:600]}', '_event_id': event.id})
                continue
            if path and command in ('view', 'str_replace', 'insert', 'create', 'undo_edit'):
                record = files.setdefault(path, {'version': 0, 'lines': {}, 'sources': [], 'fallback': []})
                if command != 'view':
                    superseded += len(record['sources'])
                    record['version'] += 1
                    record['lines'], record['sources'], record['fallback'] = {}, [], []
                    content = getattr(obs, 'new_content', None)
                    if content is not None:
                        record['lines'] = dict(enumerate(content.splitlines(), 1))
                    else:
                        record['fallback'].append('Mutation confirmed; earlier file evidence invalidated.\n' + text)
                else:
                    numbered = [(int(m.group(1)), m.group(2)) for line in text.splitlines()
                                if (m := re.match(r'^\s*(\d+)\t(.*)$', line))]
                    if numbered:
                        for number, line in numbered:
                            duplicates += int(record['lines'].get(number) == line)
                            record['lines'][number] = line
                    elif text not in record['fallback']:
                        record['fallback'].append(text)
                    else:
                        duplicates += 1
                record['sources'].append(event.id)
                continue
            other.append({'role': 'assistant', 'content': 'Tool observation: ' + text, '_event_id': event.id})
        elif type(event).__name__ == 'MessageEvent':
            message = event.to_llm_message()
            other.append({'role': 'task' if message.role == 'user' else 'assistant',
                          'content': semantic_text(message), '_event_id': event.id})
    messages = list(other)
    for path, record in files.items():
        lines = [f'{n}\t{line}' for n, line in sorted(record['lines'].items())]
        lines.extend(record['fallback'])
        blocks, block, size = [], [], 0
        for line in lines:
            if block and size + len(line) > 1000:
                blocks.append('\n'.join(block)); block, size = [], 0
            block.append(line); size += len(line) + 1
        if block:
            blocks.append('\n'.join(block))
        for part, text in enumerate(blocks):
            messages.append({'role': 'tool', 'content': f"文件={path}; version={record['version']}; observed successful result\n{text}",
                             '_event_id': f"{record['sources'][-1]}:v{record['version']}:p{part}",
                             '_metadata': {'sdk_source_ids': record['sources'], 'file_version': record['version']}})
    return messages, {'superseded_observations': superseded, 'duplicate_read_lines': duplicates,
                      'file_versions': {p: r['version'] for p, r in files.items()}}
