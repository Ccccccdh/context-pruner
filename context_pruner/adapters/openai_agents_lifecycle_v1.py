"""Opt-in, client-history Agents SDK evidence lifecycle. Frozen adapters stay unchanged.

Control events enter only through trusted host tool callbacks. Model-input calls
advance TTL. One adapter belongs to one task and one serial, non-streaming Runner.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from ..archive import InMemoryArchiveStore, redact_sensitive_text
from ..types import ContextItem, ContextType, ContextStatus, estimate_tokens


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def source_path(value):
    raw = str(value).replace("\\", "/")
    p = PurePosixPath(raw)
    if not raw or p.is_absolute() or ".." in p.parts or ":" in raw or str(p) == ".":
        raise ValueError("evidence path must be workspace-relative")
    return str(p)


def atomic_spans(items):
    """Separate completed batches, retaining reasoning and trailing assistant items.

    An incomplete/unknown batch is never eligible for archive. Every output must
    have exactly one call; duplicate invocation IDs are rejected by synchronization.
    """
    spans, current = [], []
    calls, outputs = [], []

    def flush():
        if current:
            known = all(i.get("type", "message") in {"message", "reasoning", "function_call", "function_call_output"} for i in current)
            valid = known and bool(calls) and len(calls) == len(set(calls)) and sorted(calls) == sorted(outputs)
            spans.append({"items": list(current), "calls": list(calls), "complete": valid})
        current.clear()
        calls.clear()
        outputs.clear()

    for item in items:
        typ = item.get("type", "message")
        role = item.get("role")
        completed = bool(calls) and sorted(calls) == sorted(outputs)
        if (typ in {"function_call", "reasoning"} and completed) or role in {"user", "system", "developer"}:
            flush()
        if not current and typ not in {"function_call", "function_call_output", "reasoning"}:
            spans.append({"items": [item], "calls": [], "complete": False})
            continue
        current.append(item)
        if typ == "function_call":
            calls.append(str(item.get("call_id") or ""))
        elif typ == "function_call_output":
            outputs.append(str(item.get("call_id") or ""))
    flush()
    return spans


class ModelInputBudgetExceeded(RuntimeError):
    pass


class OpenAIAgentsEvidenceLifecycle:
    def __init__(self, *, namespace, trusted_tools, current_versions=None, archive_store=None,
                 lifecycle_enabled=True, recovery_enabled=True, ttl_model_calls=2,
                 recovery_limit=3, max_recovery_attempts=3, hard_limit_tokens=28000,
                 fixed_reserved_tokens=0, token_counter=None):
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("nonempty task namespace required")
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0
               for v in (ttl_model_calls, recovery_limit, max_recovery_attempts, hard_limit_tokens)):
            raise ValueError("positive integer limits required")
        if isinstance(fixed_reserved_tokens, bool) or not isinstance(fixed_reserved_tokens, int) or fixed_reserved_tokens < 0:
            raise ValueError("reserved tokens must be nonnegative")
        self.namespace = namespace
        self.trusted_tools = set(trusted_tools)
        self.archive_store = archive_store if archive_store is not None else InMemoryArchiveStore()
        if getattr(self.archive_store, "namespace", namespace) != namespace:
            raise ValueError("archive namespace mismatch")
        self.current_versions = {}
        self.update_current_versions(current_versions or {})
        self.lifecycle_enabled, self.recovery_enabled = bool(lifecycle_enabled), bool(recovery_enabled)
        self.ttl_model_calls, self.recovery_limit = ttl_model_calls, recovery_limit
        self.max_recovery_attempts, self.hard_limit_tokens = max_recovery_attempts, hard_limit_tokens
        self.fixed_reserved_tokens = fixed_reserved_tokens
        self.token_counter = token_counter or estimate_tokens
        self.clock = 0
        self.receipts, self.groups = {}, {}
        self.completed_stages, self.processed_receipts = set(), set()
        self.attempts, self.events, self.inputs = {}, [], []
        self.history_digests = []
        self.metrics = {k: 0 for k in ("archives", "recoveries", "recovery_misses", "recovery_signals",
                                      "redemotions", "budget_rejections", "redaction_skips", "model_calls")}

    def update_current_versions(self, versions):
        validated = {}
        for path, version in versions.items():
            if not isinstance(version, str) or not version.strip():
                raise ValueError("nonempty host source version required")
            validated[source_path(path)] = version
        self.current_versions = validated

    def observe_tool_result(self, call_id, tool_name, result):
        """Host API: only allowlisted local tool callbacks may supply control receipts."""
        if tool_name not in self.trusted_tools:
            return
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("host callback lacks tool_call_id")
        if not isinstance(result, str):
            raise ValueError("trusted lifecycle tools must return JSON strings")
        try:
            payload = json.loads(result)
        except (ValueError, TypeError):
            return  # ordinary tool error/text does not request recovery
        if not isinstance(payload, dict):
            return
        kind = payload.get("kind")
        if kind not in {"evidence", "stage_complete", "missing_evidence"}:
            return
        if kind == "evidence":
            source_path(payload.get("source_path", ""))
            for field in ("source_version", "entity", "stage"):
                if not isinstance(payload.get(field), str) or not payload[field].strip():
                    raise ValueError("incomplete evidence metadata")
            if "pin" in payload and not isinstance(payload["pin"], bool):
                raise ValueError("pin must be a bool")
        elif not isinstance(payload.get("stage" if kind == "stage_complete" else "entity"), str):
            raise ValueError("incomplete control signal")
        receipt = {"tool_name": tool_name, "payload": payload, "output_sha256": hashlib.sha256(result.encode()).hexdigest()}
        previous = self.receipts.get(call_id)
        if previous is not None and previous != receipt:
            raise ValueError("tool call ID reused with changed source")
        self.receipts[call_id] = receipt

    def _eligible(self, group):
        return all(self.current_versions.get(s["source_path"]) == s["source_version"] for s in group["sources"])

    def _sync(self, spans):
        observed_ids, signals, present = set(), [], set()
        for span in spans:
            for item in span["items"]:
                if item.get("type") == "function_call":
                    cid = item.get("call_id")
                    if not cid or cid in observed_ids:
                        raise ValueError("empty or duplicate invocation ID")
                    observed_ids.add(cid)
            if not span["complete"]:
                continue
            sources = []
            for item in span["items"]:
                if item.get("type") != "function_call_output":
                    continue
                cid = item["call_id"]
                receipt = self.receipts.get(cid)
                if receipt is None:
                    continue
                call = next(i for i in span["items"] if i.get("type") == "function_call" and i["call_id"] == cid)
                if call.get("name") != receipt["tool_name"] or not isinstance(item.get("output"), str) or hashlib.sha256(item["output"].encode()).hexdigest() != receipt["output_sha256"]:
                    raise ValueError("model-visible source differs from host tool receipt")
                payload = receipt["payload"]
                if payload["kind"] == "evidence":
                    sources.append({k: payload[k] for k in ("source_path", "source_version", "entity", "stage")})
                    sources[-1]["source_path"] = source_path(sources[-1]["source_path"])
                    sources[-1]["pin"] = bool(payload.get("pin", False))
                if cid not in self.processed_receipts:
                    self.processed_receipts.add(cid)
                    self.events.append({"kind": "tool_result", "call_id": cid, "clock": self.clock,
                                        "result_kind": payload["kind"], "output_sha256": receipt["output_sha256"]})
                    if payload["kind"] == "stage_complete":
                        self.completed_stages.add(payload["stage"])
                    elif payload["kind"] == "missing_evidence":
                        signals.append(payload["entity"])
                        self.metrics["recovery_signals"] += 1
            if not sources or any(cid not in self.receipts for cid in span["calls"]):
                continue
            gid = "agents:" + digest([self.namespace, span["calls"]])
            present.add(gid)
            span["group_id"] = gid
            record = self.groups.get(gid)
            if record is not None and record["status"] != "active" and record["hash"] != digest(span["items"]):
                raise ValueError("archived source group changed")
            if record is None or record["status"] == "active":
                self.groups[gid] = {"items": deepcopy(span["items"]), "hash": digest(span["items"]),
                                    "sources": sources, "status": "active", "expires_at": None}
        if set(self.groups) - present:
            raise ValueError("complete client-managed history required for registered evidence")
        return signals

    def _validate_group(self, gid, group):
        spans = atomic_spans(group["items"])
        if len(spans) != 1 or not spans[0]["complete"]:
            raise ValueError("checkpoint/archive group has invalid atomic pairing")
        span = spans[0]
        if gid != "agents:" + digest([self.namespace, span["calls"]]) or digest(group["items"]) != group["hash"]:
            raise ValueError("source identity/hash mismatch")
        sources = []
        for item in span["items"]:
            if item.get("type") != "function_call_output":
                continue
            cid = item["call_id"]
            receipt = self.receipts.get(cid)
            call = next(i for i in span["items"] if i.get("type") == "function_call" and i["call_id"] == cid)
            if receipt is None or call.get("name") not in self.trusted_tools or call.get("name") != receipt["tool_name"]:
                raise ValueError("source lacks trusted host receipt")
            raw = item.get("output")
            if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != receipt["output_sha256"] or json.loads(raw) != receipt["payload"]:
                raise ValueError("source output/receipt binding mismatch")
            p = receipt["payload"]
            if p.get("kind") == "evidence":
                source = {k: p[k] for k in ("source_path", "source_version", "entity", "stage")}
                source["source_path"] = source_path(source["source_path"])
                source["pin"] = bool(p.get("pin", False))
                sources.append(source)
        if sources != group["sources"] or not sources:
            raise ValueError("source metadata differs from verified host receipt")

    def _archive(self, gid, group):
        raw = encoded(group["items"])
        if redact_sensitive_text(raw)[1]:
            if not group.get("redaction_skip"):
                self.metrics["redaction_skips"] += 1
                group["redaction_skip"] = True
            return
        item = ContextItem(chunk_id=gid, ctype=ContextType.TOOL_INTERACTION, text=raw,
                           source_event_ids=(gid,), status=ContextStatus.ARCHIVED,
                           metadata={"namespace": self.namespace, "group_hash": group["hash"],
                                     "sources": group["sources"]})
        entry = self.archive_store.put(item, self.clock, "host_stage_complete")
        if entry.item.text != raw:
            return
        group["status"], group["archive_id"] = "archived", entry.archive_id
        self.metrics["archives"] += 1
        self.events.append({"kind": "archived", "source_event_id": gid, "clock": self.clock, "sha256": group["hash"]})

    def _recover(self, entity):
        if not self.recovery_enabled:
            return []
        if any(g["status"] == "recovered" and self._eligible(g) and
               any(s["entity"] == entity for s in g["sources"]) for g in self.groups.values()):
            return []  # repeated signal does not renew the lease
        if self.attempts.get(entity, 0) >= self.max_recovery_attempts:
            return []
        self.attempts[entity] = self.attempts.get(entity, 0) + 1
        entries = {e.archive_id: e for e in self.archive_store.entries()}
        selected = []
        for gid, group in self.groups.items():
            if group["status"] != "archived" or not self._eligible(group) or not any(s["entity"] == entity for s in group["sources"]):
                continue
            self._validate_group(gid, group)
            entry = entries.get(group.get("archive_id"))
            if entry is None:
                continue
            if (entry.item.metadata.get("namespace") != self.namespace or entry.item.source_event_ids != (gid,)
                or entry.item.metadata.get("group_hash") != group["hash"]
                or entry.item.metadata.get("sources") != group["sources"]
                or digest(json.loads(entry.item.text)) != group["hash"]):
                raise ValueError("archive provenance/hash mismatch")
            selected.append(gid)
            if len(selected) == self.recovery_limit:
                break
        if not selected:
            self.metrics["recovery_misses"] += 1
        for gid in selected:
            self.groups[gid].update(status="recovered", expires_at=self.clock + self.ttl_model_calls)
        return selected

    def filter_items(self, items, instructions=None, *, reserved_tokens=0):
        if isinstance(reserved_tokens, bool) or not isinstance(reserved_tokens, int) or reserved_tokens < 0:
            raise ValueError("reserved tokens must be a nonnegative integer")
        original = deepcopy(list(items))
        if any(not isinstance(i, dict) for i in original):
            raise ValueError("Responses input items must be mappings")
        history_digests = [digest(i) for i in original]
        if history_digests[:len(self.history_digests)] != self.history_digests:
            raise ValueError("authoritative history must be complete, immutable and append-only")
        self.clock += 1
        self.metrics["model_calls"] += 1
        spans = atomic_spans(original)
        signals = self._sync(spans)
        new_recoveries = []
        if self.lifecycle_enabled:
            for gid, g in self.groups.items():
                if g["status"] == "recovered" and (self.clock >= g["expires_at"] or not self._eligible(g)):
                    g.update(status="archived", expires_at=None)
                    self.metrics["redemotions"] += 1
                    self.events.append({"kind": "redemoted", "source_event_id": gid, "clock": self.clock})
            latest = next((s.get("group_id") for s in reversed(spans) if s["calls"]), None)
            for gid, g in self.groups.items():
                if g["status"] == "active" and gid != latest and not any(s["pin"] for s in g["sources"]) and all(s["stage"] in self.completed_stages for s in g["sources"]):
                    self._archive(gid, g)
            for entity in dict.fromkeys(signals):
                new_recoveries.extend(self._recover(entity))
        result = [item for span in spans for item in span["items"]
                  if not self.lifecycle_enabled or self.groups.get(span.get("group_id"), {}).get("status") != "archived"]
        tokens = self.token_counter(encoded(result)) + self.token_counter(instructions or "") + self.fixed_reserved_tokens + reserved_tokens
        if tokens > self.hard_limit_tokens:
            self.metrics["budget_rejections"] += 1
            for gid in new_recoveries:
                self.groups[gid].update(status="archived", expires_at=None)
            self.events.append({"kind": "budget_rejected", "clock": self.clock, "tokens": tokens})
            raise ModelInputBudgetExceeded(f"model input estimate {tokens} exceeds {self.hard_limit_tokens}")
        for gid in new_recoveries:
            self.metrics["recoveries"] += 1
            self.events.append({"kind": "recovered_sent", "source_event_id": gid, "clock": self.clock,
                                "expires_at": self.groups[gid]["expires_at"], "sha256": self.groups[gid]["hash"]})
        self.inputs.append({"clock": self.clock, "before_sha256": digest(original), "after_sha256": digest(result),
                            "tokens": tokens, "recovered_groups": list(new_recoveries)})
        self.history_digests = history_digests
        return result

    def __call__(self, data):
        """Direct filter configuration requires caller-managed full history.

        SDK CallModelData.context is application context, not Runner transport
        configuration. Use run_sync for enforced rejection of remote history.
        """
        from agents.run import ModelInputData
        schemas = [{"name": t.name, "description": getattr(t, "description", ""),
                    "parameters": getattr(t, "params_json_schema", {})} for t in data.agent.tools]
        transformed = self.filter_items(data.model_data.input, data.model_data.instructions,
                                        reserved_tokens=self.token_counter(encoded(schemas)))
        return ModelInputData(input=transformed, instructions=data.model_data.instructions)

    def run_sync(self, agent, input, *, context=None, max_turns=10,
                 previous_response_id=None, conversation_id=None, auto_previous_response_id=False):
        """Supported synchronous SDK entry point; rejects remote history before model execution."""
        if previous_response_id is not None or conversation_id is not None or auto_previous_response_id:
            raise ValueError("server-managed/incremental history unsupported")
        from agents import Runner, RunConfig
        return Runner.run_sync(agent, input=input, context=context, max_turns=max_turns,
            previous_response_id=None, conversation_id=None, auto_previous_response_id=False,
            hooks=self.sdk_hooks(), run_config=RunConfig(call_model_input_filter=self, tracing_disabled=True))

    def sdk_hooks(self):
        from agents import RunHooks
        adapter = self

        class EvidenceHooks(RunHooks):
            async def on_tool_end(self, context, agent, tool, result):
                if tool.name in adapter.trusted_tools:
                    adapter.observe_tool_result(getattr(context, "tool_call_id", None), tool.name, result)

        return EvidenceHooks()

    def export_state(self):
        return deepcopy({"schema": "agents_evidence_lifecycle_v1", "namespace": self.namespace,
                         "config": {k: getattr(self, k) for k in ("lifecycle_enabled", "recovery_enabled", "ttl_model_calls", "recovery_limit", "max_recovery_attempts", "hard_limit_tokens", "fixed_reserved_tokens")},
                         "trusted_tools": sorted(self.trusted_tools), "current_versions": self.current_versions,
                         "clock": self.clock, "receipts": self.receipts, "groups": self.groups,
                         "completed_stages": sorted(self.completed_stages), "processed_receipts": sorted(self.processed_receipts),
                         "attempts": self.attempts, "events": self.events, "metrics": self.metrics,
                         "history_digests": self.history_digests})

    @classmethod
    def from_state(cls, state, *, namespace, archive_store=None, token_counter=None):
        if state.get("schema") != "agents_evidence_lifecycle_v1" or state.get("namespace") != namespace:
            raise ValueError("checkpoint namespace/schema mismatch")
        a = cls(namespace=namespace, trusted_tools=state["trusted_tools"], current_versions=state["current_versions"],
                archive_store=archive_store, token_counter=token_counter, **state["config"])
        a.receipts = deepcopy(state["receipts"])
        history = state.get("history_digests")
        if not isinstance(history, list) or any(not isinstance(h, str) or len(h) != 64 for h in history):
            raise ValueError("checkpoint lacks valid authoritative history digests")
        if isinstance(state["clock"], bool) or not isinstance(state["clock"], int) or state["clock"] < 0:
            raise ValueError("invalid model-boundary clock")
        for gid, g in state["groups"].items():
            a._validate_group(gid, g)
            hashes = [digest(i) for i in g["items"]]
            if not any(history[i:i+len(hashes)] == hashes for i in range(len(history)-len(hashes)+1)):
                raise ValueError("checkpoint evidence is absent from authoritative history")
            if g["status"] not in {"active", "archived", "recovered"}:
                raise ValueError("invalid checkpoint lifecycle state")
            for s in g["sources"]:
                source_path(s["source_path"])
            if g["status"] == "recovered" and (not isinstance(g["expires_at"], int) or g["expires_at"] < 0):
                raise ValueError("invalid recovery TTL lease")
            if g["status"] in {"archived", "recovered"}:
                a.archive_store.put(ContextItem(chunk_id=gid, ctype=ContextType.TOOL_INTERACTION,
                    text=encoded(g["items"]), source_event_ids=(gid,), metadata={"namespace": namespace,
                    "group_hash": g["hash"], "sources": g["sources"]}), state["clock"], "checkpoint_restore")
        for key in ("clock", "receipts", "groups", "attempts", "events", "metrics"):
            setattr(a, key, deepcopy(state[key]))
        a.completed_stages, a.processed_receipts = set(state["completed_stages"]), set(state["processed_receipts"])
        a.history_digests = deepcopy(history)
        return a
