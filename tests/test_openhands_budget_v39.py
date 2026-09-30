"""The first phase must release calls for host-feedback correction."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/openhands'))
from budget_policy_v39 import FeedbackReserveBudgetPolicy


def test_first_phase_closes_at_26_and_correction_gets_eight_work_calls():
    policy = FeedbackReserveBudgetPolicy(lambda: 'rev')
    assert policy.decide(23, 0, 1000).phase == 'work'
    assert policy.decide(23, 0, 1000).remaining_work_requests == 1
    assert policy.reserve_input(0) == 10 * 80000
    verify = policy.decide(24, 0, 1000)
    assert verify.phase == 'verify'
    assert policy.allowed_tools(verify, ['scoped_editor', 'scoped_tests', 'finish']) == {'scoped_tests'}
    policy.observe_test('{"passed": false, "code_revision": "rev"}')
    finish = policy.decide(25, 0, 1000)
    assert finish.phase == 'finish'
    assert policy.allowed_tools(finish, ['scoped_editor', 'scoped_tests', 'finish']) == {'finish'}
    assert policy.can_correct(26, 0)
    policy.begin_correction()
    assert policy.reserve_input(26) == 2 * 80000
    assert policy.decide(26, 0, 1000).phase == 'work'
    assert policy.decide(33, 0, 1000).phase == 'work'
    assert policy.decide(34, 0, 1000).phase == 'verify'
    assert policy.decide(35, 0, 1000).phase == 'finish'


def test_public_verification_can_end_first_phase_early():
    policy = FeedbackReserveBudgetPolicy(lambda: 'rev')
    policy.observe_test('{"passed": true, "code_revision": "rev"}')
    assert policy.decide(24, 0, 1000).phase == 'finish'
    assert policy.can_correct(25, 0)
    policy.begin_correction()
    assert not policy.is_verified
    assert policy.decide(25, 0, 1000).phase == 'work'
