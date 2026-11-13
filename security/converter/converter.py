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
- The files of results: it reads the newest OCSF file of each system that Prowler wrote,
  and the result file of the API checks, and serves the number of resources that fail
  each check, and the time of the result. A file that it can not read gives no result
  and no time for its system, so the rule for a late result fires. Each finding that
  fails in the newest file of a system is one line of the security stream `findings`
  in the same Loki: a part of its OCSF record, with no data of the resource.

The converter sends GET requests to Loki, and POST requests to its push API for the stream
`findings` only. It never writes to the volume of results, and it keeps no state on a disk.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import http.client
import io
import json
import logging
import os
import re
import stat
import threading
import time
import urllib.error
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
FILES_SKIPPED = "homelab_security_converter_files_skipped"
FINDINGS_PENDING = "homelab_security_converter_findings_pending"
FINDINGS_SENT = "homelab_security_converter_findings_lines_sent_total"
FINDINGS_REFUSED = "homelab_security_converter_findings_lines_refused_total"
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


class Refused(Exception):
    """Loki answered that it will never take the lines: a status 4xx that is not 429."""


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

    def push(self, labels: dict[str, str], entries: list[tuple[int, str]]):
        """Send lines of one stream to the push API. Refused when Loki will never take
        them, NoAnswer for each other error and for no answer: then the converter sends
        them again in its next cycle. A 413 (the body is larger than the limit of the
        gateway) is NoAnswer too: the lines wait, and the converter does not lose them.
        The body is ASCII, so a text that is not UTF-8 (a lone surrogate) is an escape."""
        body = json.dumps({"streams": [{"stream": labels, "values": [[str(moment), line] for moment, line in entries]}]},
                          separators=(",", ":"), ensure_ascii=True).encode("ascii")
        request = urllib.request.Request(f"{self.url}/loki/api/v1/push", data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                response.read(MAX_ANSWER_BYTES)
        except urllib.error.HTTPError as error:
            # Only the status: the text of an answer can hold a line, and never reaches a log.
            error.close()
            if 400 <= error.code < 500 and error.code not in (413, 429):
                raise Refused(f"HTTPError {error.code}") from None
            raise NoAnswer(f"HTTPError {error.code}") from None
        except (OSError, http.client.HTTPException) as error:
            raise NoAnswer(type(error).__name__) from None


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

# The entry fields that the converter reads. `reason` stays in the file.
ACCEPTED_FIELDS = ("source", "check", "system", "until")
MAX_LIST_BYTES = 1024 * 1024
# The form of a check ID of each tool: Prowler, Trivy (`k8s-cis-1.23/1.2.3`), Wazuh, the
# API checks. A value of another form is no check, so it never reaches a label.
CHECK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,199}$")
# The plain values that PyYAML reads as another type than text: the implicit types of
# YAML 1.1 in its resolver. The reader takes such a value as text only when PyYAML gives
# the same text (a decimal number other than 0), and as a day when it is a date.
TRUE_WORDS = {"yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON"}
FALSE_WORDS = {"no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF", "null", "Null", "NULL", "~"}
YAML_NUMBER = re.compile(r"""^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+
    |[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+
    |[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$""", re.VERBOSE)
YAML_TIMESTAMP = re.compile(r"""^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]
    |[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?
    (?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$""", re.VERBOSE)
DECIMAL = re.compile(r"^[1-9][0-9]*$")
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# A character that YAML does not let a file hold. PyYAML stops on it.
NOT_PRINTABLE = re.compile("[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")
KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ ]+(.*))?$")
BLOCK_STARTS = ("|", ">", "|-", ">-", "|+", ">+")


class Undecoded:
    """A value that the reader does not decode: a value on more lines, or a value in
    double quotes with an escape of YAML that JSON does not have. Only `reason` can have
    one, and `text` says whether it holds more than space."""

    def __init__(self, text: bool = False):
        self.text = text


def has_text(segment: str, mark: str = "") -> bool:
    """Whether a part of a value holds a character that is not space. A backslash and the
    quote mark do not count, so an escape of a space counts as space: the reader takes
    such a reason as empty, and mutes less."""
    return any(char not in " \t\\" + mark for char in segment)


class Unreadable(ValueError):
    """A line of the list that the reader does not understand. After such a line the
    reader can not know where the next entry starts, so the file gives no entry."""


# The escapes of a double-quoted YAML value, and the number of hex digits after x, u, U.
YAML_ESCAPES = {**dict.fromkeys('0abt\tnvfre "/\\N_LP', 0), "x": 2, "u": 4, "U": 8}
HEX = re.compile(r"[0-9A-Fa-f]+")


def quote_end(text: str, mark: str, start: int) -> int | None:
    """The index after the quote that closes a value opened with `mark`, or None when the
    value goes on to the next line. In single quotes `''` is a quote of the text; in
    double quotes a backslash escapes the next character, also the end of the line.
    Unreadable for an escape that YAML does not have: PyYAML stops on it."""
    at = start
    while at < len(text):
        char = text[at]
        if mark == "'" and char == "'":
            if text[at + 1:at + 2] == "'":
                at += 2
                continue
            return at + 1
        if mark == '"' and char == "\\":
            escape = text[at + 1:at + 2]
            if escape == "":
                return None  # the value goes on, with no space
            digits = YAML_ESCAPES.get(escape)
            if digits is None or (digits and not HEX.fullmatch(text[at + 2:at + 2 + digits] or "-")
                                  or len(text[at + 2:at + 2 + digits]) < digits):
                raise Unreadable("an escape that YAML does not have")
            at += 2 + digits
            continue
        if mark == '"' and char == '"':
            return at + 1
        at += 1
    return None


def plain(value: str) -> str | Undecoded:
    """The text of a plain value as PyYAML reads it, or Undecoded when PyYAML reads it as
    another type than text with another text. Its `text` is True for a word that PyYAML
    reads as true, and False for each other: so a reason of `false`, `null` or `0` is no
    reason, as for the Prowler job, and a number other than a decimal one counts as no
    reason too (it mutes less). Unreadable where PyYAML stops: `=`, `<<`, a date that is
    no day; and for a time of day, which the reader does not check."""
    if value in ("=", "<<"):
        raise Unreadable("a value of a type that PyYAML does not build")
    if value in TRUE_WORDS:
        return Undecoded(True)
    if value in FALSE_WORDS:
        return Undecoded(False)
    if YAML_NUMBER.match(value):
        return value if DECIMAL.match(value) else Undecoded(False)
    if YAML_TIMESTAMP.match(value):
        if not DATE.match(value):
            raise Unreadable("a time of day in a plain value")
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise Unreadable("a date that is no day") from None
    return value


def scalar(text: str) -> tuple[str | Undecoded | None, str | None]:
    """The value of a `key: value` line, and what may follow it: "block" (the lines of a
    block value), "plain" (more lines of a plain value), "empty", None, or the quote mark
    of a quoted value that goes on to the next lines. The value is None when it is empty,
    or a plain value that PyYAML reads as another type. It is Undecoded for a block, a
    quoted value that goes on, and a double-quoted value that the JSON decoder does not
    read. Unreadable for a flow
    collection, an anchor, an alias, a tag, or a plain value with a colon."""
    if text == "" or text.startswith("#"):
        return None, "empty"
    if text.split(" #", 1)[0].rstrip() in BLOCK_STARTS:
        return None, "block"
    if text[0] in "'\"":
        end = quote_end(text, text[0], 1)
        if end is None:
            return None, text[0]
        rest = text[end:].strip()
        if rest and not rest.startswith("#"):
            raise Unreadable("text after a quoted value")
        if text[0] == "'":
            return text[1:end - 1].replace("''", "'"), None
        try:
            return json.loads(text[:end]), None
        except ValueError:
            return Undecoded(has_text(text[1:end - 1], '"')), None
    if text[0] in "[]{},&*!%@`|>" or text.startswith(("- ", "? ", ": ")) or text in ("-", "?", ":"):
        raise Unreadable("a flow collection, an anchor, an alias, a tag or an indicator")
    value = text.split(" #", 1)[0].rstrip()
    if ": " in value or value.endswith(":"):
        raise Unreadable("a plain value with a colon")
    return plain(value), "plain"


def yaml_list(text: str) -> list[dict[str, str | Undecoded | None]]:
    """The entries of the list of accepted findings, in the block form of
    security/README.md and of `yaml.safe_dump`: a YAML list of mappings, one `key: value`
    on each line. The Python image of the converter has no YAML library, so this reader
    takes that form only, and raises Unreadable at the first line of another form.

    A quoted value can go on over more lines, as YAML lets it: the reader goes to its
    closing quote, and reads no key in those lines. A block value (`|`, `>`) and a plain
    value can go on over lines that are more indented than the key. A value on more lines
    is Undecoded, so only `reason` can have one: an entry with such a `check`, `system`,
    `source` or `until` does not fit."""
    if NOT_PRINTABLE.search(text):
        raise Unreadable("a character that YAML does not let a file hold")
    entries: list[dict[str, str | Undecoded | None]] = []
    dash = key_column = last_key = block_column = None
    follows = None
    for number, raw in enumerate(text.splitlines(), 1):
        if follows in ("'", '"'):
            if raw.startswith(("---", "...")) and raw[3:4] in ("", " ", "\t"):
                raise Unreadable(f"line {number}: the end of a document inside a quoted value")
            end = quote_end(raw, follows, 0)
            value = entries[-1][last_key]
            value.text = value.text or has_text(raw if end is None else raw[:end - 1], follows)
            if end is not None:
                rest = raw[end:].strip()
                if rest and not rest.startswith("#"):
                    raise Unreadable(f"line {number}: text after a quoted value")
                follows = None
            continue
        line = raw.rstrip()
        content = line.lstrip(" ")
        indent = len(line) - len(content)
        if follows == "block":
            # The lines of a block value: blank, or more indented than the key. The
            # first line sets the column of the value.
            if not content:
                continue
            if indent > key_column:
                if block_column is None:
                    block_column = indent
                if indent < block_column:
                    raise Unreadable(f"line {number}: a line of a block value at another column")
                entries[-1][last_key].text = True
                continue
            follows = None
        if not content or content.startswith("#") or (line == "---" and dash is None):
            if content.startswith("#") and follows == "plain":
                follows = None  # a comment ends a plain value
            continue
        if content[0] == "\t":
            raise Unreadable(f"line {number}: a tab in the indentation")
        if (content == "-" or content.startswith("- ")) and indent == (indent if dash is None else dash):
            dash = indent
            entries.append({})
            key_column = last_key = follows = None
            rest = content[1:].lstrip(" ")
            if not rest or rest.startswith("#"):
                continue
            if rest[0] == "\t":
                raise Unreadable(f"line {number}: a tab in the indentation")
            indent = indent + len(content) - len(rest)
            key_column, content = indent, rest
        if not entries:
            raise Unreadable(f"line {number}: a line outside the list")
        if key_column is None and indent > dash:
            key_column = indent
        entry = entries[-1]
        if indent == key_column:
            match = KEY_LINE.match(content)
            if not match:
                raise Unreadable(f"line {number}: not a `key: value` line")
            if match[1] in entry:
                raise Unreadable(f"line {number}: a key two times in one entry")
            value, follows = scalar(match[2] or "")
            if follows in ("'", '"'):
                value = Undecoded(has_text((match[2] or "")[1:], follows))
            elif follows == "block":
                value = Undecoded()
            entry[match[1]], last_key, block_column = value, match[1], None
        elif key_column is not None and indent > key_column and follows == "plain":
            # A line of a plain value on more lines.
            if ": " in content or content.endswith(":") or " #" in content or "\t#" in content:
                raise Unreadable(f"line {number}: a colon or a comment in a plain value")
            entry[last_key] = Undecoded(True)
        else:
            raise Unreadable(f"line {number}: a line at a column of no key")
    if follows in ("'", '"'):
        raise Unreadable("the file ends inside a quoted value")
    return entries


def has_reason(reason: object) -> bool:
    """Whether an entry has a reason (Design Doc 4.1.2: an accepted finding has one). The
    reason stays in the file: only whether it is there leaves this function. A reason
    that PyYAML reads as empty, null, false or 0 is no reason, as for the Prowler job."""
    if isinstance(reason, Undecoded):
        return reason.text
    return isinstance(reason, str) and reason != ""


def end_of(until: object) -> int:
    """The Unix time at which an accepted finding ends: the start of the day after its
    end date, UTC (security/README.md, "Accepted findings")."""
    day = datetime.date.fromisoformat(str(until))
    return calendar.timegm((day + datetime.timedelta(days=1)).timetuple())


def accepted_entries(text: str, sources: set[str], systems: set[str]
                     ) -> tuple[dict[tuple[str, str, str], int], int]:
    """{(source, check, system): end} for each entry that fits, and the number of entries
    that do not fit: a field is missing, empty or on more lines, the reason is missing or
    empty, the source or the system is not in the registry, the check is not a check ID,
    or the end date is not a date. Two entries of one finding keep the later end. Unreadable when a line of
    the file has a form that the reader does not read: then the file gives no entry."""
    result: dict[tuple[str, str, str], int] = {}
    skipped = 0
    for index, entry in enumerate(yaml_list(text), 1):
        source, check, system, until = (entry.get(field) for field in ACCEPTED_FIELDS)
        if not all(isinstance(value, str) and value for value in (source, check, system, until)):
            why = "a field is missing, empty or on more lines"
        elif not has_reason(entry.get("reason")):
            why = "the reason is missing or empty"
        elif source not in sources or system not in systems:
            why = "the source or the system is not in the registry"
        elif not CHECK_ID.match(check):
            why = "the check is not a check ID"
        else:
            try:
                end = end_of(until)
            except ValueError:
                why = "the end date is not a date as YYYY-MM-DD"
            else:
                key = (source, check, system)
                result[key] = max(end, result.get(key, end))
                continue
        # The number of the entry only: a value of the list never reaches a log.
        logger.warning("entry %d of the list of accepted findings is skipped: %s", index, why)
        skipped += 1
    return result, skipped


class AcceptedList(Loop):
    """The end of each accepted finding, from the list that Flux decrypts to the Secret
    security-accepted-findings. With no Secret, the list is empty. The converter reads
    the file each cycle, so a new list counts from the next cycle.

    The reader mutes too little rather than too much: an entry that does not fit is
    skipped, and a file with a line that the reader does not understand gives no entry
    at all, with homelab_security_converter_accepted_list_read at 0."""

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
            # The number of the line and the kind of its form, and no text of the file.
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


# --- The files of results --------------------------------------------------------------

# prowler/<provider>/<system>.<YYYYMMDDTHHMMSSZ>.ocsf.json (README, "The OCSF files").
OCSF_NAME = re.compile(r"^(?P<system>[^/]+)\.(?P<stamp>\d{8}T\d{6}Z)\.ocsf\.json$")
API_FILE = Path("api-check/results.json")
# A time of a result that is this far in the future is no result.
CLOCK_SKEW_SECONDS = 300
# The converter reads an OCSF file one item at a time, so its memory does not grow
# with the file. These limits stop a file that is not a file of Prowler.
MAX_OCSF_BYTES = 256 * 1024 * 1024
MAX_ITEM_CHARS = 4 * 1024 * 1024
CHUNK_CHARS = 64 * 1024
MAX_API_BYTES = 16 * 1024 * 1024
# A file of Prowler holds one record of some kB for each resource of each check: some
# thousand records in an account. These limits keep the memory of the counts, and the
# number of series, small for a file that is not a file of Prowler.
MAX_RECORDS = 200_000
MAX_CHECKS = 5_000
# The values of the label `severity`, from low to high: the OCSF names in lower case,
# as the rule of Trivy Operator writes them.
SEVERITIES = ("unknown", "informational", "low", "medium", "high", "critical")


class BadFile(ValueError):
    """A file of results that does not fit its contract. The text says why, and never
    holds a value of the file."""


def severity(value: object) -> str:
    text = str(value).lower() if isinstance(value, str) else ""
    return text if text in SEVERITIES else "unknown"


def higher(one: str, two: str) -> str:
    return max(one, two, key=SEVERITIES.index)


def json_array(stream, chunk: int = CHUNK_CHARS, limit: int = MAX_ITEM_CHARS):
    """Each object of the JSON array in a text stream, with one object in memory at a
    time. BadFile when the stream is not one array of objects, or one object is larger
    than the limit."""
    decoder = json.JSONDecoder()
    buffer, at, ended = "", 0, False

    def fill():
        nonlocal buffer, at, ended
        data = stream.read(chunk)
        ended = not data
        buffer, at = buffer[at:] + data, 0

    def skip_space():
        nonlocal at
        while True:
            while at < len(buffer) and buffer[at] in " \t\r\n":
                at += 1
            if at < len(buffer) or ended:
                return
            fill()

    skip_space()
    if at >= len(buffer) or buffer[at] != "[":
        raise BadFile("the file is not a JSON array")
    at += 1
    skip_space()
    if at < len(buffer) and buffer[at] == "]":
        at += 1
    else:
        while True:
            while True:
                try:
                    item, end = decoder.raw_decode(buffer, at)
                    break
                except json.JSONDecodeError:
                    if ended or len(buffer) - at > limit:
                        raise BadFile("an item is not JSON, is larger than the limit, or does not end") from None
                    fill()
            if not isinstance(item, dict):
                raise BadFile("an item of the array is not an object")
            at = end
            yield item
            skip_space()
            if at < len(buffer) and buffer[at] == ",":
                at += 1
                skip_space()
            elif at < len(buffer) and buffer[at] == "]":
                at += 1
                break
            else:
                raise BadFile("the array does not end")
    skip_space()
    if at < len(buffer):
        raise BadFile("text after the array")


def ocsf_checks(records) -> dict[str, tuple[int, str]]:
    """{check: (resources that fail, severity)} from the records of one OCSF file of
    Prowler (Detection Finding). The check is `metadata.event_code`. A record with
    `status_code` FAIL fails, unless its `status` is "Suppressed": the list of accepted
    findings muted it. A check with no PASS and no FAIL record (MANUAL only) has no
    result. The severity of a check is the highest of its records. A resource is the
    `uid` of its first resource, or the `uid` of the finding when it has none. BadFile
    for a check that is not a check ID, or a file over the limits of records or checks."""
    checks: dict[str, tuple[set[int], bool, str]] = {}
    for index, record in enumerate(records):
        if index >= MAX_RECORDS:
            raise BadFile("the file has more records than the limit")
        metadata = record.get("metadata")
        check = metadata.get("event_code") if isinstance(metadata, dict) else None
        status = record.get("status_code")
        if not isinstance(check, str) or not CHECK_ID.match(check):
            raise BadFile(f"record {index} has no check ID")
        if not isinstance(status, str):
            raise BadFile(f"record {index} has no status")
        if check not in checks and len(checks) >= MAX_CHECKS:
            raise BadFile("the file has more checks than the limit")
        failed, ran, level = checks.get(check, (set(), False, "unknown"))
        level = higher(level, severity(record.get("severity")))
        if status in ("PASS", "FAIL"):
            ran = True
        if status == "FAIL" and record.get("status") != "Suppressed":
            # The hash of the ID, not the ID: the memory stays some bytes for each.
            failed.add(hash(resource_id(record) or f"record {index}"))
        checks[check] = failed, ran, level
    return {check: (len(failed), level) for check, (failed, ran, level) in checks.items() if ran}


def resource_id(record: dict) -> str | None:
    """The ID of the resource of a record: the `uid` of its first resource, which is the
    ARN on AWS, or else the `uid` of the finding, which Prowler makes from the name of
    the resource."""
    resources = record.get("resources")
    first = resources[0] if isinstance(resources, list) and resources else None
    info = record.get("finding_info")
    for value in (first.get("uid") if isinstance(first, dict) else None,
                  info.get("uid") if isinstance(info, dict) else None):
        if isinstance(value, str) and value:
            return value
    return None


def read_ocsf(file) -> dict[str, tuple[int, str]]:
    return ocsf_checks(json_array(io.TextIOWrapper(file, encoding="utf-8")))


def number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def api_results(payload: object, now: float) -> tuple[float, list[dict]]:
    """The time and the results of the result file of the API checks, in the form of
    README, "The result file of the API checks". BadFile for each other form."""
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("source") != "api-check":
        raise BadFile("the file is not version 1 of the API checks")
    moment, results = payload.get("time"), payload.get("results")
    if not number(moment) or not 0 < moment <= now + CLOCK_SKEW_SECONDS:
        raise BadFile("the time of the file is not a time of the past")
    if not isinstance(results, list):
        raise BadFile("the file has no list of results")
    if len(results) > MAX_CHECKS:
        raise BadFile("the file has more results than the limit")
    seen = set()
    for index, entry in enumerate(results):
        if not isinstance(entry, dict):
            raise BadFile(f"result {index} is not an object")
        check, system, failed = entry.get("check"), entry.get("system"), entry.get("failed")
        if not isinstance(check, str) or not CHECK_ID.match(check):
            raise BadFile(f"result {index} has no check ID")
        if not isinstance(system, str) or not system:
            raise BadFile(f"result {index} has no system")
        if not isinstance(failed, int) or isinstance(failed, bool) or failed < 0:
            raise BadFile(f"result {index} has no number of resources that fail")
        if "severity" in entry and severity(entry["severity"]) != entry["severity"]:
            raise BadFile(f"result {index} has a severity that is not one of the list")
        findings = entry.get("findings", [])
        if not isinstance(findings, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("resource"), str)
                or not isinstance(item.get("message"), str) for item in findings):
            raise BadFile(f"result {index} has findings that are not in the form")
        if (check, system) in seen:
            raise BadFile(f"result {index} is a second result of one check and system")
        seen.add((check, system))
    return float(moment), results


def open_regular(path: Path):
    """The open file and its status, for a regular file only: the converter follows no
    link, and a FIFO would stop the loop. BadFile for each other kind of file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise BadFile(f"the file can not be opened: {type(error).__name__}") from None
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise BadFile("the file is not a regular file")
    return os.fdopen(descriptor, "rb"), info


def read_api(file, now: float) -> tuple[float, list[dict]]:
    raw = file.read(MAX_API_BYTES + 1)
    if len(raw) > MAX_API_BYTES:
        raise BadFile("the file is larger than the limit")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise BadFile("the file is not JSON") from None
    return api_results(payload, now)


# --- The stream `findings` --------------------------------------------------------------

FINDINGS_STREAM = "findings"
# A line is a part of the OCSF record: what failed, where, and why, in the names of OCSF.
# No data, no tag and no label of a resource, so no setting of a resource is in a line.
TEXT_LIMIT = 1024
DETAIL_LIMIT = 8192
MAX_RESOURCES = 20
# The bytes of a batch on the wire: the JSON of its lines in the body of the push. The
# gateway of Loki takes a body of 4 MiB at most (`client_max_body_size` of the chart).
MAX_BATCH_BYTES = 1024 * 1024
# The time, the brackets, the quotes and the comma of one line in the body.
LINE_OVERHEAD_BYTES = 32
# Loki refuses a line of more than 256 KB (`max_line_size`). A line with long texts of
# 4 bytes in UTF-8 could reach that, so a line above this size has shorter texts.
MAX_LINE_BYTES = 64 * 1024
# The limits of the texts of a line: the first that gives a line under MAX_LINE_BYTES.
LINE_LIMITS = ((TEXT_LIMIT, DETAIL_LIMIT, MAX_RESOURCES), (256, 2048, 5), (64, 512, 1))


def text(value: object, limit: int = TEXT_LIMIT) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def compact(mapping: dict) -> dict:
    return {key: value for key, value in mapping.items() if value is not None and value != {} and value != []}


def child(value: object, key: str) -> dict:
    found = value.get(key) if isinstance(value, dict) else None
    return found if isinstance(found, dict) else {}


def ocsf_line(record: dict) -> str:
    """The line of the stream `findings` for one OCSF record, with the first limits of
    LINE_LIMITS that give a line of at most MAX_LINE_BYTES in UTF-8."""
    for limits in LINE_LIMITS:
        line = ocsf_line_within(record, *limits)
        if len(line.encode("utf-8", "backslashreplace")) <= MAX_LINE_BYTES:
            break
    return line


def ocsf_line_within(record: dict, text_limit: int, detail_limit: int, max_resources: int) -> str:
    """The line for one OCSF record: its class, time, severity, status and detail, its
    check (`metadata.event_code`), the ID and title of the finding, the ID, name, type,
    Region and service of each resource, and the account. Each other field of the
    record stays in the file."""
    def text(value: object, limit: int = text_limit) -> str | None:
        return value[:limit] if isinstance(value, str) and value else None

    cloud, account = child(record, "cloud"), child(child(record, "cloud"), "account")
    resources = record.get("resources") if isinstance(record.get("resources"), list) else []
    moment = record.get("time")
    line = compact({
        "class_uid": record.get("class_uid") if number(record.get("class_uid")) else None,
        "time": int(moment) if number(moment) else None,
        "severity": text(record.get("severity")),
        "status": text(record.get("status")),
        "status_code": text(record.get("status_code")),
        "status_detail": text(record.get("status_detail"), detail_limit),
        "metadata": compact({"event_code": text(child(record, "metadata").get("event_code"))}),
        "finding_info": compact({key: text(child(record, "finding_info").get(key)) for key in ("uid", "title")}),
        "resources": [item for item in (
            compact({**{key: text(resource.get(key)) for key in ("uid", "name", "type", "region")},
                     "group": compact({"name": text(child(resource, "group").get("name"))})})
            for resource in resources[:max_resources] if isinstance(resource, dict)) if item],
        "cloud": compact({"provider": text(cloud.get("provider")), "region": text(cloud.get("region")),
                          "account": compact({key: text(account.get(key)) for key in ("uid", "name")})}),
    })
    return json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def ocsf_lines(path: Path):
    """The line of each finding that fails in an OCSF file, one record at a time."""
    file, _ = open_regular(path)
    with file:
        for index, record in enumerate(json_array(io.TextIOWrapper(file, encoding="utf-8"))):
            if index >= MAX_RECORDS:
                raise BadFile("the file has more records than the limit")
            if record.get("status_code") == "FAIL" and record.get("status") != "Suppressed":
                yield ocsf_line(record)


def api_lines(entries: list[dict], system: str, moment: float):
    """The line of each finding of the API checks for one system, in the same OCSF names."""
    for entry in entries:
        if entry["system"] != system:
            continue
        level = entry.get("severity", "unknown")
        for finding in entry.get("findings", []):
            yield json.dumps(compact({
                "time": int(moment), "severity": level.capitalize(), "status": "New", "status_code": "FAIL",
                "status_detail": text(finding["message"], DETAIL_LIMIT),
                "metadata": {"event_code": entry["check"]},
                "resources": [compact({"uid": text(finding["resource"])})],
            }), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def wire_bytes(line: str) -> int:
    """The bytes of one line in the body of a push: its JSON in ASCII, and the time."""
    return len(json.dumps(line)) + LINE_OVERHEAD_BYTES


def batches(lines, moment: int, limit: int = MAX_BATCH_BYTES):
    """Lists of (time, line) whose lines take at most `limit` bytes in the body of a
    push. A batch has one line at least: a line is MAX_LINE_BYTES at most, so its body
    stays far under the limit of the gateway."""
    batch, size = [], 0
    for line in lines:
        cost = wire_bytes(line)
        if batch and size + cost > limit:
            yield batch
            batch, size = [], 0
        batch.append((moment, line))
        size += cost
    if batch:
        yield batch


class Findings:
    """The lines of the security stream `findings` in the Loki of the site. Each
    (source, system) is one stream, with the labels `security_stream`, `source` and
    `system`, and each line has the time of its file. The converter sends the lines of a
    file one time. After a start it sends the newest file of each system again, with the
    same times and the same text, and a query of Loki gives each line one time."""

    def __init__(self, loki, batch_bytes: int = MAX_BATCH_BYTES):
        self.loki, self.batch_bytes = loki, batch_bytes
        # (source, system) -> the key of the file whose lines Loki has.
        self.sent: dict[tuple[str, str], object] = {}
        self.pending: dict[tuple[str, str], bool] = {}
        self.counts = {"sent": {"prowler": 0, "api-check": 0}, "refused": {"prowler": 0, "api-check": 0}}
        self.lock = threading.Lock()

    def send(self, offers: dict[tuple[str, str], tuple[object, float, object]]):
        """Send the lines of each newest file whose lines Loki does not have yet.
        `offers` maps (source, system) to (key of the file, time of the file, a function
        that gives its lines)."""
        pending = {}
        for (source, system), (key, moment, lines) in sorted(offers.items(), key=lambda item: item[0]):
            if self.sent.get((source, system)) == key:
                pending[(source, system)] = False
                continue
            labels = {"security_stream": FINDINGS_STREAM, "source": source, "system": system}
            sent = refused = 0
            try:
                for batch in batches(lines(), int(moment * NANOSECONDS), self.batch_bytes):
                    try:
                        self.loki.push(labels, batch)
                        sent += len(batch)
                    except Refused as error:
                        refused += len(batch)
                        logger.warning("Loki refused %d lines of findings of %s for %s: %s", len(batch), source,
                                       system, error)
            except Exception as error:  # noqa: BLE001 - one stream must not stop the others
                # The kind of the error, and the text of NoAnswer and BadFile, which hold
                # no value of a file and no answer of Loki.
                logger.warning("the lines of findings of %s for %s wait for the next cycle: %s", source, system,
                               error if isinstance(error, (NoAnswer, BadFile)) else type(error).__name__)
                pending[(source, system)] = True
                continue
            finally:
                with self.lock:
                    self.counts["sent"][source] = self.counts["sent"].get(source, 0) + sent
                    self.counts["refused"][source] = self.counts["refused"].get(source, 0) + refused
            self.sent[(source, system)] = key
            pending[(source, system)] = False
            logger.info("%d lines of findings of %s for %s are in Loki", sent, source, system)
        # A stream with no newest file now has nothing to send, and nothing to keep.
        self.sent = {stream: key for stream, key in self.sent.items() if stream in offers}
        with self.lock:
            self.pending = pending

    def collect(self, metrics: Metrics):
        with self.lock:
            pending, counts = dict(self.pending), {kind: dict(values) for kind, values in self.counts.items()}
        for (source, system), waits in sorted(pending.items()):
            metrics.add(FINDINGS_PENDING, int(waits), source=source, system=system)
        for source, count in sorted(counts["sent"].items()):
            metrics.add(FINDINGS_SENT, count, source=source)
        for source, count in sorted(counts["refused"].items()):
            metrics.add(FINDINGS_REFUSED, count, source=source)


class ResultFiles(Loop):
    """The results of the files on the volume of results (README, "The files of
    results"): the newest OCSF file of each system of each provider of Prowler, and the
    result file of the API checks. The converter reads a file again only when it
    changes. A file that does not fit gives no result and no time for its system, and
    it counts in homelab_security_converter_files_skipped."""

    name = "files"

    def __init__(self, root: Path, registry: dict, findings: Findings | None = None,
                 interval: float = INTERVAL_SECONDS, clock=time.time, monotonic=time.monotonic):
        super().__init__(interval, clock, monotonic)
        self.root, self.findings = Path(root), findings
        self.result_series = registry["series"]["result"]
        self.time_series = registry["series"]["last_result"]
        self.providers = {provider: set(systems) for provider, systems in registry["prowler"].items()}
        self.systems = set(registry["systems"])
        # path -> (size, modified, inode), and what the converter read: a result or BadFile.
        self.cache: dict[Path, tuple[tuple[int, int, int], object]] = {}
        self.noted: set[str] = set()
        self.result: tuple[list, list, dict[str, int], float] | None = None

    def note(self, text: str):
        """A log line one time, for a name that the converter does not read."""
        if text not in self.noted:
            self.noted.add(text)
            logger.info(text)

    def newest(self, provider: str, now: float) -> dict[str, tuple[Path, int]]:
        """{system: (path, time)} of the newest OCSF file of each system of the provider."""
        directory = self.root / "prowler" / provider
        found: dict[str, tuple[Path, int]] = {}
        with os.scandir(directory) as listing:
            entries = list(listing)
        for entry in entries:
            if entry.name == "tmp" or not entry.is_file(follow_symlinks=False):
                continue
            match = OCSF_NAME.match(entry.name)
            if not match:
                self.note(f"prowler/{provider}/{entry.name} is not the name of an OCSF file: not read")
                continue
            if match["system"] not in self.providers[provider]:
                self.note(f"prowler/{provider}/{entry.name} is not a system of {provider} in the registry: not read")
                continue
            try:
                stamp = calendar.timegm(time.strptime(match["stamp"], "%Y%m%dT%H%M%SZ"))
            except ValueError:
                self.note(f"prowler/{provider}/{entry.name} has a time that is not a time: not read")
                continue
            if stamp > now + CLOCK_SKEW_SECONDS:
                self.note(f"prowler/{provider}/{entry.name} has a time in the future: not read")
                continue
            if match["system"] not in found or stamp > found[match["system"]][1]:
                found[match["system"]] = Path(entry.path), stamp
        return found

    def read(self, path: Path, reader, limit: int):
        """What `reader` gives for the open file, from the cache while the file stays
        the same. BadFile when the file does not fit, or can not be read. The converter
        opens a regular file only: no link, and no FIFO that would stop the loop."""
        try:
            file, info = open_regular(path)
        except BadFile as error:
            logger.warning("%s is skipped: %s", path.relative_to(self.root), error)
            self.cache.pop(path, None)
            return error
        with file:
            signature = info.st_size, info.st_mtime_ns, info.st_ino
            cached = self.cache.get(path)
            if cached and cached[0] == signature:
                return cached[1]
            if info.st_size > limit:
                result = BadFile("the file is larger than the limit")
            else:
                try:
                    result = reader(file)
                except BadFile as error:
                    result = error
                except (OSError, UnicodeDecodeError, RecursionError) as error:
                    result = BadFile(f"the file can not be read: {type(error).__name__}")
        if isinstance(result, BadFile):
            logger.warning("%s is skipped: %s", path.relative_to(self.root), result)
        self.cache[path] = signature, result
        return result

    def clear(self):
        with self.lock:
            self.result = None

    def step(self):
        now = self.clock()
        results: list[tuple[str, str, str, int, str]] = []
        times: list[tuple[str, str, float]] = []
        skipped = {"prowler": 0, "api-check": 0}
        offers: dict[tuple[str, str], tuple[object, float, object]] = {}
        read: set[Path] = set()
        for provider in sorted(self.providers):
            try:
                newest = self.newest(provider, now)
            except FileNotFoundError:
                newest = {}
            except OSError as error:
                # A directory that can not be read counts as one file skipped, and the
                # other providers and the API checks go on.
                logger.warning("prowler/%s can not be read: %s", provider, type(error).__name__)
                skipped["prowler"] += 1
                continue
            for system, (path, stamp) in sorted(newest.items()):
                read.add(path)
                checks = self.read(path, read_ocsf, MAX_OCSF_BYTES)
                if isinstance(checks, BadFile):
                    skipped["prowler"] += 1
                    continue
                times.append(("prowler", system, float(stamp)))
                results += [("prowler", check, system, failed, level)
                            for check, (failed, level) in sorted(checks.items())]
                offers[("prowler", system)] = (path.name, float(stamp), lambda path=path: ocsf_lines(path))
        api = self.root / API_FILE
        if os.path.lexists(api):
            read.add(api)
            content = self.read(api, lambda file: read_api(file, now), MAX_API_BYTES)
            if isinstance(content, BadFile):
                skipped["api-check"] += 1
            else:
                moment, entries = content
                systems = set()
                for entry in entries:
                    if entry["system"] not in self.systems:
                        self.note(f"{API_FILE} names a system that is not in the registry: its result is not read")
                        continue
                    systems.add(entry["system"])
                    results.append(("api-check", entry["check"], entry["system"], entry["failed"],
                                    entry.get("severity", "unknown")))
                times += [("api-check", system, moment) for system in sorted(systems)]
                for system in systems:
                    offers[("api-check", system)] = (
                        moment, moment, lambda entries=entries, system=system, moment=moment:
                        api_lines(entries, system, moment))
        self.cache = {path: value for path, value in self.cache.items() if path in read}
        with self.lock:
            self.result = results, times, skipped, self.monotonic()
        if self.findings is not None:
            try:
                self.findings.send(offers)
            except Exception as error:  # noqa: BLE001 - the results are served all the same
                logger.error("the lines of findings were not sent: %s", type(error).__name__)

    def collect(self, metrics: Metrics, now: float):
        with self.lock:
            result = self.result
        if result is None or not self.fresh(result[3], now):
            return
        results, times, skipped, _ = result
        for source, count in sorted(skipped.items()):
            metrics.add(FILES_SKIPPED, count, source=source)
        for source, system, moment in times:
            metrics.add(self.time_series, moment, source=source, system=system)
        for source, check, system, failed, level in results:
            metrics.add(self.result_series, failed, source=source, check=check, system=system, severity=level)
        if self.findings is not None:
            self.findings.collect(metrics)


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
    parser.add_argument("--accepted", default="/accepted/accepted.yaml",
                        help="the list of accepted findings, from its Secret")
    parser.add_argument("--results", default="/results",
                        help="the root of the volume of results, read-only")
    parser.add_argument("--loki", default=os.environ.get("LOKI_URL"), help="the address of Loki")
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--interval", type=float, default=INTERVAL_SECONDS,
                        help="the seconds between two cycles")
    args = parser.parse_args()
    if not args.loki:
        parser.error("the address of Loki is not set (--loki or LOKI_URL)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    registry = json.loads(Path(args.registry).read_text())
    loki = Loki(args.loki)
    streams = StreamSource(json.loads(Path(args.streams).read_text()), loki, interval=args.interval)
    accepted = AcceptedList(Path(args.accepted), registry, interval=args.interval)
    files = ResultFiles(Path(args.results), registry, Findings(loki), interval=args.interval)
    converter = Converter([streams, accepted, files], interval=args.interval)
    logger.info("the converter asks %s each %d seconds for %d streams, and reads the list of accepted findings"
                " and the files of results in %s", args.loki, args.interval, len(streams.entries), args.results)
    threading.Thread(target=converter.run, args=(threading.Event(),), daemon=True, name="converter").start()
    ThreadingHTTPServer(("0.0.0.0", args.port), handler(converter)).serve_forever()


if __name__ == "__main__":
    main()
