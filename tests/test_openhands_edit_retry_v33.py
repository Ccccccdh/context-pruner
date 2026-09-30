"""Behavioral checks for the shared zero-change edit retry guard."""
from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations' / 'openhands'))
from edit_retry_guard_v33 import EditRetryGuard


def action(command, old=None, new=None):
    return SimpleNamespace(command=command, old_str=old, new_str=new)


def test_repeated_noop_requires_view_before_real_edit():
    guard = EditRetryGuard()
    assert 'No edit occurred' in guard.check(action('str_replace', 'x', 'x'))
    assert 'No edit occurred' in guard.check(action('str_replace', 'x', 'x'))
    assert 'view is required' in guard.check(action('str_replace', 'x', 'y'))
    assert guard.check(action('view')) is None
    assert guard.check(action('str_replace', 'x', 'y')) is None


def test_successful_change_clears_noop_streak():
    guard = EditRetryGuard()
    assert guard.check(action('str_replace', 'a', 'a'))
    assert guard.check(action('str_replace', 'a', 'b')) is None
    assert guard.check(action('str_replace', 'c', 'c'))
    assert not guard.needs_view
