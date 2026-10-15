import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from experiments.runners.agents_provider_audit_v2 import (
    RequestLedger, AuditedTransport, RequestRejected, request_scope, make_sdk_model, sdk_settings,
)


@contextmanager
def temporary():
    root = Path(__file__).resolve().parents[1]/".tooling/agents-natural-v2-tests"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        yield Path(tmp)


def body():
    return {"model":"deepseek-flash", "messages":[{"role":"user","content":"hi"}],
            "max_tokens":2048,"temperature":0,"thinking":{"type":"disabled"},
            "response_format":{"type":"json_object"}}


class BudgetTest(unittest.TestCase):
    def test_nonfinite_or_noninteger_budget_values_rejected(self):
        for field,value in (("cap_usd",float('nan')),("cap_usd",float('inf')),("max_body_bytes",float('nan')),("max_body_bytes",True)):
            with temporary() as tmp:
                with self.assertRaises(ValueError):RequestLedger(tmp,**{field:value})

    def test_count_duplicate_concurrent_and_repair_guards(self):
        with temporary() as tmp:
            l = RequestLedger(tmp, max_requests=2)
            n=l.begin("s", "initial", body())
            with self.assertRaises(RequestRejected): l.begin("t", "initial", body())
            l.finish(n, status="returned", response={"usage":{"prompt_tokens":3,"completion_tokens":2}})
            with self.assertRaises(RequestRejected): l.begin("s", "initial", body())
            with self.assertRaises(RequestRejected): l.begin("s", "repair", body())
            l.authorize_repair("s")
            n=l.begin("s", "repair", body());l.finish(n,status="returned",response={})
            with self.assertRaises(RequestRejected): l.begin("t", "initial", body())
            self.assertEqual(2,len(l.records))
            self.assertIsNone(l.records[1]["usage"]["prompt_tokens"])

    def test_money_and_size_reject_before_dispatch(self):
        with temporary() as tmp:
            l=RequestLedger(tmp,cap_usd=0.000001)
            with self.assertRaises(RequestRejected): l.begin("s","initial",body())
            self.assertEqual([],l.records)

    def test_invalid_usage_is_flagged_preserved_and_not_priced(self):
        usages=[{"prompt_tokens":-1,"completion_tokens":-1,"total_tokens":-2,"prompt_cache_hit_tokens":-2,"prompt_cache_miss_tokens":1},
                {"prompt_tokens":10,"completion_tokens":2,"total_tokens":99,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2},
                {"prompt_tokens":10,"completion_tokens":3000,"total_tokens":3010,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2}]
        for usage in usages:
            with temporary() as tmp:
                ledger=RequestLedger(tmp);n=ledger.begin("s","initial",body())
                ledger.finish(n,status="returned",response={"usage":usage})
                self.assertEqual("invalid_usage",ledger.records[0]["status"])
                self.assertEqual(usage,ledger.records[0]["raw_usage"])
                self.assertIsNone(ledger.records[0]["peak_usage_priced_usd"])
        with temporary() as tmp:
            l=RequestLedger(tmp,max_body_bytes=10)
            with self.assertRaises(RequestRejected): l.begin("s","initial",body())
            self.assertEqual([],l.records)


class SDKTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_wire_body_size_ceiling_uses_transmitted_bytes(self):
        with temporary() as tmp:
            calls=[]
            ledger=RequestLedger(tmp,max_body_bytes=1000)
            def respond(request):
                calls.append(1);return httpx.Response(200,json={})
            transport=AuditedTransport(ledger,httpx.MockTransport(respond))
            request=httpx.Request("POST","https://api.deepseek.com/chat/completions",content=json.dumps(body())+" "*1500)
            with request_scope("s","initial"):
                with self.assertRaises(RequestRejected):await transport.handle_async_request(request)
            self.assertEqual([],calls)

    async def test_completion_storage_failure_does_not_erase_returned_usage(self):
        with temporary() as tmp:
            ledger=RequestLedger(tmp)
            transport=AuditedTransport(ledger,httpx.MockTransport(lambda r:httpx.Response(200,json={"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}})))
            original=ledger._event
            failures=[]
            def event(data):
                if data['event']=='complete' and not failures:
                    failures.append(1);raise OSError("controlled disk failure")
                return original(data)
            with patch.object(ledger,'_event',side_effect=event),request_scope("s","initial"):
                with self.assertRaises(OSError):
                    await transport.handle_async_request(httpx.Request("POST","https://api.deepseek.com/chat/completions",json=body()))
            self.assertEqual(10,ledger.records[0]['usage']['prompt_tokens'])
            self.assertTrue(ledger.records[0]['audit_persistence_error'])
    async def test_frozen_body_mismatch_has_no_dispatch_or_network(self):
        with temporary() as tmp:
            calls=[]
            transport=AuditedTransport(RequestLedger(tmp),httpx.MockTransport(lambda r:calls.append(1)))
            request=httpx.Request("POST","https://api.deepseek.com/chat/completions",json=body())
            with request_scope("s","initial",expected_body_sha256="wrong"):
                with self.assertRaises(RequestRejected):await transport.handle_async_request(request)
            self.assertEqual([],calls);self.assertEqual([],transport.ledger.records)

    async def test_missing_usage_preserved_as_unknown(self):
        with temporary() as tmp:
            ledger=RequestLedger(tmp)
            transport=AuditedTransport(ledger,httpx.MockTransport(lambda r:httpx.Response(200,json={"id":"r"})))
            with request_scope("s","initial"):
                await transport.handle_async_request(httpx.Request("POST","https://api.deepseek.com/chat/completions",json=body()))
            self.assertTrue(all(v is None for v in ledger.records[0]["usage"].values()))
            self.assertIsNone(ledger.records[0]["peak_usage_priced_usd"])

    async def test_network_exception_remains_counted(self):
        with temporary() as tmp:
            def fail(request):raise httpx.ConnectError("controlled failure")
            ledger=RequestLedger(tmp);transport=AuditedTransport(ledger,httpx.MockTransport(fail))
            with request_scope("s","initial"):
                with self.assertRaises(httpx.ConnectError):
                    await transport.handle_async_request(httpx.Request("POST","https://api.deepseek.com/chat/completions",json=body()))
            self.assertEqual(1,len(ledger.records));self.assertEqual("transport_error",ledger.records[0]["status"])
            self.assertIsNone(ledger.inflight)

    async def test_sdk_uses_audited_http_and_preserves_raw_provider_usage(self):
        from agents import Agent, Runner, RunConfig
        with temporary() as tmp:
            def respond(request):
                return httpx.Response(200,json={"id":"r1","object":"chat.completion","created":0,
                    "model":"deepseek-flash","choices":[{"index":0,"finish_reason":"stop", "message":{"role":"assistant","content":"{\"read\":{\"path\":\"x.py\",\"first\":1,\"last\":2}}"}}],
                    "usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2}},headers={"x-request-id":"req1"})
            ledger=RequestLedger(tmp)
            transport=AuditedTransport(ledger,httpx.MockTransport(respond))
            model,client=make_sdk_model("not-a-real-key",transport)
            try:
                with request_scope("sample","initial"):
                    r=await Runner.run(Agent(name="probe",model=model,model_settings=sdk_settings()),input="hi",max_turns=1,run_config=RunConfig(tracing_disabled=True))
                self.assertIn('"read"',r.final_output)
                self.assertEqual(1,len(ledger.records))
                self.assertEqual(8,ledger.records[0]["usage"]["prompt_cache_hit_tokens"])
                self.assertEqual("req1",ledger.records[0]["request_id"])
                self.assertNotIn("not-a-real-key",(tmp/"requests.jsonl").read_text())
            finally: await client.close()

    async def test_http_failure_is_counted_and_never_automatically_retried(self):
        from agents import Agent,Runner,RunConfig
        with temporary() as tmp:
            calls=[]
            def fail(request):
                calls.append(1);return httpx.Response(500,json={"error":{"message":"failure","type":"server_error"}})
            ledger=RequestLedger(tmp)
            model,client=make_sdk_model("dummy",AuditedTransport(ledger,httpx.MockTransport(fail)))
            try:
                with request_scope("s","initial"):
                    with self.assertRaises(Exception):
                        await Runner.run(Agent(name="probe",model=model,model_settings=sdk_settings()),input="hi",max_turns=1,run_config=RunConfig(tracing_disabled=True))
                self.assertEqual(1,len(calls));self.assertEqual(1,len(ledger.records))
                self.assertEqual("http_error",ledger.records[0]["status"])
            finally: await client.close()


if __name__ == "__main__": unittest.main()
