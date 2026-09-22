import unittest

from src.deduplicate import unique_in_order
from src.filtering import active_records
from src.normalize import normalize_id


class RecordPipelineTests(unittest.TestCase):
    def test_identifier_is_trimmed_and_lowercased(self):
        self.assertEqual("order-17", normalize_id("  ORDER-17  "))

    def test_deduplication_preserves_first_seen_order(self):
        self.assertEqual(["b", "a", "c"], unique_in_order(["b", "a", "b", "c", "a"]))

    def test_only_explicitly_active_records_are_kept(self):
        rows = [
            {"id": "a", "active": True},
            {"id": "b", "active": False},
            {"id": "c"},
        ]
        self.assertEqual([{"id": "a", "active": True}], active_records(rows))


if __name__ == "__main__":
    unittest.main()
