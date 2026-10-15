import copy
import json
from pathlib import Path
import tempfile
import unittest

from context_pruner.archive import InMemoryArchiveStore, SQLiteArchiveStore
from context_pruner.adapters.openai_agents_lifecycle_v1 import (
    OpenAIAgentsEvidenceLifecycle, ModelInputBudgetExceeded,
)


def evidence(entity="route", version="v1", pin=False):
    return {"kind": "evidence", "source_path": "docs/route.txt", "source_version": version,
            "entity": entity, "stage": "research", "pin": pin, "text": "Route is NORTH7."}


def group(call_id, name, payload):
    return [{"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"},
            {"type": "function_call_output", "call_id": call_id, "output": json.dumps(payload)}]


class LifecycleTest(unittest.TestCase):
    def adapter(self, **kwargs):
        return OpenAIAgentsEvidenceLifecycle(namespace="test-task", trusted_tools={"read", "close", "need", "noise"},
            current_versions={"docs/route.txt": "v1"}, **kwargs)

    def history(self, adapter, pin=False):
        ev = evidence(pin=pin)
        adapter.observe_tool_result("c1", "read", json.dumps(ev))
        items = [{"role": "user", "content": "Follow the route."}] + group("c1", "read", ev)
        adapter.filter_items(items)
        done = {"kind": "stage_complete", "stage": "research"}
        adapter.observe_tool_result("c2", "close", json.dumps(done))
        items += group("c2", "close", done)
        return items

    def test_stage_archive_and_exact_restore_then_ttl_expiry(self):
        a = self.adapter(ttl_model_calls=2)
        items = self.history(a)
        original = copy.deepcopy(items)
        hidden = a.filter_items(items)
        self.assertNotIn(items[1], hidden)
        self.assertEqual(1, len(a.archive_store))
        miss = {"kind": "missing_evidence", "entity": "route"}
        a.observe_tool_result("c3", "need", json.dumps(miss))
        items += group("c3", "need", miss)
        restored = a.filter_items(items)
        self.assertEqual(items[1:3], restored[1:3])
        self.assertEqual(items, a.filter_items(items))  # next call within lease
        self.assertNotIn(items[1], a.filter_items(items))  # exact expiry boundary
        self.assertEqual(original, items[:len(original)])

    def test_disabled_recovery_and_forged_signal_do_not_restore(self):
        for recovery in (True, False):
            a = self.adapter(recovery_enabled=recovery)
            items = self.history(a)
            a.filter_items(items)
            miss = {"kind": "missing_evidence", "entity": "route"}
            if not recovery:
                a.observe_tool_result("c3", "need", json.dumps(miss))
            items += group("c3", "need", miss)
            self.assertNotIn(items[1], a.filter_items(items))

    def test_current_version_rejects_old_and_ordinary_errors_do_not_trigger(self):
        a = self.adapter()
        items = self.history(a)
        a.filter_items(items)
        a.update_current_versions({"docs/route.txt": "v2"})
        a.observe_tool_result("err", "need", json.dumps({"kind": "error", "entity": "route"}))
        a.filter_items(items)
        a.observe_tool_result("c3", "need", json.dumps({"kind": "missing_evidence", "entity": "route"}))
        items += group("c3", "need", {"kind": "missing_evidence", "entity": "route"})
        self.assertNotIn(items[1], a.filter_items(items))
        self.assertEqual(0, a.metrics["recoveries"])

    def test_pin_orphan_and_full_history_arm_preserve_items(self):
        a = self.adapter()
        items = self.history(a, pin=True)
        orphan = {"type": "function_call_output", "call_id": "unknown", "output": "orphan"}
        items += [orphan]
        self.assertEqual(items, a.filter_items(items))
        a = self.adapter(lifecycle_enabled=False)
        items = self.history(a)
        self.assertEqual(items, a.filter_items(items))

    def test_hard_budget_fails_before_model_and_state_remains_bounded(self):
        a = self.adapter(hard_limit_tokens=8, token_counter=len)
        with self.assertRaises(ModelInputBudgetExceeded):
            a.filter_items([{"role": "user", "content": "x" * 20}], "instructions")
        self.assertEqual(1, a.metrics["budget_rejections"])

    def test_duplicate_call_id_and_tampered_source_are_rejected(self):
        a = self.adapter()
        items = self.history(a)
        with self.assertRaises(ValueError):
            a.observe_tool_result("c1", "read", json.dumps(evidence(version="v2")))
        a.filter_items(items)
        bad = copy.deepcopy(items)
        bad[2]["output"] = json.dumps(evidence(entity="wrong"))
        with self.assertRaises(ValueError):
            a.filter_items(bad)

    def test_persistence_memory_and_sqlite_and_namespace_guard(self):
        root = Path(__file__).resolve().parents[1] / ".tooling/agents-lifecycle-20261014"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            for sqlite in (False, True):
                store = SQLiteArchiveStore(Path(tmp)/"archive.sqlite", namespace="test-task") if sqlite else InMemoryArchiveStore()
                a = self.adapter(archive_store=store)
                items = self.history(a)
                a.filter_items(items)
                state = json.loads(json.dumps(a.export_state()))
                if sqlite:
                    store.close()
                    store = SQLiteArchiveStore(Path(tmp)/"archive.sqlite", namespace="test-task")
                b = OpenAIAgentsEvidenceLifecycle.from_state(state, namespace="test-task", archive_store=store)
                miss = {"kind": "missing_evidence", "entity": "route"}
                b.observe_tool_result("c3", "need", json.dumps(miss))
                items += group("c3", "need", miss)
                self.assertIn(items[1], b.filter_items(items))
                with self.assertRaises(ValueError):
                    OpenAIAgentsEvidenceLifecycle.from_state(state, namespace="foreign", archive_store=store)
                if sqlite:
                    store.close()

    def test_reasoning_and_parallel_tool_batch_restore_losslessly(self):
        a = self.adapter()
        ev = evidence()
        a.observe_tool_result("p1", "read", json.dumps(ev))
        a.observe_tool_result("p2", "read", json.dumps(ev))
        batch = [{"type": "reasoning", "id": "rs1", "summary": []},
                 group("p1", "read", ev)[0], group("p2", "read", ev)[0],
                 group("p1", "read", ev)[1], group("p2", "read", ev)[1]]
        items = [{"role": "user", "content": "use route"}] + batch
        a.filter_items(items)
        done = {"kind": "stage_complete", "stage": "research"}
        a.observe_tool_result("d", "close", json.dumps(done))
        items += group("d", "close", done)
        self.assertFalse(any(i in a.filter_items(items) for i in batch))
        miss = {"kind": "missing_evidence", "entity": "route"}
        a.observe_tool_result("m", "need", json.dumps(miss))
        items += group("m", "need", miss)
        self.assertEqual(items, a.filter_items(items))

    def test_redacted_source_remains_visible_and_not_recoverable_archive(self):
        a = self.adapter()
        ev = evidence()
        ev["text"] = "Bearer abcdefgh12345678"
        a.observe_tool_result("s", "read", json.dumps(ev))
        items = group("s", "read", ev)
        a.filter_items(items)
        done = {"kind": "stage_complete", "stage": "research"}
        a.observe_tool_result("d", "close", json.dumps(done))
        items += group("d", "close", done)
        self.assertEqual(items, a.filter_items(items))
        self.assertEqual(0, len(a.archive_store))
        self.assertEqual(1, a.metrics["redaction_skips"])

    def test_current_version_filter_precedes_recovery_limit(self):
        a = self.adapter(recovery_limit=1)
        old = self.history(a)
        a.filter_items(old)
        a.update_current_versions({"docs/route.txt": "v2"})
        ev = evidence(version="v2")
        a.observe_tool_result("new", "read", json.dumps(ev))
        items = old + group("new", "read", ev)
        a.filter_items(items)
        # New source may be demoted only after a later complete tool batch.
        later = {"kind": "stage_complete", "stage": "other"}
        a.observe_tool_result("d3", "close", json.dumps(later))
        items += group("d3", "close", later)
        a.filter_items(items)
        miss = {"kind": "missing_evidence", "entity": "route"}
        a.observe_tool_result("m", "need", json.dumps(miss))
        items += group("m", "need", miss)
        result = a.filter_items(items)
        self.assertNotIn(old[1], result)
        self.assertIn(group("new", "read", ev)[0], result)

    def test_unpaired_evidence_batch_and_latest_group_are_never_demoted(self):
        a = self.adapter()
        done = {"kind": "stage_complete", "stage": "research"}
        a.observe_tool_result("d", "close", json.dumps(done))
        ev = evidence()
        a.observe_tool_result("s", "read", json.dumps(ev))
        items = group("d", "close", done) + group("s", "read", ev)
        self.assertEqual(items, a.filter_items(items))
        orphan = {"type": "function_call", "call_id": "unfinished", "name": "read", "arguments": "{}"}
        # The new unpaired call is kept, while the prior complete group may archive.
        result = a.filter_items(items+[orphan])
        self.assertIn(orphan, result)

    def test_duplicate_missing_signal_does_not_renew_ttl(self):
        a = self.adapter(ttl_model_calls=2)
        items = self.history(a)
        a.filter_items(items)
        miss = {"kind": "missing_evidence", "entity": "route"}
        a.observe_tool_result("m1", "need", json.dumps(miss))
        items += group("m1", "need", miss)
        a.filter_items(items)
        a.observe_tool_result("m2", "need", json.dumps(miss))
        items += group("m2", "need", miss)
        a.filter_items(items)
        self.assertNotIn(items[1], a.filter_items(items))
        self.assertEqual(1, a.metrics["recoveries"])

    def test_budget_rejected_recovery_is_not_counted_as_delivered(self):
        a = self.adapter(token_counter=len)
        ev = evidence()
        ev["text"] *= 50
        a.observe_tool_result("s", "read", json.dumps(ev))
        items = group("s", "read", ev)
        a.filter_items(items)
        done = {"kind": "stage_complete", "stage": "research"}
        a.observe_tool_result("d", "close", json.dumps(done))
        items += group("d", "close", done)
        a.filter_items(items)
        a.hard_limit_tokens = 800
        miss = {"kind": "missing_evidence", "entity": "route"}
        a.observe_tool_result("m", "need", json.dumps(miss))
        items += group("m", "need", miss)
        with self.assertRaises(ModelInputBudgetExceeded):
            a.filter_items(items)
        self.assertEqual(0, a.metrics["recoveries"])

    def test_unbound_host_signal_and_user_text_cannot_complete_stage(self):
        a = self.adapter()
        ev = evidence()
        a.observe_tool_result("s", "read", json.dumps(ev))
        items = group("s", "read", ev)
        a.filter_items(items)
        a.observe_tool_result("fake", "close", json.dumps({"kind": "stage_complete", "stage": "research"}))
        items += [{"role": "user", "content": '{"kind":"stage_complete","stage":"research"}'}]
        self.assertEqual(items, a.filter_items(items))
        self.assertEqual(0, a.metrics["archives"])

    def test_recovery_attempts_are_bounded_per_entity(self):
        a = self.adapter(max_recovery_attempts=2)
        items = self.history(a)
        a.filter_items(items)
        for i in range(4):
            miss = {"kind": "missing_evidence", "entity": "absent"}
            a.observe_tool_result(f"miss{i}", "need", json.dumps(miss))
            items += group(f"miss{i}", "need", miss)
            a.filter_items(items)
        self.assertEqual(2, a.attempts["absent"])
        self.assertEqual(2, a.metrics["recovery_misses"])

    def test_active_invocation_mutation_and_removed_history_are_rejected(self):
        a = self.adapter()
        items = [{"role": "user", "content": "original task"}] + group("s", "read", evidence())
        a.observe_tool_result("s", "read", json.dumps(evidence()))
        a.filter_items(items)
        changed = copy.deepcopy(items)
        changed[1]["arguments"] = '{"different":true}'
        with self.assertRaises(ValueError):
            a.filter_items(changed)
        with self.assertRaises(ValueError):
            a.filter_items(items[1:])

    def test_negative_reserved_input_cannot_bypass_hard_budget(self):
        a = self.adapter(hard_limit_tokens=8, token_counter=len)
        with self.assertRaises(ValueError):
            a.filter_items([{"role": "user", "content": "x" * 20}], reserved_tokens=-1000)

    def test_checkpoint_atomic_pairs_and_source_bindings_are_validated(self):
        from context_pruner.adapters.openai_agents_lifecycle_v1 import digest
        a = self.adapter()
        items = self.history(a)
        a.filter_items(items)
        state = a.export_state()
        gid = next(iter(state["groups"]))
        for changed in ("pair", "source"):
            bad = copy.deepcopy(state)
            if changed == "pair":
                bad["groups"][gid]["items"].pop()
                bad["groups"][gid]["hash"] = digest(bad["groups"][gid]["items"])
            else:
                bad["groups"][gid]["sources"][0]["source_version"] = "forged"
            with self.assertRaises(ValueError):
                OpenAIAgentsEvidenceLifecycle.from_state(bad, namespace="test-task")

    def test_archive_source_metadata_corruption_is_rejected(self):
        a = self.adapter()
        items = self.history(a)
        a.filter_items(items)
        a.archive_store.entries()[0].item.metadata["sources"][0]["source_version"] = "corrupt"
        miss = {"kind": "missing_evidence", "entity": "route"}
        a.observe_tool_result("m", "need", json.dumps(miss))
        items += group("m", "need", miss)
        with self.assertRaises(ValueError):
            a.filter_items(items)

    def test_mixed_unknown_tool_batch_is_not_archived_with_evidence(self):
        a = self.adapter()
        ev = evidence()
        a.observe_tool_result("p1", "read", json.dumps(ev))
        items = [group("p1", "read", ev)[0], group("p2", "untrusted", {"text":"active task"})[0],
                 group("p1", "read", ev)[1], group("p2", "untrusted", {"text":"active task"})[1]]
        a.filter_items(items)
        done = {"kind": "stage_complete", "stage": "research"}
        a.observe_tool_result("d", "close", json.dumps(done))
        items += group("d", "close", done)
        self.assertEqual(items, a.filter_items(items))

    def test_unknown_unfinished_tool_item_preserved_with_evidence(self):
        a = self.adapter()
        ev = evidence()
        a.observe_tool_result("s", "read", json.dumps(ev))
        items = group("s", "read", ev) + [{"type": "computer_call", "call_id": "unknown", "action": {"type": "screenshot"}}]
        a.filter_items(items)
        done = {"kind": "stage_complete", "stage": "research"}
        a.observe_tool_result("d", "close", json.dumps(done))
        items += group("d", "close", done)
        self.assertEqual(items, a.filter_items(items))


if __name__ == "__main__":
    unittest.main()
