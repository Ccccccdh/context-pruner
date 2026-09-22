import unittest

from agent_demo.env import KnowledgeBase


class SearchFallbackTest(unittest.TestCase):
    def test_no_match_returns_all_docs(self):
        kb = KnowledgeBase({"d1": "LLMLingua 压缩", "d2": "语义摘要"})
        self.assertEqual(kb.search("完全无关的查询"), ["d1", "d2"])

    def test_exact_match_still_works(self):
        kb = KnowledgeBase({"d1": "LLMLingua 压缩", "d2": "语义摘要"})
        self.assertEqual(kb.search("LLMLingua"), ["d1"])


if __name__ == "__main__":
    unittest.main()
