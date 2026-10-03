"""Zero-API gate for the SDK conversation fork's workspace isolation."""

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from integrations.openhands.same_prefix_fork_v54 import (
    attach_branch_callback,
    capture_workspace,
    fork_to_isolated_workspace,
    fork_with_agent,
    restore_workspace,
    tree_hashes,
)


class FakeWorkspace:
    def __init__(self, working_dir):
        self.working_dir = str(working_dir)


class FakeState:
    def __init__(self, workspace):
        self.workspace = workspace


class FakeConversation:
    def __init__(self, workspace, events=(), agent=None):
        self.workspace = FakeWorkspace(workspace)
        self._state = FakeState(self.workspace)
        self.agent = agent or SimpleNamespace(llm=object())
        self._state.agent = self.agent
        self.events = list(events)

    def fork(self, *, agent, reset_metrics):
        assert reset_metrics and agent is not None
        return FakeConversation(self.workspace.working_dir, self.events, agent=agent)

    def _bind_conversation_context(self, llm):
        assert llm is self.agent.llm


def test_fork_has_identical_prefix_and_isolated_workspace(tmp_path: Path):
    source_root = tmp_path / 'source'
    source_root.mkdir()
    (source_root / 'code.py').write_text('value = 1\n', encoding='utf-8')
    source = FakeConversation(source_root, events=['user', 'tool', 'model'])
    hashes = tree_hashes(source_root)
    fork = fork_to_isolated_workspace(
        source, workspace=tmp_path / 'branch',
        agent=SimpleNamespace(llm=object()), expected_source_hashes=hashes,
    )
    assert fork.events == source.events
    assert tree_hashes(tmp_path / 'branch') == hashes
    (tmp_path / 'branch' / 'code.py').write_text('value = 2\n', encoding='utf-8')
    assert tree_hashes(source_root) == hashes
    assert Path(fork._state.workspace.working_dir) == tmp_path / 'branch'


def test_fork_rejects_stale_or_overlapping_checkpoint(tmp_path: Path):
    source_root = tmp_path / 'source'
    source_root.mkdir()
    (source_root / 'code.py').write_text('value = 1\n', encoding='utf-8')
    source = FakeConversation(source_root)
    with pytest.raises(ValueError, match='changed'):
        fork_to_isolated_workspace(source, workspace=tmp_path / 'branch', agent=object(),
                                   expected_source_hashes={'code.py': 'stale'})
    with pytest.raises(ValueError, match='separate'):
        fork_to_isolated_workspace(source, workspace=source_root / 'branch', agent=object())


def test_installed_sdk_fork_isolated_without_model_call(tmp_path: Path, monkeypatch):
    """Exercise the installed SDK's real fork implementation, without run()."""
    from openhands.sdk import Agent, LLM
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations' / 'openhands'))
    from bounded_llm_v30 import BudgetAwareLLM

    # The SDK's optional vision-profile probe reads a user-home lock.  This
    # experiment does not use vision; keep the zero-API gate self-contained.
    monkeypatch.setattr('openhands.sdk.agent.base.has_vision_profile_available',
                        lambda: False)

    source_root = tmp_path / 'source'
    source_root.mkdir()
    (source_root / 'code.py').write_text('value = 1\n', encoding='utf-8')
    source_agent = Agent(llm=LLM(model='openai/deepseek-v4-flash', api_key='unused'), tools=[])
    branch_llm = BudgetAwareLLM(model='openai/deepseek-v4-flash', api_key='unused')
    ledger = {'calls': [], 'estimated_input': 0}
    policy = object()
    branch_llm._ledger = ledger
    branch_llm._sample = tmp_path
    branch_llm._policy = policy
    branch_agent = Agent(llm=branch_llm, tools=[])
    source = LocalConversation(agent=source_agent, workspace=str(source_root),
                               persistence_dir=str(tmp_path / 'conversations'),
                               profile_store_dir=str(tmp_path / 'profiles'),
                               delete_on_close=False, visualizer=None)
    fork = None
    try:
        source.send_message('Inspect code.py')
        prefix_ids = [event.id for event in source.state.events]
        fork = fork_to_isolated_workspace(source, workspace=tmp_path / 'branch',
                                          agent=branch_agent, expected_source_hashes=tree_hashes(source_root))
        assert [event.id for event in fork.state.events] == prefix_ids
        assert fork.agent is branch_agent and fork.agent is not source.agent
        assert fork.agent.llm is branch_llm
        assert fork.agent.llm._ledger is ledger
        assert fork.agent.llm._policy is policy
        seen = []
        attach_branch_callback(fork, lambda event: seen.append(event.id))
        fork.send_message('Continue from the same prefix')
        assert len(seen) == 1
        assert len(list(fork.state.events)) == len(prefix_ids) + 1
        assert [event.id for event in source.state.events] == prefix_ids
        with pytest.raises(RuntimeError, match='already attached'):
            attach_branch_callback(fork, lambda event: None)
        (tmp_path / 'branch' / 'code.py').write_text('value = 2\n', encoding='utf-8')
        assert (source_root / 'code.py').read_text(encoding='utf-8') == 'value = 1\n'
        assert fork.state.workspace.working_dir == str(tmp_path / 'branch')
    finally:
        if fork is not None:
            fork.close()
        source.close()


def test_branch_rejects_shared_agent(tmp_path: Path):
    from openhands.sdk import Agent, LLM
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation

    agent = Agent(llm=LLM(model='openai/deepseek-v4-flash', api_key='unused'), tools=[])
    source = LocalConversation(agent=agent, workspace=str(tmp_path / 'source'),
                               profile_store_dir=str(tmp_path / 'profiles'),
                               delete_on_close=False, visualizer=None)
    try:
        with pytest.raises(ValueError, match='distinct branch agent'):
            fork_with_agent(source, agent)
    finally:
        source.close()


def test_same_path_snapshot_restores_added_modified_and_deleted_files(tmp_path: Path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'code.py').write_text('base\n', encoding='utf-8')
    (workspace / 'gone.py').write_text('keep\n', encoding='utf-8')
    frozen = capture_workspace(workspace, tmp_path / 'snapshot', experiment_root=tmp_path)
    (workspace / 'code.py').write_text('modified\n', encoding='utf-8')
    (workspace / 'gone.py').unlink()
    (workspace / 'added.py').write_text('new\n', encoding='utf-8')
    restore_workspace(workspace, tmp_path / 'snapshot', experiment_root=tmp_path,
                      expected_hashes=frozen)
    assert tree_hashes(workspace) == frozen
    assert not (workspace / 'added.py').exists()
    with pytest.raises(ValueError, match='outside experiment root'):
        restore_workspace(tmp_path.parent / 'foreign', tmp_path / 'snapshot',
                          experiment_root=tmp_path, expected_hashes=frozen)
