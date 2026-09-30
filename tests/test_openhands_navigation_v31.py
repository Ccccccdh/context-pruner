"""Offline checks for source navigation using prior failed trajectories."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'integrations/openhands'))
from source_navigation_v31 import SourceNavigator
from budget_policy_v31 import NavigationBudgetPolicy


def test_symbol_lookup_is_bounded_read_only_and_revisions_change(tmp_path):
    root = tmp_path/'workspace';root.mkdir()
    source = root/'core.py'
    source.write_text('class Group:\n    def list_commands(self):\n        pass\n\n'
                      'class Command:\n    pass\n', encoding='utf-8')
    nav = SourceNavigator(root, [source], 'Add Group.command_catalog to Group')
    first = nav.search(source, 'Group')
    assert first['matches'][0]['name'] == 'Group' and first['matches'][0]['line'] == 1
    assert 'Group.list_commands' in [row['name'] for row in first['matches']]
    assert nav.view_hint(source, [1, 1]) and 'Group 1-3' in nav.view_hint(source, [1, 1])
    assert 'already read' in nav.view_hint(source, [1, 1])
    with pytest.raises(ValueError):nav.search(root/'../outside.py', 'Group')
    with pytest.raises(ValueError):nav.search(source, 'G')
    with pytest.raises(ValueError):nav.search(source, 'x'*65)
    old = first['revision']
    source.write_text('\n'+source.read_text(encoding='utf-8'),encoding='utf-8')
    assert nav.search(source,'Group')['revision'] != old
    assert 'already read' not in (nav.view_hint(source,[1,1]) or '')


@pytest.mark.parametrize('sample', ['r2-click_catalog-pruner_v11', 'r3-click_catalog-none'])
def test_failed_trajectory_gets_a_target_before_stuck(sample):
    out = ROOT/'runs/stage5-openhands/budget-aware-3x3-v30'
    rp = out/sample/'report.json'
    if not rp.exists():pytest.skip('Historical v30 run not in this checkout')
    report = json.loads(rp.read_text(encoding='utf-8'))
    assert report['sdk_status'] == 'stuck'
    task = report['task']
    from validation_tasks_v29 import TASKS
    workspace = out/'workspaces'/('w008' if sample.startswith('r2-') else 'w012')
    allowed = [workspace/p for p in TASKS[task]['allowed']]
    nav = SourceNavigator(workspace, allowed, TASKS[task]['problem'])
    events = [json.loads(path.read_text(encoding='utf-8')) for path in
              (out/sample/'conversation').glob('**/events/*.json')]
    events.sort(key=lambda item: item['timestamp'])
    views = []
    for event in events:
        if event.get('kind')=='ActionEvent' and event.get('tool_name')=='scoped_editor' and event.get('action',{}).get('command')=='view':
            action=event['action'];hint=nav.view_hint(action['path'],action.get('view_range'))
            views.append((action,hint))
    assert len(views)>10
    target = [index for index,(_,hint) in enumerate(views) if hint and 'Group ' in hint]
    assert target and target[0] < 4
    assert any(hint and 'already read' in hint for _,hint in views[:8])


def test_budget_allows_navigation_only_while_work_is_open():
    policy=NavigationBudgetPolicy(lambda:'r1')
    offered=['scoped_editor','scoped_symbols','scoped_tests','finish']
    decision=policy.decide(3,0,1000)
    assert 'scoped_symbols' in policy.allowed_tools(decision,offered)
    decision=policy.decide(34,0,1000)
    assert policy.allowed_tools(decision,offered)=={'scoped_tests'}


def test_sdk_symbol_tool_returns_bounded_observation(tmp_path):
    from symbol_tool_v31 import ScopedSymbolsAction, ScopedSymbolsTool
    root=tmp_path/'workspace';root.mkdir()
    path=root/'source.py'
    path.write_text('class Group:\n    def command(self):\n        pass\n',encoding='utf-8')
    nav=SourceNavigator(root,[path],'Group.command')
    tool=ScopedSymbolsTool.create(None,nav)[0]
    assert tool.name=='scoped_symbols'
    observation=tool.executor(ScopedSymbolsAction(path=str(path),query='Group'))
    result=json.loads(observation.text)
    assert not observation.is_error and len(result['matches'])==2
    assert result['matches'][0]['line']==1
    denied=tool.executor(ScopedSymbolsAction(path=str(root/'other.py'),query='Group'))
    assert denied.is_error


def test_sdk_conversation_executes_symbol_tool_without_provider(tmp_path):
    from unittest.mock import patch
    from pydantic import SecretStr
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.tool import Tool, register_tool
    from openhands.sdk.llm import Message, MessageToolCall, TextContent
    from openhands.sdk.llm.llm_response import LLMResponse
    from openhands.sdk.llm.utils.metrics import MetricsSnapshot
    from litellm.types.utils import ModelResponse
    from bounded_llm_v30 import BudgetAwareLLM
    from symbol_tool_v31 import ScopedSymbolsTool as BaseSymbolsTool

    workspace=tmp_path/'workspace';workspace.mkdir()
    source=workspace/'core.py'
    source.write_text('class Group:\n    def command(self):\n        pass\n',encoding='utf-8')
    nav=SourceNavigator(workspace,[source],'Group.command')

    class ScopedSymbolsTool(BaseSymbolsTool):
        @classmethod
        def create(cls,conv_state):
            return super().create(conv_state,nav)

    register_tool(ScopedSymbolsTool.name,ScopedSymbolsTool)
    policy=NavigationBudgetPolicy(lambda:'revision')
    llm=BudgetAwareLLM(model='openai/deepseek-v4-flash',api_key=SecretStr('unused'))
    llm._policy=policy;llm._ledger={'calls':[],'estimated_input':0};llm._sample=tmp_path
    seen=[]

    def fake_provider(messages,tools,*args,**kwargs):
        index=len(llm._ledger['calls'])
        assert 'scoped_symbols' in {tool.name for tool in tools}
        if index==1:
            message=Message(role='assistant',content=[],tool_calls=[MessageToolCall(
                id='symbols-1',name='scoped_symbols',
                arguments=json.dumps({'path':str(source),'query':'Group'}),origin='completion')])
        else:
            message=Message(role='assistant',content=[TextContent(text='Located class Group.')])
        return LLMResponse(message=message,metrics=MetricsSnapshot(),
                           raw_response=ModelResponse(id=f'response-{index}',choices=[]))

    agent=Agent(llm=llm,tools=[Tool(name=ScopedSymbolsTool.name)],include_default_tools=['FinishTool'])
    with patch('openhands.sdk.agent.base.has_vision_profile_available',return_value=False), patch.object(LLM,'completion',side_effect=fake_provider) as backend:
        conv=Conversation(agent=agent,workspace=str(workspace),persistence_dir=str(tmp_path/'conversation'),
                          callbacks=[seen.append],visualizer=None,max_iteration_per_run=10)
        try:
            conv.send_message('Find Group.')
            conv.run()
        finally:
            conv.close()
    assert backend.call_count==2
    observations=[event for event in seen if getattr(event,'tool_name',None)=='scoped_symbols' and hasattr(event,'observation')]
    assert len(observations)==1
    assert json.loads(observations[0].observation.text)['matches'][0]['line']==1
