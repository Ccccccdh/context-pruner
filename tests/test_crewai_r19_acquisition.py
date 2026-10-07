"""Zero-API negative controls for r19 task schema and independent acquisition audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.audits.audit_crewai_r19_acquisition import audit
from experiments.commands.run_crewai_r19_acquisition import ROOT, PINNED, TASK_IDS, MAX_API_REQUESTS, sha
from experiments.runners import crewai_handoff_v17_tasks as loader
from experiments.runners import crewai_semantic_equivalence_v8 as judge

TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r19_acquisition.json"
SMOKE = ROOT / "runs/stage5-crewai/crewai-r19-fullcapture-acquisition-mock-preflight-01"


class R19AcquisitionControls(unittest.TestCase):
    def test_three_complete_new_tasks_and_frozen_judge_shapes(self):
        tasks = loader.load_pilot_tasks(TASK_FILE)
        self.assertEqual(tuple(t["task_id"] for t in tasks), TASK_IDS)
        self.assertEqual(len({tool["name"] for task in tasks for tool in task["tools"]}), 12)
        for task in tasks:
            self.assertEqual(len(task["tools"]), 4)
            handoff = judge.judge_handoff(judge.handle_rule_for(tasks, task["task_id"]),
                                          "HANDOFF " + " ".join(task["handle_facts"]))
            answer = judge.judge_answer(judge.answer_rule_for(tasks, task["task_id"]),
                f"RESULT task={task['task_id']} decision={task['decision']} evidence=" +
                " ".join(task["answer_facts"]))
            self.assertTrue(handoff.strict_pass and handoff.semantic_pass)
            self.assertTrue(answer.strict_pass and answer.semantic_pass)

    def test_independent_audit_fails_on_capture_token_action_and_freeze_tamper(self):
        self.assertTrue(SMOKE.is_dir(), "run mock preflight before audit controls")
        base_manifest = json.loads((SMOKE / "manifest.json").read_text(encoding="utf-8"))
        base_rows = [json.loads(x) for x in (SMOKE / "results.jsonl").read_text(encoding="utf-8").splitlines() if x]
        freeze = {"purpose": "payload_acquisition_only", "max_api_requests": MAX_API_REQUESTS,
                  "tasks": list(TASK_IDS), "methods": ["none"],
                  "citable_as_saving": False,
                  "source_sha256": {x: sha(ROOT / x) for x in PINNED}}
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            f = folder / "freeze.json"
            f.write_text(json.dumps(freeze), encoding="utf-8")
            base_manifest["freeze_sha256"] = sha(f)
            base_manifest["source_sha256"] = freeze["source_sha256"]

            def check(rows, manifest=None):
                (folder / "manifest.json").write_text(json.dumps(manifest or base_manifest), encoding="utf-8")
                (folder / "results.jsonl").write_text("\n".join(json.dumps(x) for x in rows)+"\n", encoding="utf-8")
                return audit(folder, f)

            self.assertTrue(check(base_rows)["complete"])
            missing = copy.deepcopy(base_rows)
            missing[0]["full_attempt_capture"][0]["raw_model_output"] = None
            self.assertTrue(any("missing full output" in x for x in check(missing)["errors"]))
            tokens = copy.deepcopy(base_rows)
            tokens[0]["full_attempt_capture"][0]["input_tokens"] += 1
            self.assertTrue(any("complete token ledger" in x for x in check(tokens)["errors"]))
            action = copy.deepcopy(base_rows)
            action[0]["full_attempt_capture"][0]["host_parsed_action"] = "wrong_tool"
            self.assertTrue(any("parsed/executed tool" in x for x in check(action)["errors"]))
            bad_manifest = copy.deepcopy(base_manifest)
            bad_manifest["freeze_sha256"] = "0" * 64
            self.assertTrue(any("freeze/source binding" in x for x in check(base_rows, bad_manifest)["errors"]))


if __name__ == "__main__":
    unittest.main()
