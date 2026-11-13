#!/usr/bin/env python3
"""Copy the new files of the AWS trail to a volume, and send each event to Loki.

One run, each 15 minutes (../cronjob.yaml):

1. Find the prefixes of the trail in its bucket: one for each account and Region.
   The list of prefixes stays on the volume (state.json) for a day.
2. List each prefix that is due (see `due`), from its newest local copy less an
   overlap for a file that AWS delivers late, and take the files with no local
   copy, oldest first.
3. For each file: download it, send each of its events to Loki as one line of
   the stream of its account and Region, and only then give the copy its final
   name. So a file with its final name is a file that Loki has. A file with a
   line that Loki refused gets the name .bad, and the run fails at its end.
4. Delete the local copies that are older than KEEP_DAYS.

A run that stops half way loses nothing: the next run takes the same files
again, and a query of Loki gives a line one time (same time, same text).

The Python standard library only, so the image is the plain Python image and
the pod needs no package index. The session comes from the environment (the
Secret that the credential refresher writes). Nothing here prints it.
"""

import calendar
import datetime
import gzip
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UTC = datetime.UTC
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
STREAM_NAME = "cloudtrail"
# AWSLogs/[<organization>/]<account>/CloudTrail/<Region>/<year>/<month>/<day>/<file>
TRAIL_KEY = re.compile(r"^AWSLogs/(?:o-[a-z0-9]+/)?(\d{12})/CloudTrail/([a-z0-9-]+)/")
# <account>_CloudTrail_<Region>_<time of the file>_<random>.json.gz
FILE_TIME = re.compile(r"_(\d{8}T\d{4}Z)_[^/]*\.json\.gz$")
RECORDS = re.compile(r'"Records"\s*:\s*\[')
# Loki refuses a line over 256 KiB (max_line_size). An event is rarely near it.
MAX_LINE = 200 * 1024
MAX_RECORD = 4 * 1024 * 1024
MAX_BATCH = 1024 * 1024
BIG_FIELDS = ("requestParameters", "responseElements", "additionalEventData", "serviceEventDetails")
# A line that is still too large keeps only these fields, each one if it is small.
STUB_FIELDS = ("eventVersion", "eventTime", "eventSource", "eventName", "eventType", "eventCategory",
               "eventID", "requestID", "awsRegion", "recipientAccountId", "sourceIPAddress", "userAgent",
               "errorCode", "errorMessage", "readOnly", "managementEvent", "userIdentity")
MAX_STUB_FIELD = 2048
# The words of a 400 of Loki for a line that it refuses for its time.
REFUSED_FOR_AGE = ("too far behind", "too old", "out of order", "too new")
# A prefix whose newest file is younger than this is active: each run lists it.
ACTIVE = datetime.timedelta(hours=1)
# When the newest file of the whole trail is this old, each run lists each prefix,
# half an hour before the stream is late (late_after 2h, security/registry/sources.yaml).
ALL_AFTER = datetime.timedelta(minutes=90)
STATE = "state.json"
PLACEHOLDER = "pending"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"{name} is not set")
    return value


# --- S3, signed with Signature Version 4 ---


def quote(text: str, safe: str = "") -> str:
    return urllib.parse.quote(text, safe=safe + "-_.~")


def signed_headers(method: str, host: str, path: str, query: dict, region: str, creds: dict,
                   now: datetime.datetime, extra: dict | None = None) -> dict:
    """The headers of one S3 request with an empty body, with its Authorization."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    headers = {"host": host, "x-amz-content-sha256": EMPTY_SHA256, "x-amz-date": amz_date}
    if creds.get("token"):
        headers["x-amz-security-token"] = creds["token"]
    headers.update({key.lower(): value for key, value in (extra or {}).items()})
    names = ";".join(sorted(headers))
    canonical = "\n".join([
        method,
        quote(path, safe="/"),
        canonical_query(query),
        "".join(f"{key}:{headers[key].strip()}\n" for key in sorted(headers)),
        names,
        EMPTY_SHA256,
    ])
    scope = f"{amz_date[:8]}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + creds["secret_key"]).encode()
    for part in (amz_date[:8], region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={creds['access_key']}/{scope}, SignedHeaders={names}, Signature={signature}"
    )
    return headers


def canonical_query(query: dict) -> str:
    return "&".join(f"{quote(key)}={quote(value)}" for key, value in sorted(query.items()))


class S3:
    """The two calls that the job makes: list a prefix, and read an object. It counts each request."""

    def __init__(self, bucket: str, region: str, creds: dict, opener=urllib.request.urlopen):
        self.host = f"{bucket}.s3.{region}.amazonaws.com"
        self.region = region
        self.creds = creds
        self.opener = opener
        self.requests = {"list": 0, "get": 0}

    def request(self, path: str, query: dict):
        self.requests["list" if query else "get"] += 1
        headers = signed_headers("GET", self.host, path, query, self.region, self.creds, datetime.datetime.now(UTC))
        url = f"https://{self.host}{quote(path, safe='/')}"
        if query:
            url += "?" + canonical_query(query)
        try:
            return self.opener(urllib.request.Request(url, headers=headers), timeout=60)
        except urllib.error.HTTPError as error:
            # The code and the message only: the body of some errors has the
            # ID of the access key.
            raise RuntimeError(f"S3 answered {error.code} {s3_error(error.read())} for {path or '/'}") from None

    def list(self, prefix: str, start_after: str = "", delimiter: str = "", limit: int = 0) -> tuple[list, list]:
        """The keys and the common prefixes under a prefix, in the order of S3."""
        keys, prefixes, token = [], [], ""
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if delimiter:
                query["delimiter"] = delimiter
            if token:
                query["continuation-token"] = token
            elif start_after:
                query["start-after"] = start_after
            with self.request("/", query) as answer:
                root = ET.fromstring(answer.read())
            keys += [item.findtext(f"{S3_NS}Key") for item in root.iter(f"{S3_NS}Contents")]
            prefixes += [item.findtext(f"{S3_NS}Prefix") for item in root.iter(f"{S3_NS}CommonPrefixes")]
            token = root.findtext(f"{S3_NS}NextContinuationToken") or ""
            if root.findtext(f"{S3_NS}IsTruncated") != "true" or not token or (limit and len(keys) >= limit):
                return keys, prefixes

    def download(self, key: str, path: str) -> None:
        with self.request("/" + key, {}) as answer, open(path, "wb") as out:
            while chunk := answer.read(65536):
                out.write(chunk)


def s3_error(body: bytes) -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return ""
    return f"{root.findtext('Code') or ''}: {root.findtext('Message') or ''}"


# --- the state on the volume: a cache of what to list ---


def fresh_state() -> dict:
    return {"prefixes": [], "prefixes_at": 0, "listed": {}}


def load_state(data_dir: str) -> dict:
    """The list of prefixes, and the time of the last list of each prefix. With no file, or a file
    that does not fit, a fresh state: the run lists the prefixes and each prefix, and loses nothing."""
    try:
        with open(os.path.join(data_dir, STATE), encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return fresh_state()
    except (OSError, ValueError) as error:
        print(f"the state file can't be read, so this run lists each prefix: {type(error).__name__}", file=sys.stderr)
        return fresh_state()
    fits = (isinstance(state, dict) and isinstance(state.get("prefixes"), list)
            and all(isinstance(prefix, str) for prefix in state["prefixes"])
            and isinstance(state.get("prefixes_at"), int | float) and isinstance(state.get("listed"), dict)
            and all(isinstance(at, int | float) for at in state["listed"].values()))
    if not fits:
        print("the state file does not fit, so this run lists each prefix", file=sys.stderr)
        return fresh_state()
    return state


def save_state(data_dir: str, state: dict) -> None:
    path = os.path.join(data_dir, STATE)
    with open(path + ".tmp", "w", encoding="utf-8") as stream:
        json.dump(state, stream, indent=1, sort_keys=True)
    os.replace(path + ".tmp", path)


# --- which files ---


def trail_prefixes(s3: S3) -> list[str]:
    """AWSLogs/[<organization>/]<account>/CloudTrail/<Region>/, each one that the bucket has."""
    found = []
    _, tops = s3.list("AWSLogs/", delimiter="/")
    for top in tops:
        _, children = s3.list(top, delimiter="/")
        # An organization trail has the ID of the organization above the accounts.
        accounts = [top] if f"{top}CloudTrail/" in children else children
        for account in accounts:
            _, regions = s3.list(f"{account}CloudTrail/", delimiter="/")
            for region in regions:
                if TRAIL_KEY.fullmatch(region):
                    found.append(region)
                else:
                    print(f"a prefix that is not of the form of the trail, not read: {region}", file=sys.stderr)
    return sorted(found)


def cached_prefixes(s3: S3, state: dict, now: datetime.datetime, refresh_hours: int) -> list[str]:
    """The prefixes of the trail. The run lists them again when the list is empty or older than
    refresh_hours."""
    if not state["prefixes"] or now.timestamp() - state["prefixes_at"] >= refresh_hours * 3600:
        state["prefixes"], state["prefixes_at"] = trail_prefixes(s3), now.timestamp()
    return state["prefixes"]


def file_time(key: str) -> datetime.datetime | None:
    match = FILE_TIME.search(key)
    if not match:
        return None
    return datetime.datetime.strptime(match.group(1), "%Y%m%dT%H%MZ").replace(tzinfo=UTC)


def stream_labels(key: str) -> dict:
    """The labels of the Loki stream of a file: one stream for each account and Region.

    Loki refuses a line that is more than 1 hour older than the newest line of its
    stream. The job sends the files of one prefix in the order of their time, so a
    stream for each prefix gets its lines in that order."""
    match = TRAIL_KEY.match(key)
    if not match:
        raise ValueError(f"not a key of the trail: {key}")
    return {"security_stream": STREAM_NAME, "aws_account": match.group(1), "aws_region": match.group(2)}


def local_path(data_dir: str, key: str) -> str:
    parts = key.split("/")
    if parts[0] != "AWSLogs" or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"not a key of the trail: {key}")
    return os.path.join(data_dir, "files", *parts)


def done(data_dir: str, key: str) -> bool:
    path = local_path(data_dir, key)
    return os.path.exists(path) or os.path.exists(path + ".bad")


def newest_local(directory: str) -> datetime.datetime | None:
    """The time of the newest copy under the directory of one prefix (year/month/day/file)."""
    try:
        names = sorted(os.listdir(directory), reverse=True)
    except FileNotFoundError:
        return None
    for name in names:
        path = os.path.join(directory, name)
        if os.path.isdir(path):
            found = newest_local(path)
        else:
            found = file_time(name.removesuffix(".bad"))
        if found:
            return found
    return None


def start_after(prefix: str, start: datetime.datetime) -> str:
    """The key that sorts just before the first file of that minute."""
    parts = prefix.split("/")
    account, region = parts[-4], parts[-2]
    return f"{prefix}{start:%Y/%m/%d}/{account}_CloudTrail_{region}_{start:%Y%m%dT%H%MZ}"


def due(newest: datetime.datetime | None, newest_trail: datetime.datetime | None, listed_at: float | None,
        now: datetime.datetime, quiet_minutes: int) -> bool:
    """Whether this run lists a prefix. Each run lists a prefix with no list yet, a prefix with a file
    in the last hour, and each prefix when the whole trail had no file in 90 minutes. A quiet prefix
    waits quiet_minutes between two lists."""
    if listed_at is None or (newest and now - newest < ACTIVE):
        return True
    if newest_trail is None or now - newest_trail >= ALL_AFTER:
        return True
    return now.timestamp() - listed_at >= quiet_minutes * 60


def new_files(s3: S3, data_dir: str, now: datetime.datetime, settings: dict, state: dict) -> tuple[list[str], int]:
    """The keys with no local copy, oldest first, and at most max_files of them. And the number of
    prefixes that the run listed.

    The state gets the time of each list that found no new key. A prefix with new keys loses its
    time, so it is due in the next run, also when this run does not take each of its keys."""
    floor = now - datetime.timedelta(hours=settings["start_hours"])
    prefixes = cached_prefixes(s3, state, now, settings["prefix_hours"])
    newest = {prefix: newest_local(os.path.join(data_dir, "files", *prefix.rstrip("/").split("/")))
              for prefix in prefixes}
    newest_trail = max((moment for moment in newest.values() if moment), default=None)
    listed = {prefix: at for prefix, at in state["listed"].items() if prefix in newest}
    keys, count = [], 0
    for prefix in prefixes:
        if not due(newest[prefix], newest_trail, listed.get(prefix), now, settings["quiet_minutes"]):
            continue
        start = floor
        if newest[prefix]:
            start = max(newest[prefix] - datetime.timedelta(minutes=settings["overlap_minutes"]), floor)
        # One page of S3 is 1000 keys. No more pages than this run can take.
        found, _ = s3.list(prefix, start_after=start_after(prefix, start), limit=2 * settings["max_files"])
        count += 1
        found = [key for key in found if TRAIL_KEY.match(key) and file_time(key) and not done(data_dir, key)]
        if found:
            listed.pop(prefix, None)
        else:
            listed[prefix] = now.timestamp()
        keys += found
    state["listed"] = listed
    keys.sort(key=lambda key: (file_time(key), key))
    return keys[:settings["max_files"]], count


# --- the events of a file ---


def records(stream, chunk: int = 65536):
    """Each object of the "Records" array of a trail file, with only one of them in memory."""
    decoder = json.JSONDecoder()
    buffer = ""
    while not (match := RECORDS.search(buffer)):
        more = stream.read(chunk)
        if not more or len(buffer) > MAX_RECORD:
            raise ValueError("no Records array")
        buffer += more
    buffer = buffer[match.end():]
    ended = False
    while True:
        buffer = buffer.lstrip(" \t\r\n,")
        if buffer.startswith("]"):
            return
        try:
            record, end = decoder.raw_decode(buffer)
        except json.JSONDecodeError:
            if ended or len(buffer) > MAX_RECORD:
                raise ValueError("the Records array does not end, or one record is over the limit") from None
            more = stream.read(chunk)
            ended = not more
            buffer += more
            continue
        buffer = buffer[end:]
        yield record


def dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def too_large(line: str) -> bool:
    return len(line.encode()) > MAX_LINE


def entry(record: dict, fallback: datetime.datetime) -> tuple[int, str]:
    """One Loki entry: the time of the event in nanoseconds, and the event as one line of JSON.

    A line over MAX_LINE loses the large fields. If it is still over, it keeps only the small
    fields that name the event. So no line is over the limit of Loki."""
    if not isinstance(record, dict):
        raise ValueError("a record is not an object")  # noqa: TRY004 - the file can not be read, as a JSON error
    try:
        seconds = calendar.timegm(time.strptime(record["eventTime"], "%Y-%m-%dT%H:%M:%SZ"))
    except (KeyError, TypeError, ValueError):
        seconds = int(fallback.timestamp())
    line = dumps(record)
    if too_large(line):
        line = dumps({**{key: value for key, value in record.items() if key not in BIG_FIELDS}, "truncated": True})
    if too_large(line):
        stub = {key: record[key] for key in STUB_FIELDS
                if key in record and len(dumps(record[key]).encode()) <= MAX_STUB_FIELD}
        line = dumps({**stub, "truncated": True})
    return seconds * 1_000_000_000, line


def batches(entries, limit: int = MAX_BATCH):
    """Lists of entries of at most about `limit` bytes of lines, each in the order of time."""
    batch, size = [], 0
    for item in entries:
        if batch and size + len(item[1]) > limit:
            yield sorted(batch)
            batch, size = [], 0
        batch.append(item)
        size += len(item[1])
    if batch:
        yield sorted(batch)


# --- Loki ---


class LokiError(RuntimeError):
    pass


class Refused(Exception):
    """Loki answered 400: it will never take some lines of the batch. It took the rest."""

    def __init__(self, text: str):
        super().__init__(text)
        self.for_time = any(reason in text for reason in REFUSED_FOR_AGE)


class Loki:
    def __init__(self, url: str, opener=urllib.request.urlopen, sleep=time.sleep):
        self.url = url
        self.opener = opener
        self.sleep = sleep

    def push(self, labels: dict, batch: list) -> None:
        """Send one batch to one stream. Five tries for an answer of 429 or 5xx, and for no answer.
        A 400 raises Refused, because the same request gets the same answer."""
        body = json.dumps(
            {"streams": [{"stream": labels, "values": [[str(nanoseconds), line] for nanoseconds, line in batch]}]},
        ).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        for wait in (2, 4, 8, 16, None):
            try:
                with self.opener(request, timeout=60) as answer:
                    answer.read()
                return
            except urllib.error.HTTPError as error:
                text = error.read().decode(errors="replace")[:500]
                if error.code == 400:
                    raise Refused(text) from None
                if error.code != 429 and error.code < 500:
                    raise LokiError(f"Loki answered {error.code}: {text}") from None
                problem = f"Loki answered {error.code}: {text}"
            except OSError as error:
                problem = f"Loki did not answer: {type(error).__name__}: {error}"
            if wait is None:
                raise LokiError(problem)
            self.sleep(wait)


# --- one run ---


class BadFile(Exception):
    """A file that Loki does not have whole. Its copy has the name .bad, so the next runs pass it."""

    def __init__(self, message: str, refused: bool = False, events: int = 0):
        super().__init__(message)
        self.refused = refused
        self.events = events


def copy_file(s3: S3, loki: Loki, data_dir: str, key: str) -> int:
    """Download one file, send its events, then give the copy its final name. Returns the events."""
    path = local_path(data_dir, key)
    labels = stream_labels(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    s3.download(key, part)
    fallback = file_time(key)
    count, refusals = 0, []
    try:
        with gzip.open(part, "rt", encoding="utf-8") as stream:
            for batch in batches(entry(record, fallback) for record in records(stream)):
                try:
                    loki.push(labels, batch)
                except Refused as refusal:
                    # The next batches of the file still go: Loki took the rest of this one.
                    refusals.append(refusal)
                    continue
                count += len(batch)
    except (OSError, EOFError, ValueError) as error:
        # A file that can't be read stays as .bad, so the next run goes on
        # with the files after it. This run fails at its end.
        os.replace(part, path + ".bad")
        raise BadFile(f"{key}: the file can't be read: {type(error).__name__}: {error}", events=count) from None
    if refusals:
        os.replace(part, path + ".bad")
        why = "for their time" if all(refusal.for_time for refusal in refusals) else "with a 400"
        raise BadFile(f"{key}: Loki refused lines of {len(refusals)} batches {why}: {refusals[0]}",
                      refused=True, events=count)
    os.replace(part, path)
    return count


def clean(data_dir: str, now: datetime.datetime, keep_days: int) -> int:
    """Delete each local copy that is older than keep_days, and each directory that is empty then."""
    limit = (now - datetime.timedelta(days=keep_days)).timestamp()
    deleted = 0
    for directory, _, names in os.walk(os.path.join(data_dir, "files"), topdown=False):
        for name in names:
            path = os.path.join(directory, name)
            if os.path.getmtime(path) < limit:
                os.remove(path)
                deleted += 1
        if not os.listdir(directory) and directory != os.path.join(data_dir, "files"):
            os.rmdir(directory)
    return deleted


def run(s3: S3, loki: Loki, data_dir: str, now: datetime.datetime, settings: dict, clock=time.monotonic) -> dict:
    os.makedirs(os.path.join(data_dir, "files"), exist_ok=True)
    state = load_state(data_dir)
    keys, listed = new_files(s3, data_dir, now, settings, state)
    # Saved before the copy, so a run that stops later keeps what it listed.
    save_state(data_dir, state)
    result = {"new": len(keys), "files": 0, "events": 0, "bad": [], "refused": 0, "left": 0,
              "prefixes": len(state["prefixes"]), "listed": listed}
    end = clock() + settings["run_seconds"]
    for number, key in enumerate(keys):
        if clock() > end:
            result["left"] = len(keys) - number
            break
        try:
            result["events"] += copy_file(s3, loki, data_dir, key)
            result["files"] += 1
        except BadFile as error:
            result["events"] += error.events
            result["refused"] += int(error.refused)
            result["bad"].append(str(error))
    result["deleted"] = clean(data_dir, now, settings["keep_days"])
    return result


def main() -> None:
    creds = {
        "access_key": env("AWS_ACCESS_KEY_ID"),
        "secret_key": env("AWS_SECRET_ACCESS_KEY"),
        "token": env("AWS_SESSION_TOKEN"),
    }
    if creds["access_key"] == PLACEHOLDER:
        # The Secret before the first run of the credential refresher.
        print("the session is not there yet: nothing copied in this run")
        return
    settings = {
        "start_hours": int(env("START_HOURS", "144")),
        "overlap_minutes": int(env("OVERLAP_MINUTES", "120")),
        "max_files": int(env("MAX_FILES", "500")),
        "run_seconds": int(env("RUN_SECONDS", "600")),
        "keep_days": int(env("KEEP_DAYS", "7")),
        "quiet_minutes": int(env("QUIET_MINUTES", "240")),
        "prefix_hours": int(env("PREFIX_HOURS", "24")),
    }
    s3 = S3(env("TRAIL_BUCKET"), env("AWS_REGION"), creds)
    loki = Loki(env("LOKI_URL").rstrip("/") + "/loki/api/v1/push")
    result = run(s3, loki, env("DATA_DIR", "/data"), datetime.datetime.now(UTC), settings)
    for line in result["bad"]:
        print(f"a file set aside as .bad: {line}", file=sys.stderr)
    # The last line: the counts of the run, also the S3 requests that the cost rests on.
    print(
        f"{result['files']} of {result['new']} new files copied, {result['events']} events sent to Loki,"
        f" {len(result['bad'])} files set aside as .bad ({result['refused']} with lines that Loki refused),"
        f" {result['left']} files left for the next run, {result['deleted']} old copies deleted,"
        f" {result['listed']} of {result['prefixes']} prefixes listed,"
        f" S3 requests: {s3.requests['list']} list and {s3.requests['get']} get",
        flush=True,
    )
    if result["bad"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
