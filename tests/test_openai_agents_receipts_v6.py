import copy
import json
import unittest

from context_pruner.adapters.openai_agents_receipts_v6 import OpenAIAgentsReceiptLifecycle
from context_pruner.adapters.openai_agents_lifecycle_v1 import ModelInputBudgetExceeded, atomic_spans, encoded


def group(cid, name, payload):
    return [{"type": "function_call", "call_id": cid, "name": name, "arguments": "{}"},
            {"type": "function_call_output", "call_id": cid, "output": json.dumps(payload)}]


class ReceiptTest(unittest.TestCase):
    def adapter(self, **kw):
        return OpenAIAgentsReceiptLifecycle(namespace="local", trusted_tools={"read_document", "close_research"},
            current_versions={"docs/north.json": "a" * 64, "docs/south.json": "b" * 64}, **kw)

    def history(self, a, mixed=False, historical=False, trusted=True):
        h = [{"role": "user", "content": "task"}]
        for entity, version in (("north", "c" * 64 if historical else "a" * 64), ("south", "b" * 64)):
            p = dict(kind="evidence", entity=entity, source_path=f"docs/{entity}.json", source_version=version,
                     stage="research", text="PRIVATE RECORD CONTENT")
            if trusted: a.observe_tool_result(entity, "read_document", json.dumps(p))
            h += group(entity, "read_document", p)
        if not mixed: a.filter_items(h)
        p = dict(kind="stage_complete", stage="research", summary="summary without identifiers")
        if trusted: a.observe_tool_result("close", "close_research", json.dumps(p))
        if mixed:
            h = h[:1] + [h[1], h[3], group("close", "close_research", p)[0], h[2], h[4], group("close", "close_research", p)[1]]
        else: h += group("close", "close_research", p)
        return h

    def manifest(self, visible):
        values = [json.loads(i["content"]) for i in visible if i.get("role") == "user" and isinstance(i.get("content"), str) and i["content"].startswith('{"kind":"host_source_receipts"')]
        self.assertEqual(len(values), 1)
        return values[0]

    def test_preserves_only_identifiers_across_arms_and_boundaries(self):
        manifests = []
        for life, recovery in ((False, False), (True, False), (True, True)):
            a = self.adapter(lifecycle_enabled=life, recovery_enabled=recovery)
            h = self.history(a); original = copy.deepcopy(h)
            visible = a.filter_items(h); m = self.manifest(visible); manifests.append(m)
            self.assertEqual([s["entity"] for s in m["sources"]], ["north", "south"])
            self.assertNotIn("PRIVATE", json.dumps(m))
            self.assertEqual(m, self.manifest(a.filter_items(h)))
            self.assertEqual(h, original)
            self.assertEqual(a.metrics["recoveries"], 0)
            self.assertTrue(all(s["complete"] for s in atomic_spans(visible) if s["calls"]))
        self.assertEqual(manifests[0], manifests[1]); self.assertEqual(manifests[1], manifests[2])

    def test_forged_and_unclosed_receipts_do_not_create_manifest(self):
        a = self.adapter(); h = self.history(a, trusted=False)
        self.assertFalse(any(i.get("content", "").startswith('{"kind":"host_source_receipts"') for i in a.filter_items(h)))
        a = self.adapter(); self.assertEqual(a.filter_items([{"role": "user", "content": "start"}]), [{"role": "user", "content": "start"}])

    def test_historical_and_changed_versions_excluded(self):
        a = self.adapter(); h = self.history(a, historical=True)
        self.assertEqual([s["entity"] for s in self.manifest(a.filter_items(h))["sources"]], ["south"])
        a.update_current_versions({"docs/north.json": "d" * 64, "docs/south.json": "e" * 64})
        self.assertEqual(self.manifest(a.filter_items(h))["sources"], [])

    def test_mixed_atomic_group_never_split_and_manifest_survives_archive(self):
        a = self.adapter(); h = self.history(a, mixed=True)
        a.filter_items(h)
        h += group("next", "ordinary", {"kind": "work_order"})
        visible = a.filter_items(h)
        self.assertEqual(len(self.manifest(visible)["sources"]), 2)
        self.assertNotIn("PRIVATE RECORD CONTENT", json.dumps(visible))
        self.assertTrue(all(s["complete"] for s in atomic_spans(visible) if s["calls"]))

    def test_checkpoint_namespace_and_deterministic_reconstruction(self):
        a = self.adapter(); h = self.history(a); expected = self.manifest(a.filter_items(h))
        restored = OpenAIAgentsReceiptLifecycle.from_state(a.export_state(), namespace="local")
        self.assertEqual(expected, self.manifest(restored.filter_items(h)))
        with self.assertRaises(ValueError): OpenAIAgentsReceiptLifecycle.from_state(a.export_state(), namespace="other")
        bad = a.export_state(); bad.pop("receipt_projection_schema")
        with self.assertRaises(ValueError): OpenAIAgentsReceiptLifecycle.from_state(bad, namespace="local")

    def test_final_projection_is_budgeted_and_manifest_bounded(self):
        a = self.adapter(); h = self.history(a); a.filter_items(h)
        a.hard_limit_tokens = 1
        with self.assertRaises(ModelInputBudgetExceeded): a.filter_items(h)
        a = self.adapter(); h = self.history(a)
        a.manifest_max_bytes = 20
        with self.assertRaises(ValueError): a.filter_items(h)

    def test_visible_output_must_equal_trusted_receipt(self):
        a = self.adapter(); h = self.history(a)
        h[2]["output"] = h[2]["output"].replace("PRIVATE", "CHANGED")
        with self.assertRaises(ValueError): a.filter_items(h)

    def test_valid_read_batched_with_nonreceipt_tool_still_preserves_identity(self):
        a = self.adapter()
        payload = dict(kind='evidence', entity='north', source_path='docs/north.json',
                       source_version='a'*64, stage='research', text='record')
        a.observe_tool_result('read', 'read_document', json.dumps(payload))
        read = group('read', 'read_document', payload)
        ordinary = group('ordinary', 'work_order', {'kind':'error','error':'not yet closed'})
        h = [{'role':'user','content':'task'}, read[0], ordinary[0], read[1], ordinary[1]]
        a.filter_items(h)
        done = {'kind':'stage_complete','stage':'research'}
        a.observe_tool_result('close','close_research',json.dumps(done))
        h += group('close','close_research',done)
        self.assertEqual(self.manifest(a.filter_items(h))['sources'],
                         [dict(entity='north',source_path='docs/north.json',source_version='a'*64)])

    def test_final_only_overflow_does_not_record_unsent_recovery(self):
        a = self.adapter(); a.trusted_tools.add('retrieve_evidence')
        h = self.history(a); a.filter_items(h)
        need = dict(kind='missing_evidence',entity='north')
        a.observe_tool_result('need','retrieve_evidence',json.dumps(need))
        h += group('need','retrieve_evidence',need)
        probe = OpenAIAgentsReceiptLifecycle.from_state(a.export_state(),namespace='local')
        final = probe.filter_items(h)
        before = a.token_counter(encoded(probe.lifecycle_visible))
        after = a.token_counter(encoded(final))
        self.assertGreater(after,before)
        a.hard_limit_tokens = (before+after)//2
        inputs_before = len(a.inputs)
        with self.assertRaises(ModelInputBudgetExceeded): a.filter_items(h)
        self.assertEqual(a.metrics['recoveries'],0)
        self.assertFalse(any(e['kind']=='recovered_sent' for e in a.events))
        self.assertFalse(any(g['status']=='recovered' for g in a.groups.values()))
        self.assertEqual(len(a.inputs),inputs_before)


if __name__ == "__main__": unittest.main()
