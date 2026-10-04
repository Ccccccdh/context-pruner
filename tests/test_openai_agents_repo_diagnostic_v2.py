"""Real SDK, zero-provider gate for v2 input evidence wiring."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from context_pruner import ContextBudget
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners.run_openai_agents_repo_diagnostic_v2 import evidence_wiring
from experiments.audits.audit_openai_agents_repo_diagnostic_v2 import audit_evidence


class RepoDiagnosticV2Gate(unittest.TestCase):
    def test_provider_model_factory_is_wrapped_without_network(self):
        gate = Path(__file__).resolve().parents[1] / '.tooling/gate_openai_agents_3arm.py'
        spec = importlib.util.spec_from_file_location('openai_v2_factory_gate_helpers', gate)
        assert spec and spec.loader
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        case = v1.build_case('pytest_mro', 0)
        request_budget = base.RequestBudget(30)
        client = helpers.StubAsyncOpenAI(request_budget)

        def fake_recording_model(*args, **kwargs):
            return helpers.StubApiModel(case, request_budget=request_budget)

        async def run_one(output):
            filt = base._build_filter(
                'pruner_v1', case=case, client=client, model_name='stub-model',
                budget=ContextBudget(5435, 16304, 4076), fixed_reserved_tokens=512,
                max_summary_tokens=1024, max_summary_calls=4, timeout=30.0,
                request_budget=request_budget,
            )
            return await base.run_case(
                case, method='pruner_v1', client=client, model_name='stub-model',
                request_budget=request_budget, budget=ContextBudget(5435, 16304, 4076),
                fixed_reserved_tokens=512, max_turns=6, max_output_tokens=256,
                max_api_retries=0, retry_base_delay=0.0,
                input_cost_per_million=0.0, output_cost_per_million=0.0,
                context_filter=filt,
            )

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(base, 'RecordingRetryModel', fake_recording_model), evidence_wiring(Path(temp)):
                row = asyncio.run(run_one(Path(temp)))
            self.assertEqual(row['model_calls'], row['input_evidence_model_calls'])
            self.assertEqual([], audit_evidence(Path(temp), [row]))

    def test_all_three_arms_record_model_boundary_without_changing_sdk_run(self):
        gate = Path(__file__).resolve().parents[1] / '.tooling/gate_openai_agents_3arm.py'
        spec = importlib.util.spec_from_file_location('openai_v2_runner_gate_helpers', gate)
        assert spec and spec.loader
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)

        async def run_grid():
            rows = []
            for repeat in range(3):
                for task in v1.TASKS:
                    case = v1.build_case(task, repeat)
                    for method in ('none', 'native_summary', 'pruner_v1'):
                        rows.append(await helpers._run_arm_case(
                            task, repeat, method, budget=ContextBudget(5435, 16304, 4076), case=case,
                        ))
            return rows

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with evidence_wiring(output), patch.object(helpers, '_build_filter', base._build_filter), patch.object(helpers, 'run_case', base.run_case):
                rows = asyncio.run(run_grid())
            self.assertEqual(27, len(rows))
            for row in rows:
                self.assertEqual(row['model_calls'], row['input_evidence_model_calls'])
                evidence = output / row['input_evidence_file']
                records = [json.loads(line) for line in evidence.read_text(encoding='utf-8').splitlines()]
                model = [record for record in records if record['stage'] == 'model_input']
                before = [record for record in records if record['stage'] == 'filter_before']
                after = [record for record in records if record['stage'] == 'filter_after']
                self.assertEqual(row['model_calls'], len(model))
                self.assertEqual(len(before), len(after))
                self.assertEqual(row['input_evidence_filter_calls'], len(before))
                if row['method'] == 'none':
                    self.assertFalse(before)
                else:
                    self.assertTrue(before)
                self.assertNotIn(v1.issue_text(row['scenario']), evidence.read_text(encoding='utf-8'))
            self.assertEqual([], audit_evidence(output, rows))
            broken = dict(rows[0], input_evidence_model_calls=rows[0]['model_calls'] + 1)
            issues = audit_evidence(output, [broken, *rows[1:]])
            self.assertTrue(any('model call evidence mismatch' in issue for issue in issues))


if __name__ == '__main__':
    unittest.main()
