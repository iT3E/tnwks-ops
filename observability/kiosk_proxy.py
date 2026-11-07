"""Exact-query, aggregate-only Prometheus facade for the isolated household Grafana."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def sanitize(result: dict) -> dict:
    """Drop all upstream identifiers except the approved household domain."""
    allowed_domains = {"internet", "wifi", "automation", "cameras", "apps", "monitoring"}
    if result.get("status") != "success" or result.get("data", {}).get("resultType") != "vector":
        raise ValueError("unexpected aggregate response")
    grouped: dict[str, list[tuple[float, float]]] = {}
    for item in result["data"].get("result", []):
        domain = item.get("metric", {}).get("domain")
        value = item.get("value", [])
        if domain not in allowed_domains or len(value) != 2:
            raise ValueError("unapproved aggregate schema")
        state = float(value[1])
        sample_time = float(value[0])
        if state not in (0, 1, 2, 3, 4, 5, 6) or not math.isfinite(sample_time):
            raise ValueError("unapproved aggregate value")
        grouped.setdefault(domain, []).append((float(value[0]), state))
    cleaned = []
    for domain, observations in sorted(grouped.items()):
        states = {state for _, state in observations}
        # Never let a later healthy sample mask failure or unknown evidence from
        # another observer. Preserve one aggregate series per approved domain.
        state = next((state for state in (3, 2, 0, 6) if state in states), None)
        if state is None:
            state = 1 if 1 in states else 4 if 4 in states else 5
        cleaned.append({"metric": {"domain": domain},
                        "value": [max(t for t, _ in observations), str(state)]})
    return {"status": "success", "data": {"resultType": "vector", "result": cleaned}}


def handler(allowed: set[str], upstream: str):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code: int, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.query(urllib.parse.urlparse(self.path).query)

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.respond(400, {"status": "error", "error": "invalid length"})
                return
            if size > 2048 or size < 0:
                self.respond(413, {"status": "error", "error": "request too large"})
                return
            try:
                encoded = self.rfile.read(size).decode()
            except UnicodeDecodeError:
                self.respond(400, {"status": "error", "error": "invalid encoding"})
                return
            self.query(encoded)

        def query(self, encoded: str):
            if len(self.path) + len(encoded) > 4096:
                self.respond(413, {"status": "error", "error": "request too large"})
                return
            path = urllib.parse.urlparse(self.path).path
            if path == "/healthz":
                self.respond(200, {"status": "ok"})
                return
            if path != "/api/v1/query":
                self.respond(403, {"status": "error", "error": "endpoint not allowed"})
                return
            params = urllib.parse.parse_qs(encoded, keep_blank_values=True)
            # Reject duplicate queries, arbitrary proxy parameters, range/metadata/label
            # endpoints and general PromQL. Folder permissions cannot provide this boundary.
            if set(params) - {"query", "time", "timeout"} or len(params.get("query", [])) != 1:
                self.respond(403, {"status": "error", "error": "query not allowed"})
                return
            query = params["query"][0]
            if query == "1+1":
                self.respond(200, {"status": "success", "data": {"resultType": "vector", "result": []}})
                return
            if query not in allowed:
                self.respond(403, {"status": "error", "error": "query not allowed"})
                return
            # Always read current approved aggregates: arbitrary historical queries would
            # expose a different contract and are not required by the kiosk stat panels.
            url = upstream + "/api/v1/query?" + urllib.parse.urlencode({"query": query, "timeout": "5s"})
            try:
                with urllib.request.urlopen(url, timeout=6) as response:
                    raw = response.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("aggregate response too large")
                    cleaned = sanitize(json.loads(raw))
            except (OSError, ValueError, urllib.error.URLError):
                self.respond(503, {"status": "error", "error": "aggregate evidence unavailable"})
                return
            self.respond(200, cleaned)

        def log_message(self, *_):
            pass

    return Handler


def main():
    allowed = set(json.loads(Path(os.environ.get("ALLOWLIST", "/config/allowlist.json")).read_text()))
    upstream = os.environ.get("PROMETHEUS_URL", "http://thanos-query.monitoring.svc.cluster.local:9090")
    Handler = handler(allowed, upstream)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "9877"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
