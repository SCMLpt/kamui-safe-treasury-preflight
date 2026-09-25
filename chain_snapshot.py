"""Optional, read-only Ethereum balance snapshot for the offline preflight tool.

The configured RPC receives the Safe and token addresses. Never use this with
private addresses unless that disclosure to the RPC operator is acceptable.
Only eth_chainId, eth_blockNumber, eth_getBlockByNumber, eth_getBalance,
eth_getCode, and eth_call are sent. No signing or transaction methods exist.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from preflight import InputError, address, load_json


HEX_QUANTITY = re.compile(r"^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$")
HEX_BYTES = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
BALANCE_OF = "0x70a08231"
GET_THRESHOLD = "0xe75235b8"
GET_OWNERS = "0xa0e67e2b"


def hex_quantity(value, label: str) -> int:
    if not isinstance(value, str) or not HEX_QUANTITY.fullmatch(value):
        raise InputError(f"{label} must be an Ethereum hex quantity")
    return int(value, 16)


def safe_account_shape(threshold_result: str, owners_result: str) -> tuple[int, int]:
    """Check the public Safe owner/threshold interface, not bytecode provenance."""
    if not isinstance(threshold_result, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", threshold_result):
        raise InputError("Safe getThreshold response is invalid")
    threshold = int(threshold_result, 16)
    if not isinstance(owners_result, str) or not HEX_BYTES.fullmatch(owners_result):
        raise InputError("Safe getOwners response is invalid")
    payload = bytes.fromhex(owners_result[2:])
    if len(payload) < 64 or len(payload) % 32 != 0 or int.from_bytes(payload[:32]) != 32:
        raise InputError("Safe getOwners ABI response is invalid")
    count = int.from_bytes(payload[32:64])
    if not 1 <= count <= 100 or len(payload) != 64 + 32 * count:
        raise InputError("Safe owner count or ABI length is invalid")
    owners = []
    for i in range(count):
        word = payload[64 + 32 * i:96 + 32 * i]
        if word[:12] != bytes(12) or word[12:] == bytes(20):
            raise InputError("Safe owner address is invalid")
        owners.append(word[12:])
    if len(set(owners)) != count or not 1 <= threshold <= count:
        raise InputError("Safe owner/threshold configuration is invalid")
    return threshold, count


class Rpc:
    def __init__(self, endpoint: str):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise InputError("RPC endpoint must be an HTTPS URL without embedded credentials")
        self.endpoint = endpoint
        self.next_id = 1

    def call(self, method: str, params: list):
        if method not in {"eth_chainId", "eth_blockNumber", "eth_getBlockByNumber",
                          "eth_getBalance", "eth_getCode", "eth_call"}:
            raise InputError(f"disallowed RPC method: {method}")
        ident = self.next_id
        self.next_id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": ident, "method": method,
                              "params": params}).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=payload,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "Kamui-Safe-Preflight/0.2"}, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise InputError("RPC response exceeds 1 MB")
        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InputError("RPC returned invalid JSON") from exc
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or result.get("id") != ident:
            raise InputError("RPC response ID or format mismatch")
        if "error" in result:
            raise InputError(f"RPC returned an error for {method}")
        return result.get("result")


def snapshot(batch: dict, tokens: list[str], rpc: Rpc) -> dict:
    if batch.get("version") != "1.0" or not isinstance(batch.get("meta"), dict):
        raise InputError("expected a Safe Transaction Builder version 1.0 batch")
    chain_id = batch.get("chainId")
    if not isinstance(chain_id, str) or not chain_id.isdecimal() or int(chain_id) <= 0:
        raise InputError("batch chainId must be a positive decimal string")
    safe = address(batch["meta"].get("createdFromSafeAddress"), "Safe address")
    token_list = [address(token, "token address") for token in tokens]
    if len(token_list) > 20 or len(set(token_list)) != len(token_list):
        raise InputError("specify at most 20 distinct token addresses")
    observed_chain = hex_quantity(rpc.call("eth_chainId", []), "RPC chainId")
    if observed_chain != int(chain_id):
        raise InputError(f"RPC chainId {observed_chain} differs from batch chainId {chain_id}")
    block_number = hex_quantity(rpc.call("eth_blockNumber", []), "block number")
    block_tag = hex(block_number)
    block = rpc.call("eth_getBlockByNumber", [block_tag, False])
    if not isinstance(block, dict) or block.get("number") != block_tag or not isinstance(block.get("hash"), str):
        raise InputError("RPC block response inconsistent with selected block")
    block_hash = block["hash"]
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", block_hash):
        raise InputError("RPC block hash is invalid")
    native = hex_quantity(rpc.call("eth_getBalance", [safe, block_tag]), "native balance")
    code = rpc.call("eth_getCode", [safe, block_tag])
    if not isinstance(code, str) or not HEX_BYTES.fullmatch(code):
        raise InputError("RPC Safe code response is invalid")
    threshold = None
    owner_count = None
    if code != "0x":
        threshold_result = rpc.call("eth_call", [{"to": safe, "data": GET_THRESHOLD}, block_tag])
        owners_result = rpc.call("eth_call", [{"to": safe, "data": GET_OWNERS}, block_tag])
        threshold, owner_count = safe_account_shape(threshold_result, owners_result)
    token_balances = {}
    for token in token_list:
        calldata = BALANCE_OF + "0" * 24 + safe[2:]
        value = rpc.call("eth_call", [{"to": token, "data": calldata}, block_tag])
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
            raise InputError(f"ERC-20 balanceOf response invalid for {token}")
        token_balances[token] = str(int(value, 16))
    return {"chainId": chain_id, "native_wei": str(native), "tokens": token_balances,
            "snapshot_metadata": {"safe": safe, "block_number": block_number,
                                  "block_hash": block_hash, "account_code_present": code != "0x",
                                  "safe_interface_checked": threshold is not None,
                                  "safe_threshold": threshold, "safe_owner_count": owner_count,
                                  "source": "unverified third-party RPC response; fixed-block read only",
                                  "warning": "The Safe-shaped owner/threshold interface does not prove authentic Safe implementation. Token code, RPC honesty and reorg stability are unverified."}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--token", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = snapshot(load_json(args.batch), args.token, Rpc(args.rpc_url))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Read-only snapshot saved at block {result['snapshot_metadata']['block_number']}; "
          f"Account contract code present: {result['snapshot_metadata']['account_code_present']}")


if __name__ == "__main__":
    main()
