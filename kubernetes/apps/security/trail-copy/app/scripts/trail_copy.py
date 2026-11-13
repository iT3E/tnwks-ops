#!/usr/bin/env python3
"""Copy the new files of the AWS trail to a volume, and send each event to Loki.

One run, each 15 minutes (../cronjob.yaml):

1. Find the prefixes of the trail in its bucket: one for each account and Region.
2. List each prefix from the newest local copy, less an overlap for a file that
   AWS delivers late, and take the files with no local copy, oldest first.
3. For each file: download it, send each of its events to Loki as one line of
   the stream {security_stream="cloudtrail"}, and only then give the copy its
   final name. So a file with its final name is a file that Loki has.
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
STREAM = {"security_stream": "cloudtrail"}
# <account>_CloudTrail_<Region>_<time of the file>_<random>.json.gz
FILE_TIME = re.compile(r"_(\d{8}T\d{4}Z)_[^/]*\.json\.gz$")
RECORDS = re.compile(r'"Records"\s*:\s*\[')
# Loki refuses a line over 256 KiB (max_line_size). An event is rarely near it.
MAX_LINE = 200 * 1024
MAX_RECORD = 4 * 1024 * 1024
MAX_BATCH = 1024 * 1024
BIG_FIELDS = ("requestParameters", "responseElements", "additionalEventData", "serviceEventDetails")
# Loki answers 400 for a line that it will never take. The rest of the batch is in.
REFUSED_FOR_AGE = ("too far behind", "too old", "out of order", "too new")
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
    """The two calls that the job makes: list a prefix, and read an object."""

    def __init__(self, bucket: str, region: str, creds: dict, opener=urllib.request.urlopen):
        self.host = f"{bucket}.s3.{region}.amazonaws.com"
        self.region = region
        self.creds = creds
        self.opener = opener

    def request(self, path: str, query: dict):
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
            found += regions
    return sorted(found)


def file_time(key: str) -> datetime.datetime | None:
    match = FILE_TIME.search(key)
    if not match:
        return None
    return datetime.datetime.strptime(match.group(1), "%Y%m%dT%H%MZ").replace(tzinfo=UTC)


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


def new_files(s3: S3, data_dir: str, now: datetime.datetime, start_hours: int, overlap_minutes: int,
              max_files: int) -> list[str]:
    """The keys with no local copy, oldest first, and at most max_files of them."""
    floor = now - datetime.timedelta(hours=start_hours)
    keys = []
    for prefix in trail_prefixes(s3):
        newest = newest_local(os.path.join(data_dir, "files", *prefix.rstrip("/").split("/")))
        start = max(newest - datetime.timedelta(minutes=overlap_minutes), floor) if newest else floor
        # One page of S3 is 1000 keys. No more pages than this run can take.
        listed, _ = s3.list(prefix, start_after=start_after(prefix, start), limit=2 * max_files)
        keys += [key for key in listed if file_time(key) and not done(data_dir, key)]
    keys.sort(key=lambda key: (file_time(key), key))
    return keys[:max_files]


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


def entry(record: dict, fallback: datetime.datetime) -> tuple[int, str]:
    """One Loki entry: the time of the event in nanoseconds, and the event as one line of JSON."""
    try:
        seconds = calendar.timegm(time.strptime(record["eventTime"], "%Y-%m-%dT%H:%M:%SZ"))
    except (KeyError, TypeError, ValueError):
        seconds = int(fallback.timestamp())
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    if len(line.encode()) > MAX_LINE:
        small = {key: value for key, value in record.items() if key not in BIG_FIELDS}
        small["truncated"] = True
        line = json.dumps(small, separators=(",", ":"), ensure_ascii=False)
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


class Loki:
    def __init__(self, url: str, opener=urllib.request.urlopen, sleep=time.sleep):
        self.url = url
        self.opener = opener
        self.sleep = sleep
        self.refused = 0

    def push(self, batch: list) -> None:
        """Send one batch. Five tries for an answer of 429 or 5xx, and for no answer."""
        body = json.dumps(
            {"streams": [{"stream": STREAM, "values": [[str(nanoseconds), line] for nanoseconds, line in batch]}]},
        ).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        for wait in (2, 4, 8, 16, None):
            try:
                with self.opener(request, timeout=60) as answer:
                    answer.read()
                return
            except urllib.error.HTTPError as error:
                text = error.read().decode(errors="replace")[:500]
                if error.code == 400 and any(reason in text for reason in REFUSED_FOR_AGE):
                    self.refused += 1
                    print(f"Loki refused lines of a batch for their time: {text}", file=sys.stderr)
                    return
                if error.code != 429 and error.code < 500:
                    raise LokiError(f"Loki answered {error.code}: {text}") from None
                problem = f"Loki answered {error.code}: {text}"
            except OSError as error:
                problem = f"Loki did not answer: {type(error).__name__}: {error}"
            if wait is None:
                raise LokiError(problem)
            self.sleep(wait)


# --- one run ---


def copy_file(s3: S3, loki: Loki, data_dir: str, key: str) -> int:
    """Download one file, send its events, then give the copy its final name. Returns the events."""
    path = local_path(data_dir, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    s3.download(key, part)
    fallback = file_time(key)
    count = 0
    try:
        with gzip.open(part, "rt", encoding="utf-8") as stream:
            for batch in batches(entry(record, fallback) for record in records(stream)):
                loki.push(batch)
                count += len(batch)
    except (OSError, EOFError, ValueError) as error:
        # A file that can't be read stays as .bad, so the next run goes on
        # with the files after it. This run fails at its end.
        os.replace(part, path + ".bad")
        raise BadFile(f"{key}: {type(error).__name__}: {error}") from None
    os.replace(part, path)
    return count


class BadFile(Exception):
    pass


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
    keys = new_files(s3, data_dir, now, settings["start_hours"], settings["overlap_minutes"], settings["max_files"])
    result = {"new": len(keys), "files": 0, "events": 0, "bad": [], "left": 0}
    end = clock() + settings["run_seconds"]
    for number, key in enumerate(keys):
        if clock() > end:
            result["left"] = len(keys) - number
            break
        try:
            result["events"] += copy_file(s3, loki, data_dir, key)
            result["files"] += 1
        except BadFile as error:
            result["bad"].append(str(error))
    result["refused"] = loki.refused
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
        "overlap_minutes": int(env("OVERLAP_MINUTES", "60")),
        "max_files": int(env("MAX_FILES", "500")),
        "run_seconds": int(env("RUN_SECONDS", "600")),
        "keep_days": int(env("KEEP_DAYS", "7")),
    }
    s3 = S3(env("TRAIL_BUCKET"), env("AWS_REGION"), creds)
    result = run(s3, Loki(env("LOKI_URL").rstrip("/") + "/loki/api/v1/push"), env("DATA_DIR", "/data"), datetime.datetime.now(UTC), settings)
    print(
        f"{result['files']} of {result['new']} new files copied, {result['events']} events sent to Loki,"
        f" {result['refused']} batches with lines that Loki refused for their time,"
        f" {result['left']} files left for the next run, {result['deleted']} old copies deleted",
    )
    for line in result["bad"]:
        print(f"a file that can't be read: {line}", file=sys.stderr)
    if result["bad"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
