"""Request-cap recovery controls for the new r22 ID; no provider calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

from experiments.commands.run_crewai_r22_orderfree_3arm import budget_class, prior_slots


def setup_partial(tmp_path: Path, calls: int = 2) -> tuple[Path, Path]:
    out = tmp_path / "batch"
    out.mkdir()
    freeze = tmp_path / "freeze.json"
    freeze.write_text('{"batch":"control"}\n', encoding="utf-8")
    digest = hashlib.sha256(freeze.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps({
        "freeze_sha256": digest, "mode": "api", "max_api_requests": 390,
    }) + "\n", encoding="utf-8")
    budget = budget_class(out, 0)(390)
    for _ in range(calls):
        budget.consume()
    (out / "results.jsonl").write_text(json.dumps({
        "task_id": "sample", "repeat": 0, "method": "none",
        "api_request_attempts": calls,
    }) + "\n", encoding="utf-8")
    return out, freeze


class CumulativeBudgetTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".tooling/tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_resume_starts_after_prior_charged_slots(self):
        out, freeze = setup_partial(self.root)
        self.assertEqual(prior_slots(out, freeze), 2)
        restored = budget_class(out, 2)(390)
        self.assertEqual(restored.used, 2)
        restored.consume()
        self.assertEqual(restored.used, 3)
        self.assertEqual([json.loads(line)["slot"] for line in (out / "request-slots.jsonl")
                          .read_text(encoding="utf-8").splitlines()], [1, 2, 3])

    def test_resume_refuses_unrecorded_and_duplicate_attempts(self):
        out, freeze = setup_partial(self.root)
        budget_class(out, 2)(390).consume()
        with self.assertRaisesRegex(ValueError, "unrecorded request"):
            prior_slots(out, freeze)
        (out / "request-slots.jsonl").write_text('{"slot":1}\n{"slot":1}\n',
                                                  encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not contiguous"):
            prior_slots(out, freeze)

    def test_resume_refuses_manifest_or_trailing_row_drift(self):
        out, freeze = setup_partial(self.root)
        freeze.write_text('{"batch":"changed"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            prior_slots(out, freeze)
        freeze.write_text('{"batch":"control"}\n', encoding="utf-8")
        with (out / "results.jsonl").open("ab") as handle:
            handle.write(b"{")
        with self.assertRaisesRegex(ValueError, "incomplete trailing"):
            prior_slots(out, freeze)

    def test_cli_plan_forwards_flag_without_creating_batch(self):
        batch_root = self.root / "plan"
        result = subprocess.run(
            [sys.executable, "-m", "experiments.commands.run_crewai_r22_orderfree_3arm",
             "--plan", "--out", str(batch_root)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No API request was sent in --plan mode", result.stdout)
        self.assertFalse(batch_root.exists())

