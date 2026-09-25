import json
import tempfile
import unittest
from pathlib import Path

from preflight import InputError, build_report, load_json


FIXTURES = Path(__file__).with_name("fixtures")


def inputs():
    return tuple(json.loads((FIXTURES / name).read_text()) for name in (
        "synthetic-batch.json", "synthetic-balances.json", "scenarios.json"))


def transfer_data(recipient: str, amount: int):
    return "0xa9059cbb" + ("0" * 24) + recipient[2:] + f"{amount:064x}"


class PreflightTests(unittest.TestCase):
    def test_nominal_vs_stress_and_unknown_call(self):
        report = build_report(*inputs(), expected_safe="0x" + "1" * 40)
        scenarios = {x["id"]: x for x in report["scenario_results"]}
        self.assertEqual(scenarios["nominal"]["funded_prefix"], 3)
        self.assertEqual(scenarios["higher-outflow-and-fee"]["funded_prefix"], 2)
        self.assertEqual(report["unclassified_indices"], [3])
        self.assertFalse(report["all_modeled_calls_funded"])

    def test_raw_erc20_transfer_only_on_allowlisted_token(self):
        batch, balances, scenarios = inputs()
        token = "0x" + "2" * 40
        batch["transactions"] = [{"to": token, "value": "0", "data":
                                  transfer_data("0x" + "3" * 40, 900000)}]
        report = build_report(batch, balances, scenarios)
        self.assertEqual(report["calls"][0]["kind"], "erc20_transfer")
        self.assertEqual(report["calls"][0]["amount_raw"], "900000")
        self.assertTrue(report["all_modeled_calls_funded"])
        balances["tokens"] = {}
        report = build_report(batch, balances, scenarios)
        self.assertEqual(report["calls"][0]["kind"], "unclassified")
        self.assertFalse(report["all_modeled_calls_funded"])

    def test_metadata_cannot_forge_transfer(self):
        batch, balances, scenarios = inputs()
        batch["transactions"] = [{"to": "0x" + "2" * 40, "value": "0", "data": "0x1234",
                                  "contractMethod": {"name": "transfer"},
                                  "contractInputsValues": {"amount": "1"}}]
        report = build_report(batch, balances, scenarios)
        self.assertEqual(report["calls"][0]["kind"], "unclassified")

    def test_null_calldata_export_is_accepted_but_not_modeled(self):
        batch, balances, scenarios = inputs()
        batch["transactions"] = [{"to": "0x" + "2" * 40, "value": "0",
                                  "data": None,
                                  "contractMethod": {"name": "transfer", "payable": False,
                                                     "inputs": [{"name": "recipient", "type": "address"},
                                                                {"name": "amount", "type": "uint256"}]},
                                  "contractInputsValues": {"recipient": "0x" + "3" * 40,
                                                           "amount": "1"}}]
        report = build_report(batch, balances, scenarios)
        self.assertEqual(report["calls"][0]["kind"], "unclassified")
        self.assertEqual(report["worst_case_funded_prefix"], 0)
        self.assertIn("raw calldata absent", report["calls"][0]["reason"])

    def test_delegate_operation_never_classified_as_plain_transfer(self):
        batch, balances, scenarios = inputs()
        batch["transactions"] = [{"to": "0x" + "3" * 40, "value": "1", "data": "0x",
                                  "operation": 1}]
        report = build_report(batch, balances, scenarios)
        self.assertEqual(report["calls"][0]["kind"], "unclassified")
        self.assertEqual(report["worst_case_funded_prefix"], 0)

    def test_chain_and_safe_mismatch_fail(self):
        batch, balances, scenarios = inputs()
        balances["chainId"] = "1"
        with self.assertRaises(InputError):
            build_report(batch, balances, scenarios)
        balances["chainId"] = batch["chainId"]
        with self.assertRaises(InputError):
            build_report(batch, balances, scenarios, expected_safe="0x" + "9" * 40)

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"chainId":"1","chainId":"2"}')
            with self.assertRaises(InputError):
                load_json(path)

    def test_overdrawn_token_stops_at_first_transfer(self):
        batch, balances, scenarios = inputs()
        batch["transactions"] = [{"to": "0x" + "2" * 40, "value": "0", "data":
                                  transfer_data("0x" + "3" * 40, 1000001)}]
        report = build_report(batch, balances, scenarios)
        self.assertEqual(report["worst_case_funded_prefix"], 0)
        self.assertEqual(report["scenario_results"][0]["first_blocker"]["reason"],
                         "modeled balance shortfall")

    def test_onchain_snapshot_rejects_undeployed_or_different_account(self):
        batch, balances, scenarios = inputs()
        balances["snapshot_metadata"] = {"safe": "0x" + "1" * 40,
                                         "account_code_present": False}
        with self.assertRaisesRegex(InputError, "no contract code"):
            build_report(batch, balances, scenarios)
        balances["snapshot_metadata"] = {"safe": "0x" + "9" * 40,
                                         "account_code_present": True}
        with self.assertRaisesRegex(InputError, "does not match"):
            build_report(batch, balances, scenarios)
        balances["snapshot_metadata"] = {"safe": "0x" + "1" * 40,
                                         "account_code_present": True}
        with self.assertRaisesRegex(InputError, "interface was not checked"):
            build_report(batch, balances, scenarios)


if __name__ == "__main__":
    unittest.main()
