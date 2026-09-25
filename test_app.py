import json
import unittest

from app import FIXTURES, ROOT, parse_request
from preflight import InputError


class BrowserRequestTests(unittest.TestCase):
    def setUp(self):
        self.request = {name: (ROOT / path).read_text(encoding="utf-8") for name, path in FIXTURES.items()}
        self.request["expected_safe"] = "0x1111111111111111111111111111111111111111"

    def test_synthetic_browser_flow(self):
        report = parse_request(json.dumps(self.request).encode())
        self.assertEqual(report["worst_case_funded_prefix"], 2)
        self.assertEqual(len(report["scenario_results"]), 2)
        self.assertEqual(report["unclassified_indices"], [3])

    def test_duplicate_key_inside_uploaded_batch_rejected(self):
        self.request["batch"] = '{"version":"1.0","version":"1.0"}'
        with self.assertRaisesRegex(InputError, "duplicate JSON key"):
            parse_request(json.dumps(self.request).encode())

    def test_expected_safe_mismatch_rejected(self):
        self.request["expected_safe"] = "0x9999999999999999999999999999999999999999"
        with self.assertRaisesRegex(InputError, "does not match"):
            parse_request(json.dumps(self.request).encode())


if __name__ == "__main__":
    unittest.main()
