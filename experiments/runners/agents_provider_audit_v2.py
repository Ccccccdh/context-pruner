"""Agents SDK provider boundary with durable per-HTTP-request limits and evidence."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time

import httpx

MODEL = "deepseek-flash"
URL = "https://api.deepseek.com/chat/completions"
MAX_OUTPUT = 2048
PRICES = {"offpeak": {"hit": .003, "miss": .15, "output": .6},
          "peak": {"hit": .006, "miss": .30, "output": 1.2}}
SCOPE = ContextVar("agents_audit_request_scope", default=None)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class RequestRejected(RuntimeError):
    pass


@contextmanager
def request_scope(sample_id, phase, **metadata):
    token = SCOPE.set({"sample_id": sample_id, "phase": phase, **metadata})
    try:
        yield
    finally:
        SCOPE.reset(token)


def usage_fields(response):
    raw = response.get("usage") if isinstance(response, dict) else None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    return {k: raw.get(k) if isinstance(raw, dict) else None for k in keys}


class RequestLedger:
    def __init__(self, out, *, max_requests=72, cap_usd=1., max_body_bytes=24000):
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests <= 0:
            raise ValueError("positive request ceiling required")
        if isinstance(cap_usd,bool) or not isinstance(cap_usd,(int,float)) or not math.isfinite(cap_usd) or cap_usd<=0 or isinstance(max_body_bytes,bool) or not isinstance(max_body_bytes,int) or max_body_bytes<=0:
            raise ValueError("positive cost/body ceilings required")
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.journal = self.out / "requests.jsonl"
        with self.journal.open("x", encoding="utf-8"):
            pass  # never resume/overwrite a consumed ledger
        self.max_requests, self.cap_usd, self.max_body_bytes = max_requests, cap_usd, max_body_bytes
        self.records, self.keys, self.repairs = [], set(), set()
        self.reserved_usd, self.inflight = 0., None
        self.lock = threading.Lock()

    def _event(self, data):
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(canonical(data)+"\n");f.flush();os.fsync(f.fileno())

    def begin(self, sample_id, phase, body, *, wire_body_bytes=None, **metadata):
        with self.lock:
            if phase not in {"initial", "repair"} or not isinstance(sample_id, str) or not sample_id:
                raise RequestRejected("invalid sample/phase")
            if self.inflight is not None:
                raise RequestRejected("concurrent requests unsupported")
            if (sample_id,phase) in self.keys:
                raise RequestRejected("duplicate sample-phase request")
            if phase == "repair" and sample_id not in self.repairs:
                raise RequestRejected("format repair not authorized")
            size=len(canonical(body).encode("utf-8")) if wire_body_bytes is None else wire_body_bytes
            if isinstance(size,bool) or not isinstance(size,int) or size<=0:
                raise RequestRejected("invalid transmitted body size")
            if size > self.max_body_bytes:
                raise RequestRejected("request body byte ceiling")
            reserved=(size+4096)*PRICES["peak"]["miss"]/1e6 + MAX_OUTPUT*PRICES["peak"]["output"]/1e6
            if len(self.records) >= self.max_requests or self.reserved_usd+reserved > self.cap_usd:
                raise RequestRejected("request or conservative cost ceiling")
            index=len(self.records)+1
            record={"index":index,"sample_id":sample_id,"phase":phase,"status":"dispatched",
                    "started_utc":datetime.now(timezone.utc).isoformat(),"body_sha256":sha(body),
                    "body_bytes":size,"reserved_peak_allmiss_usd":reserved,"prompt_token_reserve":size+4096,
                    "usage":usage_fields({}), **metadata}
            request_file=f"request_{index:03d}.json"
            (self.out/request_file).write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding="utf-8")
            record["request_file"]=request_file
            self._event({"event":"dispatch", **record})
            self.records.append(record);self.keys.add((sample_id,phase));self.inflight=index
            self.reserved_usd+=reserved
            return index

    def finish(self, index, *, status, response=None, elapsed_seconds=None, request_id=None, **extra):
        with self.lock:
            if self.inflight != index:
                raise RequestRejected("completion lacks corresponding dispatch")
            record=self.records[index-1]
            raw=response.get("usage") if isinstance(response,dict) else None
            fields=usage_fields(response)
            errors=[]
            for k,v in fields.items():
                if v is not None and (isinstance(v,bool) or not isinstance(v,int) or v<0):
                    errors.append("invalid "+k);fields[k]=None
            if all(fields[k] is not None for k in ("prompt_tokens","completion_tokens","total_tokens")) and fields["prompt_tokens"]+fields["completion_tokens"]!=fields["total_tokens"]:
                errors.append("total token mismatch")
            if all(fields[k] is not None for k in ("prompt_tokens","prompt_cache_hit_tokens","prompt_cache_miss_tokens")) and fields["prompt_cache_hit_tokens"]+fields["prompt_cache_miss_tokens"]!=fields["prompt_tokens"]:
                errors.append("cache token mismatch")
            if fields["prompt_tokens"] is not None and fields["prompt_tokens"]>record["prompt_token_reserve"]:
                errors.append("input usage exceeds conservative reserve")
            if fields["completion_tokens"] is not None and fields["completion_tokens"]>MAX_OUTPUT:
                errors.append("output usage exceeds declared ceiling")
            if errors:status="invalid_usage"
            record.update(status=status, elapsed_seconds=elapsed_seconds,request_id=request_id,
                          usage=fields,raw_usage=deepcopy(raw),usage_errors=errors, **extra)
            u=record["usage"]
            for period, rates in PRICES.items():
                record[f"{period}_usage_priced_usd"]=(
                    (u["prompt_cache_hit_tokens"]*rates["hit"]+u["prompt_cache_miss_tokens"]*rates["miss"]+
                     u["completion_tokens"]*rates["output"])/1e6
                    if not errors and all(u[k] is not None for k in ("prompt_cache_hit_tokens","prompt_cache_miss_tokens","completion_tokens")) else None)
            self.inflight=None
            try:self._event({"event":"complete", **record})
            except BaseException:
                record["audit_persistence_error"]=True
                raise

    def authorize_repair(self,sample_id):
        previous=next((r for r in self.records if r["sample_id"]==sample_id and r["phase"]=="initial"),None)
        if previous is None or previous["status"] != "returned":
            raise RequestRejected("only completed initial replies may receive one format repair")
        self.repairs.add(sample_id)


class AuditedTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, inner=None):
        self.ledger=ledger
        self.inner=inner if inner is not None else httpx.AsyncHTTPTransport(retries=0)
        self.mock=isinstance(self.inner,httpx.MockTransport)

    async def handle_async_request(self, request):
        scope=SCOPE.get()
        if scope is None or request.method != "POST" or str(request.url) != URL:
            raise RequestRejected("unexpected endpoint/method or missing sample context")
        body=json.loads(request.content)
        if body.get("model") != MODEL or body.get("max_tokens") != MAX_OUTPUT or body.get("temperature") != 0 or body.get("stream",False) or body.get("thinking") != {"type":"disabled"} or body.get("response_format") != {"type":"json_object"}:
            raise RequestRejected("unfrozen request settings")
        if scope.get("expected_body_sha256") is not None and sha(body)!=scope["expected_body_sha256"]:
            raise RequestRejected("SDK converted request differs from frozen body")
        if scope.get("expected_messages") is not None and body.get("messages")!=scope["expected_messages"]:
            raise RequestRejected("SDK messages differ from frozen/repair history")
        credential=request.headers.get("authorization", "").removeprefix("Bearer ")
        if credential and credential in canonical(body):
            raise RequestRejected("credential in request content")
        index=self.ledger.begin(scope["sample_id"],scope["phase"],body,
            wire_body_bytes=len(request.content),
            sdk_input_sha256=scope.get("sdk_input_sha256"), wire_body_sha256=hashlib.sha256(request.content).hexdigest(),
            transport_kind="mock" if self.mock else "live")
        started=time.monotonic()
        try:
            response=await self.inner.handle_async_request(request)
            raw=await response.aread()
            try: payload=json.loads(raw)
            except ValueError: payload={"unparseable_response":True}
            safe=canonical(payload)
            if credential: safe=safe.replace(credential,"[REDACTED]")
            response_file=f"response_{index:03d}.json"
            (self.ledger.out/response_file).write_text(safe,encoding="utf-8")
            self.ledger.finish(index,status="returned" if response.status_code<400 else "http_error",response=payload,
                elapsed_seconds=time.monotonic()-started,request_id=response.headers.get("x-request-id"),
                status_code=response.status_code,response_id=payload.get("id"),response_file=response_file,
                raw_response_sha256=hashlib.sha256(raw).hexdigest())
            return response
        except BaseException as exc:
            if self.ledger.inflight==index:
                self.ledger.finish(index,status="transport_error",elapsed_seconds=time.monotonic()-started,
                                   error_type=type(exc).__name__)
            raise

    async def aclose(self):
        await self.inner.aclose()


def sdk_settings():
    from agents import ModelSettings
    return ModelSettings(temperature=0,max_tokens=MAX_OUTPUT,preserve_raw_usage=True,
                         extra_body={"thinking":{"type":"disabled"},"response_format":{"type":"json_object"}},retry=None)


def make_sdk_model(key, transport):
    from openai import AsyncOpenAI
    from agents import OpenAIChatCompletionsModel
    client=AsyncOpenAI(api_key=key,base_url="https://api.deepseek.com",max_retries=0,
        timeout=90.,http_client=httpx.AsyncClient(transport=transport,timeout=90.,follow_redirects=False))
    return OpenAIChatCompletionsModel(MODEL,client,strict_feature_validation=True),client
