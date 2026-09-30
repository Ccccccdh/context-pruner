"""Failure feedback must retain every named failure when log tails cannot."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations/openhands'))
from feedback_v40 import format_test_feedback


def test_all_failure_names_survive_long_tracebacks_and_paths_are_compact():
    root = r'C:\experiment\host-workspace'
    sections = []
    for number in range(1, 18):
        kind = 'ERROR' if number % 2 else 'FAIL'
        sections.append(
            '=' * 70 + '\n'
            + f'{kind}: test_case_{number} (aggregation.tests.Case)\n'
            + '-' * 70 + '\nTraceback (most recent call last):\n'
            + (f'  File "{root}\\django\\db\\models\\sql\\query.py", line 450\n' * 20)
            + f'AssertionError: diagnostic {number}\n')
    log = '\n'.join(sections) + '\nRan 120 tests in 0.3s\nFAILED (failures=8, errors=9)\n'
    assert re.search(r'(?m)^(?:FAIL|ERROR): test_case_1 \(', log[-4000:]) is None
    feedback = format_test_feedback(log, workspace_root=root)
    for number in range(1, 18):
        assert f'test_case_{number}' in feedback
    assert 'diagnostic 1' in feedback
    assert '<workspace>/django/db/models/sql/query.py' not in feedback  # Windows separator retained
    assert '<workspace>/django\\db\\models\\sql\\query.py' in feedback
    assert root not in feedback
    assert len(feedback) <= 14000


def test_import_failure_and_pass_are_distinct():
    failed = ('Traceback (most recent call last):\n'
              '  File "module.py", line 1, in <module>\n'
              'ImportError: cannot import name Ref\n')
    assert 'ImportError: cannot import name Ref' in format_test_feedback(failed)
    success = 'Ran 116 tests in 0.3s\nOK\n'
    assert 'No individual test headings' not in format_test_feedback(success)
