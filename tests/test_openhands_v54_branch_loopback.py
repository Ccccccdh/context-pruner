"""Exercise sequential same-path OpenHands branches without a provider call."""

import json
from pathlib import Path
from unittest.mock import patch

from pydantic import PrivateAttr
from litellm.types.utils import ModelResponse
from openhands.sdk import Agent, LLM
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import Message, MessageToolCall
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.tool import Tool, register_tool
from openhands.sdk.tool.tool import Action, Observation, ToolDefinition, ToolExecutor

from integrations.openhands.same_prefix_fork_v54 import (
    attach_branch_callback,
    capture_workspace,
    fork_with_agent,
    restore_workspace,
    tree_hashes,
)


class ProbeLLM(LLM):
    _calls: list = PrivateAttr(default_factory=list)


class ProbeAction(Action):
    marker: str


class ProbeObservation(Observation):
    pass


class ProbeExecutor(ToolExecutor):
    def __init__(self, workspace):
        self.workspace = Path(workspace)

    def __call__(self, action, conversation=None):
        before = (self.workspace / 'state.txt').read_text(encoding='utf-8')
        (self.workspace / 'state.txt').write_text(action.marker, encoding='utf-8')
        return ProbeObservation.from_text(text=f'{before}->{action.marker}')


class BranchProbeTool(ToolDefinition[ProbeAction, ProbeObservation]):
    @classmethod
    def create(cls, conv_state):
        return [cls(action_type=ProbeAction, observation_type=ProbeObservation,
                    description='Zero-API branch workspace probe.',
                    executor=ProbeExecutor(conv_state.workspace.working_dir))]


register_tool(BranchProbeTool.name, BranchProbeTool)


def _response(name, index, arguments):
    return LLMResponse(
        message=Message(role='assistant', content=[], tool_calls=[MessageToolCall(
            id=f'probe-{index}', name=name, arguments=json.dumps(arguments), origin='completion')]),
        metrics=MetricsSnapshot(),
        raw_response=ModelResponse(id=f'probe-response-{index}', choices=[]),
    )


def test_three_sequential_branches_restore_same_path_and_keep_independent_ledgers(tmp_path, monkeypatch):
    monkeypatch.setattr('openhands.sdk.agent.base.has_vision_profile_available',
                        lambda: False)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'state.txt').write_text('prefix', encoding='utf-8')
    source_llm = ProbeLLM(model='openai/deepseek-v4-flash', api_key='unused', usage_id='prefix')
    source_agent = Agent(llm=source_llm, tools=[Tool(name=BranchProbeTool.name)],
                         include_default_tools=['FinishTool'])
    source = LocalConversation(agent=source_agent, workspace=str(workspace),
                               persistence_dir=str(tmp_path / 'source-events'),
                               profile_store_dir=str(tmp_path / 'profiles'),
                               delete_on_close=False, visualizer=None)
    try:
        source.send_message('Continue from this shared workspace checkpoint.')
        branch_ledgers = []
        branch_event_ids = []

        def fake_provider(llm, messages, tools, *args, **kwargs):
            assert isinstance(llm, ProbeLLM)
            index = len(llm._calls) + 1
            llm._calls.append({'index': index, 'tools': sorted(tool.name for tool in tools)})
            if index == 1:
                return _response(BranchProbeTool.name, index, {'marker': llm.usage_id})
            assert index == 2
            return _response('finish', index, {'message': 'Probe complete'})

        with patch('openhands.sdk.agent.base.has_vision_profile_available', return_value=False), \
             patch.object(LLM, 'completion', fake_provider):
            source.run()
            assert source.state.execution_status.value == 'finished'
            assert len(source_llm._calls) == 2
            prefix_ids = [event.id for event in source.state.events]
            frozen = capture_workspace(workspace, tmp_path / 'snapshot', experiment_root=tmp_path)
            for arm in ('none', 'native_summary', 'pruner'):
                restore_workspace(workspace, tmp_path / 'snapshot', experiment_root=tmp_path,
                                  expected_hashes=frozen)
                assert tree_hashes(workspace) == frozen
                llm = ProbeLLM(model='openai/deepseek-v4-flash', api_key='unused', usage_id=arm)
                agent = Agent(llm=llm, tools=[Tool(name=BranchProbeTool.name)],
                              include_default_tools=['FinishTool'])
                fork = fork_with_agent(source, agent)
                observed = []
                attach_branch_callback(fork, lambda event: observed.append(event))
                try:
                    assert [event.id for event in fork.state.events] == prefix_ids
                    assert fork.state.workspace.working_dir == str(workspace)
                    fork.send_message('Continue this completed prefix with branch feedback.')
                    fork.run()
                    assert fork.state.execution_status.value == 'finished'
                    assert (workspace / 'state.txt').read_text(encoding='utf-8') == arm
                    assert set(tree_hashes(workspace)) == set(frozen)
                    assert len(llm._calls) == 2
                    assert any(type(event).__name__ == 'ObservationEvent' for event in observed)
                    suffix = list(fork.state.events)[len(prefix_ids):]
                    assert [event.id for event in observed] == [event.id for event in suffix]
                    assert len({event.id for event in suffix}) == len(suffix)
                    assert [event.id for event in source.state.events] == prefix_ids
                    branch_ledgers.append(llm._calls)
                    branch_event_ids.append({event.id for event in observed})
                finally:
                    fork.close()

        assert len({id(ledger) for ledger in branch_ledgers}) == 3
        assert all(len(ledger) == 2 for ledger in branch_ledgers)
        assert not branch_event_ids[0] & branch_event_ids[1]
        assert not branch_event_ids[1] & branch_event_ids[2]
    finally:
        source.close()
