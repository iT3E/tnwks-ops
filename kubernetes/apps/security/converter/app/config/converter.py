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
