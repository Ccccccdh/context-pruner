"""Budgeted source evidence with protected task files and explicit recovery paths.

Only observations already returned to the agent are used. Frozen v5 is untouched.
"""
from __future__ import annotations

import ast
import re
from pydantic import PrivateAttr
from openhands.sdk.context.condenser.utils import get_total_token_count
from openhands.sdk.context.view import View
from openhands.sdk.event.condenser import Condensation
from context_pruner.adapters.openhands_v5 import ContextPrunerCondenserV5, project_current_evidence
from context_pruner.adapters.openhands_v4 import semantic_text


def evidence_units(events, task_text):
    """Deduplicate/version with v5, then select atomic observed function spans."""
    messages, stats = project_current_evidence(events)
    files, failures = {}, []
    for message in messages:
        content = message['content']
        match = re.match(r'文件=(.*); version=(\d+); observed successful result\n', content)
        if not match:
            if content.startswith('Tool failed;'):
                failures.append(content)
            continue
        path, version = match.group(1), int(match.group(2))
        record = files.setdefault(path, {'version': version, 'lines': {}, 'sources': [], 'other': []})
        record['sources'] = message['_metadata']['sdk_source_ids']
        for line in content[match.end():].splitlines():
            numbered = re.match(r'^(\d+)\t(.*)$', line)
            if numbered:
                record['lines'][int(numbered.group(1))] = numbered.group(2)
            else:
                record['other'].append(line)
    contracts, units, index = [], [], []
    terms = set(re.findall(r'[a-zA-Z_][a-zA-Z_0-9]*', task_text.lower()))
    for path, record in files.items():
        lines = record['lines']
        if path.replace('\\', '/').rsplit('/', 1)[-1].upper() == 'TASK.MD':
            contracts.append('\n'.join(lines[n] for n in sorted(lines)))
            continue
        if not lines:
            continue  # Directory listings are not durable code evidence.
        lo, hi = min(lines), max(lines)
        index.append({'path': path, 'version': record['version'], 'range': [lo, hi],
                      'sdk_source_ids': record['sources']})
        # Blank positions represent unobserved lines, never fabricated code.
        source = '\n'.join(lines.get(n, '') for n in range(1, hi + 1))
        spans = []
        try:
            tree = ast.parse(source) if path.endswith('.py') else None
        except (SyntaxError, ValueError):
            tree = None
        covered = set()
        if tree:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                    end = node.end_lineno
                    spans.append((start, end, node.name))
                    covered.update(range(start, end + 1))
            # Preserve imports, class headers and module state in small excerpts.
        if not tree and path.endswith('.py'):
            # Sparse class excerpts cannot be parsed as whole modules. Keep each
            # observed method span together using indentation and contiguous lines.
            ordered = sorted(lines)
            for start in ordered:
                header = re.match(r'^(\s*)(?:async\s+)?def\s+(\w+)\s*\(', lines[start])
                if not header or start in covered:
                    continue
                indent, name = len(header.group(1).expandtabs(8)), header.group(2)
                end = start
                header_done = lines[start].rstrip().endswith(':')
                for n in range(start + 1, hi + 1):
                    if n not in lines:
                        break
                    text = lines[n]
                    depth = len(text[:len(text) - len(text.lstrip())].expandtabs(8))
                    if not header_done:
                        end = n
                        header_done = text.rstrip().endswith(':')
                        continue
                    if text.strip() and depth <= indent and not text.lstrip().startswith('#'):
                        break
                    end = n
                spans.append((start, end, name))
                covered.update(range(start, end + 1))
        remaining = [n for n in sorted(lines) if n not in covered]
        block = []
        for n in remaining:
            if block and (n != block[-1] + 1 or len(block) >= 40):
                spans.append((block[0], block[-1], 'module_context')); block = []
            block.append(n)
        if block:
            spans.append((block[0], block[-1], 'module_context'))
        for start, end, name in spans:
            observed = [n for n in range(start, end + 1) if n in lines]
            if not observed:
                continue
            text = '\n'.join(f'{n}\t{lines[n]}' if n in lines else '# [unobserved line]' for n in range(start, end + 1))
            words = set(re.findall(r'[a-zA-Z_][a-zA-Z_0-9]*', text.lower()))
            name_words = set(name.lower().split('_')) - {''}
            score = (100 if name.lower() in terms and name != 'module_context' else 0)
            score += 10 * len(name_words & terms) + min(20, len(words & terms))
            score += 4 if record['version'] else 0
            score += 2 if '/tests/' not in '/' + path else 0
            if name == 'module_context':
                score -= 8
            units.append({'path': path, 'version': record['version'], 'range': [start, end],
                          'name': name, 'score': score, 'text': text,
                          'sdk_source_ids': record['sources']})
    # Reserve affordable named API evidence before large individual functions.
    # The score density uses observed characters only; no unobserved source.
    units.sort(key=lambda u: (u['name'] == 'module_context',
                              -u['score'] / max(1, len(u['text']) / 400) ** 0.5,
                              u['path'], u['range'][0]))
    # Preserve confirmed public-test feedback, never substitute agent claims.
    verification = [semantic_text(e.to_llm_message()) for e in events
                    if hasattr(e, 'observation') and getattr(e, 'tool_name', '') == 'scoped_tests']
    failures.extend('Observed public verification at that event; later edits invalidate the result:\n' + text for text in verification[-2:])
    return contracts, units, index, failures[-3:], stats


class ContextPrunerCondenserV9(ContextPrunerCondenserV5):
    """OpenHands view adapter with a protected recent editing window.

    Defaults match the v21 development validation. Full lifecycle restoration
    and broad quality equivalence are not claimed by this adapter.
    """
    trigger_tokens: int = 24000
    target_tokens: int = 20000
    hard_tokens: int = 32000
    _evidence_index: list = PrivateAttr(default_factory=list)
    _selected_units: list = PrivateAttr(default_factory=list)

    def condense(self, view, agent_llm=None):
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
        ends = [i for i in view.manipulation_indices if 2 < i < len(view.events) - 1]
        if 2 not in view.manipulation_indices or not ends:
            return view
        # Keep up to four complete recent tool batches as a live editing window.
        # Shrink only at SDK-safe boundaries if that window consumes the hard
        # budget; reserve room for protected task text and the evidence index.
        end = max(ends)
        for proposed in sorted(ends)[-4:]:
            older_users = [e for e in view.events[2:proposed]
                           if type(e).__name__ == 'MessageEvent' and e.to_llm_message().role == 'user']
            retained = list(view.events[:2]) + older_users + list(view.events[proposed:])
            if get_total_token_count(retained, agent_llm) <= self.hard_tokens - 5000:
                end = proposed
                break
        users = [e for e in view.events[2:end] if type(e).__name__ == 'MessageEvent' and e.to_llm_message().role == 'user']
        protected = {e.id for e in users}
        forgotten = [e for e in view.events[2:end] if e.id not in protected]
        if not forgotten or all(type(e).__name__ == 'CondensationSummaryEvent' for e in forgotten):
            return view
        ids = self._archived_ids | {e.id for e in forgotten if e.id in self._original_events}
        originals = [e for key, e in self._original_events.items() if key in ids]
        user_text = '\n'.join(semantic_text(e.to_llm_message()) for e in view.events
                              if type(e).__name__ == 'MessageEvent' and e.to_llm_message().role == 'user')
        contracts, _, _, _, _ = evidence_units(originals, user_text)
        query = user_text + '\n' + '\n'.join(contracts)
        contracts, units, index, failures, stats = evidence_units(originals, query)
        prefix = ('Archived source evidence; immutable original SDK events retained. '
                  'Newer retained successful edits supersede these prefix versions. '
                  'Omitted code is NOT deleted. Recover it using scoped_editor view with the path and range below.\n'
                  'PROTECTED TASK CONTRACT (verbatim observed TASK.md):\n' + '\n'.join(contracts) + '\n'
                  'OBSERVED FILE INDEX (ranges can contain unobserved gaps):\n' +
                  '\n'.join(f"{r['path']} version={r['version']} view_range={r['range']}" for r in index) + '\n' +
                  '\n'.join(failures) + '\nSELECTED OBSERVED CODE:\n')
        selected = []
        def candidate():
            memory = prefix + '\n'.join(f"{u['path']} version={u['version']} {u['name']} range={u['range']}\n{u['text']}" for u in selected)
            result = Condensation(forgotten_event_ids={e.id for e in forgotten}, summary=memory,
                                  summary_offset=2, llm_response_id='context-pruner-local-v9')
            return result, get_total_token_count(list(result.apply(view.events)), agent_llm)
        result, fixed = candidate()
        if fixed > self.hard_tokens:
            self._audit.append({'before': before, 'skipped': 'protected_context_exceeds_hard_budget', 'protected_tokens': fixed})
            return view  # Never silently truncate required instructions/latest batch.
        limit = max(self.target_tokens, fixed)
        from litellm import token_counter
        allowance = max(0, limit - fixed - 256)
        for unit in units:
            size = token_counter(model='gpt-4o', text=unit['text']) + 80
            if size <= allowance:
                selected.append(unit); allowance -= size
        result, after = candidate()
        while after > limit and selected:
            selected.pop(); result, after = candidate()
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
        self._audit.append({'before': before, 'after': after, 'forgotten_ids': sorted(result.forgotten_event_ids),
                            'retained_ids': [e.id for e in view.events if e.id not in result.forgotten_event_ids],
                            'hard_budget_exceeded': False, 'protected_tokens': fixed,
                            'task_contract_chars': sum(len(c) for c in contracts), 'selected_units': len(selected),
                            'omitted_units': len(units) - len(selected), **stats})
        return result

    def export_state(self):
        return {'adapter': 'openhands-condenser-v9', 'audit': self._audit,
                'archived_sdk_source_ids': sorted(self._archived_ids),
                'evidence_index': self._evidence_index, 'selected_units': self._selected_units,
                'middleware': None}
