"""Test span for Alloy's agent trace receiver (otelcol.processor.transform
"drop_text" in helmrelease.yaml).

Sends one span over OTLP/HTTP with a new marker text in every place the filter
removes, then reads the trace back from Tempo. Fails (exit 1) when Tempo has
the marker, or when an attribute the filter must keep is missing. Prints one
JSON line for each result. The span holds no real data.
"""

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

ALLOY = os.environ.get("ALLOY_OTLP_URL", "http://alloy.monitoring.svc.cluster.local:4318")
TEMPO = os.environ.get("TEMPO_URL", "http://tempo.monitoring.svc.cluster.local:3200")
WAIT_SECONDS = int(os.environ.get("WAIT_SECONDS", "180"))

MARKER = "trace-filter-marker-" + secrets.token_hex(8)

# Attributes of a span that carry text: the filter removes each one.
REMOVED_SPAN = [
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.prompt.0.content",
    "gen_ai.completion.0.content",
    "input.value",
    "output.value",
    "llm.input_messages.0.message.content",
    "llm.output_messages.0.message.content",
    "memory.query",
    "memory.results",
    "memory.content",
]
# Attributes the filter keeps, with the value to find in Tempo.
KEPT_SPAN = {
    "gen_ai.request.model": "check-model",
    "gen_ai.tool.name": "check-tool",
    "check.kept": "yes",
}
KEPT_EVENT = {"check.event.kept": "yes"}


def text(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def span(trace_id: str) -> dict:
    now = time.time_ns()
    attributes = [text(k, MARKER) for k in REMOVED_SPAN]
    attributes += [text(k, v) for k, v in KEPT_SPAN.items()]
    attributes.append({"key": "gen_ai.usage.input_tokens", "value": {"intValue": "7"}})
    events = [
        # Events of the older conventions and of agent memory: every
        # attribute goes.
        {"name": "gen_ai.user.message", "timeUnixNano": str(now),
         "attributes": [text("content", MARKER)]},
        {"name": "gen_ai.choice", "timeUnixNano": str(now),
         "attributes": [text("message", MARKER)]},
        {"name": "memory.results", "timeUnixNano": str(now),
         "attributes": [text("results", MARKER)]},
        # Another event: only the text attributes go.
        {"name": "check.event", "timeUnixNano": str(now),
         "attributes": [text("gen_ai.input.messages", MARKER)]
         + [text(k, v) for k, v in KEPT_EVENT.items()]},
    ]
    return {
        "resourceSpans": [{
            "resource": {"attributes": [text("service.name", "trace-filter-check")]},
            "scopeSpans": [{
                "scope": {"name": "trace-filter-check"},
                "spans": [{
                    "traceId": trace_id,
                    "spanId": secrets.token_hex(8),
                    "name": "trace-filter-check",
                    "kind": 1,
                    "startTimeUnixNano": str(now),
                    "endTimeUnixNano": str(now + 1_000_000),
                    "attributes": attributes,
                    "events": events,
                }],
            }],
        }],
    }


failures = 0


def report(check: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(json.dumps({"check": check, "result": "ok" if ok else "failed",
                      "detail": detail}), flush=True)


def send(trace_id: str) -> None:
    request = urllib.request.Request(
        ALLOY.rstrip("/") + "/v1/traces", method="POST",
        data=json.dumps(span(trace_id)).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as r:
        r.read()


def fetch(trace_id: str) -> str | None:
    """The trace as Tempo returns it, or None while Tempo doesn't have it."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{TEMPO.rstrip('/')}/api/traces/{trace_id}",
                                        timeout=30) as r:
                body = r.read().decode()
            if "trace-filter-check" in body:
                return body
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        time.sleep(5)
    return None


def main() -> None:
    trace_id = secrets.token_hex(16)
    send(trace_id)
    report("span sent to alloy", True, trace_id)
    body = fetch(trace_id)
    if body is None:
        report("trace in tempo", False, f"not found after {WAIT_SECONDS}s")
        return
    report("trace in tempo", True)
    report("no marker text in tempo", MARKER not in body,
           f"{body.count(MARKER)} copies of the marker")
    for key, value in {**KEPT_SPAN, **KEPT_EVENT}.items():
        report(f"kept {key}", key in body and value in body)
    report("kept gen_ai.usage.input_tokens", "gen_ai.usage.input_tokens" in body)
    for name in ("gen_ai.user.message", "gen_ai.choice", "memory.results", "check.event"):
        report(f"kept event {name}", name in body)


if __name__ == "__main__":
    main()
    sys.exit(1 if failures else 0)
