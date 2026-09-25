import json
import unittest
from pathlib import Path

from chain_snapshot import InputError, Rpc, safe_account_shape, snapshot


FIXTURE = Path(__file__).with_name("fixtures") / "synthetic-batch.json"


class FakeRpc:
    def __init__(self, chain="0x1"):
        self.chain = chain
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return self.chain
        if method == "eth_blockNumber":
            return "0x10"
        if method == "eth_getBlockByNumber":
            return {"number": "0x10", "hash": "0x" + "a" * 64}
        if method == "eth_getBalance":
            return "0x2a"
        if method == "eth_getCode":
            return "0x6000"
        if method == "eth_call":
            if params[0]["data"] == "0xe75235b8":
                return "0x" + f"{1:064x}"
            if params[0]["data"] == "0xa0e67e2b":
                return "0x" + f"{32:064x}{2:064x}{int('1'*40,16):064x}{int('2'*40,16):064x}"
            return "0x" + f"{11:064x}"
        raise AssertionError(method)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.batch = json.loads(FIXTURE.read_text())

    def test_fixed_block_and_token_balance_of(self):
        rpc = FakeRpc(chain=hex(int(self.batch["chainId"])))
        token = "0x" + "2" * 40
        result = snapshot(self.batch, [token], rpc)
        self.assertEqual(result["native_wei"], "42")
        self.assertEqual(result["tokens"][token], "11")
        self.assertTrue(result["snapshot_metadata"]["account_code_present"])
        self.assertTrue(result["snapshot_metadata"]["safe_interface_checked"])
        self.assertEqual(result["snapshot_metadata"]["safe_threshold"], 1)
        self.assertEqual(result["snapshot_metadata"]["safe_owner_count"], 2)
        self.assertEqual(rpc.calls[3][1][1], "0x10")
        self.assertEqual(rpc.calls[-1][1][1], "0x10")
        self.assertEqual(rpc.calls[-1][1][0]["data"], "0x70a08231" + "0" * 24 + "1" * 40)

    def test_wrong_chain_stops_before_address_queries(self):
        rpc = FakeRpc()
        with self.assertRaises(InputError):
            snapshot(self.batch, [], rpc)
        self.assertEqual([name for name, _ in rpc.calls], ["eth_chainId"])

    def test_rpc_rejects_credentials_and_disallowed_methods(self):
        with self.assertRaises(InputError):
            Rpc("https://user:secret@example.com/rpc")
        rpc = Rpc("https://example.com/rpc")
        with self.assertRaises(InputError):
            rpc.call("eth_sendRawTransaction", [])

    def test_invalid_safe_interface_is_rejected(self):
        rpc = FakeRpc(chain=hex(int(self.batch["chainId"])))
        original_call = rpc.call

        def bad_call(method, params):
            if method == "eth_call" and params[0]["data"] == "0xa0e67e2b":
                return "0x"
            return original_call(method, params)

        rpc.call = bad_call
        with self.assertRaisesRegex(InputError, "getOwners"):
            snapshot(self.batch, [], rpc)
        with self.assertRaisesRegex(InputError, "threshold"):
            safe_account_shape("0x" + f"{3:064x}",
                               "0x" + f"{32:064x}{1:064x}{int('1'*40,16):064x}")


if __name__ == "__main__":
    unittest.main()
