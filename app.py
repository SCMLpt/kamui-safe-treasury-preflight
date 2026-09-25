"""Loopback-only browser interface for the read-only Safe preflight model."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from preflight import InputError, _unique_pairs, build_report


ROOT = Path(__file__).resolve().parent
MAX_REQUEST_BYTES = 3_000_000
ASSETS = {
    "/": ("web/index.html", "text/html; charset=utf-8"),
    "/app.js": ("web/app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("web/styles.css", "text/css; charset=utf-8"),
}
FIXTURES = {
    "batch": "fixtures/synthetic-batch.json",
    "balances": "fixtures/synthetic-balances.json",
    "scenarios": "fixtures/scenarios.json",
}


def parse_request(body: bytes) -> dict:
    try:
        wrapper = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputError(f"invalid request JSON: {exc}") from exc
    if not isinstance(wrapper, dict):
        raise InputError("request must be a JSON object")
    values = {}
    for key in FIXTURES:
        raw = wrapper.get(key)
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1_000_000:
            raise InputError(f"{key} must be JSON text up to 1 MB")
        try:
            values[key] = json.loads(raw, object_pairs_hook=_unique_pairs)
        except json.JSONDecodeError as exc:
            raise InputError(f"invalid {key} JSON: {exc}") from exc
    expected_safe = wrapper.get("expected_safe")
    if expected_safe is not None and not isinstance(expected_safe, str):
        raise InputError("expected_safe must be a string")
    return build_report(values["batch"], values["balances"], values["scenarios"], expected_safe)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, value):
        self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path == "/demo":
            self._json(200, {key: (ROOT / path).read_text(encoding="utf-8") for key, path in FIXTURES.items()})
            return
        asset = ASSETS.get(self.path)
        if asset is None:
            self._json(404, {"error": "not found"})
            return
        path, content_type = asset
        self._send(200, (ROOT / path).read_bytes(), content_type)

    def do_POST(self):
        if self.path != "/analyze":
            self._json(404, {"error": "not found"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            size = 0
        if not 0 < size <= MAX_REQUEST_BYTES:
            self._json(413, {"error": "request must be 1–3,000,000 bytes"})
            return
        try:
            report = parse_request(self.rfile.read(size))
        except (InputError, KeyError, TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, report)

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="Run a loopback-only Safe preflight demo")
    parser.add_argument("--port", type=int, default=4181)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1–65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Kamui Safe Treasury Preflight: http://127.0.0.1:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
