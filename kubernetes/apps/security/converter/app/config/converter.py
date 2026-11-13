"""The converter of the security platform: it serves the results of the sources that
export no metrics of their own, in the series of the control registry (security/README.md).

This file holds the stream source. Each 5 minutes it asks the Loki of its site for the
time of the newest line of each security stream, and it serves that time as the time of
the last result. It reads a time and no line, it sends GET requests only, and it keeps
no state on a disk. When Loki has no line, gives an error or gives no answer, it serves
no time for that stream, so the rule for a late result reads the stream as late.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger("converter")

# The series of the converter about itself. They are not in the contract of the registry.
STREAMS = "homelab_security_converter_streams"
LAST_CYCLE = "homelab_security_converter_last_cycle_timestamp_seconds"
QUERY_SUCCESS = "homelab_security_converter_stream_query_success"

INTERVAL_SECONDS = 300
# The converter looks back three times the `late_after` of a stream, and one hour at
# least. A stream whose newest line is older has no time, which the rule reads as late.
LOOKBACK_FACTOR = 3
MIN_LOOKBACK_SECONDS = 3600
TIMEOUT_SECONDS = 10
# One time and the labels of one stream are some hundred bytes. A larger answer is
# not the answer to the query of the converter.
MAX_ANSWER_BYTES = 1024 * 1024
NANOSECONDS = 1_000_000_000


def quote(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    def __init__(self):
        self.lines: list[str] = []

    def add(self, metric_name: str, value: float, **labels):
        suffix = ",".join(f'{key}="{quote(val)}"' for key, val in sorted(labels.items()))
        number = f"{value:.3f}" if isinstance(value, float) else str(int(value))
        self.lines.append(f"{metric_name}{{{suffix}}} {number}" if suffix else f"{metric_name} {number}")

    def render(self) -> bytes:
        return ("\n".join(self.lines) + "\n").encode()


class NoAnswer(Exception):
    """Loki gave an error, an answer that does not fit the query, or no answer."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def selector(labels: dict[str, str]) -> str:
    """The LogQL selector of the streams that carry each label."""
    return "{" + ",".join(f'{key}="{quote(value)}"' for key, value in sorted(labels.items())) + "}"


def newest_time(payload: object, labels: dict[str, str], start: int, end: int) -> int | None:
    """The newest time in an answer of Loki to a log query, in nanoseconds, or None when
    the answer has no line. An answer that does not fit the query raises NoAnswer: a
    stream with other labels, or a time outside the window that the query named."""
    try:
        if payload["status"] != "success" or payload["data"]["resultType"] != "streams":
            raise NoAnswer("the answer is not a success with streams")
        times = []
        for stream in payload["data"]["result"]:
            if any(stream["stream"].get(key) != value for key, value in labels.items()):
                raise NoAnswer("the answer has a stream that the query did not name")
            times += [int(entry[0]) for entry in stream["values"]]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise NoAnswer("the answer does not have the form of a log query") from None
    if any(not start <= moment <= end for moment in times):
        raise NoAnswer("the answer has a time outside the window of the query")
    return max(times, default=None)


class Loki:
    """The read path to one Loki: the time of the newest line of a stream, and no line."""

    def __init__(self, url: str, timeout: float = TIMEOUT_SECONDS):
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("the address of Loki must start with http:// or https://")
        self.url, self.timeout = url.rstrip("/"), timeout
        # No proxy of the environment, and no redirect to a second host.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

    def newest(self, labels: dict[str, str], start: float, end: float) -> float | None:
        """The Unix time of the newest line of the streams with `labels` between `start`
        and `end`. None when Loki has no line there. NoAnswer when Loki gave an error,
        an answer that does not fit, or no answer."""
        window = int(start * NANOSECONDS), int(end * NANOSECONDS)
        # `line_format` puts a constant in the place of the line, so Loki sends the time
        # of the newest line and nothing of its text.
        query = urllib.parse.urlencode({
            "query": selector(labels) + ' | line_format "-"', "start": window[0], "end": window[1],
            "limit": 1, "direction": "backward"})
        request = urllib.request.Request(f"{self.url}/loki/api/v1/query_range?{query}",
                                         headers={"Accept": "application/json"}, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_ANSWER_BYTES + 1)
        except (OSError, http.client.HTTPException) as error:
            # Only the kind of the error: the text of an answer never reaches a log.
            status = getattr(error, "code", None)
            getattr(error, "close", lambda: None)()
            raise NoAnswer(f"{type(error).__name__}{f' {status}' if status else ''}") from None
        if len(raw) > MAX_ANSWER_BYTES:
            raise NoAnswer("the answer is larger than the limit")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise NoAnswer("the answer is not JSON") from None
        newest = newest_time(payload, labels, *window)
        return None if newest is None else newest / NANOSECONDS


class StreamSource:
    """The time of the last result of each security stream, for each system.

    `streams` is the map that security/generate.py writes from the registry: one entry
    for each stream and each system that has it as a check, with the Loki selector of
    its lines. The source keeps the result of its last cycle only. A result goes away
    when the next query has no line or no answer, and when no cycle replaced it in two
    intervals. So the source never serves the time of an older cycle as the newest one.
    """

    def __init__(self, streams: dict, loki, interval: float = INTERVAL_SECONDS,
                 clock=time.time, monotonic=time.monotonic):
        self.series, self.entries, self.loki = streams["series"], streams["streams"], loki
        self.interval, self.clock, self.monotonic = interval, clock, monotonic
        self.max_age = 2 * interval
        self.lock = threading.Lock()
        # (source, check, system) -> (time of the newest line or None, Loki answered, read at)
        self.results: dict[tuple[str, str, str], tuple[float | None, bool, float]] = {}
        self.last_cycle: tuple[float, float] | None = None
        self.started = monotonic()

    @staticmethod
    def key(entry: dict) -> tuple[str, str, str]:
        return entry["source"], entry["check"], entry["system"]

    def cycle(self):
        """Ask Loki one time for each stream and system, and replace each old result."""
        for entry in self.entries:
            now = self.clock()
            lookback = max(MIN_LOOKBACK_SECONDS, LOOKBACK_FACTOR * entry["late_after_seconds"])
            try:
                result = self.loki.newest(entry["selector"], now - lookback, now), True
            except NoAnswer as error:
                logger.warning("no answer for the stream %s of %s: %s", entry["check"], entry["system"], error)
                result = None, False
            except Exception as error:  # noqa: BLE001 - a fault here must not keep an old time
                logger.error("the query for the stream %s of %s failed: %s", entry["check"], entry["system"],
                             type(error).__name__)
                result = None, False
            with self.lock:
                self.results[self.key(entry)] = (*result, self.monotonic())
        with self.lock:
            self.last_cycle = self.clock(), self.monotonic()

    def run(self, stop: threading.Event):
        """One cycle at the start, and one each interval after it."""
        while not stop.is_set():
            started = self.monotonic()
            try:
                self.cycle()
            except Exception as error:  # noqa: BLE001 - the loop must go on
                logger.error("the cycle failed: %s", type(error).__name__)
            stop.wait(max(0.0, self.interval - (self.monotonic() - started)))

    def healthy(self) -> bool:
        """The loop runs: a cycle ended in the last three intervals, or the start was."""
        with self.lock:
            newest = self.last_cycle[1] if self.last_cycle else self.started
        return self.monotonic() - newest <= 3 * self.interval

    def render(self) -> bytes:
        metrics = Metrics()
        now = self.monotonic()
        with self.lock:
            results, last_cycle = dict(self.results), self.last_cycle
        metrics.add(STREAMS, len(self.entries))
        if last_cycle and now - last_cycle[1] <= self.max_age:
            metrics.add(LAST_CYCLE, float(last_cycle[0]))
        current = []
        for entry in self.entries:
            newest, answered, read_at = results.get(self.key(entry), (None, False, None))
            if read_at is None or now - read_at > self.max_age:
                # Not asked yet after a start, or the loop stopped: no series at all.
                continue
            labels = {"source": entry["source"], "check": entry["check"], "system": entry["system"]}
            current.append((labels, newest))
            metrics.add(QUERY_SUCCESS, int(answered), **labels)
        for labels, newest in current:
            if newest is not None:
                metrics.add(self.series, float(newest), **labels)
        return metrics.render()


def handler(source: StreamSource) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                status, body = (200, b"ok\n") if source.healthy() else (503, b"the loop does not run\n")
            elif self.path == "/metrics":
                status, body = 200, source.render()
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams", default="/config/streams.json",
                        help="the map of the streams, from security/generate.py")
    parser.add_argument("--loki", default=os.environ.get("LOKI_URL"), help="the address of Loki")
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--interval", type=float, default=INTERVAL_SECONDS,
                        help="the seconds between two cycles")
    args = parser.parse_args()
    if not args.loki:
        parser.error("the address of Loki is not set (--loki or LOKI_URL)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    streams = json.loads(Path(args.streams).read_text())
    source = StreamSource(streams, Loki(args.loki), interval=args.interval)
    logger.info("the stream source asks %s each %d seconds for %d streams",
                args.loki, args.interval, len(source.entries))
    threading.Thread(target=source.run, args=(threading.Event(),), daemon=True, name="streams").start()
    ThreadingHTTPServer(("0.0.0.0", args.port), handler(source)).serve_forever()


if __name__ == "__main__":
    main()
