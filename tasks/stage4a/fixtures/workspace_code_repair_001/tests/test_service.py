import unittest

from src.calculator import total
from src.redaction import redact
from src.retry import retry_delay


class ServiceUtilityTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(10, total([2, 3, 5]))
        self.assertEqual(0, total([]))

    def test_retry_delay_is_capped(self):
        self.assertEqual(2, retry_delay(0))
        self.assertEqual(16, retry_delay(3))
        self.assertEqual(30, retry_delay(8))

    def test_redaction(self):
        self.assertEqual("[REDACTED]", redact("secret-token"))
        self.assertEqual("", redact(""))


if __name__ == "__main__":
    unittest.main()
