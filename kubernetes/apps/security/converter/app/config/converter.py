"""The converter of the security platform: it serves the results of the sources that
export no metrics of their own, in the series of the control registry (security/README.md).

Each 5 minutes one loop runs each part, and a fault of one part does not stop the others
(security/converter/README.md):

- The stream source asks the Loki of its site for the time of the newest line of each
  security stream, and serves that time as the time of the last result. It reads a time
  and no line. When Loki has no line, gives an error or gives no answer, it serves no
  time for that stream, so the rule for a late result reads the stream as late.
- The list of accepted findings: it reads the list that Flux decrypts to a Secret, and
  serves the end of each accepted finding. No reason leaves the file.

The converter sends GET requests only, and it keeps no state on a disk.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import http.client
import json
import logging
import os
import re
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
ACCEPTED_SKIPPED = "homelab_security_converter_accepted_skipped_entries"
ACCEPTED_READ = "homelab_security_converter_accepted_list_read"

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
    # A character that is not UTF-8 (a lone surrogate) becomes its escape, as text.
    text = str(value).encode("utf-8", "backslashreplace").decode("utf-8")
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    """The text form of Prometheus. The lines of one series name stay together, also when
    two parts serve the same name."""

    def __init__(self):
        self.lines: dict[str, list[str]] = {}

    def add(self, metric_name: str, value: float, **labels):
        suffix = ",".join(f'{key}="{quote(val)}"' for key, val in sorted(labels.items()))
        number = f"{value:.3f}" if isinstance(value, float) else str(int(value))
        self.lines.setdefault(metric_name, []).append(
            f"{metric_name}{{{suffix}}} {number}" if suffix else f"{metric_name} {number}")

    def render(self) -> bytes:
        # A text that can not be UTF-8 (a lone surrogate) never stops a scrape.
        text = "\n".join(line for lines in self.lines.values() for line in lines) + "\n"
        return text.encode("utf-8", "backslashreplace")


class Loop:
    """A part with a cycle: one at the start, and one each interval after it. A part keeps
    the result of its last cycle only, and serves no result that no cycle replaced in two
    intervals. So a loop that stopped never serves an old result as a new one."""

    def __init__(self, interval: float = INTERVAL_SECONDS, clock=time.time, monotonic=time.monotonic):
        self.interval, self.clock, self.monotonic = interval, clock, monotonic
        self.max_age = 2 * interval
        self.lock = threading.Lock()
        self.last_cycle: tuple[float, float] | None = None
        self.started = monotonic()

    def step(self):
        raise NotImplementedError

    def collect(self, metrics: Metrics, now: float):
        raise NotImplementedError

    def clear(self):
        """Forget the result of the last cycle, after a cycle that failed."""

    def cycle(self):
        self.step()
        with self.lock:
            self.last_cycle = self.clock(), self.monotonic()

    def run(self, stop: threading.Event):
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

    def fresh(self, read_at: float | None, now: float) -> bool:
        return read_at is not None and now - read_at <= self.max_age

    def render(self) -> bytes:
        metrics = Metrics()
        now = self.monotonic()
        with self.lock:
            last_cycle = self.last_cycle
        if last_cycle and now - last_cycle[1] <= self.max_age:
            metrics.add(LAST_CYCLE, float(last_cycle[0]))
        self.collect(metrics, now)
        return metrics.render()


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


class StreamSource(Loop):
    """The time of the last result of each security stream, for each system.

    `streams` is the map that security/generate.py writes from the registry: one entry
    for each stream and each system that has it as a check, with the Loki selector of
    its lines. A result goes away when the next query has no line or no answer.
    """

    name = "streams"

    def __init__(self, streams: dict, loki, interval: float = INTERVAL_SECONDS,
                 clock=time.time, monotonic=time.monotonic):
        super().__init__(interval, clock, monotonic)
        self.series, self.entries, self.loki = streams["series"], streams["streams"], loki
        # (source, check, system) -> (time of the newest line or None, Loki answered, read at)
        self.results: dict[tuple[str, str, str], tuple[float | None, bool, float]] = {}

    @staticmethod
    def key(entry: dict) -> tuple[str, str, str]:
        return entry["source"], entry["check"], entry["system"]

    def clear(self):
        with self.lock:
            self.results = {}

    def step(self):
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

    def collect(self, metrics: Metrics, now: float):
        with self.lock:
            results = dict(self.results)
        metrics.add(STREAMS, len(self.entries))
        current = []
        for entry in self.entries:
            newest, answered, read_at = results.get(self.key(entry), (None, False, None))
            if not self.fresh(read_at, now):
                # Not asked yet after a start, or the loop stopped: no series at all.
                continue
            labels = {"source": entry["source"], "check": entry["check"], "system": entry["system"]}
            current.append((labels, newest))
            metrics.add(QUERY_SUCCESS, int(answered), **labels)
        for labels, newest in current:
            if newest is not None:
                metrics.add(self.series, float(newest), **labels)


# --- The list of accepted findings ------------------------------------------------------

# The fields of an entry (security/README.md, "Accepted findings"). Each one is a JSON
# string, and an entry has these five keys and no other.
ACCEPTED_FIELDS = ("source", "check", "system", "until", "reason")
MAX_LIST_BYTES = 1024 * 1024
# The form of a check ID of each tool: Prowler, Trivy (`k8s-cis-1.23/1.2.3`), Wazuh, the
# API checks. A value of another form is no check, so it never reaches a label.
CHECK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}$")
# The one form of an end date. date.fromisoformat also reads 20271231 and 2027-W05-1
# (since Python 3.11). ASCII digits only (\d also takes other digits), and the whole
# value (fullmatch). The Prowler run checks the same form.
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Unreadable(ValueError):
    """A list that gives no entry: not JSON, not an array, or a key two times in one
    object. The text says why, and holds no value of the list."""


def one_key_once(pairs: list[tuple[str, object]]) -> dict:
    """An object of the list. Unreadable for a key that is there two times: `json.loads`
    would keep the last value, and the Prowler job must read the same entry."""
    entry = dict(pairs)
    if len(entry) != len(pairs):
        raise Unreadable("a key two times in one object")
    return entry


def json_list(text: str) -> list:
    """The items of the list: one JSON array, read with `json.loads` as the Prowler job
    reads it. NaN and Infinity are numbers to `json.loads`, so an entry with one is an
    entry with a value that is not a string."""
    try:
        items = json.loads(text, object_pairs_hook=one_key_once)
    except Unreadable:
        raise
    except (ValueError, RecursionError) as error:
        # The kind of the error and its place: its message can quote the text.
        raise Unreadable(f"not JSON: {type(error).__name__} at line {getattr(error, 'lineno', '?')}") from None
    if not isinstance(items, list):
        raise Unreadable("not a JSON array")
    return items


def end_of(until: str) -> int:
    """The Unix time at which an accepted finding ends: the start of the day after its
    end date, UTC (security/README.md, "Accepted findings"). ValueError when until is
    not a day of the form YYYY-MM-DD."""
    if not DATE.fullmatch(until):
        raise ValueError(f"not of the form YYYY-MM-DD: {until!r}")
    day = datetime.date.fromisoformat(until)
    return calendar.timegm((day + datetime.timedelta(days=1)).timetuple())


def why_not(entry: object, sources: set[str], systems: set[str]) -> str | None:
    """Why an item of the list does not fit, or None when it fits."""
    if not isinstance(entry, dict):
        return "not an object"
    if set(entry) != set(ACCEPTED_FIELDS):
        return "a field is missing, or a key is not a field"
    if not all(isinstance(entry[field], str) for field in ACCEPTED_FIELDS):
        return "a value is not a string"
    if not all(entry[field] for field in ACCEPTED_FIELDS) or not entry["reason"].strip():
        return "a value is empty"
    if entry["source"] not in sources or entry["system"] not in systems:
        return "the source or the system is not in the registry"
    if not CHECK_ID.match(entry["check"]):
        return "the check is not a check ID"
    try:
        end_of(entry["until"])
    except ValueError:
        return "the end date is not a date"
    return None


def accepted_entries(text: str, sources: set[str], systems: set[str]
                     ) -> tuple[dict[tuple[str, str, str], int], int]:
    """{(source, check, system): end} for each entry that fits, and the number of items
    that do not fit. Two entries of one finding keep the later end. Unreadable when the
    list gives no entry. The reason stays in the list: only whether it is there counts."""
    result: dict[tuple[str, str, str], int] = {}
    skipped = 0
    for index, entry in enumerate(json_list(text), 1):
        why = why_not(entry, sources, systems)
        if why:
            # The number of the entry only: a value of the list never reaches a log.
            logger.warning("entry %d of the list of accepted findings is skipped: %s", index, why)
            skipped += 1
            continue
        key = (entry["source"], entry["check"], entry["system"])
        end = end_of(entry["until"])
        result[key] = max(end, result.get(key, end))
    return result, skipped


class AcceptedList(Loop):
    """The end of each accepted finding, from the list that Flux decrypts to the Secret
    security-accepted-findings. With no Secret, the list is empty. The converter reads
    the file each cycle, so a new list counts from the next cycle.

    An entry that does not fit is skipped, and a list that is not one JSON array gives
    no entry at all, with homelab_security_converter_accepted_list_read at 0."""

    name = "accepted"

    def __init__(self, path: Path, registry: dict, interval: float = INTERVAL_SECONDS,
                 clock=time.time, monotonic=time.monotonic):
        super().__init__(interval, clock, monotonic)
        self.path = Path(path)
        self.series = registry["series"]["accepted"]
        self.sources, self.systems = set(registry["sources"]), set(registry["systems"])
        self.result: tuple[dict[tuple[str, str, str], int], int, bool, float] | None = None

    def read(self) -> tuple[dict[tuple[str, str, str], int], int, bool]:
        """The entries, the number of skipped entries, and whether the file was read."""
        try:
            with self.path.open("rb") as file:
                raw = file.read(MAX_LIST_BYTES + 1)
        except FileNotFoundError:
            return {}, 0, True
        except OSError as error:
            logger.warning("the list of accepted findings can not be read: %s", type(error).__name__)
            return {}, 0, False
        if len(raw) > MAX_LIST_BYTES:
            logger.warning("the list of accepted findings is larger than the limit")
            return {}, 0, False
        try:
            entries, skipped = accepted_entries(raw.decode("utf-8"), self.sources, self.systems)
        except UnicodeDecodeError:
            logger.warning("the list of accepted findings is not UTF-8")
            return {}, 0, False
        except Unreadable as error:
            # The kind of the fault and its line, and no text of the list.
            logger.warning("the list of accepted findings gives no entry: %s", error)
            return {}, 0, False
        return entries, skipped, True

    def step(self):
        entries, skipped, read = self.read()
        with self.lock:
            self.result = entries, skipped, read, self.monotonic()

    def clear(self):
        with self.lock:
            self.result = None

    def collect(self, metrics: Metrics, now: float):
        with self.lock:
            result = self.result
        if result is None or not self.fresh(result[3], now):
            return
        entries, skipped, read, _ = result
        metrics.add(ACCEPTED_READ, int(read))
        metrics.add(ACCEPTED_SKIPPED, skipped)
        for (source, check, system), end in sorted(entries.items()):
            metrics.add(self.series, end, source=source, check=check, system=system)


# --- The service ------------------------------------------------------------------------


class Converter(Loop):
    """Each part of the converter in one loop. A fault of one part does not stop the
    others, and a part whose cycle failed serves nothing of an older cycle."""

    def __init__(self, parts: list[Loop], interval: float = INTERVAL_SECONDS,
                 clock=time.time, monotonic=time.monotonic):
        super().__init__(interval, clock, monotonic)
        self.parts = parts

    def step(self):
        for part in self.parts:
            try:
                part.step()
            except Exception as error:  # noqa: BLE001 - one part must not stop the others
                logger.error("the part %s failed: %s", part.name, type(error).__name__)
                part.clear()

    def collect(self, metrics: Metrics, now: float):
        for part in self.parts:
            part.collect(metrics, now)


def handler(source: Loop) -> type[BaseHTTPRequestHandler]:
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
    parser.add_argument("--registry", default="/config/registry.json",
                        help="the names of the registry, from security/generate.py")
    parser.add_argument("--accepted", default="/accepted/accepted.json",
                        help="the list of accepted findings, from its Secret")
    parser.add_argument("--loki", default=os.environ.get("LOKI_URL"), help="the address of Loki")
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--interval", type=float, default=INTERVAL_SECONDS,
                        help="the seconds between two cycles")
    args = parser.parse_args()
    if not args.loki:
        parser.error("the address of Loki is not set (--loki or LOKI_URL)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    registry = json.loads(Path(args.registry).read_text())
    streams = StreamSource(json.loads(Path(args.streams).read_text()), Loki(args.loki), interval=args.interval)
    accepted = AcceptedList(Path(args.accepted), registry, interval=args.interval)
    converter = Converter([streams, accepted], interval=args.interval)
    logger.info("the converter asks %s each %d seconds for %d streams, and reads the list of accepted findings",
                args.loki, args.interval, len(streams.entries))
    threading.Thread(target=converter.run, args=(threading.Event(),), daemon=True, name="converter").start()
    ThreadingHTTPServer(("0.0.0.0", args.port), handler(converter)).serve_forever()


if __name__ == "__main__":
    main()
