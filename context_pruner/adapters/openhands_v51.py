"""v51 candidate: list evidence *references* instead of re-embedding source bodies.

Diagnosis that motivates this variant (V49_COMPRESSION_DIAGNOSTIC.md, zero API):

  * In v49 every sample that triggered condensation became far more expensive than its
    own no-compression baseline (plugin: -107.5%, -139.9%, -140.3%), while every sample
    that did not trigger was flat (+0.1%, +2.4%, -0.0%).
  * 66%-86% of the injected summary was the `SELECTED OBSERVED CODE` section, which
    pastes the full text of each selected evidence unit.
  * That text is redundant: the same summary already carries
    `OBSERVED FILE INDEX (path, version, view_range)` and
    `OBSERVED SYMBOL LOCATIONS (name:start-end)` for every observed file, and the
    prefix instructs the model to recover omitted code with `scoped_editor view`.

Implementation note: the body text is assembled inside v41's `condense`, so this
class re-implements that method with exactly one behavioural change - the selected
units are emitted as references rather than as bodies. Everything else is kept as in
v41/v42: the same trigger/target/hard budgets, the same protected recent editing
window, the same evidence ranking, the same protected contract plus current-state
injection, the same structural validation, the same refusal to truncate required
instructions, and the same audit fields.

This isolates the diagnostic's item 1 ("reference not content") from item 3
("raise the threshold"), so the two can be measured as separate ablations.
"""
from __future__ import annotations

from pydantic import Field, PrivateAttr
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation

from context_pruner.adapters.openhands_v41 import (
    confirmed_edit_notes,
    evidence_units,
    symbol_locations,
)
from context_pruner.adapters.openhands_v42 import ContextPrunerCondenserV42
from context_pruner.adapters.openhands_v4 import semantic_text


class ContextPrunerCondenserV51(ContextPrunerCondenserV42):
    """v42 with reference-only evidence lists."""

    #: Recorded per condensation so the mechanism change is auditable offline.
    _reference_audit: list = PrivateAttr(default_factory=list)
    #: Character total of the reference listing emitted by the last condensation.
    _last_reference_chars: int = PrivateAttr(default=0)

    def condense(self, view, agent_llm=None):
        original = self.protected_contract
        try:
            self.protected_contract = (
                original + '\n' + self._current_state).strip()
            return self._condense_reference_only(view, agent_llm)
        finally:
            self.protected_contract = original

    def _condense_reference_only(self, view, agent_llm=None):
        """v41's condense with bodies replaced by references. See module docstring."""
        for event in view.events[2:]:
            if type(event).__name__ != 'CondensationSummaryEvent':
                self._original_events.setdefault(event.id, event)
        if agent_llm is None or len(view.events) < 5:
            return view
        before = get_total_token_count(view.events, agent_llm)
        if before <= self.trigger_tokens:
            return view
        if any(type(e).__name__ == 'CondensationSummaryEvent' and e.id not in self._summary_ids
               for e in view.events):
            self._audit.append({'before': before, 'skipped': 'unknown_summary_preserved'})
            return view
        ends = [i for i in view.manipulation_indices if 2 < i < len(view.events) - 1]
        if 2 not in view.manipulation_indices or not ends:
            return view
        end = max(ends)
        for proposed in sorted(ends)[-4:]:
            older_users = [e for e in view.events[2:proposed]
                           if type(e).__name__ == 'MessageEvent'
                           and e.to_llm_message().role == 'user']
            retained = list(view.events[:2]) + older_users + list(view.events[proposed:])
            if get_total_token_count(retained, agent_llm) <= min(
                    self.target_tokens - 4000, self.hard_tokens - 5000):
                end = proposed
                break
        users = [e for e in view.events[2:end]
                 if type(e).__name__ == 'MessageEvent' and e.to_llm_message().role == 'user']
        protected = {e.id for e in users}
        forgotten = [e for e in view.events[2:end] if e.id not in protected]
        if not forgotten or all(type(e).__name__ == 'CondensationSummaryEvent' for e in forgotten):
            return view
        ids = self._archived_ids | {e.id for e in forgotten if e.id in self._original_events}
        originals = [e for key, e in self._original_events.items() if key in ids]
        user_text = '\n'.join(semantic_text(e.to_llm_message()) for e in view.events
                              if type(e).__name__ == 'MessageEvent'
                              and e.to_llm_message().role == 'user')
        contracts, _, _, _, _ = evidence_units(originals, user_text)
        host_contract = self.protected_contract.strip()
        query = user_text + '\n' + host_contract + '\n' + '\n'.join(contracts)
        contracts, units, index, failures, stats = evidence_units(originals, query)
        edit_notes, touched = confirmed_edit_notes(originals)
        for unit in units:
            if (unit['path'], unit['name']) in touched:
                unit['score'] += 200
                unit['edited_symbol'] = True
        units.sort(key=lambda u: (not u.get('edited_symbol', False),
            '/tests/' in '/' + u['path'].replace('\\', '/'),
            -u['score'] / max(1, len(u['text']) / 400) ** 0.5, u['path'], u['range'][0]))
        prefix = ('Archived source evidence; immutable original SDK events retained. '
                  'Newer retained successful edits supersede these prefix versions. '
                  'Omitted code is NOT deleted. Recover it using scoped_editor view with the path and range below.\n'
                  'PROTECTED TASK CONTRACT (host-supplied scope and observed TASK.md):\n' +
                  '\n'.join([part for part in [host_contract, *contracts] if part]) + '\n'
                  'OBSERVED FILE INDEX (ranges can contain unobserved gaps):\n' +
                  '\n'.join(f"{r['path']} version={r['version']} view_range={r['range']}" for r in index) + '\n' +
                  'OBSERVED SYMBOL LOCATIONS (bodies may be omitted; newer edits can shift lines):\n' +
                  symbol_locations(units) + '\nCONFIRMED EDIT OBSERVATIONS (not public verification):\n' +
                  '\n'.join(f"{r['path']} {r['command']} symbols={r['symbols']} source_id={r['sdk_source_id']}"
                            for r in edit_notes) + '\n' +
                  '\n'.join(failures) + '\nSELECTED OBSERVED CODE (references only; bodies omitted):\n')

        def render(selection):
            """v51's one behavioural change: references, never bodies."""
            lines = [f"{u['path']} version={u['version']} {u['name']} "
                     f"range={u['range']} ({u['range'][1] - u['range'][0] + 1} lines omitted; "
                     f"recover with scoped_editor view)" for u in selection]
            return prefix + ('\n'.join(lines) + '\n' if lines else '(no unit selected)\n')

        selected = []

        def candidate():
            memory = render(selected)
            result = Condensation(forgotten_event_ids={e.id for e in forgotten}, summary=memory,
                                  summary_offset=2, llm_response_id='context-pruner-local-v51')
            return result, get_total_token_count(list(result.apply(view.events)), agent_llm)

        result, fixed = candidate()
        if fixed > self.hard_tokens:
            self._audit.append({'before': before, 'skipped': 'protected_context_exceeds_hard_budget',
                                'protected_tokens': fixed,
                                'task_contract_chars': len(host_contract) + sum(map(len, contracts))})
            return view

        # The budget loop is retained so the same units are considered as v41/v42;
        # because references are small the whole ranked list normally fits, which is
        # the intended effect: fewer omissions, no pasted bodies.
        limit = min(self.hard_tokens, max(self.target_tokens, fixed + 2048))
        from litellm import token_counter
        allowance = max(0, limit - fixed - 256)
        for unit in units:
            size = token_counter(model='gpt-4o', text=unit['text']) + 80
            if size <= allowance:
                selected.append(unit)
                allowance -= size
        result, after = candidate()
        while after > limit and selected:
            selected.pop()
            result, after = candidate()
        checked = View(events=list(result.apply(view.events)))
        checked.enforce_properties(view.events)
        if [e.id for e in checked.events] != [e.id for e in result.apply(view.events)]:
            self._audit.append({'before': before, 'skipped': 'structural_validation_failed'})
            return view
        if after >= before or after > self.hard_tokens:
            self._audit.append({'before': before, 'after': after, 'skipped': 'no_safe_budget_reduction'})
            return view
        self._archived_ids = ids
        self._summary_ids.add(result.summary_event.id)
        self._evidence_index = index
        self._selected_units = [{k: v for k, v in u.items() if k != 'text'} for u in selected]
        reference_chars = len(render(selected))
        body_chars = sum(len(u['text']) for u in selected)
        self._last_reference_chars = reference_chars
        self._reference_audit.append({
            'before': before, 'after': after, 'condensed': True,
            'selected_units': len(selected), 'omitted_units': len(units) - len(selected),
            'reference_chars': reference_chars, 'body_chars_avoided': body_chars,
            'chars_saved_vs_bodies': body_chars,
            'summary_chars': len(result.summary_event.summary)
            if hasattr(result.summary_event, 'summary') else reference_chars,
        })
        self._audit.append({'before': before, 'after': after,
                            'forgotten_ids': sorted(result.forgotten_event_ids),
                            'retained_ids': [e.id for e in view.events if e.id not in result.forgotten_event_ids],
                            'hard_budget_exceeded': False, 'protected_tokens': fixed,
                            'task_contract_chars': len(host_contract) + sum(map(len, contracts)),
                            'selected_units': len(selected),
                            'omitted_units': len(units) - len(selected),
                            'selection_limit': limit, 'confirmed_edit_notes': len(edit_notes),
                            'reference_only_chars': reference_chars,
                            'body_chars_avoided': body_chars,
                            'available_code_tokens': max(0, limit - fixed), **stats})
        return result

    def export_state(self):
        state = super().export_state()
        state['adapter'] = 'openhands-reference-only-v51-candidate'
        state['reference_audit'] = self._reference_audit
        return state
