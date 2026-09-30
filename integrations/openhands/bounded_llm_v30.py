"""Transient live budget notices after condensation; no additional API calls."""
from dataclasses import asdict
import json
from pathlib import Path
import time
from pydantic import PrivateAttr
from litellm import token_counter
from openhands.sdk import LLM
from openhands.sdk.llm import Message, TextContent
from budget_policy_v30 import BudgetPolicy

class BudgetAwareLLM(LLM):
    _ledger: dict = PrivateAttr(default_factory=dict)
    _sample: Path = PrivateAttr()
    _kind: str = PrivateAttr(default='agent')
    _policy: BudgetPolicy = PrivateAttr()
    _max_summary_calls: int = PrivateAttr(default=16)

    def _save(self):
        (self._sample/'ledger.json').write_text(json.dumps(self._ledger,indent=2,ensure_ascii=False),encoding='utf-8')

    @staticmethod
    def _estimate(messages, tools):
        data = {'messages':[m.model_dump(mode='json') for m in messages],
                'tools':[t.model_dump(mode='json') for t in tools]}
        return token_counter(model='gpt-4o',text=json.dumps(data)) + 256

    @staticmethod
    def _validate_batches(messages):
        pending=set()
        for message in messages:
            if message.role=='tool':
                assert message.tool_call_id in pending, 'Orphan tool result'
                pending.remove(message.tool_call_id)
            else:
                assert not pending, 'Incomplete tool batch'
                pending.update(c.id for c in message.tool_calls or [])
        assert not pending, 'Unresolved tool call'

    def completion(self,*a,**kw):
        ledger=self._ledger
        if ledger.get('provider_failure'):
            raise RuntimeError('Provider connectivity failure preserved; additional model request blocked')
        messages=kw.pop('messages',a[0] if a else [])
        offered=list(kw.pop('tools',a[1] if len(a)>1 else []) or [])
        extra=a[2:]
        self._validate_batches(messages)
        count=sum(c['kind']==self._kind for c in ledger['calls'])
        agent_used=sum(c['kind']=='agent' for c in ledger['calls'])
        limit=self._policy.max_agent_calls if self._kind=='agent' else self._max_summary_calls
        if count>=limit:
            raise RuntimeError('Frozen experiment call/token limit')
        original=list(messages)
        outgoing=list(original)
        tools=offered
        decision=None
        notice=None
        allowed=None
        estimated=self._estimate(outgoing,tools)
        if self._kind=='agent':
            decision=self._policy.decide(agent_used,ledger['estimated_input'],estimated)
            for _ in range(2):
                allowed=self._policy.allowed_tools(decision,[t.name for t in offered])
                tools=[t for t in offered if t.name in allowed]
                notice=self._policy.notice(decision)
                outgoing=original+[Message(role='system',content=[TextContent(text=notice)])]
                estimated=self._estimate(outgoing,tools)
                next_decision=self._policy.decide(agent_used,ledger['estimated_input'],estimated)
                if next_decision==decision:
                    break
                decision=next_decision
            if decision.phase!='work':
                kw['tool_choice']={'type':'function','function':{'name':next(iter(allowed))}}
            # Include notices/tool filtering in the same exact accounting gate.
            if decision.phase=='work' and ledger['estimated_input']+estimated+self._policy.reserve_input(agent_used)>self._policy.max_total_input:
                raise RuntimeError('Work request would consume verification/Finish input reserve')
        elif ledger['estimated_input']+estimated+self._policy.reserve_input(agent_used)>self._policy.max_total_input:
            self._policy.closing_reason=self._policy.closing_reason or 'summary_input_reserve'
            ledger.setdefault('blocked_requests',[]).append({'kind':'summary','reason':'verification_finish_input_reserve'})
            self._save()
            raise RuntimeError('Summary would consume verification/Finish input reserve')
        if estimated>self._policy.max_single_input or ledger['estimated_input']+estimated>self._policy.max_total_input:
            raise RuntimeError('Frozen experiment call/token limit')
        self._validate_batches(outgoing)
        record={'kind':self._kind,'estimated_input':estimated,'structural_valid':True,'status':'started'}
        if decision is not None:
            record.update(budget=asdict(decision),budget_notice=notice,allowed_tools=sorted(allowed))
        ledger['calls'].append(record)
        ledger['estimated_input']+=estimated
        self._save()
        print(f'{self._sample.name} {self._kind} call={count+1} input~{estimated} phase={decision.phase if decision else "summary"}',flush=True)
        start=time.perf_counter()
        try:
            response=super().completion(outgoing,tools,*extra,**kw)
            record['status']='returned'
            if decision is not None:
                calls=response.message.tool_calls or []
                valid=all(c.name in allowed for c in calls)
                if decision.phase!='work':
                    valid=valid and bool(calls)
                if any(c.name=='finish' for c in calls):
                    # A mixed batch cannot edit code and finish against old tests.
                    for call in calls:
                        if call.name=='scoped_editor':
                            try:
                                command=json.loads(call.arguments).get('command')
                            except ValueError:
                                command=None
                            if command!='view':
                                valid=False
                record['budget_response_valid']=valid
                if not valid:
                    raise RuntimeError('Budget policy blocked an unexpected tool before execution')
            return response
        except Exception as exc:
            if record['status']=='started':
                record.update(status='failed',error_type=type(exc).__name__)
                if any(word in str(exc).lower() for word in ('connection error','request timed out','timeout','insufficient balance')):
                    ledger['provider_failure']=type(exc).__name__
            raise
        finally:
            record['latency_seconds']=time.perf_counter()-start
            self._save()
