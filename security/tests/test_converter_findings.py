"""The stream `findings` of the converter keeps the promises of security/converter/README.md,
"The stream findings": one line for each finding that fails, with the fields of OCSF that
the README names and no other, sent one time for each file. Each record is invented."""

import calendar
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
import converter
import generate

NOW = calendar.timegm((2026, 11, 13, 12, 0, 0))
REGISTRY = {
    "series": {"result": generate.RESULT, "last_result": generate.LAST_RESULT, "accepted": generate.ACCEPTED},
    "sources": ["prowler", "api-check"],
    "systems": ["account-one", "account-two", "example-router"],
    "prowler": {"aws": ["account-one", "account-two"]},
}
# A value that stands for the setting of a resource: it must never be in a line.
SETTING = "an-invented-setting-value"
ALLOWED = {"class_uid", "time", "severity", "status", "status_code", "status_detail", "metadata", "finding_info",
           "resources", "cloud"}


def record(check, status_code="FAIL", status="New", resource="r1", **more):
    """One invented record with each field that Prowler writes."""
    return {
        "message": f"An invented message about {resource}.", "activity_id": 1, "activity_name": "Create",
        "class_uid": 2004, "category_uid": 2, "time": NOW - 900, "time_dt": "2026-11-13T11:45:00Z",
        "severity": "High", "severity_id": 4, "status": status, "status_id": 1, "status_code": status_code,
        "status_detail": f"An invented detail about {resource}.", "risk_details": "An invented risk.",
        "metadata": {"event_code": check, "product": {"name": "Prowler", "version": "0.0.0"},
                     "profiles": ["cloud", "datetime"], "tenant_uid": "example-org"},
        "finding_info": {"uid": f"example-{check}-{resource}", "title": "An invented title",
                         "desc": "An invented description.", "types": ["example"]},
        "remediation": {"desc": "An invented remedy.", "references": ["https://example.invalid/"]},
        "resources": [{"uid": f"arn:example:{resource}", "name": resource, "type": "ExampleType",
                       "region": "example-region", "group": {"name": "example-service"},
                       "cloud_partition": "example", "labels": [f"tag={SETTING}"],
                       "data": {"details": SETTING, "metadata": {"environment": {"KEY": SETTING}}}}],
        "cloud": {"provider": "aws", "region": "example-region",
                  "account": {"uid": "000000000000", "name": "example-account", "type": "AWS Account",
                              "labels": [f"owner={SETTING}"]},
                  "org": {"uid": "example-org", "name": "example-org-name"}},
        "unmapped": {"compliance": {"EXAMPLE": ["1.1"]}, "notes": SETTING},
        **more,
    }


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def time(self):
        return float(NOW)

    def monotonic(self):
        return self.elapsed


class FakeLoki(ThreadingHTTPServer):
    """The push API of a Loki on the loopback address. `status` is the answer to each push."""

    def __init__(self):
        self.pushes, self.status = [], 204
        super().__init__(("127.0.0.1", 0), self.Handler)
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def close(self):
        self.shutdown()
        self.server_close()

    def lines(self):
        """[(labels, time, line)] of each push that Loki took."""
        return [(stream["stream"], int(moment), line) for path, headers, body, status in self.pushes if status == 204
                for stream in body["streams"] for moment, line in stream["values"]]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status = self.server.status
            self.server.pushes.append((self.path, dict(self.headers), body, status))
            text = b"an-answer-of-loki" if status != 204 else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)

        def log_message(self, *_):
            pass


@pytest.fixture
def loki():
    server = FakeLoki()
    yield server
    server.close()


def stamp(seconds_ago):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(NOW - seconds_ago))


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def prowler_text(records):
    return "[" + ",".join(json.dumps(item, indent=4) for item in records) + "]"


def files_for(root, loki, clock=None, timeout=5):
    clock = clock or Clock()
    findings = converter.Findings(converter.Loki(loki.url, timeout=timeout))
    return converter.ResultFiles(root, REGISTRY, findings, clock=clock.time, monotonic=clock.monotonic)


def series(part, name):
    found = {}
    for line in part.render().decode().splitlines():
        if line.startswith(name + "{"):
            found[line[len(name):line.index("}") + 1]] = float(line.rsplit(" ", 1)[1])
    return found


RECORDS = [record("check_a", resource="r1"), record("check_a", resource="r2"),
           record("check_a", "PASS", resource="r3"), record("check_b", status="Suppressed"),
           record("check_c", "MANUAL")]


def test_each_finding_that_fails_is_one_line_of_its_stream_at_the_time_of_its_file(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(RECORDS))
    part = files_for(tmp_path, loki)
    part.cycle()
    lines = loki.lines()
    assert [json.loads(line)["resources"][0]["name"] for _, _, line in lines] == ["r1", "r2"]
    assert {json.dumps(labels, sort_keys=True) for labels, _, _ in lines} == {
        json.dumps({"security_stream": "findings", "source": "prowler", "system": "account-one"}, sort_keys=True)}
    assert {moment for _, moment, _ in lines} == {(NOW - 3600) * converter.NANOSECONDS}
    (path, headers, _, _), = loki.pushes
    assert path == "/loki/api/v1/push" and headers["Content-Type"] == "application/json"
    assert series(part, converter.FINDINGS_PENDING) == {'{source="prowler",system="account-one"}': 0}
    assert series(part, converter.FINDINGS_SENT) == {'{source="api-check"}': 0, '{source="prowler"}': 2}


def test_a_line_holds_the_named_fields_of_ocsf_and_no_data_of_a_resource(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("check_a")]))
    part = files_for(tmp_path, loki)
    part.cycle()
    (_, _, body, _), = loki.pushes
    assert SETTING not in json.dumps(body)
    line = json.loads(loki.lines()[0][2])
    assert set(line) <= ALLOWED
    assert line == {
        "class_uid": 2004, "time": NOW - 900, "severity": "High", "status": "New", "status_code": "FAIL",
        "status_detail": "An invented detail about r1.",
        "metadata": {"event_code": "check_a"},
        "finding_info": {"uid": "example-check_a-r1", "title": "An invented title"},
        "resources": [{"uid": "arn:example:r1", "name": "r1", "type": "ExampleType", "region": "example-region",
                       "group": {"name": "example-service"}}],
        "cloud": {"provider": "aws", "region": "example-region",
                  "account": {"uid": "000000000000", "name": "example-account"}},
    }


def test_a_long_detail_is_cut(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json",
          prowler_text([record("check_a", status_detail="x" * 20000, finding_info={"title": "y" * 5000})]))
    part = files_for(tmp_path, loki)
    part.cycle()
    line = json.loads(loki.lines()[0][2])
    assert len(line["status_detail"]) == converter.DETAIL_LIMIT
    assert len(line["finding_info"]["title"]) == converter.TEXT_LIMIT


def test_the_lines_of_a_file_are_sent_one_time_and_a_new_file_is_sent(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(90000)}.ocsf.json", prowler_text([record("old")]))
    part = files_for(tmp_path, loki)
    part.cycle()
    part.cycle()
    assert len(loki.pushes) == 1, "the older file is not sent again"
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("new")]))
    part.cycle()
    assert [json.loads(line)["metadata"]["event_code"] for _, _, line in loki.lines()] == ["old", "new"]


def test_after_a_start_the_newest_file_is_sent_again_with_the_same_times_and_text(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(RECORDS))
    files_for(tmp_path, loki).cycle()
    files_for(tmp_path, loki).cycle()
    first, second = loki.pushes
    assert first[2] == second[2], "Loki keeps one line for the same time and the same text"


@pytest.mark.parametrize("status", [500, 503, 429])
def test_lines_that_loki_did_not_take_wait_for_the_next_cycle(tmp_path, loki, status, caplog):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(RECORDS))
    part = files_for(tmp_path, loki)
    loki.status = status
    part.cycle()
    assert series(part, converter.FINDINGS_PENDING) == {'{source="prowler",system="account-one"}': 1}
    assert series(part, generate.LAST_RESULT), "the results do not wait for Loki"
    loki.status = 204
    part.cycle()
    assert series(part, converter.FINDINGS_PENDING) == {'{source="prowler",system="account-one"}': 0}
    assert len(loki.lines()) == 2
    assert "an-answer-of-loki" not in caplog.text


def test_a_loki_that_is_down_holds_back_no_result(tmp_path):
    down = FakeLoki()
    down.close()
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(RECORDS))
    part = files_for(tmp_path, down, timeout=1)
    part.cycle()
    assert series(part, converter.FINDINGS_PENDING) == {'{source="prowler",system="account-one"}': 1}
    assert len(series(part, generate.RESULT)) == 2, "check_a and check_b; check_c is MANUAL only"


def test_lines_that_loki_refused_are_counted_and_not_sent_again(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(RECORDS))
    part = files_for(tmp_path, loki)
    loki.status = 400
    part.cycle()
    part.cycle()
    assert len(loki.pushes) == 1
    assert series(part, converter.FINDINGS_REFUSED) == {'{source="api-check"}': 0, '{source="prowler"}': 2}
    assert series(part, converter.FINDINGS_PENDING) == {'{source="prowler",system="account-one"}': 0}


def test_a_bad_file_sends_no_line(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(RECORDS)[:-30])
    part = files_for(tmp_path, loki)
    part.cycle()
    assert loki.pushes == []
    assert series(part, converter.FINDINGS_PENDING) == {}


def test_the_lines_go_in_batches_of_a_limited_size(tmp_path, loki):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json",
          prowler_text([record("check_a", resource=f"r{index}") for index in range(50)]))
    part = files_for(tmp_path, loki)
    part.findings.batch_bytes = 4000
    part.cycle()
    assert len(loki.pushes) > 5
    assert len(loki.lines()) == 50


def test_the_findings_of_the_api_checks_are_lines_of_their_system(tmp_path, loki):
    payload = {"version": 1, "source": "api-check", "time": NOW - 600, "results": [
        {"check": "example-test", "system": "example-router", "failed": 2, "severity": "high",
         "findings": [{"resource": "example-1", "message": "An invented text.", "extra": SETTING},
                      {"resource": "example-2", "message": "An invented text."}]},
        {"check": "other-test", "system": "account-two", "failed": 0},
    ]}
    write(tmp_path, "api-check/results.json", json.dumps(payload))
    part = files_for(tmp_path, loki)
    part.cycle()
    lines = loki.lines()
    assert SETTING not in json.dumps([push[2] for push in loki.pushes])
    assert [labels["system"] for labels, _, _ in lines] == ["example-router", "example-router"]
    assert {moment for _, moment, _ in lines} == {(NOW - 600) * converter.NANOSECONDS}
    assert json.loads(lines[0][2]) == {
        "time": NOW - 600, "severity": "High", "status": "New", "status_code": "FAIL",
        "status_detail": "An invented text.", "metadata": {"event_code": "example-test"},
        "resources": [{"uid": "example-1"}]}
    part.cycle()
    assert len(loki.pushes) == 1
