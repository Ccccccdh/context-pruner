"""Observed-source metadata projection; does not restore evidence or mutate history."""
from copy import deepcopy
import re

from .openai_agents_lifecycle_v1 import (
    OpenAIAgentsEvidenceLifecycle, ModelInputBudgetExceeded, atomic_spans, digest, encoded, source_path,
)


class OpenAIAgentsReceiptLifecycle(OpenAIAgentsEvidenceLifecycle):
    manifest_max_bytes = 2048

    def filter_items(self, items, instructions=None, *, reserved_tokens=0):
        input_count, event_count = len(self.inputs), len(self.events)
        recoveries_before = self.metrics['recoveries']
        history_before = deepcopy(self.history_digests)
        try:
            return self._filter_with_receipts(items, instructions, reserved_tokens=reserved_tokens)
        except Exception:
            # V1 commits its view before the added projection is budgeted. An
            # unsuccessful projection must not claim a recovery was delivered.
            sent = [e['source_event_id'] for e in self.events[event_count:] if e['kind'] == 'recovered_sent']
            for gid in sent: self.groups[gid].update(status='archived', expires_at=None)
            self.metrics['recoveries'] = recoveries_before
            self.events[event_count:] = [e for e in self.events[event_count:] if e['kind'] != 'recovered_sent']
            self.inputs[input_count:] = []
            self.history_digests = history_before
            self.receipt_projection = None
            raise

    def _filter_with_receipts(self, items, instructions=None, *, reserved_tokens=0):
        original = deepcopy(list(items))
        visible = super().filter_items(original, instructions, reserved_tokens=reserved_tokens)
        self.lifecycle_visible = deepcopy(visible)
        self.receipt_projection = None
        spans = atomic_spans(original)
        anchor = None
        for index, span in enumerate(spans):
            for item in span["items"]:
                if item.get("type") != "function_call_output":
                    continue
                cid = item.get("call_id")
                receipt = self.receipts.get(cid)
                if (cid in self.processed_receipts and receipt and receipt["tool_name"] == "close_research"
                        and receipt["payload"].get("kind") == "stage_complete"
                        and receipt["payload"].get("stage") == "research"):
                    anchor = index
                    break
            if anchor is not None:
                break
        if anchor is None:
            return visible
        sources = {}
        for cid, receipt in self.receipts.items():
            if cid not in self.processed_receipts or receipt['tool_name'] != 'read_document' or receipt['payload'].get('kind') != 'evidence':
                continue
            source = receipt['payload']
            entity, path, version = source['entity'], source_path(source['source_path']), source['source_version']
            if self.current_versions.get(path) != version:
                continue
            if entity not in {'north', 'south'} or path != f'docs/{entity}.json' or not re.fullmatch(r'[0-9a-f]{64}', version):
                raise ValueError('unsupported current source receipt')
            sources[(entity, path)] = dict(entity=entity, source_path=path, source_version=version)
        manifest = {"kind": "host_source_receipts", "schema": "agents_receipts_v6",
                    "sources": [sources[key] for key in sorted(sources)]}
        content = encoded(manifest)
        if len(sources) > 2 or len(content.encode("utf-8")) > self.manifest_max_bytes:
            raise ValueError("receipt manifest byte ceiling")
        position = 0
        for span in spans[:anchor + 1]:
            gid = "agents:" + digest([self.namespace, span["calls"]]) if span["calls"] else None
            if not self.lifecycle_enabled or self.groups.get(gid, {}).get("status") != "archived":
                position += len(span["items"])
        visible.insert(position, {"role": "user", "content": content})
        tokens = self.token_counter(encoded(visible)) + self.token_counter(instructions or "") + self.fixed_reserved_tokens + reserved_tokens
        if tokens > self.hard_limit_tokens:
            self.metrics["budget_rejections"] += 1
            raise ModelInputBudgetExceeded("final receipt projection exceeds model input budget")
        self.receipt_projection = {"manifest": manifest, "manifest_sha256": digest(manifest),
                                   "bytes": len(content.encode("utf-8")), "position": position}
        self.inputs[-1].update(after_sha256=digest(visible), tokens=tokens)
        return visible

    def export_state(self):
        return {**super().export_state(), "receipt_projection_schema": "agents_receipts_v6"}

    @classmethod
    def from_state(cls, state, *, namespace, archive_store=None, token_counter=None):
        if state.get("receipt_projection_schema") != "agents_receipts_v6":
            raise ValueError("receipt projection checkpoint schema mismatch")
        return super().from_state(state, namespace=namespace, archive_store=archive_store, token_counter=token_counter)
