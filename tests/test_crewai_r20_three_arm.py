"""Zero-API checks for the r20 three-arm batch: the summary meter, the grid, the decisions.

The r20 pilot's own run cannot be rehearsed for free at the API level, so the pieces that could
silently produce a wrong number are tested here instead: the summary arm's meter (an unmetered
summary request would be an invisible side channel), the grid check (the frozen plan rotates
the arm order, so a fixed-order check would wrongly fail it), and the two decisions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.audits import audit_crewai_r20_orderfree_3arm as batch_audit  # noqa: E402
from experiments.audits import criteria_crewai_r20_orderfree as criteria  # noqa: E402
from experiments.commands import run_crewai_r20_orderfree_3arm as command  # noqa: E402
from experiments.runners import crewai_capture_v20 as capture  # noqa: E402

FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_3ARM_01.json"
MOCK_BATCH = ROOT / "runs/stage5-crewai/crewai-r20-orderfree-3arm-mock-01"


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeUsage:
    def __init__(self, prompt: int, completion: int, cached: int | None = None) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        if cached is not None:
            self.prompt_tokens_details = types.SimpleNamespace(cached_tokens=cached)


class FakeResponse:
    def __init__(self, content: str, usage: FakeUsage) -> None:
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
        self.usage = usage


class FakeAsyncCompletions:
    def __init__(self, usage: FakeUsage) -> None:
        self.usage = usage
        self.requests: list[dict] = []

    async def create(self, **request):
        self.requests.append(request)
        return FakeResponse("summary text", self.usage)


class FakeAsyncClient:
    """The shape the summary path uses: ``.chat.completions.create`` and ``.close``."""

    def __init__(self, usage: FakeUsage) -> None:
        self.completions = FakeAsyncCompletions(usage)
        self.chat = types.SimpleNamespace(completions=self.completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeBudget:
    """The request budget the LLM constructors require."""

    def __init__(self, limit: int = 390) -> None:
        self.limit = limit
        self.used = 0

    def consume(self) -> None:
        self.used += 1


class SummaryMeterTest(unittest.TestCase):
    """An unmetered summary request would be exactly the side channel r17 forbids."""

    def test_summary_request_is_recorded_with_its_cache_split(self) -> None:
        records: list[dict] = []
        usage = FakeUsage(prompt=500, completion=40, cached=300)
        meter = capture.SummaryClientMeter(FakeAsyncClient(usage), records)
        response = asyncio.run(
            meter.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}])
        )
        self.assertIsNotNone(response)
        self.assertEqual(1, len(records))
        entry = records[0]
        self.assertEqual("native_summary_request", entry["kind"])
        self.assertEqual(500, entry["input_tokens"])
        self.assertEqual(40, entry["output_tokens"])
        self.assertEqual(300, entry["prompt_cache_hit_tokens"])
        self.assertEqual(200, entry["prompt_cache_miss_tokens"])
        self.assertEqual("success", entry["status"])
        self.assertIsInstance(entry["off_peak_window"], bool)
        self.assertTrue(entry["utc_started"])
        self.assertTrue(entry["utc_finished"])
        self.assertEqual("", entry["error_type"])

    def test_each_request_gets_its_own_slot(self) -> None:
        records: list[dict] = []
        meter = capture.SummaryClientMeter(
            FakeAsyncClient(FakeUsage(prompt=10, completion=1)), records
        )
        for _ in range(3):
            asyncio.run(meter.chat.completions.create(model="m", messages=[]))
        self.assertEqual([1, 2, 3], [entry["request_slot"] for entry in records])

    def test_a_failed_summary_request_is_recorded_not_hidden(self) -> None:
        records: list[dict] = []

        class Boom(FakeAsyncCompletions):
            async def create(self, **request):
                raise RuntimeError("provider down")

        client = FakeAsyncClient(FakeUsage(prompt=1, completion=1))
        client.completions = Boom(FakeUsage(prompt=1, completion=1))
        client.chat = types.SimpleNamespace(completions=client.completions)
        meter = capture.SummaryClientMeter(client, records)
        with self.assertRaises(RuntimeError):
            asyncio.run(meter.chat.completions.create(model="m", messages=[]))
        self.assertEqual("error", records[0]["status"])
        self.assertEqual("RuntimeError", records[0]["error_type"])

    def test_close_is_delegated(self) -> None:
        client = FakeAsyncClient(FakeUsage(prompt=1, completion=1))
        meter = capture.SummaryClientMeter(client, [])
        asyncio.run(meter.close())
        self.assertTrue(client.closed)

    def test_summary_meter_delegates_unknown_attributes(self) -> None:
        client = FakeAsyncClient(FakeUsage(prompt=1, completion=1))
        client.some_extra = "kept"
        meter = capture.SummaryClientMeter(client, [])
        self.assertEqual("kept", meter.some_extra)


class CaptureConstructionTest(unittest.TestCase):
    """The paid path's own construction, which a mock run never exercises.

    Both bugs this class covers were invisible until the first real API call: the module had
    stopped re-exporting the plain capture class, and the summary meter was built before the
    parent's ``__init__`` had a chance to wipe it.  A test that only instantiates the meter
    directly would have missed both.
    """

    def test_module_exports_the_plain_capture_class(self) -> None:
        self.assertTrue(hasattr(capture, "CapturedOpenAICompatCrewAILLM"))
        self.assertIs(capture.CapturedOpenAICompatCrewAILLM,
                      capture.v19.CapturedOpenAICompatCrewAILLM)

    def test_plain_capture_class_constructs(self) -> None:
        llm = capture.CapturedOpenAICompatCrewAILLM(
            client=types.SimpleNamespace(), model="deepseek-v4-flash",
            request_budget=_FakeBudget(), max_output_tokens=512, thinking_mode="disabled",
            executed_tool_trace=[], frozen_tool_names=["a", "b"],
        )
        self.assertEqual([], llm.capture)
        self.assertEqual(("a", "b"), llm.frozen_tool_names)

    def test_summary_capture_has_its_records_and_a_meter(self) -> None:
        records: list[dict] = []
        llm = capture.CapturedNativeSummaryCrewAILLM(
            client=types.SimpleNamespace(), model="deepseek-v4-flash",
            request_budget=_FakeBudget(), max_output_tokens=512, thinking_mode="disabled",
            summary_client_factory=lambda: FakeAsyncClient(FakeUsage(1, 1)),
            summary_model="deepseek-v4-flash", soft_limit_tokens=1200,
            hard_limit_tokens=3000, target_tokens=900, fixed_reserved_tokens=300,
            max_summary_tokens=1024, max_summary_calls=4,
            executed_tool_trace=[], frozen_tool_names=["a"], summary_records=records,
        )
        self.assertIs(llm.summary_records, records)
        self.assertIsInstance(llm.summary_client, capture.SummaryClientMeter)
        self.assertEqual([], llm.capture)

    def test_re_attaching_the_meter_is_idempotent(self) -> None:
        llm = capture.CapturedNativeSummaryCrewAILLM(
            client=types.SimpleNamespace(), model="deepseek-v4-flash",
            request_budget=_FakeBudget(), max_output_tokens=512, thinking_mode="disabled",
            summary_client_factory=lambda: FakeAsyncClient(FakeUsage(1, 1)),
            summary_model="deepseek-v4-flash", soft_limit_tokens=1200,
            hard_limit_tokens=3000, target_tokens=900, fixed_reserved_tokens=300,
            max_summary_tokens=1024, max_summary_calls=4,
            executed_tool_trace=[], frozen_tool_names=["a"], summary_records=[],
        )
        first = llm.summary_client
        llm.attach_summary_meter()
        self.assertIs(first, llm.summary_client)

    def test_the_metered_summary_client_reaches_the_provider_and_records(self) -> None:
        records: list[dict] = []
        llm = capture.CapturedNativeSummaryCrewAILLM(
            client=types.SimpleNamespace(), model="deepseek-v4-flash",
            request_budget=_FakeBudget(), max_output_tokens=512, thinking_mode="disabled",
            summary_client_factory=lambda: FakeAsyncClient(FakeUsage(500, 40, cached=300)),
            summary_model="deepseek-v4-flash", soft_limit_tokens=1200,
            hard_limit_tokens=3000, target_tokens=900, fixed_reserved_tokens=300,
            max_summary_tokens=1024, max_summary_calls=4,
            executed_tool_trace=[], frozen_tool_names=["a"], summary_records=records,
        )
        asyncio.run(llm.summary_client.chat.completions.create(model="m", messages=[]))
        self.assertEqual(1, len(records))
        self.assertEqual(300, records[0]["prompt_cache_hit_tokens"])
        self.assertEqual(200, records[0]["prompt_cache_miss_tokens"])
        self.assertNotEqual("", records[0]["cache_tokens_source"])


class FrozenGridTest(unittest.TestCase):
    """The frozen plan rotates arm order; a fixed-order check would wrongly fail the run."""

    def test_rotated_plan_is_accepted(self) -> None:
        methods = ["none", "pruner_v1", "native_summary"]
        tasks = ["t1"]
        rows = []
        for repeat in range(3):
            offset = repeat % 3
            rotated = methods[offset:] + methods[:offset]
            rows.extend({"task_id": "t1", "repeat": repeat, "method": m} for m in rotated)
        grid = batch_audit.grid_check(rows, tasks, methods, 3)
        self.assertTrue(grid["complete"])
        self.assertTrue(grid["arm_order_rotated"])

    def test_a_missing_cell_fails_the_grid(self) -> None:
        methods = ["none", "pruner_v1"]
        rows = [{"task_id": "t1", "repeat": 0, "method": "none"}]
        grid = batch_audit.grid_check(rows, ["t1"], methods, 1)
        self.assertFalse(grid["complete"])
        self.assertEqual(1, grid["missing_cells"])

    def test_a_duplicated_cell_fails_the_grid(self) -> None:
        methods = ["none"]
        rows = [
            {"task_id": "t1", "repeat": 0, "method": "none"},
            {"task_id": "t1", "repeat": 0, "method": "none"},
        ]
        grid = batch_audit.grid_check(rows, ["t1"], methods, 1)
        self.assertFalse(grid["complete"])

    def test_an_unbalanced_block_fails_the_grid(self) -> None:
        methods = ["none", "pruner_v1"]
        rows = [
            {"task_id": "t1", "repeat": 0, "method": "none"},
            {"task_id": "t1", "repeat": 0, "method": "none"},
            {"task_id": "t1", "repeat": 0, "method": "pruner_v1"},
        ]
        grid = batch_audit.grid_check(rows, ["t1"], methods, 1)
        self.assertFalse(grid["complete"])
        self.assertEqual(1, grid["unbalanced_blocks"])


class DecisionTest(unittest.TestCase):
    """The two decisions must read complete provider tokens and the frozen criteria only."""

    def _row(self, method: str, tokens: int, *, strict: bool = True, repeat: int = 0,
             trace: list[str] | None = None) -> dict:
        trace = trace or ["a", "b", "c", "d"]
        return {
            "task_id": "t1", "repeat": repeat, "method": method,
            "strict_success": strict, "semantic_success": strict,
            "first_role_trace": trace, "expected_first_role_trace": ["a", "b", "c", "d"],
            "guard_installed": False,
            "full_attempt_capture": [
                {"input_tokens": tokens, "output_tokens": 0,
                 "prompt_cache_hit_tokens": tokens, "prompt_cache_miss_tokens": 0,
                 "cache_tokens_source": "usage_top_level", "utc_started": "2026-10-06T16:00:00Z",
                 "utc_finished": "2026-10-06T16:00:01Z", "off_peak_window": True,
                 "full_input_messages": [{"role": "user", "content": "x"}]}
            ],
        }

    def test_complete_provider_tokens_counts_summary_requests_too(self) -> None:
        row = self._row("native_summary", 100)
        row["native_summary_capture"] = [
            {"input_tokens": 900, "output_tokens": 50, "prompt_cache_hit_tokens": 0,
             "prompt_cache_miss_tokens": 900, "cache_tokens_source": "usage_top_level"}
        ]
        totals = batch_audit.complete_provider_tokens(row)
        self.assertEqual(1050, totals["complete_provider_tokens"])
        self.assertEqual(1, totals["summary_requests"])

    def test_paired_comparison_pairs_by_task_and_repeat(self) -> None:
        import unittest.mock as mock

        # A task id outside the frozen task file has no pair to report, and that must show up
        # as zero units rather than as a pass.
        unmatched = [self._row("none", 1000), self._row("pruner_v1", 600)]
        with mock.patch.object(batch_audit, "TASK_FILE", batch_audit.TASK_FILE):
            paired = batch_audit.paired_comparison(unmatched)
        self.assertEqual(0, paired["arm_summaries"]["pruner_v1"]["units"])

        task_id = "ledger_lock_attestation"
        rows = [
            {**self._row("none", 1000, repeat=0), "task_id": task_id},
            {**self._row("pruner_v1", 600, repeat=0), "task_id": task_id},
            {**self._row("none", 1200, repeat=1), "task_id": task_id},
            {**self._row("pruner_v1", 500, repeat=1), "task_id": task_id},
        ]
        paired = batch_audit.paired_comparison(rows)
        summary = paired["arm_summaries"]["pruner_v1"]
        self.assertEqual(2, summary["units"])
        self.assertEqual(550.0, summary["mean_delta_tokens"])
        self.assertEqual(2, summary["positive_units"])
        self.assertTrue(summary["positive_majority"])
        self.assertTrue(summary["quality_not_below_baseline"])

    def test_a_quality_drop_below_baseline_is_reported(self) -> None:
        task_id = "ledger_lock_attestation"
        rows = [
            {**self._row("none", 1000, strict=True), "task_id": task_id},
            {**self._row("pruner_v1", 600, strict=False), "task_id": task_id},
        ]
        paired = batch_audit.paired_comparison(rows)
        self.assertFalse(
            paired["arm_summaries"]["pruner_v1"]["quality_not_below_baseline"]
        )

    def test_report_ids_never_become_gates(self) -> None:
        self.assertEqual((criteria.CRIT_COVERAGE,), criteria.QUALITY_CRITERIA)


class FreezeTest(unittest.TestCase):
    """The formal freeze is written once and must cover its own arithmetic."""

    def test_freeze_exists_and_covers_the_worst_case(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertTrue(FREEZE.is_file())
        self.assertGreaterEqual(freeze["max_api_requests"],
                                freeze["request_arithmetic"]["worst_case_total"])
        self.assertEqual(390, freeze["max_api_requests"])

    def test_freeze_installs_no_guard_and_records_the_six_cache_fields(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        guard = freeze["guard_configuration"]
        self.assertEqual("none", guard["guard_mode"])
        self.assertEqual([], guard["guard_installed_arms"])
        self.assertEqual("not_installed", guard["order_guard"])
        self.assertEqual("not_installed", guard["actionless_rule"])
        for field in command.CACHE_FIELDS:
            self.assertIn(field, freeze["cache_accounting"]["fields"])
        self.assertEqual(6, len(command.CACHE_FIELDS))

    def test_freeze_is_not_citable_and_names_its_purpose(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("three_arm_pilot", freeze["purpose"])
        self.assertIs(freeze["citable_as_saving"], False)
        self.assertIs(freeze["citable_as_quality_equivalence"], False)

    def test_every_pinned_source_hashes_to_its_frozen_digest(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        for relative, digest in freeze["source_sha256"].items():
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(digest, sha(path), relative)

    def test_the_enforcer_accepts_the_formal_freeze(self) -> None:
        freeze = command.check_freeze(FREEZE)
        self.assertEqual(390, freeze["max_api_requests"])
        self.assertEqual("none", freeze["guard_configuration"]["guard_mode"])

    def test_the_enforcer_refuses_a_guard_scope_change(self) -> None:
        import tempfile

        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        freeze["guard_configuration"]["guard_installed_arms"] = ["pruner_v1"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "altered.json"
            path.write_text(json.dumps(freeze, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(SystemExit):
                command.check_freeze(path)

    def test_the_enforcer_refuses_a_cap_change(self) -> None:
        import tempfile

        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        freeze["max_api_requests"] = 400
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "altered.json"
            path.write_text(json.dumps(freeze, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(SystemExit):
                command.check_freeze(path)

    def test_the_failure_line_catches_a_guard_anomaly(self) -> None:
        """The failure line must fire on a *recorded* guard anomaly, not fail to look."""
        import tempfile

        rows = [{"task_id": "t1", "repeat": 0, "method": "none", "guard_rejections": 0,
                 "guard_installed": False, "guard_exhausted": False,
                 "guard_enabled": False, "guard_rejection_records": []}]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "manifest.json").write_text(
                json.dumps({"guard_installed_arms": []}), encoding="utf-8"
            )
            self.assertEqual([], command.assert_no_guard_activity(output, rows))
            (output / "manifest.json").write_text(
                json.dumps({"guard_installed_arms": ["pruner_v1"]}), encoding="utf-8"
            )
            problems = command.assert_no_guard_activity(output, rows)
            self.assertTrue(any("manifest lists installed guard arms" in p for p in problems))
        for broken in (
            [dict(rows[0], guard_rejections=1)],
            [dict(rows[0], guard_exhausted=True)],
            [dict(rows[0], guard_enabled=True)],
            [dict(rows[0], guard_rejection_records=[{"why": "x"}])],
            [dict(rows[0], guard_installed=True)],
        ):
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                (output / "manifest.json").write_text(
                    json.dumps({"guard_installed_arms": []}), encoding="utf-8"
                )
                self.assertTrue(command.assert_no_guard_activity(output, broken))


if __name__ == "__main__":
    unittest.main()
