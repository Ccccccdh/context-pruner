from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from experiments.runners.run_agents_checkpoint_natural_v2 import (
    select_checkpoints, build_plan, parse_action, verify_freeze,
)

ROOT=Path(__file__).resolve().parents[1]
REPLAY=ROOT/"runs/agents-evidence-lifecycle-v1-20261014/checkpoint-replay"


class CheckpointNaturalTest(unittest.TestCase):
    def test_actual_selection_rule_reduces_matrix_without_replacement(self):
        selected=select_checkpoints(REPLAY)
        self.assertEqual(4,len(selected))
        self.assertEqual({"django-15569","django-15572"},{r["task"] for r in selected})
        self.assertEqual(["django-15569-k0-r1-c3","django-15569-k0-r1-c4",
                          "django-15572-k0-r1-c4","django-15572-k1-r3-c3"],[r["id"] for r in selected])
        plan=build_plan(selected)
        self.assertEqual(36,len(plan))
        self.assertEqual(36,len({r["sample_id"] for r in plan}))
        for row in plan:
            self.assertIn(row["arm"],{"none","k1_guarded","k0_guarded"})

    def test_action_parser_rejects_malformed_ranges_and_classifies_read(self):
        self.assertEqual("read",parse_action('{"read":{"path":"django/x.py","first":1,"last":20}}')["kind"])
        self.assertEqual("invalid",parse_action('{"read":{"first":"nope"}}')["kind"])
        self.assertEqual("invalid",parse_action("plain text")["kind"])

    def test_candidate_patch_cannot_change_independent_tests_or_escape_source(self):
        from experiments.runners.run_agents_checkpoint_natural_v2 import check_patch_scope
        for path in ('tests/runtests.py','tests/schema/tests.py','django/../../tests/test.py','../other.py'):
            patch=f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n'
            self.assertFalse(check_patch_scope(patch)["allowed"])
        patch='diff --git a/django/db/models/sql/compiler.py b/django/db/models/sql/compiler.py\n--- a/django/db/models/sql/compiler.py\n+++ b/django/db/models/sql/compiler.py\n@@ -1 +1 @@\n-old\n+new\n'
        self.assertTrue(check_patch_scope(patch)["allowed"])

    def test_malformed_patch_schema_is_not_coerced_to_valid_patch(self):
        self.assertEqual("invalid",parse_action('{"patch":{"fake":1}}')["kind"])

    def test_quality_freeze_includes_hidden_assets_source_state_and_runtime(self):
        from experiments.runners.run_agents_checkpoint_natural_v2 import capture_quality_assets
        assets=capture_quality_assets(["django-15569"])
        self.assertTrue(any(p.endswith('host-tests.patch') for p in assets['files_sha256']))
        self.assertTrue(any(p.endswith('instance.json') for p in assets['files_sha256']))
        self.assertTrue(any(p.endswith('reference.patch') for p in assets['files_sha256']))
        self.assertTrue(assets['clones']['django-15569']['ok'])
        self.assertIn('python_version',assets['runtime'])

    def test_cleanup_failure_cannot_suppress_batch_summary(self):
        from experiments.runners.run_agents_checkpoint_natural_v2 import finalize_batch
        with tempfile.TemporaryDirectory(dir=ROOT/".tooling/agents-natural-v2-tests") as tmp:
            async def fail():raise OSError('controlled close failure')
            client=SimpleNamespace(close=fail)
            ledger=SimpleNamespace(records=[],reserved_usd=0.)
            freeze={"experiment_id":"test","samples":0}
            summary=__import__('asyncio').run(finalize_batch(client,Path(tmp),freeze,"sha",[],ledger,None,True))
            self.assertEqual('OSError',summary['cleanup_error'])
            self.assertTrue((Path(tmp)/'summary.json').exists())

    def test_freeze_tampering_is_rejected(self):
        base=ROOT/".tooling/agents-natural-v2-tests"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            p=Path(tmp);(p/"freeze.json").write_text(json.dumps({"schema":"wrong"}))
            with self.assertRaises(ValueError):verify_freeze(p)

    def test_endpoint_task_clone_gate_uses_its_actual_contract(self):
        from experiments.runners.run_agents_checkpoint_natural_v2 import local_quality_gates
        task=SimpleNamespace(upstream_dir=ROOT,instance={"base_commit":"expected"},
            verify_clone=lambda:{"ok":False,"head":"wrong","expected":"expected"})
        endpoint=SimpleNamespace(Task=SimpleNamespace(load=lambda _:task))
        with patch("experiments.runners.run_agents_checkpoint_natural_v2.load_tooling",return_value=SimpleNamespace(_endpoint=lambda:endpoint)),patch("experiments.runners.run_agents_checkpoint_natural_v2.subprocess.run",return_value=SimpleNamespace(stdout="wrong")):
            with self.assertRaises(ValueError):local_quality_gates(["task"],"test")


if __name__ == "__main__":unittest.main()
