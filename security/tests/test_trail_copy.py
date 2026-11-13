"""The trail copy job of the security platform keeps its contract.

kubernetes/apps/security/trail-copy: the script signs its S3 requests, reads a
trail file one event at a time, sends each event to Loki as a line of the
stream of its account and Region, with security_stream="cloudtrail", and gives
a local copy its final name only when Loki has its events. A file with a line
that Loki refused gets the name .bad and fails the run. A quiet prefix is
listed less often. Its manifests agree with each other.
"""

import datetime
import gzip
import importlib.util
import io
import json
import os
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/security/trail-copy/app"
UTC = datetime.UTC
NOW = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
ORG = "AWSLogs/o-example/"
PREFIX_A = f"{ORG}111111111111/CloudTrail/us-west-2/"
PREFIX_B = f"{ORG}222222222222/CloudTrail/us-east-1/"
SETTINGS = {"start_hours": 144, "overlap_minutes": 120, "max_files": 500, "run_seconds": 600, "keep_days": 7,
            "quiet_minutes": 240, "prefix_hours": 24}
LABELS_A = {"security_stream": "cloudtrail", "aws_account": "111111111111", "aws_region": "us-west-2"}
LABELS_B = {"security_stream": "cloudtrail", "aws_account": "222222222222", "aws_region": "us-east-1"}


@pytest.fixture(scope="module")
def trail_copy():
    spec = importlib.util.spec_from_file_location("trail_copy", APP / "scripts/trail_copy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def key(prefix: str, moment: datetime.datetime, tail: str = "AbCd") -> str:
    account, region = prefix.split("/")[-4], prefix.split("/")[-2]
    return f"{prefix}{moment:%Y/%m/%d}/{account}_CloudTrail_{region}_{moment:%Y%m%dT%H%MZ}_{tail}.json.gz"


def trail_file(events: list) -> bytes:
    return gzip.compress(json.dumps({"Records": events}).encode())


def event(moment: datetime.datetime, name: str = "DescribeTrails", **more) -> dict:
    return {"eventVersion": "1.11", "eventTime": f"{moment:%Y-%m-%dT%H:%M:%SZ}", "eventName": name,
            "recipientAccountId": "111111111111", **more}


class FakeS3:
    """The bucket as a dict of key -> bytes, with the two calls of the job."""

    def __init__(self, objects: dict):
        self.objects = objects
        self.lists = []
        self.requests = {"list": 0, "get": 0}

    def list(self, prefix, start_after="", delimiter="", limit=0):
        self.lists.append((prefix, start_after, delimiter))
        self.requests["list"] += 1
        keys, prefixes = [], set()
        for name in sorted(self.objects):
            if not name.startswith(prefix) or name <= start_after:
                continue
            rest = name[len(prefix):]
            if delimiter and delimiter in rest:
                prefixes.add(prefix + rest.split(delimiter)[0] + delimiter)
            else:
                keys.append(name)
        return keys, sorted(prefixes)

    def download(self, name, path):
        self.requests["get"] += 1
        Path(path).write_bytes(self.objects[name])


class FakeLoki:
    """Loki as a list of (labels, batch). `refuse` gives the 400 text for a batch, or None."""

    def __init__(self, fail_after: int | None = None, refuse=None, trail_copy=None):
        self.batches = []
        self.fail_after = fail_after
        self.refuse = refuse
        self.trail_copy = trail_copy

    def push(self, labels, batch):
        if self.fail_after is not None and len(self.batches) >= self.fail_after:
            raise RuntimeError("Loki answered 503")
        text = self.refuse(labels, batch) if self.refuse else None
        if text:
            raise self.trail_copy.Refused(text)
        self.batches.append((labels, batch))

    @property
    def lines(self):
        return [json.loads(line) for _, batch in self.batches for _, line in batch]


def files_of(trail_copy, s3, data_dir):
    """new_files with a fresh state, as a first run sees it."""
    return trail_copy.new_files(s3, str(data_dir), NOW, SETTINGS, trail_copy.fresh_state())[0]


def put(trail_copy, data_dir, name, suffix=""):
    """A local copy of a file, as a run that took it leaves it."""
    path = Path(trail_copy.local_path(str(data_dir), name) + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


# --- the signature ---

# The two examples of the Amazon S3 API reference, "Authenticating Requests:
# Using the Authorization Header (AWS Signature Version 4)", with the example
# key pair that the AWS documentation uses everywhere.
EXAMPLE = {"access_key": "AKIAIOSFODNN7EXAMPLE", "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "token": ""}
EXAMPLE_TIME = datetime.datetime(2013, 5, 24, tzinfo=UTC)
EXAMPLE_HOST = "examplebucket.s3.amazonaws.com"


def test_the_signature_of_a_get_is_the_one_of_the_s3_reference(trail_copy):
    headers = trail_copy.signed_headers("GET", EXAMPLE_HOST, "/test.txt", {}, "us-east-1", EXAMPLE, EXAMPLE_TIME,
                                        {"Range": "bytes=0-9"})
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


def test_the_signature_of_a_list_is_the_one_of_the_s3_reference(trail_copy):
    headers = trail_copy.signed_headers("GET", EXAMPLE_HOST, "/", {"max-keys": "2", "prefix": "J"}, "us-east-1",
                                        EXAMPLE, EXAMPLE_TIME)
    assert headers["Authorization"].endswith(
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")


def test_a_session_token_is_a_signed_header(trail_copy):
    creds = {"access_key": "an-access-key-id", "secret_key": "a-secret-access-key", "token": "a-session-token"}
    headers = trail_copy.signed_headers("GET", "bucket.s3.us-west-2.amazonaws.com", "/", {}, "us-west-2", creds, NOW)
    assert headers["x-amz-security-token"] == "a-session-token"
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date;x-amz-security-token," in headers["Authorization"]


def answer(body: bytes):
    response = io.BytesIO(body)
    response.__enter__ = lambda *_: response
    return response


def test_a_list_follows_the_pages_and_an_error_shows_no_key_id(trail_copy):
    ns = "http://s3.amazonaws.com/doc/2006-03-01/"
    first = (f'<ListBucketResult xmlns="{ns}"><IsTruncated>true</IsTruncated><Contents><Key>a/1</Key></Contents>'
             "<NextContinuationToken>next</NextContinuationToken></ListBucketResult>")
    second = (f'<ListBucketResult xmlns="{ns}"><IsTruncated>false</IsTruncated><Contents><Key>a/2</Key></Contents>'
              "<CommonPrefixes><Prefix>a/b/</Prefix></CommonPrefixes></ListBucketResult>")
    pages = [first, second]
    urls = []

    def opener(request, timeout):
        urls.append(request.full_url)
        return answer(pages[len(urls) - 1].encode())

    creds = {"access_key": "an-access-key-id", "secret_key": "a-secret-access-key", "token": "a-session-token"}
    s3 = trail_copy.S3("the-bucket", "us-west-2", creds, opener)
    assert s3.list("a/", start_after="a/0") == (["a/1", "a/2"], ["a/b/"])
    assert urls[0] == "https://the-bucket.s3.us-west-2.amazonaws.com/?list-type=2&prefix=a%2F&start-after=a%2F0"
    assert "continuation-token=next" in urls[1] and "start-after" not in urls[1]

    def refuses(request, timeout):
        body = b"<Error><Code>ExpiredToken</Code><Message>The provided token has expired.</Message>" \
               b"<AWSAccessKeyId>an-access-key-id</AWSAccessKeyId></Error>"
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    with pytest.raises(RuntimeError) as failure:
        trail_copy.S3("the-bucket", "us-west-2", creds, refuses).list("a/")
    assert "ExpiredToken" in str(failure.value)
    assert "an-access-key-id" not in str(failure.value)


# --- which files ---


def test_the_prefixes_of_an_organization_trail_and_of_an_account_trail(trail_copy):
    moment = NOW - datetime.timedelta(hours=1)
    alone = "AWSLogs/333333333333/CloudTrail/eu-west-1/"
    s3 = FakeS3({key(PREFIX_A, moment): b"", key(PREFIX_B, moment): b"", key(alone, moment): b"",
                 f"{ORG}111111111111/CloudTrail-Digest/us-west-2/2026/11/13/digest.json.gz": b""})
    assert trail_copy.trail_prefixes(s3) == sorted([alone, PREFIX_A, PREFIX_B])


def test_the_first_run_starts_six_days_back_and_takes_the_oldest_first(trail_copy, tmp_path):
    old = key(PREFIX_A, NOW - datetime.timedelta(days=6, hours=1))
    first = key(PREFIX_B, NOW - datetime.timedelta(hours=3))
    second = key(PREFIX_A, NOW - datetime.timedelta(hours=2))
    third = key(PREFIX_B, NOW - datetime.timedelta(hours=1))
    s3 = FakeS3(dict.fromkeys([old, first, second, third], b""))
    assert files_of(trail_copy, s3, tmp_path) == [first, second, third]
    two = trail_copy.new_files(s3, str(tmp_path), NOW, {**SETTINGS, "max_files": 2}, trail_copy.fresh_state())
    assert two == ([first, second], 2)


def test_a_later_run_lists_from_the_newest_copy_less_the_overlap(trail_copy, tmp_path):
    copied = key(PREFIX_A, NOW - datetime.timedelta(hours=2))
    late = key(PREFIX_A, NOW - datetime.timedelta(hours=2, minutes=30), "Late")
    older = key(PREFIX_A, NOW - datetime.timedelta(hours=5))
    new = key(PREFIX_A, NOW - datetime.timedelta(minutes=20))
    s3 = FakeS3(dict.fromkeys([copied, late, older, new], b""))
    put(trail_copy, tmp_path, copied)
    # The file that AWS delivered late is inside the overlap. The older one is
    # before it, and the copied one has its copy.
    assert files_of(trail_copy, s3, tmp_path) == [late, new]
    listed = [call for call in s3.lists if call[0] == PREFIX_A and not call[2]]
    start = NOW - datetime.timedelta(hours=4)
    assert listed == [(PREFIX_A, f"{PREFIX_A}{start:%Y/%m/%d}/111111111111_CloudTrail_us-west-2_{start:%Y%m%dT%H%MZ}", "")]


def test_a_key_that_is_not_of_the_trail_gets_no_local_path(trail_copy, tmp_path):
    for bad in ("other/file.json.gz", "AWSLogs/../../etc/file.json.gz", "AWSLogs//file.json.gz"):
        with pytest.raises(ValueError, match="not a key of the trail"):
            trail_copy.local_path(str(tmp_path), bad)


# --- the events of a file ---


@pytest.mark.parametrize("chunk", [1, 7, 65536])
def test_the_records_of_a_file_come_one_at_a_time(trail_copy, chunk):
    events = [event(NOW, "A", requestParameters={"text": "a ] and a } and a \" in a string"}), event(NOW, "B"),
              event(NOW, "C", nested={"list": [1, {"deep": [2, 3]}]})]
    text = json.dumps({"Records": events}, indent=1)
    assert list(trail_copy.records(io.StringIO(text), chunk)) == events
    assert list(trail_copy.records(io.StringIO('{"Records":[]}'), chunk)) == []


def test_a_file_that_stops_half_way_is_an_error(trail_copy):
    text = json.dumps({"Records": [event(NOW), event(NOW)]})
    with pytest.raises(ValueError, match="does not end"):
        list(trail_copy.records(io.StringIO(text[:-30]), 16))
    with pytest.raises(ValueError, match="no Records array"):
        list(trail_copy.records(io.StringIO('{"Other":[]}'), 16))


def test_an_entry_has_the_time_of_the_event_and_the_event_as_one_line(trail_copy):
    moment = datetime.datetime(2026, 11, 13, 11, 22, 33, tzinfo=UTC)
    nanoseconds, line = trail_copy.entry(event(moment, userAgent="é"), NOW)
    assert nanoseconds == int(moment.timestamp()) * 1_000_000_000
    assert "\n" not in line and json.loads(line) == event(moment, userAgent="é")
    # An event with no time gets the time of its file.
    assert trail_copy.entry({"eventName": "A"}, NOW)[0] == int(NOW.timestamp()) * 1_000_000_000


def test_a_line_over_the_limit_of_loki_loses_its_large_fields_only(trail_copy):
    large = event(NOW, "PutBucketPolicy", requestParameters={"policy": "x" * 300_000}, eventID="an-event-id")
    _, line = trail_copy.entry(large, NOW)
    small = json.loads(line)
    assert len(line) < trail_copy.MAX_LINE
    assert small["truncated"] is True and "requestParameters" not in small
    assert small["eventID"] == "an-event-id" and small["eventName"] == "PutBucketPolicy"


def test_a_batch_stays_under_its_limit_and_is_in_the_order_of_time(trail_copy):
    entries = [(3, "c" * 40), (1, "a" * 40), (2, "b" * 40), (5, "e" * 40), (4, "d" * 40)]
    assert list(trail_copy.batches(entries, limit=100)) == [
        [(1, "a" * 40), (3, "c" * 40)], [(2, "b" * 40), (5, "e" * 40)], [(4, "d" * 40)]]
    assert list(trail_copy.batches([])) == []


# --- Loki ---


def loki_with(trail_copy, answers: list):
    """A Loki whose opener gives each answer in turn: None for a success, or a (code, text)."""
    bodies, sleeps = [], []

    def opener(request, timeout):
        bodies.append(json.loads(request.data))
        code = answers[len(bodies) - 1]
        if code is None:
            return answer(b"")
        if code == "down":
            raise urllib.error.URLError("connection refused")
        raise urllib.error.HTTPError(request.full_url, code[0], "", {}, io.BytesIO(code[1].encode()))

    return trail_copy.Loki("http://loki/loki/api/v1/push", opener, sleeps.append), bodies, sleeps


def test_a_push_is_one_stream_with_the_labels_of_its_file(trail_copy):
    loki, bodies, _ = loki_with(trail_copy, [None])
    loki.push(trail_copy.stream_labels(key(PREFIX_A, NOW)), [(1, "a"), (2, "b")])
    assert bodies == [{"streams": [{"stream": LABELS_A, "values": [["1", "a"], ["2", "b"]]}]}]


def test_a_push_waits_and_tries_again_then_fails(trail_copy):
    loki, bodies, sleeps = loki_with(trail_copy, [(429, "slow down"), "down", (503, "busy"), None])
    loki.push(LABELS_A, [(1, "a")])
    assert len(bodies) == 4 and sleeps == [2, 4, 8]
    loki, bodies, sleeps = loki_with(trail_copy, [(503, "busy")] * 5)
    with pytest.raises(trail_copy.LokiError, match="503"):
        loki.push(LABELS_A, [(1, "a")])
    assert len(bodies) == 5 and sleeps == [2, 4, 8, 16]
    # Another answer of 4xx is not the fault of one file: the run stops.
    loki, _, sleeps = loki_with(trail_copy, [(404, "not found")])
    with pytest.raises(trail_copy.LokiError, match="404"):
        loki.push(LABELS_A, [(1, "a")])
    assert sleeps == []


def test_a_400_is_a_refusal_with_no_second_try_and_says_if_it_is_for_the_time(trail_copy):
    too_far = "entry with timestamp 2026-11-13 10:00:00 ignored, reason: 'entry too far behind'"
    loki, bodies, sleeps = loki_with(trail_copy, [(400, too_far)])
    with pytest.raises(trail_copy.Refused) as refusal:
        loki.push(LABELS_A, [(1, "a")])
    assert refusal.value.for_time and len(bodies) == 1 and sleeps == []
    loki, _, sleeps = loki_with(trail_copy, [(400, "Max entry size '262144' bytes exceeded")])
    with pytest.raises(trail_copy.Refused) as refusal:
        loki.push(LABELS_A, [(1, "a")])
    assert not refusal.value.for_time and sleeps == []


# --- one run ---


def test_a_run_copies_sends_and_names_the_copy(trail_copy, tmp_path):
    first, second = NOW - datetime.timedelta(hours=2), NOW - datetime.timedelta(hours=1)
    one, two = key(PREFIX_A, first), key(PREFIX_B, second)
    s3 = FakeS3({one: trail_file([event(first, "A"), event(first, "B")]), two: trail_file([event(second, "C")])})
    loki = FakeLoki()
    result = trail_copy.run(s3, loki, str(tmp_path), NOW, SETTINGS)
    assert (result["new"], result["files"], result["events"], result["bad"]) == (2, 2, 3, [])
    assert [line["eventName"] for line in loki.lines] == ["A", "B", "C"]
    assert [labels for labels, _ in loki.batches] == [LABELS_A, LABELS_B]
    for name in (one, two):
        assert Path(trail_copy.local_path(str(tmp_path), name)).read_bytes() == s3.objects[name]
    # The next run has nothing to do.
    again = trail_copy.run(s3, FakeLoki(), str(tmp_path), NOW, SETTINGS)
    assert (again["new"], again["events"]) == (0, 0)


def test_a_run_that_loki_stops_keeps_no_final_name_and_the_next_run_sends_again(trail_copy, tmp_path):
    moment = NOW - datetime.timedelta(hours=1)
    name = key(PREFIX_A, moment)
    s3 = FakeS3({name: trail_file([event(moment, "A")])})
    with pytest.raises(RuntimeError, match="503"):
        trail_copy.run(s3, FakeLoki(fail_after=0), str(tmp_path), NOW, SETTINGS)
    path = Path(trail_copy.local_path(str(tmp_path), name))
    assert not path.exists() and Path(f"{path}.part").exists()
    loki = FakeLoki()
    assert trail_copy.run(s3, loki, str(tmp_path), NOW, SETTINGS)["files"] == 1
    assert path.exists() and not Path(f"{path}.part").exists()
    assert [line["eventName"] for line in loki.lines] == ["A"]


def test_a_file_that_cannot_be_read_is_set_aside_and_the_run_goes_on(trail_copy, tmp_path):
    first, second = NOW - datetime.timedelta(hours=2), NOW - datetime.timedelta(hours=1)
    bad, good = key(PREFIX_A, first), key(PREFIX_A, second)
    s3 = FakeS3({bad: b"not a gzip file", good: trail_file([event(second, "A")])})
    loki = FakeLoki()
    result = trail_copy.run(s3, loki, str(tmp_path), NOW, SETTINGS)
    assert result["files"] == 1 and len(result["bad"]) == 1 and bad in result["bad"][0]
    assert Path(trail_copy.local_path(str(tmp_path), bad) + ".bad").exists()
    assert trail_copy.run(s3, FakeLoki(), str(tmp_path), NOW, SETTINGS)["new"] == 0


def test_a_run_stops_taking_files_at_its_time_limit(trail_copy, tmp_path):
    moments = [NOW - datetime.timedelta(minutes=minutes) for minutes in (30, 20, 10)]
    s3 = FakeS3({key(PREFIX_A, moment): trail_file([event(moment)]) for moment in moments})
    ticks = iter([0, 1, 700, 701])
    result = trail_copy.run(s3, FakeLoki(), str(tmp_path), NOW, SETTINGS, clock=lambda: next(ticks))
    assert (result["files"], result["left"]) == (1, 2)


def test_copies_older_than_seven_days_go_and_empty_directories_with_them(trail_copy, tmp_path):
    old = Path(trail_copy.local_path(str(tmp_path), key(PREFIX_A, NOW - datetime.timedelta(days=9))))
    kept = Path(trail_copy.local_path(str(tmp_path), key(PREFIX_A, NOW - datetime.timedelta(days=1))))
    for path, days in ((old, 8), (kept, 1)):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        stamp = (NOW - datetime.timedelta(days=days)).timestamp()
        os.utime(path, (stamp, stamp))
    assert trail_copy.clean(str(tmp_path), NOW, 7) == 1
    assert kept.exists() and not old.parent.exists()
    assert (tmp_path / "files").is_dir()


def test_the_placeholder_session_is_a_run_with_nothing_to_do(trail_copy, monkeypatch, capsys):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "pending")
    trail_copy.main()
    assert "not there yet" in capsys.readouterr().out


# --- a stream for each account and Region, and the order of its lines ---

HOUR = 3600 * 1_000_000_000


def loki_with_a_window(trail_copy):
    """A Loki that refuses a line more than 1 hour older than the newest line of its stream."""
    newest = {}

    def refuse(labels, batch):
        stream = tuple(sorted(labels.items()))
        if any(moment < newest.get(stream, 0) - HOUR for moment, _ in batch):
            return "entry with timestamp ignored, reason: 'entry too far behind'"
        newest[stream] = max([newest.get(stream, 0)] + [moment for moment, _ in batch])
        return None

    return FakeLoki(refuse=refuse, trail_copy=trail_copy)


def test_each_account_and_region_is_a_stream_of_its_own(trail_copy):
    assert trail_copy.stream_labels(key(PREFIX_A, NOW)) == LABELS_A
    assert trail_copy.stream_labels(key(PREFIX_B, NOW)) == LABELS_B
    alone = "AWSLogs/333333333333/CloudTrail/eu-west-1/"
    assert trail_copy.stream_labels(key(alone, NOW))["aws_account"] == "333333333333"
    with pytest.raises(ValueError, match="not a key of the trail"):
        trail_copy.stream_labels("AWSLogs/o-example/an-account/CloudTrail/us-west-2/2026/11/13/a.json.gz")


def test_each_stream_gets_its_files_in_the_order_of_their_time(trail_copy, tmp_path):
    objects = {}
    for prefix, minutes in ((PREFIX_A, (50, 10, 30, 40, 20)), (PREFIX_B, (15, 45, 25, 35))):
        for number in minutes:
            moment = NOW - datetime.timedelta(minutes=number)
            events = [event(moment - datetime.timedelta(minutes=3)), event(moment - datetime.timedelta(minutes=1))]
            objects[key(prefix, moment)] = trail_file(events)
    loki = loki_with_a_window(trail_copy)
    result = trail_copy.run(FakeS3(objects), loki, str(tmp_path), NOW, SETTINGS)
    assert (result["files"], result["bad"]) == (9, [])
    for labels, count in ((LABELS_A, 5), (LABELS_B, 4)):
        times = [max(moment for moment, _ in batch) for stream, batch in loki.batches if stream == labels]
        assert len(times) == count and times == sorted(times)


def test_a_quiet_prefix_sent_hours_later_is_not_refused_in_its_own_stream(trail_copy, tmp_path, monkeypatch):
    # The active prefix has its new files. The quiet prefix has one file of 3 hours ago,
    # which a run takes after them, as a run that lists a quiet prefix later does.
    late = NOW - datetime.timedelta(hours=3)
    order = [key(PREFIX_A, NOW - datetime.timedelta(minutes=30)), key(PREFIX_A, NOW - datetime.timedelta(minutes=15)),
             key(PREFIX_B, late)]
    s3 = FakeS3({name: trail_file([event(trail_copy.file_time(name))]) for name in order})
    loki = loki_with_a_window(trail_copy)
    assert [trail_copy.copy_file(s3, loki, str(tmp_path / "streams"), name) for name in order] == [1, 1, 1]
    # In one stream for the whole trail, Loki would refuse it.
    monkeypatch.setattr(trail_copy, "stream_labels", lambda name: {"security_stream": "cloudtrail"})
    loki = loki_with_a_window(trail_copy)
    for name in order[:2]:
        trail_copy.copy_file(s3, loki, str(tmp_path / "one"), name)
    with pytest.raises(trail_copy.BadFile, match="too far behind"):
        trail_copy.copy_file(s3, loki, str(tmp_path / "one"), order[2])


def test_the_converter_reads_the_streams_of_each_account_and_region(trail_copy):
    spec = importlib.util.spec_from_file_location("converter_of_the_trail", ROOT / "security/converter/converter.py")
    converter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(converter)
    streams = json.loads((ROOT / "kubernetes/apps/security/converter/app/config/streams.json").read_text())
    selectors = [item["selector"] for item in streams["streams"] if item["check"] == trail_copy.STREAM_NAME]
    assert len(selectors) == 2 and all(selector.items() <= LABELS_A.items() for selector in selectors)
    answer = {"status": "success", "data": {"resultType": "streams", "result": [
        {"stream": LABELS_A, "values": [["1500", "-"]]}, {"stream": LABELS_B, "values": [["1400", "-"]]}]}}
    assert converter.newest_time(answer, selectors[0], 1000, 2000) == 1500


# --- a line that Loki refuses, and a line that is too large ---


def test_a_file_with_a_refused_line_is_set_aside_and_its_other_lines_go(trail_copy, tmp_path, monkeypatch):
    moment = NOW - datetime.timedelta(hours=1)
    name = key(PREFIX_A, moment)
    s3 = FakeS3({name: trail_file([event(moment, f"E{number}") for number in range(3)])})
    monkeypatch.setattr(trail_copy, "batches", lambda entries: ([item] for item in entries))
    pushed = []

    def refuse(labels, batch):
        pushed.append(batch)
        return "entry with timestamp ignored, reason: 'entry too far behind'" if len(pushed) == 2 else None

    loki = FakeLoki(refuse=refuse, trail_copy=trail_copy)
    result = trail_copy.run(s3, loki, str(tmp_path), NOW, SETTINGS)
    assert (result["files"], result["events"], result["refused"]) == (0, 2, 1)
    (line,) = result["bad"]
    assert name in line and "1 batches for their time" in line
    assert [item["eventName"] for item in loki.lines] == ["E0", "E2"]
    assert Path(trail_copy.local_path(str(tmp_path), name) + ".bad").exists()
    # No later run sends it again, so no loop of failed runs.
    assert trail_copy.run(s3, FakeLoki(), str(tmp_path), NOW, SETTINGS)["new"] == 0


def test_a_file_with_another_400_is_set_aside_and_the_stream_goes_on(trail_copy, tmp_path):
    first, second = NOW - datetime.timedelta(hours=2), NOW - datetime.timedelta(hours=1)
    stuck, good = key(PREFIX_A, first), key(PREFIX_A, second)
    s3 = FakeS3({stuck: trail_file([event(first, "Stuck")]), good: trail_file([event(second, "Good")])})

    def refuse(labels, batch):
        return "Max entry size '262144' bytes exceeded" if "Stuck" in batch[0][1] else None

    loki = FakeLoki(refuse=refuse, trail_copy=trail_copy)
    result = trail_copy.run(s3, loki, str(tmp_path), NOW, SETTINGS)
    assert result["files"] == 1 and result["refused"] == 1 and "with a 400" in result["bad"][0]
    assert [item["eventName"] for item in loki.lines] == ["Good"]
    assert Path(trail_copy.local_path(str(tmp_path), stuck) + ".bad").exists()


def test_a_record_that_is_not_an_object_sets_the_file_aside(trail_copy, tmp_path):
    name = key(PREFIX_A, NOW - datetime.timedelta(hours=1))
    s3 = FakeS3({name: gzip.compress(b'{"Records":["a text"]}')})
    result = trail_copy.run(s3, FakeLoki(), str(tmp_path), NOW, SETTINGS)
    assert len(result["bad"]) == 1 and "not an object" in result["bad"][0] and result["refused"] == 0


def test_a_line_still_over_the_limit_keeps_only_the_small_fields_that_name_the_event(trail_copy):
    who = {"type": "AssumedRole", "principalId": "an-example-principal"}
    record = event(NOW, "CreateThing", eventID="an-event-id", userIdentity=who,
                   resources=[{"ARN": "x" * 1000}] * 300)
    _, line = trail_copy.entry(record, NOW)
    assert len(line.encode()) <= trail_copy.MAX_LINE
    assert json.loads(line) == {"eventVersion": "1.11", "eventTime": "2026-11-13T12:00:00Z", "eventName": "CreateThing",
                                "recipientAccountId": "111111111111", "eventID": "an-event-id",
                                "userIdentity": who, "truncated": True}
    # A field of the stub that is too large itself goes too.
    _, line = trail_copy.entry(event(NOW, "y" * 300_000), NOW)
    assert len(line.encode()) <= trail_copy.MAX_LINE and "eventName" not in json.loads(line)


def test_a_run_with_a_file_set_aside_fails_counts_it_last_and_shows_no_credential(trail_copy, tmp_path,
                                                                                 monkeypatch, capsys):
    now = datetime.datetime.now(UTC).replace(second=0, microsecond=0)
    moment = now - datetime.timedelta(hours=1)
    refused, unreadable = key(PREFIX_A, moment), key(PREFIX_B, moment)
    s3 = FakeS3({refused: trail_file([event(moment)]), unreadable: b"not a gzip file"})
    loki = FakeLoki(refuse=lambda labels, batch: "entry too far behind" if labels == LABELS_A else None,
                    trail_copy=trail_copy)
    monkeypatch.setattr(trail_copy, "S3", lambda *args: s3)
    monkeypatch.setattr(trail_copy, "Loki", lambda *args: loki)
    creds = {"AWS_ACCESS_KEY_ID": "an-access-key-id", "AWS_SECRET_ACCESS_KEY": "a-secret-access-key",
             "AWS_SESSION_TOKEN": "a-session-token"}
    for name, value in {**creds, "TRAIL_BUCKET": "the-bucket", "AWS_REGION": "us-west-2",
                        "LOKI_URL": "http://loki", "DATA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as end:
        trail_copy.main()
    assert end.value.code == 1
    out, err = capsys.readouterr()
    last = out.strip().splitlines()[-1]
    assert "0 of 2 new files copied" in last and "2 files set aside as .bad (1 with lines that Loki refused)" in last
    assert "S3 requests: 6 list and 2 get" in last
    assert refused in err and unreadable in err
    assert not any(value in out + err for value in creds.values())


# --- the requests to S3 ---


def lists_of_a_run(trail_copy, s3, data_dir, at, settings=SETTINGS):
    s3.lists.clear()
    trail_copy.run(s3, FakeLoki(), str(data_dir), at, settings)
    return [prefix for prefix, *_ in s3.lists]


def test_a_quiet_prefix_is_listed_less_often_and_the_prefixes_once_a_day(trail_copy, tmp_path):
    def active_file(at):
        moment = at - datetime.timedelta(minutes=5)
        s3.objects[key(PREFIX_A, moment)] = trail_file([event(moment)])

    old = NOW - datetime.timedelta(days=2)
    s3 = FakeS3({key(PREFIX_B, old): trail_file([event(old)])})
    put(trail_copy, tmp_path, key(PREFIX_B, old))
    active_file(NOW)
    # The first run lists the prefixes (AWSLogs/, the organization, two accounts), then each prefix.
    assert lists_of_a_run(trail_copy, s3, tmp_path, NOW) == ["AWSLogs/", ORG, f"{ORG}111111111111/CloudTrail/",
                                                             f"{ORG}222222222222/CloudTrail/", PREFIX_A, PREFIX_B]
    # Then the active prefix each run, and the quiet one after 4 hours.
    for minutes in range(15, 240, 15):
        at = NOW + datetime.timedelta(minutes=minutes)
        active_file(at)
        assert lists_of_a_run(trail_copy, s3, tmp_path, at) == [PREFIX_A]
    at = NOW + datetime.timedelta(minutes=240)
    active_file(at)
    assert lists_of_a_run(trail_copy, s3, tmp_path, at) == [PREFIX_A, PREFIX_B]
    # The list of prefixes again after a day.
    at = NOW + datetime.timedelta(hours=24)
    active_file(at)
    assert lists_of_a_run(trail_copy, s3, tmp_path, at)[:4] == ["AWSLogs/", ORG, f"{ORG}111111111111/CloudTrail/",
                                                                f"{ORG}222222222222/CloudTrail/"]
    # No new file in the whole trail for 90 minutes: each prefix, each run, until one comes.
    for minutes in (75, 90, 105):
        at = NOW + datetime.timedelta(hours=24, minutes=minutes)
        expected = [PREFIX_A] if minutes < 90 else [PREFIX_A, PREFIX_B]
        assert lists_of_a_run(trail_copy, s3, tmp_path, at) == expected


def test_a_prefix_with_files_left_for_the_next_run_is_listed_again(trail_copy, tmp_path):
    old = NOW - datetime.timedelta(days=2)
    first, second = NOW - datetime.timedelta(hours=3), NOW - datetime.timedelta(hours=2)
    s3 = FakeS3({key(PREFIX_B, moment): trail_file([event(moment)]) for moment in (old, first, second)})
    s3.objects[key(PREFIX_A, NOW - datetime.timedelta(minutes=5))] = trail_file([event(NOW)])
    one_file = {**SETTINGS, "max_files": 1}
    put(trail_copy, tmp_path, key(PREFIX_B, old))
    assert lists_of_a_run(trail_copy, s3, tmp_path, NOW, one_file)[-2:] == [PREFIX_A, PREFIX_B]
    later = NOW + datetime.timedelta(minutes=15)
    assert lists_of_a_run(trail_copy, s3, tmp_path, later, one_file) == [PREFIX_A, PREFIX_B]
    assert Path(trail_copy.local_path(str(tmp_path), key(PREFIX_B, second))).exists()
    # The next list of the quiet prefix finds no new file, so it waits from then.
    at = NOW + datetime.timedelta(minutes=30)
    assert lists_of_a_run(trail_copy, s3, tmp_path, at, one_file) == [PREFIX_A, PREFIX_B]
    at = NOW + datetime.timedelta(minutes=45)
    assert lists_of_a_run(trail_copy, s3, tmp_path, at, one_file) == [PREFIX_A]


def test_a_state_file_that_does_not_fit_is_a_fresh_state(trail_copy, tmp_path):
    moment = NOW - datetime.timedelta(hours=1)
    s3 = FakeS3({key(PREFIX_A, moment): trail_file([event(moment)])})
    for text in ("{not json", '{"prefixes": "AWSLogs/", "prefixes_at": 0, "listed": {}}', "[]"):
        (tmp_path / "state.json").write_text(text)
        assert trail_copy.load_state(str(tmp_path)) == trail_copy.fresh_state()
    assert trail_copy.run(s3, FakeLoki(), str(tmp_path), NOW, SETTINGS)["files"] == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["prefixes"] == [PREFIX_A] and state["listed"] == {}


# --- the manifests ---


def documents(name: str) -> list:
    return [item for item in yaml.safe_load_all((APP / name).read_text()) if item]


def test_the_job_runs_on_the_security_node_inside_its_memory_line():
    (cronjob,) = documents("cronjob.yaml")
    assert cronjob["spec"]["schedule"] == "*/15 * * * *"
    pod = cronjob["spec"]["jobTemplate"]["spec"]["template"]
    (container,) = pod["spec"]["containers"]
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "${SECURITY_NODE}"}
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert container["resources"]["limits"] == {"memory": "128Mi"}
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    # The session of the trail reader, and no other Secret.
    assert container["envFrom"] == [{"secretRef": {"name": "aws-security-trail-sts"}}]
    env = {item["name"]: item["value"] for item in container["env"]}
    assert env["TRAIL_BUCKET"] == "${SECURITY_TRAIL_BUCKET}"
    assert int(env["START_HOURS"]) < 168 and env["KEEP_DAYS"] == "7"
    # The two settings of the cost in the README.
    assert (env["QUIET_MINUTES"], env["PREFIX_HOURS"]) == ("240", "24")
    (claim,) = [volume["persistentVolumeClaim"]["claimName"] for volume in pod["spec"]["volumes"]
                if "persistentVolumeClaim" in volume]
    assert claim == documents("pvc.yaml")[0]["metadata"]["name"]


def test_the_network_policy_names_the_bucket_loki_and_dns_only():
    (policy,) = documents("networkpolicy.yaml")
    (cronjob,) = documents("cronjob.yaml")
    labels = cronjob["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
    assert policy["spec"]["endpointSelector"]["matchLabels"].items() <= labels.items()
    assert "ingress" not in policy["spec"]
    egress = policy["spec"]["egress"]
    assert len(egress) == 3
    hosts = [name["matchName"] for rule in egress for name in rule.get("toFQDNs", [])]
    assert hosts == ["${SECURITY_TRAIL_BUCKET}.s3.us-west-2.amazonaws.com"]
    assert not any("toEntities" in rule or "toCIDR" in rule for rule in egress)


def test_the_stream_is_a_stream_of_the_registry_and_the_settings_name_the_bucket(trail_copy):
    sources = yaml.safe_load((ROOT / "security/registry/sources.yaml").read_text())
    (stream,) = [source for source in sources if source["name"] == "stream"]
    assert trail_copy.STREAM_NAME in stream["checks"]
    # The rule for a late result reads the stream before it is late: each prefix is
    # listed again in time (trail_copy.ALL_AFTER).
    late_after = stream["checks"][trail_copy.STREAM_NAME]["late_after"]
    assert late_after == "2h" and trail_copy.ALL_AFTER < datetime.timedelta(hours=2)
    settings = yaml.safe_load((ROOT / "kubernetes/clusters/wsl-02/cluster-settings.yaml").read_text())
    assert settings["data"]["SECURITY_TRAIL_BUCKET"].startswith("tnwks-cloudtrail-")
