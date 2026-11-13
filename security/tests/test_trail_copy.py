"""The trail copy job of the security platform keeps its contract.

kubernetes/apps/security/trail-copy: the script signs its S3 requests, reads a
trail file one event at a time, sends each event to Loki as a line of the
stream {security_stream="cloudtrail"}, and gives a local copy its final name
only when Loki has its events. Its manifests agree with each other.
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
SETTINGS = {"start_hours": 144, "overlap_minutes": 60, "max_files": 500, "run_seconds": 600, "keep_days": 7}


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

    def list(self, prefix, start_after="", delimiter="", limit=0):
        self.lists.append((prefix, start_after, delimiter))
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
        Path(path).write_bytes(self.objects[name])


class FakeLoki:
    def __init__(self, fail_after: int | None = None):
        self.batches = []
        self.refused = 0
        self.fail_after = fail_after

    def push(self, batch):
        if self.fail_after is not None and len(self.batches) >= self.fail_after:
            raise RuntimeError("Loki answered 503")
        self.batches.append(batch)

    @property
    def lines(self):
        return [json.loads(line) for batch in self.batches for _, line in batch]


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
    assert trail_copy.new_files(s3, str(tmp_path), NOW, 144, 60, 500) == [first, second, third]
    assert trail_copy.new_files(s3, str(tmp_path), NOW, 144, 60, 2) == [first, second]


def test_a_later_run_lists_from_the_newest_copy_less_the_overlap(trail_copy, tmp_path):
    copied = key(PREFIX_A, NOW - datetime.timedelta(hours=2))
    late = key(PREFIX_A, NOW - datetime.timedelta(hours=2, minutes=30), "Late")
    older = key(PREFIX_A, NOW - datetime.timedelta(hours=4))
    new = key(PREFIX_A, NOW - datetime.timedelta(minutes=20))
    s3 = FakeS3(dict.fromkeys([copied, late, older, new], b""))
    path = Path(trail_copy.local_path(str(tmp_path), copied))
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    # The file that AWS delivered late is inside the overlap. The older one is
    # before it, and the copied one has its copy.
    assert trail_copy.new_files(s3, str(tmp_path), NOW, 144, 60, 500) == [late, new]
    listed = [call for call in s3.lists if call[0] == PREFIX_A and not call[2]]
    start = NOW - datetime.timedelta(hours=3)
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


def test_a_push_is_one_stream_with_one_label(trail_copy):
    loki, bodies, _ = loki_with(trail_copy, [None])
    loki.push([(1, "a"), (2, "b")])
    assert bodies == [{"streams": [{"stream": {"security_stream": "cloudtrail"}, "values": [["1", "a"], ["2", "b"]]}]}]


def test_a_push_waits_and_tries_again_then_fails(trail_copy):
    loki, bodies, sleeps = loki_with(trail_copy, [(429, "slow down"), "down", (503, "busy"), None])
    loki.push([(1, "a")])
    assert len(bodies) == 4 and sleeps == [2, 4, 8]
    loki, bodies, sleeps = loki_with(trail_copy, [(503, "busy")] * 5)
    with pytest.raises(trail_copy.LokiError, match="503"):
        loki.push([(1, "a")])
    assert len(bodies) == 5 and sleeps == [2, 4, 8, 16]


def test_a_line_that_loki_refuses_for_its_time_is_counted_and_another_400_fails(trail_copy):
    too_old = "entry for stream '{security_stream=\"cloudtrail\"}' has timestamp too old: 2026-11-01T00:00:00Z"
    loki, bodies, _ = loki_with(trail_copy, [(400, too_old)])
    loki.push([(1, "a")])
    assert loki.refused == 1 and len(bodies) == 1
    loki, _, sleeps = loki_with(trail_copy, [(400, "error parsing labels")])
    with pytest.raises(trail_copy.LokiError, match="400"):
        loki.push([(1, "a")])
    assert sleeps == []


# --- one run ---


def test_a_run_copies_sends_and_names_the_copy(trail_copy, tmp_path):
    first, second = NOW - datetime.timedelta(hours=2), NOW - datetime.timedelta(hours=1)
    one, two = key(PREFIX_A, first), key(PREFIX_B, second)
    s3 = FakeS3({one: trail_file([event(first, "A"), event(first, "B")]), two: trail_file([event(second, "C")])})
    loki = FakeLoki()
    result = trail_copy.run(s3, loki, str(tmp_path), NOW, SETTINGS)
    assert (result["new"], result["files"], result["events"], result["bad"]) == (2, 2, 3, [])
    assert [line["eventName"] for line in loki.lines] == ["A", "B", "C"]
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
    assert trail_copy.STREAM == {"security_stream": "cloudtrail"}
    assert trail_copy.STREAM["security_stream"] in stream["checks"]
    settings = yaml.safe_load((ROOT / "kubernetes/clusters/wsl-02/cluster-settings.yaml").read_text())
    assert settings["data"]["SECURITY_TRAIL_BUCKET"].startswith("tnwks-cloudtrail-")
