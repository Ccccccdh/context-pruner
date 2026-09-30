"""Budget exhaustion, stale verification and actual SDK Finish without network."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integrations/openhands'))
from budget_policy_v30 import BudgetPolicy
from bounded_llm_v30 import BudgetAwareLLM
from openhands.sdk import Agent,Conversation,LLM
from openhands.sdk.llm import Message,TextContent,MessageToolCall
from openhands.sdk.llm.llm_response import LLMResponse
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.tool import Tool,register_tool
from openhands.sdk.tool.tool import Action,Observation,ToolDefinition,ToolExecutor
from openhands.tools.file_editor import FileEditorTool
from litellm.types.utils import ModelResponse


def ledger(used):
    return {'calls':[{'kind':'agent','status':'returned'} for _ in range(used)],'estimated_input':0}


def response(name,index=1,args=None):
    return LLMResponse(message=Message(role='assistant',content=[],tool_calls=[MessageToolCall(
        id=f'tool-{index}',name=name,arguments=json.dumps(args or {}),origin='completion')]),
        metrics=MetricsSnapshot(),raw_response=ModelResponse(id=f'response-{index}',choices=[]))


def test_last_two_requests_not_available_for_work():
    p=BudgetPolicy(lambda:'r1')
    assert p.decide(33,0,1000).phase=='work'
    d=p.decide(34,0,1000)
    assert (d.phase,d.remaining_requests,d.remaining_work_requests)==('verify',2,0)
    assert p.allowed_tools(d,['scoped_editor','scoped_tests','finish'])=={'scoped_tests'}
    p.observe_test(json.dumps({'passed':True,'code_revision':'r1'}))
    d=p.decide(35,0,1000)
    assert d.phase=='finish' and p.allowed_tools(d,['scoped_editor','scoped_tests','finish'])=={'finish'}
    with pytest.raises(RuntimeError):p.decide(36,0,1000)


def test_revision_changes_and_failed_or_invalid_tests_are_not_passes():
    state=['old'];p=BudgetPolicy(lambda:state[0])
    p.observe_test(json.dumps({'passed':True,'code_revision':'old'}));assert p.is_verified
    state[0]='new';assert not p.is_verified
    assert p.decide(34,0,1000).phase=='verify'
    p.observe_test(json.dumps({'passed':False,'code_revision':'new'}))
    assert not p.is_verified and p.decide(35,0,1000).phase=='finish'
    assert 'explain incomplete/failed' in p.notice(p.decide(35,0,1000))
    p.observe_test('passed!',is_error=False);assert not p.is_verified
    p.observe_test(json.dumps({'passed':True,'code_revision':'new'}),is_error=True);assert not p.is_verified


def test_total_input_and_single_input_headroom_close_early():
    p=BudgetPolicy(lambda:'revision')
    assert p.decide(10,1840000,1000).phase=='verify'
    assert p.closing_reason=='total_input_reserve'
    p=BudgetPolicy(lambda:'revision')
    assert p.decide(5,0,68000).phase=='verify'
    assert p.closing_reason=='single_input_headroom'
    assert not p.can_correct(34,0)
    assert not p.can_correct(5,1900000)
    assert p.can_correct(5,0)

class ScopedEditorTool(FileEditorTool):
    pass

class ScopedTestsAction(Action):
    reason:str

class ScopedTestsObservation(Observation):
    pass

class FixtureVerifier(ToolExecutor):
    def __init__(self,workspace,passed):self.workspace=Path(workspace);self.passed=passed
    def __call__(self,action,conversation=None):
        rev=hashlib.sha256((self.workspace/'code.py').read_bytes()).hexdigest()
        return ScopedTestsObservation.from_text(text=json.dumps({'passed':self.passed,'code_revision':rev}))

class ScopedTestsTool(ToolDefinition[ScopedTestsAction,ScopedTestsObservation]):
    @classmethod
    def create(cls,conv_state,passed=True):
        return [cls(action_type=ScopedTestsAction,observation_type=ScopedTestsObservation,
                    description='Fixture public verifier, mechanical SDK test only.',
                    executor=FixtureVerifier(conv_state.workspace.working_dir,passed))]

register_tool(ScopedEditorTool.name,ScopedEditorTool)
register_tool(ScopedTestsTool.name,ScopedTestsTool)

@pytest.mark.parametrize('passed',[True,False])
def test_actual_sdk_uses_request_35_for_verify_and_36_for_finish(tmp_path,passed):
    ws=tmp_path/'workspace';ws.mkdir();source=ws/'code.py';initial = ''.join(f'value_{i} = {i}\n' for i in range(1,41));source.write_text(initial,encoding='utf-8')
    policy=BudgetPolicy(lambda:hashlib.sha256(source.read_bytes()).hexdigest())
    sample=tmp_path/'sample';sample.mkdir()
    llm=BudgetAwareLLM(model='openai/deepseek-v4-flash',api_key='unused',base_url='https://api.deepseek.com',num_retries=0)
    llm._ledger=ledger(0);llm._sample=sample;llm._policy=policy
    observed=[]
    def fake_provider(messages,tools,*a,**kw):
        current=llm._ledger['calls'][-1];index=len(llm._ledger['calls']);phase=current['budget']['phase']
        assert messages[-1].role=='system' and 'HOST RUNTIME BUDGET' in messages[-1].content[0].text
        offered={tool.name for tool in tools}
        if phase=='work':
            assert 'finish' not in offered
            return response('scoped_editor',index,{'command':'view','path':str(source),'view_range':[index,index]})
        target='scoped_tests' if phase=='verify' else 'finish'
        assert offered=={target} and kw['tool_choice']['function']['name']==target
        return response(target,index,{'reason':'Ready to verify'} if target=='scoped_tests' else {'message':'Done' if passed else 'Incomplete: public verification failed'})
    def callback(event):policy.observe_event(event);observed.append(event)
    agent=Agent(llm=llm,tools=[Tool(name=ScopedEditorTool.name),Tool(name=ScopedTestsTool.name,params={'passed':passed})],include_default_tools=['FinishTool'])
    # Profile discovery is unrelated to the budget policy; keep this SDK test isolated from user profiles.
    with patch('openhands.sdk.agent.base.has_vision_profile_available',return_value=False), patch.object(LLM,'completion',side_effect=fake_provider) as backend:
        conv=Conversation(agent=agent,workspace=str(ws),persistence_dir=str(tmp_path/'conversation'),callbacks=[callback],visualizer=None,max_iteration_per_run=200)
        try:
            conv.send_message('Read this fixture as needed, then verify and finish. This is not a feature efficacy test.')
            conv.run()
            assert conv.state.execution_status.value=='finished'
        finally:conv.close()
    assert backend.call_count==36
    assert [r['budget']['phase'] for r in llm._ledger['calls']][-2:]==['verify','finish']
    assert policy.is_verified is passed
    assert all(r['structural_valid'] and r['budget_response_valid'] for r in llm._ledger['calls'])
    assert sum(getattr(e,'tool_name',None)=='finish' and type(e).__name__=='ActionEvent' for e in observed)==1
    assert source.read_text(encoding='utf-8')==initial


def test_summary_requests_cannot_consume_closing_input_reserve(tmp_path):
    llm=BudgetAwareLLM(model='openai/deepseek-v4-flash',api_key='unused')
    llm._ledger=ledger(0);llm._ledger['estimated_input']=1850000;llm._sample=tmp_path;llm._kind='summary';llm._policy=BudgetPolicy(lambda:'rev')
    with patch.object(LLM,'completion') as backend:
        with pytest.raises(RuntimeError,match='reserve'):
            llm.completion(messages=[Message(role='user',content=[TextContent(text='Summarize')])])
    backend.assert_not_called()
    assert len(llm._ledger['calls'])==0 and llm._policy.closing_reason=='summary_input_reserve'


def test_caller_history_unchanged_and_rogue_reserved_editor_blocked(tmp_path):
    from openhands.sdk.tool.builtins.finish import FinishTool
    llm=BudgetAwareLLM(model='openai/deepseek-v4-flash',api_key='unused')
    llm._ledger=ledger(35);llm._sample=tmp_path;llm._policy=BudgetPolicy(lambda:'rev')
    history=[Message(role='user',content=[TextContent(text='Task')])];before=[m.model_dump() for m in history]
    with patch.object(LLM,'completion',return_value=response('scoped_editor',36,{'command':'str_replace'})):
        with pytest.raises(RuntimeError,match='before execution'):
            llm.completion(messages=history,tools=FinishTool.create())
    assert [m.model_dump() for m in history]==before
    assert llm._ledger['calls'][-1]['status']=='returned'
    assert llm._ledger['calls'][-1]['budget_response_valid'] is False
