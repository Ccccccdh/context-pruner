"""A short first phase must not silently enlarge the correction phase."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/openhands'))

import pytest

from budget_policy_v42 import StrictFeedbackReserveBudgetPolicy


def policy():
    return StrictFeedbackReserveBudgetPolicy(lambda: 'r1',
        initial_work_requests=24, max_agent_calls=36,
        correction_call_limit=10, reserve_requests=2,
        max_single_input=80000, max_total_input=2000000,
        single_input_headroom=12000)


def test_early_first_finish_still_allows_only_ten_correction_requests():
    budget = policy()
    budget.begin_correction(20)
    assert budget.decide(20, 0, 20000).remaining_requests == 10
    assert budget.decide(20, 0, 20000).remaining_work_requests == 8
    assert budget.decide(28, 0, 20000).phase == 'verify'
    last = budget.decide(29, 0, 20000)
    assert last.phase == 'finish' and last.remaining_requests == 1
    with pytest.raises(RuntimeError, match='correction request limit'):
        budget.decide(30, 0, 20000)


def test_full_first_phase_uses_global_limit_and_never_restarts_correction():
    budget = policy()
    budget.begin_correction(26)
    assert budget.decide(26, 0, 20000).remaining_requests == 10
    assert budget.decide(35, 0, 20000).phase == 'finish'
    with pytest.raises(RuntimeError, match='already started'):
        budget.begin_correction(27)
