"""Zero-API regression for the task-anchor mechanism."""

import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.run import ModelInputData
from context_pruner import ContextBudget
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners.openai_agents_input_evidence_v2 import (
    EvidenceCaptureFilter, EvidenceModelProxy, InputEvidenceRecorder,
)
from experiments.runners.openai_agents_task_anchor_v3 import TaskAnchorFilter
from experiments.runners.run_openai_agents_repo_diagnostic_v2 import evidence_wiring
from experiments.runners.run_openai_agents_repo_diagnostic_v3 import build_anchored_filter
from experiments.audits.audit_openai_agents_repo_diagnostic_v2 import audit_evidence


class TaskAnchorGate(unittest.TestCase):
    def test_targeted_nine_sample_zero_api_grid(self):
        gate = Path(__file__).resolve().parents[1] / '.tooling/gate_openai_agents_3arm.py'
        spec = importlib.util.spec_from_file_location('openai_v3_grid_gate_helpers', gate)
        assert spec and spec.loader
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)

        async def run_grid():
            rows = []
            for repeat in range(3):
                case = v1.build_case('django_count_annotations', repeat)
                for method in ('none', 'native_summary', 'pruner_v1'):
                    rows.append(await helpers._run_arm_case(
                        case.scenario, repeat, method,
                        budget=ContextBudget(5435, 16304, 4076), case=case,
                    ))
            return rows

        original = base._build_filter

        def anchored(method, **kwargs):
            return build_anchored_filter(original, method, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with patch.object(base, '_build_filter', anchored), evidence_wiring(output), patch.object(helpers, '_build_filter', base._build_filter), patch.object(helpers, 'run_case', base.run_case):
                rows = asyncio.run(run_grid())
            self.assertEqual(9, len(rows))
            self.assertEqual([], audit_evidence(output, rows))
            for row in rows:
                if row['method'] == 'pruner_v1':
                    records = [json.loads(line) for line in (output / row['input_evidence_file']).read_text(encoding='utf-8').splitlines()]
                    last = [record for record in records if record['stage'] == 'model_input'][-1]
                    self.assertTrue(last['constraint_present_in_unprotected_messages']['other_annotation_references'])

    def test_missing_anchor_falls_back_to_original_input(self):
        original = [{'role': 'user', 'content': 'Keep this condition'}]

        class DropEverything:
            def __call__(self, data):
                return ModelInputData(input=[], instructions=data.model_data.instructions)

        wrapped = TaskAnchorFilter(DropEverything(), count=1)
        call = type('CallData', (), {'model_data': ModelInputData(input=original, instructions='same')})()
        result = asyncio.run(wrapped(call))
        self.assertIs(result, call.model_data)
        self.assertEqual(1, wrapped.restore_failures)

    def test_django_reference_rule_reaches_model_after_pruning(self):
        gate = Path(__file__).resolve().parents[1] / '.tooling/gate_openai_agents_3arm.py'
        spec = importlib.util.spec_from_file_location('openai_v3_anchor_gate_helpers', gate)
        assert spec and spec.loader
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        case = v1.build_case('django_count_annotations', 0)
        request_budget = base.RequestBudget(30)
        client = helpers.StubAsyncOpenAI(request_budget)
        recorder = InputEvidenceRecorder(case.scenario)
        budget = ContextBudget(5435, 16304, 4076)
        filt = build_anchored_filter(
            base._build_filter, 'pruner_v1', case=case, client=client,
            model_name='stub-model', budget=budget, fixed_reserved_tokens=512,
            max_summary_tokens=1024, max_summary_calls=4, timeout=30.0,
            request_budget=request_budget, trigger_policy='symmetric_budget',
        )
        self.assertIsInstance(filt.inner, TaskAnchorFilter)
        stub = helpers.StubApiModel(case, request_budget=request_budget)

        async def run_one():
            return await base.run_case(
                case, method='pruner_v1', client=client, model_name='stub-model',
                request_budget=request_budget, budget=budget,
                fixed_reserved_tokens=512, max_turns=6, max_output_tokens=256,
                max_api_retries=0, retry_base_delay=0.0,
                input_cost_per_million=0.0, output_cost_per_million=0.0,
                context_filter=EvidenceCaptureFilter(filt, recorder),
                gate_model=EvidenceModelProxy(stub, recorder),
            )

        row = asyncio.run(run_one())
        self.assertEqual(4, row['model_calls'])
        self.assertGreaterEqual(row['compressed_calls'], 1)
        self.assertEqual(0, filt.inner.restore_failures)
        last = [record for record in recorder.records if record['stage'] == 'model_input'][-1]
        self.assertTrue(last['constraint_present']['other_annotation_references'])
        self.assertTrue(last['constraint_present_in_unprotected_messages']['other_annotation_references'])
        self.assertIn(v1.issue_text(case.scenario), stub.inputs[-1][0]['content'])
        self.assertEqual(case.history[-1]['content'], stub.inputs[-1][1]['content'])


if __name__ == '__main__':
    unittest.main()
