"""Enforce a fixed host-feedback correction limit even after an early Finish."""
from dataclasses import replace

from budget_policy_v39 import FeedbackReserveBudgetPolicy


class StrictFeedbackReserveBudgetPolicy(FeedbackReserveBudgetPolicy):
    def __init__(self, current_revision, *, correction_call_limit=10, **kwargs):
        super().__init__(current_revision, **kwargs)
        if correction_call_limit < self.reserve_requests + 1:
            raise ValueError('Correction limit must leave work, verification and Finish')
        self.correction_call_limit = correction_call_limit
        self.correction_stop_at = None

    def begin_correction(self, used_agent_calls):
        if self.in_correction or self.correction_stop_at is not None:
            raise RuntimeError('Correction phase already started')
        if not 0 <= used_agent_calls < self.max_agent_calls:
            raise ValueError('Invalid correction start count')
        self.correction_stop_at = min(
            self.max_agent_calls, used_agent_calls + self.correction_call_limit)
        super().begin_correction()

    def reserve_input(self, used_agent_calls):
        if self.in_correction:
            left = max(0, self.correction_stop_at - used_agent_calls)
            return min(self.reserve_requests, left) * self.max_single_input
        return super().reserve_input(used_agent_calls)

    def decide(self, used_agent_calls, used_input, estimated_request):
        if not self.in_correction:
            return super().decide(used_agent_calls, used_input, estimated_request)
        left = self.correction_stop_at - used_agent_calls
        if left <= 0:
            raise RuntimeError('Frozen correction request limit')
        if left <= self.reserve_requests:
            self.closing_reason = self.closing_reason or 'correction_phase_reserve'
        decision = super().decide(used_agent_calls, used_input, estimated_request)
        phase = 'finish' if left == 1 else decision.phase
        return replace(decision, phase=phase, remaining_requests=left,
                       remaining_work_requests=(min(decision.remaining_work_requests,
                                                     max(0, left - self.reserve_requests))
                                                if phase == 'work' else 0),
                       reason=self.closing_reason or decision.reason)
