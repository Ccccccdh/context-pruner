"""v54 branch budget: the shared prefix's consumed requests are never re-issued.

The v54 same-prefix experiment forks one *real* conversation after a shared
prefix has already spent requests.  Every branch must therefore inherit the
request/input capacity the prefix already consumed: a branch may not receive a
fresh 36-request allowance, and the correction boundary (the prefix's work
ceiling plus the frozen correction limit) is fixed at the fork point rather
than recomputed from the branch's own call count.

`StrictFeedbackReserveBudgetPolicy` (frozen v42) already implements the
"correction phase observes a cumulative call counter" contract.  This module
adds the carry-over: the branch ledger is branch-local, and the frozen classes
are called with cumulative counters, so their thresholds keep exactly the
meaning they have in a single-start sample.  Nothing in the frozen
v30/v39/v42/v53 files is modified; this is a new module.

The tool allow-list is restated with v54's own tool names on purpose.  An
earlier v54 revision reused the bare names ``scoped_editor`` / ``scoped_tests``
while the SDK's tool registry is a process-global name map, so a *different*
module's tool registered under the same name either took over the v54 boundary
or was silently overwritten by it.  Distinct names keep the two surfaces apart
without editing the older, already-published policy module.
"""
from __future__ import annotations

import contextlib
from dataclasses import replace

from budget_policy_v42 import StrictFeedbackReserveBudgetPolicy

#: The tool names this policy allows.  They must equal the names the v54 runner
#: registers; a zero-API gate asserts the two agree.
TOOL_NAMES = frozenset({'scoped_editor', 'scoped_symbols', 'scoped_tests'})

#: Prefix-policy fields captured as fork-point evidence.
CAPTURED_STATE = ('last_attempt_revision', 'verified_revision', 'passed')


class PrefixCarryingBudgetPolicy(StrictFeedbackReserveBudgetPolicy):
    """A branch policy whose counters continue the shared prefix's counters.

    The branch LLM's ledger holds only the branch's own requests, so every
    argument arriving here is branch-local.  The frozen parent classes are
    never modified: they are simply called with counters that already include
    what the prefix spent, which is what makes "36 requests in total" and
    "the first phase ends after 24 work requests" mean the same thing for a
    branch as they did for the prefix.
    """

    def __init__(self, current_revision, *, prefix_used_agent_calls=0,
                 prefix_used_estimated_input=0, **kwargs):
        if prefix_used_agent_calls < 0 or prefix_used_estimated_input < 0:
            raise ValueError('Prefix usage must not be negative')
        super().__init__(current_revision, **kwargs)
        if prefix_used_agent_calls >= self.max_agent_calls:
            raise ValueError(
                'Prefix already consumed every agent request; a branch cannot '
                'receive a fresh allowance in the same experiment')
        self.prefix_used_agent_calls = prefix_used_agent_calls
        self.prefix_used_estimated_input = prefix_used_estimated_input
        # Fix the correction boundary at the fork point.  `begin_correction`
        # would otherwise derive it from the branch's own counter, silently
        # giving the branch the whole correction allowance again.
        self.correction_stop_at = min(
            self.max_agent_calls,
            prefix_used_agent_calls + self.correction_call_limit)

    # -- cumulative counters -------------------------------------------------
    def cumulative_agent_calls(self, used_agent_calls: int) -> int:
        return self.prefix_used_agent_calls + used_agent_calls

    def cumulative_estimated_input(self, used_input: int) -> int:
        return self.prefix_used_estimated_input + used_input

    @contextlib.contextmanager
    def _cumulative_ceilings(self):
        """Expose the shared experiment's ceilings to the frozen parent code.

        With `max_agent_calls` raised by the prefix's request count and
        `max_total_input` by the prefix's estimated input, the inherited
        arithmetic ("remaining = max - used") is correct while every counter
        the parent sees is cumulative.  The instance ceilings are restored
        afterwards, so the frozen values stay the protocol's values.
        """
        original_calls, original_input = self.max_agent_calls, self.max_total_input
        self.max_agent_calls = original_calls + self.prefix_used_agent_calls
        self.max_total_input = original_input + self.prefix_used_estimated_input
        try:
            yield
        finally:
            self.max_agent_calls, self.max_total_input = original_calls, original_input

    # -- frozen behaviour, cumulative inputs ---------------------------------
    def check_ceiling(self, used_agent_calls):
        """Refuse a branch request that would exceed the shared request budget.

        ``used_agent_calls`` counts the branch's completed requests, so the
        request about to be made is ``cumulative + 1``; the shared ceiling
        allows it while that total is at most ``max_agent_calls``.
        """
        if self.cumulative_agent_calls(used_agent_calls) >= self.max_agent_calls:
            raise RuntimeError('Frozen experiment call/token limit')

    def decide(self, used_agent_calls, used_input, estimated_request):
        # The shared ceiling is enforced here rather than through the parent's
        # range arithmetic: the parent is called with raised instance ceilings
        # (so its phase arithmetic is cumulative), which would otherwise let a
        # branch pass the protocol's request limit.
        self.check_ceiling(used_agent_calls)
        with self._cumulative_ceilings():
            decision = super().decide(self.cumulative_agent_calls(used_agent_calls),
                                      self.cumulative_estimated_input(used_input),
                                      estimated_request)
        return replace(decision, remaining_requests=self.max_agent_calls - used_agent_calls,
                       remaining_estimated_input=self.max_total_input - used_input)

    def can_correct(self, used_agent_calls, used_input):
        with self._cumulative_ceilings():
            return super().can_correct(self.cumulative_agent_calls(used_agent_calls),
                                       self.cumulative_estimated_input(used_input))

    def reserve_input(self, used_agent_calls):
        with self._cumulative_ceilings():
            return super().reserve_input(self.cumulative_agent_calls(used_agent_calls))

    def begin_correction(self, used_agent_calls):
        """Open the correction phase at the prefix's cumulative position.

        The boundary was already fixed in ``__init__``, so the frozen
        implementation's "no boundary yet" precondition is momentarily
        satisfied and its guards still run; the fixed boundary is restored
        afterwards, which is what stops a branch from re-deriving its own
        allowance.  Its ``used < max`` guard is given the shared ceiling, since
        a prefix that already spent more than the correction reserve is a valid
        fork point, not an invalid position.
        """
        original_calls, original_stop = self.max_agent_calls, self.correction_stop_at
        self.max_agent_calls = original_calls + self.prefix_used_agent_calls
        self.correction_stop_at = None
        try:
            super().begin_correction(self.cumulative_agent_calls(used_agent_calls))
        finally:
            self.max_agent_calls = original_calls
            self.correction_stop_at = original_stop

    # -- tool allow-list (v54 names) -----------------------------------------
    @staticmethod
    def allowed_tools(decision, offered_names):
        """`budget_policy_v31.allowed_tools` with v54's tool names.

        The names are identical to the frozen v31 set; this override exists so the
        allow-list is defined in one v54 place that the runner, the zero-API gate
        and the manifest all check against, instead of depending on an older
        module staying unedited.  The phase logic and the "finish only after
        current public verification" rule are copied unchanged; the `finish`
        phase still defers to the frozen base implementation.
        """
        from budget_policy_v30 import BudgetPolicy
        offered = set(offered_names)
        if decision.phase == 'verify':
            allowed = {'scoped_tests'} & offered
        elif decision.phase == 'work':
            allowed = {'scoped_editor', 'scoped_tests', 'scoped_symbols'} & offered
            if decision.public_verification != 'passed_current':
                allowed.discard('finish')
        else:
            return BudgetPolicy.allowed_tools(decision, offered_names)
        if not allowed:
            raise RuntimeError('Required budget-phase tool unavailable')
        return allowed

    # -- fork-point evidence -------------------------------------------------
    def capture_prefix_state(self) -> dict:
        """Record where the shared prefix stopped, as frozen audit evidence.

        This is not branch input: a branch's own policy starts at the fork
        point.  Re-applying the prefix's public-verification state or its
        first-phase closing reason would let the prefix's phase accounting
        decide the branch's phases.
        """
        state = {name: getattr(self, name) for name in CAPTURED_STATE}
        state['in_correction'] = self.in_correction
        state['closing_reason'] = self.closing_reason
        return state

    def check_prefix_state(self, state: dict) -> None:
        """Refuse a fork point that is not a valid branch start."""
        missing = [name for name in (*CAPTURED_STATE, 'in_correction', 'closing_reason')
                   if name not in state]
        if missing:
            raise ValueError(f'prefix policy state is incomplete: {missing}')
        if state['in_correction']:
            raise ValueError('prefix ended inside its correction phase')
