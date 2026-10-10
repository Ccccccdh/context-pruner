from __future__ import annotations

import asyncio
import json
import unittest

from experiments.runners.openai_agents_structured_final_v16 import (
    MeteredResponse,
    RequestFailure,
    render,
    settle,
)


def proposal(*, issue="sample-42", cause="cache retains OldType", fix="copy OldType", facts=None):
    return json.dumps({"issue": issue, "cause": cause, "fix": fix, "facts": ["OldType"] if facts is None else facts})


class RendererTests(unittest.TestCase):
    def test_renders_fields_verbatim_and_preserves_declared_fact(self):
        result = render(proposal())
        self.assertTrue(result.accepted)
        self.assertEqual(result.output, "RESULT issue=sample-42 cause=cache retains OldType fix=copy OldType")

    def test_exact_160_is_accepted_but_161_is_not_truncated(self):
        prefix = "RESULT issue=sample-42 cause="
        suffix = " fix=repair"
        cause = "x" * (160 - len(prefix) - len(suffix))
        exact = render(proposal(cause=cause, fix="repair", facts=["repair"]))
        self.assertTrue(exact.accepted)
        self.assertEqual(exact.chars, 160)
        long = render(proposal(cause=cause + "x", fix="repair", facts=["repair"]))
        self.assertFalse(long.accepted)
        self.assertEqual(long.reason, "over_160_chars")
        self.assertEqual(long.output, "")

    def test_rejects_missing_extra_and_duplicate_fields(self):
        self.assertEqual(render('{"issue":"sample-42","cause":"x","facts":["x"]}').reason, "missing_or_extra_field")
        self.assertEqual(render('{"issue":"sample-42","cause":"x","fix":"y","facts":["x"],"extra":"hidden"}').reason, "missing_or_extra_field")
        self.assertEqual(render('{"issue":"sample-42","issue":"other-1","cause":"x","fix":"y","facts":["x"]}').reason, "invalid_json_or_duplicate_key")

    def test_rejects_fact_loss_and_multiline(self):
        self.assertEqual(render(proposal(facts=["missing"])).reason, "declared_fact_missing_from_rendered_answer")
        self.assertEqual(render(proposal(cause="a\nb", facts=["a"])).reason, "invalid_cause_or_fix")
        self.assertEqual(render(proposal(fix="a\u2028b", facts=["a"])).reason, "invalid_cause_or_fix")

    def test_rejects_invalid_issue_and_empty_fact_list(self):
        self.assertEqual(render(proposal(issue="sample 42")).reason, "invalid_issue")
        self.assertEqual(render(proposal(), expected_issue="other-99").reason, "issue_mismatch")
        self.assertEqual(render(proposal(facts=[])).reason, "invalid_fact_list")
        self.assertEqual(render(proposal(cause="x fix=hidden", facts=["x"])).reason, "embedded_field_delimiter")

    def test_one_retry_is_metered_and_can_succeed(self):
        first = MeteredResponse(proposal(cause="x" * 150, facts=["x"]), 100, 20, 2)
        second = MeteredResponse(proposal(), 110, 18, 1)
        called = 0
        async def retry():
            nonlocal called
            called += 1
            return second
        settled = asyncio.run(settle(first, retry))
        self.assertEqual(called, 1)
        self.assertTrue(settled.accepted)
        self.assertEqual((settled.request_attempts, settled.input_tokens, settled.output_tokens), (3, 210, 38))

    def test_bad_retry_fails_closed_and_still_charges(self):
        first = MeteredResponse("bad", 100, 20, 1)
        async def retry():
            return MeteredResponse("bad again", 101, 21, 2)
        settled = asyncio.run(settle(first, retry))
        self.assertFalse(settled.accepted)
        self.assertEqual(settled.output, "")
        self.assertEqual((settled.request_attempts, settled.input_tokens, settled.output_tokens), (3, 201, 41))

    def test_transport_failure_and_unknown_exception_accounting(self):
        first = MeteredResponse("bad", 100, 20, 1)
        async def known_failure():
            raise RequestFailure("connection", request_attempts=2)
        settled = asyncio.run(settle(first, known_failure))
        self.assertFalse(settled.accepted)
        self.assertTrue(settled.accounting_complete)
        self.assertEqual(settled.request_attempts, 3)
        async def unknown_failure():
            raise RuntimeError("unmetered")
        unknown = asyncio.run(settle(first, unknown_failure))
        self.assertFalse(unknown.accepted)
        self.assertFalse(unknown.accounting_complete)
        self.assertIsNone(unknown.request_attempts)


if __name__ == "__main__":
    unittest.main()
