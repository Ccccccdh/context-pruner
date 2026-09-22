import unittest

from src.flags import is_enabled
from src.timeouts import timeout_seconds
from src.url_builder import service_url


class ConfigurationContractTests(unittest.TestCase):
    def test_service_url_uses_https(self):
        self.assertEqual("https://api.internal:8443", service_url("api.internal", 8443))

    def test_timeout_is_converted_to_seconds(self):
        self.assertEqual(15, timeout_seconds(15_000))

    def test_text_flags_are_parsed_strictly(self):
        self.assertTrue(is_enabled(" YES "))
        self.assertFalse(is_enabled("false"))
        self.assertFalse(is_enabled(""))


if __name__ == "__main__":
    unittest.main()
