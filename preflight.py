"""Offline, read-only balance stress check for Safe Transaction Builder exports.

This models explicit native-value and allowlisted ERC-20 transfer outflows only.
It never signs, proposes, executes, connects to a chain, or calls an RPC.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from decimal import Decimal
from pathlib import Path


ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
DECIMAL = re.compile(r"^(0|[1-9][0-9]*)$")
HEX = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")
TRANSFER_SELECTOR = "a9059cbb"
MAX_FILE_BYTES = 1_000_000
MAX_TRANSACTIONS = 500


class InputError(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise InputError(f"file exceeds {MAX_FILE_BYTES} bytes: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON in {path}: {exc}") from exc


def natural(value, label: str) -> int:
    if not isinstance(value, str) or len(value) > 78 or not DECIMAL.fullmatch(value):
        raise InputError(f"{label} must be a nonnegative decimal string")
    return int(value)


def address(value, label: str) -> str:
    if not isinstance(value, str) or not ADDRESS.fullmatch(value):
        raise InputError(f"{label} must be a 20-byte hex address")
    return value.lower()


def parse_balances(raw, chain_id: str):
    if not isinstance(raw, dict) or raw.get("chainId") != chain_id:
        raise InputError("balance snapshot chainId must match batch chainId")
    native = natural(raw.get("native_wei"), "native_wei")
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        raise InputError("tokens must map token addresses to raw-unit balances")
    parsed = {"NATIVE": native}
    for token, amount in tokens.items():
        canonical = address(token, "token address")
        if canonical in parsed:
            raise InputError(f"duplicate token address: {token}")
        parsed[canonical] = natural(amount, f"tokens[{token}]")
    return parsed


def parse_batch(raw, balances, expected_safe: str | None):
    if not isinstance(raw, dict) or raw.get("version") != "1.0":
        raise InputError("expected Safe Transaction Builder version 1.0 export")
    chain_id = raw.get("chainId")
    if not isinstance(chain_id, str) or not DECIMAL.fullmatch(chain_id) or chain_id == "0":
        raise InputError("batch chainId must be a positive decimal string")
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise InputError("batch meta must be an object")
    source_safe = meta.get("createdFromSafeAddress")
    if source_safe:
        source_safe = address(source_safe, "meta.createdFromSafeAddress")
    if expected_safe and source_safe != address(expected_safe, "expected Safe address"):
        raise InputError("batch Safe address does not match expected Safe address")
    txs = raw.get("transactions")
    if not isinstance(txs, list) or not 1 <= len(txs) <= MAX_TRANSACTIONS:
        raise InputError(f"transactions must contain 1–{MAX_TRANSACTIONS} items")
    calls = []
    for i, tx in enumerate(txs):
        if not isinstance(tx, dict):
            raise InputError(f"transaction {i} must be an object")
        to = address(tx.get("to"), f"transaction {i} to")
        value = natural(tx.get("value"), f"transaction {i} value")
        data = tx.get("data")
        if data is not None and (
            not isinstance(data, str) or len(data) > 131074 or not HEX.fullmatch(data)
        ):
            raise InputError(f"transaction {i} data must be null or 0x-prefixed even-length hex")
        # Transaction Builder exports can hold method metadata while raw data
        # is null. Never infer a balance effect from unauthenticated labels.
        payload = None if data is None else data[2:].lower()
        item = {"index": i, "to": to, "kind": "unclassified", "asset": None,
                "amount_raw": None, "reason": "arbitrary or unsupported contract call"}
        operation = tx.get("operation", 0)
        if isinstance(operation, bool) or operation not in (0, "0"):
            item["reason"] = "non-CALL or invalid operation; delegate calls are unsupported"
        elif payload is None:
            item["reason"] = "raw calldata absent; contractMethod metadata is not decoded"
        elif not payload and value > 0:
            item.update(kind="native_value", asset="NATIVE", amount_raw=str(value),
                        reason="explicit native-value outflow; recipient side effects unverified")
        elif value == 0 and len(payload) == 136 and payload[:8] == TRANSFER_SELECTOR:
            recipient_word = payload[8:72]
            if to in balances and to != "NATIVE" and recipient_word[:24] == "0" * 24:
                amount = int(payload[72:136], 16)
                item.update(kind="erc20_transfer", asset=to, amount_raw=str(amount),
                            recipient="0x" + recipient_word[24:],
                            reason="standard transfer selector on user-allowlisted token; code unverified")
            else:
                item["reason"] = "ERC-20-like call on unlisted token or malformed recipient"
        elif value > 0 and payload:
            item["reason"] = "native value plus contract call; possible additional effects"
        elif not payload:
            item["reason"] = "zero-value call; recipient behavior unverified"
        calls.append(item)
    return chain_id, source_safe, calls


def parse_scenarios(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
        raise InputError("scenarios must contain 1–20 items")
    result = []
    seen = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise InputError(f"scenario {i} requires a string id")
        ident = item["id"].strip()
        if not ident or len(ident) > 60 or ident in seen:
            raise InputError(f"scenario {i} id is empty, duplicated or too long")
        seen.add(ident)
        gas = natural(item.get("native_fee_reserve_wei"), f"scenario {ident} native_fee_reserve_wei")
        bps = item.get("outflow_multiplier_bps")
        if not isinstance(bps, int) or isinstance(bps, bool) or not 10_000 <= bps <= 30_000:
            raise InputError(f"scenario {ident} outflow_multiplier_bps must be 10000–30000")
        result.append({"id": ident, "native_fee_reserve_wei": gas,
                       "outflow_multiplier_bps": bps})
    return result


def requirements(cumulative, scenario):
    bps = scenario["outflow_multiplier_bps"]
    required = {asset: (amount * bps + 9_999) // 10_000 for asset, amount in cumulative.items()}
    required["NATIVE"] = required.get("NATIVE", 0) + scenario["native_fee_reserve_wei"]
    return required


def evaluate(calls, balances, scenarios):
    results = []
    known_total = {asset: 0 for asset in balances}
    for call in calls:
        if call["asset"] is not None:
            known_total[call["asset"]] += int(call["amount_raw"])
    unclassified = [call["index"] for call in calls if call["kind"] == "unclassified"]
    for scenario in scenarios:
        cumulative = {asset: 0 for asset in balances}
        funded_prefix = 0
        first_blocker = None
        trace = []
        for call in calls:
            if call["asset"] is None:
                first_blocker = {"index": call["index"], "reason": "unclassified call"}
                break
            cumulative[call["asset"]] += int(call["amount_raw"])
            required = requirements(cumulative, scenario)
            shortages = {asset: str(max(0, amount - balances[asset]))
                         for asset, amount in required.items() if amount > balances[asset]}
            trace.append({"index": call["index"], "required_raw":
                          {asset: str(amount) for asset, amount in required.items()},
                          "shortfall_raw": shortages})
            if shortages:
                first_blocker = {"index": call["index"], "reason": "modeled balance shortfall"}
                break
            funded_prefix += 1
        total_required = requirements(known_total, scenario)
        results.append({"id": scenario["id"], "funded_prefix": funded_prefix,
                        "native_fee_reserve_wei": str(scenario["native_fee_reserve_wei"]),
                        "outflow_multiplier_bps": scenario["outflow_multiplier_bps"],
                        "first_blocker": first_blocker,
                        "known_call_only_requirement_raw": {k: str(v) for k, v in total_required.items()},
                        "known_call_only_shortfall_raw": {
                            k: str(max(0, v - balances[k])) for k, v in total_required.items() if v > balances[k]
                        }, "trace": trace})
    return {"scenario_results": results, "unclassified_indices": unclassified,
            "worst_case_funded_prefix": min(x["funded_prefix"] for x in results),
            "all_modeled_calls_funded": not unclassified and all(
                x["funded_prefix"] == len(calls) for x in results)}


def build_report(batch, balance_input, scenario_input, expected_safe=None):
    if not isinstance(batch, dict):
        raise InputError("batch must be an object")
    balances = parse_balances(balance_input, batch.get("chainId"))
    chain_id, safe, calls = parse_batch(batch, balances, expected_safe)
    snapshot_metadata = balance_input.get("snapshot_metadata")
    if snapshot_metadata is not None:
        if not isinstance(snapshot_metadata, dict) or safe is None or (
            address(snapshot_metadata.get("safe"), "snapshot Safe address") != safe
        ):
            raise InputError("on-chain snapshot Safe address does not match batch Safe address")
        if snapshot_metadata.get("account_code_present") is not True:
            raise InputError("on-chain snapshot address has no contract code; not a deployed Safe")
        if snapshot_metadata.get("safe_interface_checked") is not True:
            raise InputError("on-chain snapshot Safe owner/threshold interface was not checked")
    scenarios = parse_scenarios(scenario_input)
    result = evaluate(calls, balances, scenarios)
    return {"tool": "Kamui Safe Treasury Preflight", "version": "0.1.0",
            "chainId": chain_id, "source_safe": safe,
            "snapshot_metadata": snapshot_metadata,
            "batch_sha256": hashlib.sha256(json.dumps(batch, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "balances_raw": {k: str(v) for k, v in balances.items()},
            "calls": calls, **result,
            "scope": "Offline explicit-outflow balance model, not a transaction simulation or execution guarantee. "
                     "Contract code, token behavior, calldata side effects, Safe gas settlement, current balances, "
                     "transaction ordering changes and chain state are not verified. Full-batch known-call "
                     "totals ignore unknown calls and are not a bound on real balance needs."}


def render_html(report):
    esc = lambda value: html.escape(str(value))
    def amount(asset, raw):
        if raw is None:
            return "—"
        if asset == "NATIVE":
            return f"{Decimal(int(raw)) / Decimal(10 ** 18):f} native token"
        return f"{raw} raw units"

    def shortfalls(items):
        if not items:
            return "None in recognized calls"
        return ", ".join(f"{asset}: {amount(asset, raw)}" for asset, raw in items.items())

    def blocker(item):
        return "None" if item is None else f"Call {item['index'] + 1}: {item['reason']}"

    rows = "".join(
        f"<tr><td>{x['index'] + 1}</td><td>{esc(x['kind'])}</td><td><code>{esc(x['to'])}</code></td>"
        f"<td>{esc(x['asset'] or '—')}</td><td>{esc(amount(x['asset'], x['amount_raw']))}</td>"
        f"<td>{esc(x['reason'])}</td></tr>" for x in report["calls"])
    cases = "".join(
        f"<tr><td>{esc(x['id'])}</td>"
        f"<td>{esc(amount('NATIVE', x['native_fee_reserve_wei']))} fee reserve; "
        f"{x['outflow_multiplier_bps'] / 100:.0f}% outflow</td>"
        f"<td>{x['funded_prefix']} / {len(report['calls'])}</td>"
        f"<td>{esc(blocker(x['first_blocker']))}</td>"
        f"<td>{esc(shortfalls(x['known_call_only_shortfall_raw']))}</td></tr>"
        for x in report["scenario_results"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kamui Safe Treasury Preflight</title><style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1180px;margin:40px auto;padding:0 24px;color:#15283b;background:#f8fafc}}
h1{{font-size:30px}}.card{{background:white;border:1px solid #dce5ed;border-radius:12px;padding:18px;margin:18px 0}}
.metric{{font-size:28px;font-weight:700}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{border-bottom:1px solid #dce5ed;padding:10px;text-align:left;vertical-align:top}}th{{background:#eaf1f7}}
code{{overflow-wrap:anywhere}}.warning{{background:#fff2d8;border-left:4px solid #d99000;padding:14px}}</style>
<h1>Safe Treasury Preflight</h1><p>Kamui · local, read-only research prototype</p>
<div class="card"><div class="metric">{report['worst_case_funded_prefix']} / {len(report['calls'])}</div><div>modeled calls funded in every listed scenario before the first blocker</div></div>
<p class="warning">{esc(report['scope'])}</p>
<div class="card"><b>Chain ID:</b> {esc(report['chainId'])} · <b>Safe:</b> {esc(report['source_safe'] or 'not provided')}<br><b>Batch SHA-256:</b> <code>{esc(report['batch_sha256'])}</code></div>
<h2>Scenario frontier</h2><table><thead><tr><th>Scenario</th><th>Assumption</th><th>Funded prefix</th><th>First blocker</th><th>Known-call-only shortfall</th></tr></thead><tbody>{cases}</tbody></table>
<h2>Call classification</h2><table><thead><tr><th>#</th><th>Type</th><th>Target</th><th>Asset</th><th>Amount</th><th>Assumption</th></tr></thead><tbody>{rows}</tbody></table>
<p>Any unclassified call prevents a complete-batch conclusion. Inspect the JSON report for per-call traces and all scenario assumptions.</p></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--balances", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--expected-safe")
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--html-out", type=Path)
    args = parser.parse_args()
    report = build_report(load_json(args.batch), load_json(args.balances),
                          load_json(args.scenarios), args.expected_safe)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.html_out:
        args.html_out.parent.mkdir(parents=True, exist_ok=True)
        args.html_out.write_text(render_html(report), encoding="utf-8")
    print(f"Modeled prefix across all scenarios: {report['worst_case_funded_prefix']}/{len(report['calls'])}")
    print(f"Unclassified calls: {report['unclassified_indices']}")


if __name__ == "__main__":
    main()
