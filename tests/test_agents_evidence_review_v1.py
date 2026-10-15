import copy
import unittest

from experiments.runners.agents_evidence_review_v1 import compare_checkpoint, summarize_batch


class EvidenceReviewTest(unittest.TestCase):
    def test_matrix_mismatch_and_effect_coverage(self):
        sample = {
            "arm": "k1", "accepted": False, "model_calls": 2,
            "provider_usage": [{"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}],
            "transcript": [{"history_filter": {"before_digest": "a", "after_digest": "b",
                              "tokens_before": 20, "tokens_after": 23, "tokens_saved": -3}}],
        }
        payload = {"preregistration": {"matrix": {"tasks": ["x"], "arms": ["k1"],
                   "repeats": 1, "samples": 60}}, "samples": [sample]}
        r = summarize_batch(payload)
        self.assertEqual(1, r["matrix"]["derived_samples"])
        self.assertFalse(r["matrix"]["declared_count_matches"])
        self.assertEqual(1, r["arms"]["k1"]["changed_samples"])
        self.assertEqual(0, r["arms"]["k1"]["positive_saving_samples"])
        self.assertEqual(-3, r["arms"]["k1"]["local_saved"])
        self.assertEqual(10, r["arms"]["k1"]["provider_total"])

    def test_matched_inputs_are_immutable_and_guarded_arm_cannot_grow(self):
        messages = [{"role": "system", "content": "Preserve compiler identifiers"},
                    {"role": "user", "content": '{"path":"compiler.py","text":"1: compiler = 1\\n2: x = 2\\n3: compiler = 3"}'},
                    {"role": "user", "content": '{"path":"other.py","text":"1: latest = 2"}'}]
        original = copy.deepcopy(messages)
        r = compare_checkpoint(messages)
        self.assertEqual(original, messages)
        self.assertEqual({"none", "k1", "k0", "k1_guarded", "k0_guarded"}, set(r["arms"]))
        for arm in ("k1_guarded", "k0_guarded"):
            self.assertLessEqual(r["arms"][arm]["tokens_after"], r["arms"]["none"]["tokens_after"])
            self.assertEqual(r["arms"][arm]["messages"][-1], original[-1])


if __name__ == "__main__":
    unittest.main()
