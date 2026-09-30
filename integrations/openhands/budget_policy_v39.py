"""Two-phase request budget for the Django host-feedback pilot.

The total 36 Agent request limit remains unchanged. The first conversation
must close by request 26 (up to 24 work requests, then public verification and
Finish). Once the host has run feature acceptance, begin_correction() releases
the remaining requests while the original final verification/Finish reserve
continues to apply. This policy is shared by all three arms.
"""
from dataclasses import replace
from budget_policy_v31 import NavigationBudgetPolicy


class FeedbackReserveBudgetPolicy(NavigationBudgetPolicy):
    def __init__(self, current_revision, *, initial_work_requests=24, **kwargs):
        super().__init__(current_revision, **kwargs)
        if not 1 <= initial_work_requests <= self.max_agent_calls - 4:
            raise ValueError('Initial work limit must leave verification and correction capacity')
        self.initial_work_requests = initial_work_requests
        self.in_correction = False

    def reserve_input(self, used_agent_calls):
        if not self.in_correction:
            correction_calls = self.max_agent_calls - self.initial_work_requests - 2
            return min(correction_calls, self.max_agent_calls - used_agent_calls) * self.max_single_input
        return super().reserve_input(used_agent_calls)

    def decide(self, used_agent_calls, used_input, estimated_request):
        if not self.in_correction and used_agent_calls >= self.initial_work_requests:
            self.closing_reason = self.closing_reason or 'initial_phase_feedback_reserve'
        decision = super().decide(used_agent_calls, used_input, estimated_request)
        if not self.in_correction and decision.phase == 'work':
            decision = replace(decision, remaining_work_requests=min(
                decision.remaining_work_requests,
                self.initial_work_requests - used_agent_calls))
        return decision

    def begin_correction(self):
        self.in_correction = True
        super().begin_correction()
