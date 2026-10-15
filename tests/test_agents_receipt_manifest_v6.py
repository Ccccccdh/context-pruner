import json
from pathlib import Path
import tempfile
import unittest
import httpx
from unittest.mock import patch

from experiments.runners import run_agents_receipt_manifest_v6 as v6
from experiments.runners import run_agents_confirmation_v4 as base


class ReceiptRunnerTest(unittest.TestCase):
    def test_transport_failure_produces_durable_incomplete_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v6.prepare(root/'frozen')
            def fail(request): raise httpx.ReadError('controlled transport failure')
            with patch.object(base,'ControlledMock',return_value=fail):
                summary=v6.run_batch(root/'frozen',root/'mock','offline',mock=True)
            audit=json.loads((root/'mock/audit.json').read_text())
            self.assertFalse(audit['complete']);self.assertEqual(audit['requests'],1)
            self.assertEqual(audit['unknown_usage_requests'],1)
            self.assertEqual(audit['unattempted_samples'],5)
            self.assertEqual(summary['samples_completed'],0)

    def test_before_dispatch_failure_is_audited_as_unsent_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v6.prepare(root/'frozen')
            async def reject(self,request): raise base.RequestRejected('controlled before-dispatch failure')
            with patch.object(base.StudyTransport,'handle_async_request',reject):
                v6.run_batch(root/'frozen',root/'mock','offline',mock=True)
            audit=json.loads((root/'mock/audit.json').read_text())
            self.assertFalse(audit['complete']);self.assertEqual(audit['requests'],0)
            self.assertEqual(len(audit['unsent_boundaries']),1)

    def test_actual_sdk_mock_and_single_paid_operation(self):
        old = base.StudyAdapter
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = v6.prepare(root / "frozen")
            self.assertEqual(f["samples"], 6)
            self.assertEqual(f["max_requests"], 96)
            self.assertEqual(f["cap_usd"], 1.)
            summary = v6.run_batch(root / "frozen", root / "mock", "offline", mock=True)
            self.assertEqual(summary["samples_completed"], 6)
            self.assertTrue(all(s["independent"]["passed"] for s in summary["samples"]))
            audit = v6.audit_output(root / "frozen", root / "mock", mock=True)
            self.assertEqual(audit["requests"], 66)
            self.assertTrue(audit["complete"])
            self.assertGreater(audit["manifest_boundaries"], 0)
            with patch.object(v6, "OPERATION_CLAIM", root / "operation.claim"):
                v6.reserve_operation(root / "frozen", root / "paid")
                with self.assertRaises(FileExistsError): v6.reserve_operation(root / "frozen", root / "paid2")
            p=root/'mock/summary.json';s=json.loads(p.read_text());s['samples'][0]['prompt_tokens']+=1;p.write_text(json.dumps(s))
            with self.assertRaises(ValueError): v6.audit_output(root / 'frozen', root / 'mock', mock=True)
        self.assertIs(base.StudyAdapter, old)

    def test_freeze_cannot_increase_caps_or_reset_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);v6.prepare(root / "frozen")
            for key, value in (("cap_usd", 20), ("max_requests", 1000), ("samples", 9), ("plan", [])):
                original = json.loads((root / "frozen/freeze.json").read_text())
                changed = dict(original);changed[key] = value
                base.write(root / "frozen/freeze.json", changed)
                (root / "frozen/freeze.sha256").write_text(base.file_sha(root / "frozen/freeze.json"))
                with self.assertRaises(ValueError): v6.verify(root / "frozen")
                base.write(root / "frozen/freeze.json", original)
                (root / "frozen/freeze.sha256").write_text(base.file_sha(root / "frozen/freeze.json"))


if __name__ == "__main__": unittest.main()
