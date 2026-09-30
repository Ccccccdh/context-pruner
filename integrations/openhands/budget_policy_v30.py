"""Observed request/verification state, shared equally by all experiment arms."""
from dataclasses import dataclass, asdict
import json
from typing import Callable

@dataclass(frozen=True)
class BudgetDecision:
    phase: str
    remaining_requests: int
    remaining_work_requests: int
    remaining_estimated_input: int
    public_verification: str
    reason: str

class BudgetPolicy:
    def __init__(self, current_revision: Callable[[], str], *, max_agent_calls=36,
                 reserve_requests=2, max_single_input=80000,
                 max_total_input=2000000, single_input_headroom=12000):
        if reserve_requests != 2 or max_agent_calls < 3:
            raise ValueError('Reserve one verification and one Finish request')
        if not 0 < single_input_headroom < max_single_input:
            raise ValueError('Invalid input headroom')
        self.current_revision = current_revision
        self.max_agent_calls = max_agent_calls
        self.reserve_requests = reserve_requests
        self.max_single_input = max_single_input
        self.max_total_input = max_total_input
        self.single_input_headroom = single_input_headroom
        self.closing_reason = None
        self.last_attempt_revision = None
        self.verified_revision = None
        self.passed = False

    @property
    def is_verified(self):
        return self.passed and self.verified_revision == self.current_revision()

    def observe_test(self, text, *, is_error=False):
        # Tool output only, never model claims. A failed attempt can still close
        # honestly; it cannot turn a failed public result into success.
        self.last_attempt_revision = self.current_revision()
        self.passed = False
        self.verified_revision = None
        if is_error:
            return
        decoder = json.JSONDecoder()
        for i, char in enumerate(text):
            if char != '{':
                continue
            try:
                value, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get('passed'), bool) and isinstance(value.get('code_revision'), str):
                self.passed = value['passed']
                self.verified_revision = value['code_revision']
                return

    def observe_event(self, event):
        if getattr(event, 'tool_name', None) != 'scoped_tests' or not hasattr(event, 'observation'):
            return
        observation = event.observation
        text = '\n'.join(getattr(item, 'text', '') for item in observation.to_llm_content)
        self.observe_test(text, is_error=observation.is_error)

    def reserve_input(self, used_agent_calls):
        return min(self.reserve_requests, max(0, self.max_agent_calls-used_agent_calls)) * self.max_single_input

    def can_correct(self, used_agent_calls, used_input):
        return (used_agent_calls < self.max_agent_calls-self.reserve_requests
                and used_input + self.reserve_input(used_agent_calls) + self.max_single_input <= self.max_total_input)

    def begin_correction(self):
        self.closing_reason = None
        self.last_attempt_revision = None
        self.verified_revision = None
        self.passed = False

    def decide(self, used_agent_calls, used_input, estimated_request):
        left = self.max_agent_calls-used_agent_calls
        if left <= 0:
            raise RuntimeError('Frozen experiment call/token limit')
        if left <= self.reserve_requests:
            self.closing_reason = self.closing_reason or 'request_reserve'
        if used_input + estimated_request + self.reserve_input(used_agent_calls) > self.max_total_input:
            self.closing_reason = self.closing_reason or 'total_input_reserve'
        if estimated_request >= self.max_single_input-self.single_input_headroom:
            self.closing_reason = self.closing_reason or 'single_input_headroom'
        current = self.current_revision()
        verified = self.is_verified
        if not self.closing_reason:
            phase = 'work'
        elif verified or self.last_attempt_revision == current or left == 1:
            phase = 'finish'
        else:
            phase = 'verify'
        return BudgetDecision(phase, left,
            max(0, self.max_agent_calls-self.reserve_requests-used_agent_calls) if phase == 'work' else 0,
            max(0, self.max_total_input-used_input),
            'passed_current' if verified else ('attempted_not_passed' if self.last_attempt_revision == current else 'not_currently_verified'),
            self.closing_reason or 'work_capacity_available')

    @staticmethod
    def allowed_tools(decision, offered_names):
        if decision.phase == 'verify':
            names = {'scoped_tests'}
        elif decision.phase == 'finish':
            names = {'finish'}
        else:
            names = {'scoped_editor', 'scoped_tests', 'finish'} & set(offered_names)
            if decision.public_verification != 'passed_current':
                names.discard('finish')
        allowed = names & set(offered_names)
        if not allowed:
            raise RuntimeError('Required budget-phase tool unavailable')
        return allowed

    @staticmethod
    def notice(decision):
        state = json.dumps(asdict(decision), sort_keys=True)
        instruction = {'work': 'Plan within remaining work requests. Reserve verification and Finish. Once ready code passes scoped_tests, finish promptly.',
                       'verify': 'Work budget is closed. Call scoped_tests now. Reading and editing are unavailable in this reserved request.',
                       'finish': 'Call finish now. No reading, editing or testing is available. If public verification is not passed_current, explain incomplete/failed verification honestly.'}[decision.phase]
        return ('HOST RUNTIME BUDGET (remaining requests INCLUDE this request; estimated input is not a bill): '
                + state + '\n' + instruction
                + ' Public regression success does not imply host feature acceptance. Later edits invalidate verification.')
