"""v55 prefix selection and the zero-API trigger prediction.

Two v54 defects are fixed here, and both are fixed *before* any paid request:

1. **The prefix did not need correction.**  v54 ran one committed task; its
   first phase ended with the host target test already passing, so there was
   nothing left to correct and the branch correction phase had no work to do.
   v55 walks a *pre-registered* sequence of ``(rung, task)`` attempts over the
   three frozen tasks in their frozen order and selects the first attempt whose
   phase-1 end state **fails the host target test**.  The ladder lengthens the
   phase-1 work budget only within the frozen limits
   (``initial_work_requests`` then ``first_phase_max_requests``).  Nothing about
   the selection depends on the branches, which have not run yet.

2. **The trigger prediction was arithmetic and wrong.**  v54 compared the
   ledger's *estimated input* of the final prefix call (28,275) with the frozen
   trigger (28,000) and concluded "above the trigger".  The condenser does not
   measure that quantity: it counts tokens of the **SDK view's events**, tools
   included, through the model's own tokeniser.  Replaying the frozen v54 prefix
   offline gives **22,213** view tokens - 5,787 *below* the trigger, exactly
   matching the recorded zero condensations.  v55 therefore keeps the
   pre-registered ledger margin **and** gates on an exact, zero-API replay of the
   condenser on the prefix state the branch will actually see first.

Both functions are pure: no provider client is created and no request is made.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

#: The frozen rungs: phase-1 work-request caps, in the order they are tried.
#: Rung 0 is v53/v54's ``initial_work_requests``; rung 1 is the frozen
#: ``first_phase_max_requests`` ceiling.  No rung exceeds the shared 36-request
#: sample ceiling, and the shared ceiling itself is never raised.
DEFAULT_LADDER = (24, 26)
#: Recorded phase-1 request ceilings per rung (work cap + closing reserve).
DEFAULT_RUNG_REQUEST_CEILING = (26, 28)


def attempt_plan(protocol: dict) -> list[tuple[int, int, str]]:
    """The full pre-registered ``(rung, work_cap, task)`` sequence.

    Rung-major and task-major in the frozen order: all three tasks are attempted
    at the first rung before the ladder lengthens the phase-1 budget, which is
    what "run the none arm for the first phase on the three frozen tasks in their
    frozen order ... if none fails, extend the phase-1 request budget by the
    pre-registered step" means.
    """
    ladder = tuple(protocol['phase1_work_request_ladder'])
    plan = []
    for rung, work_cap in enumerate(ladder):
        for task in protocol['tasks']:
            plan.append((rung, work_cap, task))
    return plan


def next_step(protocol: dict, attempts: Sequence[dict]) -> tuple[int, int, str] | None:
    """The next attempt to run, given what has already been recorded.

    A task that already **passed** the host target test at any earlier attempt is
    not re-attempted: the selection rule needs a failing end state, so re-running
    a fixed task cannot change the decision and would only spend money.  Every
    skip is recorded by the runner with this reason.
    """
    passed_tasks = {row['task'] for row in attempts if row.get('host_passed') is True}
    done = {(row['rung'], row['task']) for row in attempts}
    for rung, work_cap, task in attempt_plan(protocol):
        if task in passed_tasks or (rung, task) in done:
            continue
        return (rung, work_cap, task)
    return None


def selection_decision(protocol: dict, attempts: Sequence[dict]) -> dict:
    """The pre-registered decision over the recorded attempts.

    "First" means first in the frozen attempt order, not first by cost or by any
    measured outcome of the branches.  An attempt qualifies only when its
    phase-1 end state fails the host target test **and** the frozen zero-API
    prediction says the plugin arm's condenser triggers on it.
    """
    order = {(rung, task): index
             for index, (rung, _cap, task) in enumerate(attempt_plan(protocol))}
    ordered = sorted(attempts, key=lambda row: order.get((row['rung'], row['task']), 10 ** 6))
    considered = []
    for row in ordered:
        entry = {
            'task': row['task'], 'rung': row['rung'], 'work_cap': row['work_cap'],
            'host_passed': row.get('host_passed'),
            'prediction_triggered': bool((row.get('trigger_prediction') or {}).get('triggered')),
        }
        considered.append(entry)
        if row.get('host_passed') is False and entry['prediction_triggered']:
            return {
                'selected': True,
                'task': row['task'], 'rung': row['rung'], 'work_cap': row['work_cap'],
                'attempt': row,
                'considered': considered,
                'reason': ('first attempt in the frozen order whose phase-1 end state fails the '
                           'host target test and whose frozen prediction says the plugin '
                           'condenser triggers'),
            }
        if row.get('host_passed') is True:
            entry['rejection'] = 'host target test passed; the prefix would need no correction'
        elif not entry['prediction_triggered']:
            entry['rejection'] = ('plugin condenser predicted NOT to trigger on this prefix '
                                  'state (see trigger_prediction)')
        else:
            entry['rejection'] = 'recorded end state missing'
    return {
        'selected': False, 'task': None, 'rung': None, 'work_cap': None,
        'attempt': None, 'considered': considered,
        'reason': ('no attempt in the frozen ladder both failed the host target test and '
                   'satisfied the frozen trigger prediction: mechanism still not triggered'),
    }


# --------------------------------------------------------------------------
# exact zero-API trigger prediction
# --------------------------------------------------------------------------

def correction_message_event(text: str) -> Any:
    """The exact user event ``send_message(correction_prompt(...))`` produces."""
    from openhands.sdk.event.llm_convertible import MessageEvent
    from openhands.sdk.llm import Message, TextContent
    return MessageEvent(source='user',
                        llm_message=Message(role='user', content=[TextContent(text=text)]))


def _view(events: Sequence[Any]) -> Any:
    from openhands.sdk.context.view import View
    return View.from_events(list(events))


def predict_trigger(*, events: Sequence[Any], protocol: dict, task: str,
                    allowed: list[str], correction_text: str,
                    condenser_factory, current_state: str = '',
                    ledger_last_estimated_input: int | None = None) -> dict:
    """Measure, offline, whether the plugin condenser triggers on the prefix state.

    ``events`` are the live SDK events of the prefix conversation (the same
    objects the branch's first ``run()`` step will see), and ``correction_text``
    is the frozen correction prompt every branch receives.  The view is built with
    ``View.from_events`` - the SDK's own constructor - and the *real* configured
    condenser is run on it with a throwaway LLM used only for token counting.
    """
    from openhands.sdk import LLM
    from openhands.sdk.context.condenser.utils import get_total_token_count

    trigger = protocol['trigger_input_tokens']
    measured_margin = protocol['trigger_prediction_margin_tokens']
    ledger_margin = protocol['trigger_prediction_ledger_margin_tokens']

    llm = LLM(model=protocol['model'], api_key='unused')
    view = _view([*events, correction_message_event(correction_text)])
    measured = get_total_token_count(view.events, llm)
    condenser = condenser_factory()
    if current_state:
        condenser.set_current_state(current_state)
    result = condenser.condense(view, agent_llm=llm)
    replay = type(result).__name__
    replay_audit = None
    try:
        replay_audit = condenser.export_state().get('audit')
    except Exception:  # the audit trail is evidence, never a gate by itself
        replay_audit = None

    measured_ok = measured >= trigger + measured_margin
    ledger_ok = (ledger_last_estimated_input is not None
                 and ledger_last_estimated_input >= trigger + ledger_margin)
    triggered = replay == 'Condensation' and measured_ok and ledger_ok
    reasons = []
    if replay != 'Condensation':
        reasons.append(f'replayed condenser returned {replay}, not a Condensation')
    if not measured_ok:
        reasons.append(f'view tokens {measured} < trigger {trigger} + margin {measured_margin}')
    if not ledger_ok:
        reasons.append(
            f'ledger final estimated input {ledger_last_estimated_input} < trigger {trigger} '
            f'+ margin {ledger_margin}')
    return {
        'trigger_input_tokens': trigger,
        'view_event_count': len(view.events),
        'measured_view_tokens': measured,
        'measured_margin_tokens': measured - trigger,
        'required_measured_margin_tokens': measured_margin,
        'measured_ok': measured_ok,
        'ledger_last_estimated_input': ledger_last_estimated_input,
        'required_ledger_margin_tokens': ledger_margin,
        'ledger_ok': ledger_ok,
        'condenser_replay_result': replay,
        'condenser_replay_audit': replay_audit,
        'triggered': triggered,
        'reasons': reasons,
        'method': ('View.from_events(prefix events + the frozen correction user message), then '
                   'the real configured condenser is run on that view with a throwaway LLM used '
                   'only for token counting; no provider request is issued'),
    }


def ledger_last_estimated_input(ledger: dict) -> int | None:
    """The recorded estimated input of the prefix's final agent call."""
    calls = [call for call in (ledger.get('calls') or []) if call.get('kind') == 'agent']
    if not calls:
        return None
    return calls[-1].get('estimated_input')


# --------------------------------------------------------------------------
# frozen rule text (kept next to the code so the protocol document and the
# implementation cannot drift apart silently)
# --------------------------------------------------------------------------

FROZEN_SELECTION_RULE = (
    'Attempts run in the pre-registered order: rung 0 (work cap 24) over the three frozen '
    'tasks in their frozen order, then rung 1 (work cap 26) again in that order, and no '
    'further rung. A task that already passed the host target test is not re-attempted. An '
    'attempt qualifies only when its phase-1 end state FAILS the host target test and the '
    'frozen zero-API prediction reports that the plugin condenser would condense on that '
    'prefix state; the first qualifying attempt in that order is selected, whatever the '
    'branches later do. If no attempt qualifies, the round reports "mechanism still not '
    'triggered", no branch request is issued and the mechanism thresholds are not touched.'
)


def rule_fingerprint(protocol: dict) -> str:
    """A digest of the frozen rule inputs, so the report can pin what was applied."""
    import hashlib
    payload = {
        'rule': FROZEN_SELECTION_RULE,
        'tasks': list(protocol['tasks']),
        'ladder': list(protocol['phase1_work_request_ladder']),
        'trigger_input_tokens': protocol['trigger_input_tokens'],
        'trigger_prediction_margin_tokens': protocol['trigger_prediction_margin_tokens'],
        'trigger_prediction_ledger_margin_tokens':
            protocol['trigger_prediction_ledger_margin_tokens'],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
