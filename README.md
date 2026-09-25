# Safe Treasury Preflight

A local, read-only research tool that asks a narrow question before a Safe transaction batch is signed: **under stated balance and fee-shock assumptions, how many consecutive calls have modeled funding in every scenario?** It highlights the first modeled balance blocker and refuses to infer effects for unknown calldata.

This individual student entry uses a core read-only prototype built on September 25, 2026, during the hackathon window. The same core was also prepared that day for a separate Safe grant proposal; this entry adds contest-specific packaging, tests, a captioned demo, and a pitch deck. Kamui is the project brand. This is not a deployed asset-management service, investment advice, a Safe integration, a security audit, or a transaction-execution guarantee. The synthetic demonstration contains no customer funds or real transaction instructions. Development used AI coding assistance, with human-directed scope and testing.

## Run the browser demo

Requires Python 3.10+; no dependencies or accounts.

```sh
python3 app.py --port 4181
```

Open <http://127.0.0.1:4181/>, click **Load synthetic demo**, then **Run preflight**. The page accepts pasted JSON or three local files: a Safe Transaction Builder v1.0 export, a balance snapshot, and scenario assumptions. The page sends its inputs only to the local loopback server. It loads no external scripts, fonts, analytics, or wallet.

The included synthetic fixture has four calls. In the nominal scenario, three explicit native-value transfers have modeled funding; in the higher-outflow/fee scenario, two do. The fourth call has opaque calldata, so the tool makes no full-batch safety claim. A funded prefix is not proof that a transaction will execute or is safe.

## Command-line demo and tests

```sh
python3 preflight.py \
  --batch fixtures/synthetic-batch.json \
  --balances fixtures/synthetic-balances.json \
  --scenarios fixtures/scenarios.json \
  --expected-safe 0x1111111111111111111111111111111111111111 \
  --json-out /tmp/safe-preflight-report.json \
  --html-out /tmp/safe-preflight-report.html
python3 -m unittest discover -s . -v
```

The test suite includes 16 checks covering classification, balance stress behavior, JSON input validation, and read-only RPC behavior.

## Method

- Parses ordered Safe Transaction Builder v1.0 calls using raw calldata, not method labels.
- Models explicit native-value transfers and `transfer(address,uint256)` calls only when the token target is in the user-supplied balance allowlist.
- Applies a user-supplied outflow multiplier and native fee reserve to cumulative recognized outflows.
- Stops the reviewable prefix on a modeled shortfall or an unclassified call. Reports per-scenario traces and recognized-call-only totals.
- Optionally uses `chain_snapshot.py` for fixed-block, read-only Ethereum JSON-RPC balance reads, checking chain ID and Safe-shaped owner/threshold responses. It never signs, proposes, or broadcasts transactions. Queries send the specified addresses to the chosen RPC provider.

The `evidence/public-sepolia-interface-check.json` file records a read-only check of an unrelated public Safe on Sepolia. It records the account address, block, threshold, and owner count but no owner addresses. We have no relationship with its owners and did not make a live transaction. This check does not validate the tool against a real Safe execution.

## Scope and limitations

The core analysis is an offline balance model, not Safe Shield or a transaction simulator. It does not verify token code/behavior, contract side effects, real gas settlement, Safe modules, signatures, chain finality, actual balances, or current Safe UI compatibility. Input balances and scenario shocks are assumptions, not forecasts. RPC responses may be wrong or stale. Verify all calls and state separately before signing any real transaction.

Built with Python standard library, vanilla JavaScript, HTML, and CSS. No external packages are required to run the tool.

The [46-second captioned walkthrough](media/Safe-Treasury-Preflight-46s-demo.mp4) uses the actual synthetic CLI report; it is not a recording of a live browser or transaction. The four-slide pitch is available as [PDF](media/Safe-Treasury-Preflight-pitch.pdf) and [editable PowerPoint](media/Safe-Treasury-Preflight-pitch.pptx). It explains the problem, model, evidence, and next steps.
