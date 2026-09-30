"""Optional native OpenHands condenser, preserving complete tool batches.

Only a safe prefix of the model view is compressed. The SDK event store is
unchanged; Condensation events apply a derived memory view. This adapter uses
the existing deterministic pruner, with no extra summarization model calls.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import PrivateAttr
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM

from ..middleware import ContextPrunerMiddleware
from ..plugin import ContextPluginConfig
from ..types import ContextBudget


class ContextPrunerCondenserV4(CondenserBase):
    """SDK 1.49.6 view integration; keep initial instructions and latest batch.

    Latest user requirements are kept outside the removable prefix. The hard
    budget is a target for compressible history, not a guarantee when the
    protected system prompt or most recent tool batch alone exceeds it.
    """
    trigger_tokens: int = 12000
    target_tokens: int = 10000
    hard_tokens: int = 16000
    _middleware: Any = PrivateAttr(default=None)
    _audit: list = PrivateAttr(default_factory=list)

    def condense(self, view: View, agent_llm: LLM | None = None) -> View | Condensation:
        if agent_llm is None or len(view.events) < 5:
            return view
        before = get_total_token_count(view.events, agent_llm)
        if before <= self.trigger_tokens:
            return view
        safe = view.manipulation_indices
        # First system + initial user are immutable. Never cross later user
        # requirements; never remove the latest tool batch or pending actions.
        later_users = [i for i, e in enumerate(view.events[2:], 2)
                       if e.to_llm_message().role == 'user'
                       and type(e).__name__ != 'CondensationSummaryEvent']
        latest_user = max(later_users, default=len(view.events))
        ends = [i for i in safe if 2 < i < len(view.events) - 1 and i <= latest_user]
        if 2 not in safe or not ends:
            return view
        end = max(ends)
        forgotten = view.events[2:end]
        if all(type(e).__name__ == 'CondensationSummaryEvent' for e in forgotten):
            self._audit.append({'before': before, 'skipped': 'no_new_history'})
            return view
        retained = view.events[:2] + view.events[end:]
        reserve = get_total_token_count(retained, agent_llm)
        if self._middleware is None:
            def count(text):
                from litellm import token_counter
                return token_counter(model='gpt-4o', text=text)
            self._middleware = ContextPrunerMiddleware(
                ContextPluginConfig(budget=ContextBudget(self.trigger_tokens, self.hard_tokens, self.target_tokens)),
                token_counter=count)
        task_state = '\n'.join(
            semantic_text(e.to_llm_message()) for e in view.events
            if type(e).__name__ == 'MessageEvent' and e.to_llm_message().role == 'user')
        prepared = project_events(forgotten)
        hook = self._middleware.before_model(prepared, task_state=task_state, reserved_tokens=reserve)
        memory = 'Archived history (derived memory; original events retained):\n' + '\n'.join(
            str(m.get('content', '')) for m in hook.messages)
        condensation = Condensation(forgotten_event_ids={e.id for e in forgotten}, summary=memory,
                                    summary_offset=2, llm_response_id='context-pruner-local')
        projected = condensation.apply(view.events)
        checked = View(events=list(projected))
        checked.enforce_properties(view.events)
        if [e.id for e in checked.events] != [e.id for e in projected]:
            self._audit.append({'before': before, 'skipped': 'structural_validation_failed'})
            return view
        after = get_total_token_count(projected, agent_llm)
        if after >= before:
            self._audit.append({'before': before, 'after': after, 'skipped': 'no_token_reduction'})
            return view
        self._audit.append({'before': before, 'after': after, 'forgotten_ids': sorted(condensation.forgotten_event_ids),
                            'retained_ids': [e.id for e in retained], 'safe_start': 2, 'safe_end': end,
                            'hard_budget_exceeded': after > self.hard_tokens})
        return condensation

    def export_state(self):
        return {'adapter': 'openhands-condenser-v4', 'audit': self._audit,
                'middleware': self._middleware.export_state() if self._middleware else None}

    def finalize(self, metadata=None):
        if self._middleware:
            self._middleware.finalize(metadata or {})
        return self.export_state()


def semantic_text(message):
    """Extract model-visible text, never serialize transport envelopes."""
    return '\n'.join(c.text for c in message.content if hasattr(c, 'text'))


def project_events(events):
    actions = {e.id: e.action for e in events if hasattr(e, 'action')}
    messages = []
    for event in events:
        message = event.to_llm_message()
        text = semantic_text(message)
        role = message.role
        if type(event).__name__ == 'CondensationSummaryEvent':
            text = event.summary
            role = 'state'
        elif hasattr(event, 'observation'):
            action = actions.get(event.action_id)
            path = getattr(action, 'path', None) or getattr(event.observation, 'path', '')
            original_path = path
            path = compact_path(path)
            text = text.replace(str(original_path), path) if original_path else text
            command = getattr(action, 'command', getattr(event.observation, 'command', ''))
            status = 'error' if event.observation.is_error else 'success'
            # Keep plain numbered code in small source chunks. Core recognizes
            # 文件= and preserves compact source evidence instead of treating JSON
            # as prose. Errors remain explicitly labelled, never verified facts.
            lines = text.splitlines()
            blocks = []
            block = []
            for line in lines:
                if block and sum(len(s) + 1 for s in block) + len(line) > 1050:
                    blocks.append('\n'.join(block))
                    block = []
                block.append(line)
            if block:
                blocks.append('\n'.join(block))
            for part, block in enumerate(blocks):
                messages.append({'role': 'tool',
                    'content': f'文件={path}; command={command}; status={status}\n{block}',
                    '_event_id': f'{event.id}:part{part}', '_metadata': {'sdk_event_id': str(event.id)}})
            continue
        elif hasattr(event, 'action'):
            fields = event.action.model_dump(exclude_none=True)
            if 'path' in fields:
                fields['path'] = compact_path(fields['path'])
            text = 'Tool action (outcome is recorded separately): ' + json.dumps(fields, ensure_ascii=False)
            if event.thought:
                text += '\nAgent reasoning (unverified): ' + '\n'.join(c.text for c in event.thought if hasattr(c, 'text'))
        messages.append({'role': role, 'content': text, '_event_id': str(event.id)})
    return messages


def compact_path(path):
    """Relative location only; immutable SDK events keep the absolute path."""
    value = str(path).replace('\\', '/')
    value = re.sub(r'^.*?/workspaces/w[0-9]+/', '', value)
    return value.split('/workspace/', 1)[-1]
