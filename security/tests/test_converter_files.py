"""The files of results in the converter keep the contract of security/converter/README.md,
"The files of results": the newest OCSF file of each system and the result file of the
API checks become result series, and a file that does not fit gives no result and no time.
Each record, check, resource and finding here is invented."""

import calendar
import json
import os
import re
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
import converter
import generate

ROOT = Path(__file__).resolve().parents[2]
RESULT, LAST = generate.RESULT, generate.LAST_RESULT
NOW = calendar.timegm((2026, 11, 13, 12, 0, 0))
REGISTRY = {
    "series": {"result": RESULT, "last_result": LAST, "accepted": generate.ACCEPTED},
    "sources": ["prowler", "api-check"],
    "systems": ["account-one", "account-two", "org-one", "example-router", "example-system"],
    "prowler": {"aws": ["account-one", "account-two"], "github": ["org-one"]},
}


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def time(self):
        return float(NOW)

    def monotonic(self):
        return self.elapsed


def stamp(seconds_ago):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(NOW - seconds_ago))


def record(check, status_code="FAIL", status="New", severity="High", resource="example-resource", **more):
    """One invented record in the form of a Detection Finding of Prowler."""
    return {"message": "An invented text.", "metadata": {"event_code": check, "product": {"name": "Prowler"}},
            "severity": severity, "severity_id": 4, "status": status, "status_code": status_code,
            "status_detail": f"An invented detail about {resource}.",
            "finding_info": {"uid": f"example-{check}-{resource}", "title": "An invented title"},
            "resources": [{"uid": resource, "name": resource, "region": "example-region",
                           "data": {"details": "an-invented-detail"}}],
            "time": NOW - 600, **more}


def prowler_text(records):
    """The text that Prowler writes: an array, each record indented, with commas."""
    return "[" + ",".join(json.dumps(item, indent=4) for item in records) + "]"


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


def files_for(root, clock=None):
    clock = clock or Clock()
    return converter.ResultFiles(root, REGISTRY, clock=clock.time, monotonic=clock.monotonic)


def served(part, name):
    """{tuple of the labels: value} of one series in what the part serves."""
    found = {}
    for line in part.render().decode().splitlines():
        if line.startswith((name + "{", name + " ")):
            labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', line))
            found[tuple(sorted(labels.items()))] = float(line.rsplit(" ", 1)[1])
    return found


def results(part):
    """{(source, check, system): (value, severity)}."""
    return {(labels["source"], labels["check"], labels["system"]): (value, labels["severity"])
            for labels, value in ((dict(key), value) for key, value in served(part, RESULT).items())}


def times(part):
    return {(dict(key)["source"], dict(key)["system"]): value for key, value in served(part, LAST).items()}


def skipped(part):
    return {dict(key)["source"]: value for key, value in served(part, converter.FILES_SKIPPED).items()}


# --- The OCSF files of Prowler ----------------------------------------------------------

RECORDS = [
    record("check_a", resource="r1"), record("check_a", resource="r2"), record("check_a", "PASS", resource="r3"),
    record("check_a", resource="r1"),  # the same finding two times is one resource
    record("check_b", "PASS", severity="Critical"),
    record("check_c", status="Suppressed", resource="r4"), record("check_c", resource="r5", severity="Low"),
    record("check_d", status="Suppressed"),
    record("check_e", "MANUAL"),
    record("check_f", severity="Informational"), record("check_f", severity="Medium", resource="r6"),
    record("check_g", severity="Fatal"),
]


def test_each_check_of_the_newest_file_is_the_number_of_resources_that_fail(tmp_path):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(RECORDS))
    part = files_for(tmp_path)
    part.cycle()
    assert results(part) == {
        ("prowler", "check_a", "account-one"): (2, "high"),
        ("prowler", "check_b", "account-one"): (0, "critical"),
        # A finding muted by the list of accepted findings does not fail. The severity
        # of a check is the highest of its records.
        ("prowler", "check_c", "account-one"): (1, "high"),
        ("prowler", "check_d", "account-one"): (0, "high"),
        # check_e has no PASS and no FAIL: no result.
        ("prowler", "check_f", "account-one"): (2, "medium"),
        ("prowler", "check_g", "account-one"): (1, "unknown"),
    }
    assert times(part) == {("prowler", "account-one"): NOW - 3600}, "the time of the name of the file"
    assert skipped(part) == {"prowler": 0, "api-check": 0}
    text = part.render().decode()
    assert f'{RESULT}{{check="check_a",severity="high",source="prowler",system="account-one"}} 2' in text
    assert f'{LAST}{{source="prowler",system="account-one"}} {NOW - 3600:.3f}' in text


def test_the_newest_file_of_each_system_and_no_other_file(tmp_path):
    write(tmp_path, f"prowler/aws/account-one.{stamp(90000)}.ocsf.json", prowler_text([record("old")]))
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("new")]))
    # A time in the future, a half copy in tmp/, a system of another provider, a name that
    # does not fit, a system not in the registry, and a link: none of them is read.
    write(tmp_path, f"prowler/aws/account-one.{stamp(-600)}.ocsf.json", prowler_text([record("future")]))
    write(tmp_path, f"prowler/aws/tmp/account-one.{stamp(60)}.ocsf.json.partial", "[")
    write(tmp_path, f"prowler/aws/org-one.{stamp(60)}.ocsf.json", prowler_text([record("other")]))
    write(tmp_path, f"prowler/aws/account-one.{stamp(60)}.json", prowler_text([record("other")]))
    write(tmp_path, f"prowler/aws/account-nine.{stamp(60)}.ocsf.json", prowler_text([record("other")]))
    os.symlink(tmp_path / "elsewhere.json", tmp_path / f"prowler/aws/account-two.{stamp(60)}.ocsf.json")
    write(tmp_path, f"prowler/github/org-one.{stamp(7200)}.ocsf.json", prowler_text([record("gh", "PASS")]))
    part = files_for(tmp_path)
    part.cycle()
    assert results(part) == {("prowler", "new", "account-one"): (1, "high"),
                             ("prowler", "gh", "org-one"): (0, "high")}
    assert times(part) == {("prowler", "account-one"): NOW - 3600, ("prowler", "org-one"): NOW - 7200}
    assert skipped(part) == {"prowler": 0, "api-check": 0}


def test_a_file_within_five_minutes_in_the_future_is_read(tmp_path):
    write(tmp_path, f"prowler/aws/account-one.{stamp(-240)}.ocsf.json", prowler_text([record("a")]))
    part = files_for(tmp_path)
    part.cycle()
    assert times(part) == {("prowler", "account-one"): NOW + 240}


def test_with_no_volume_and_no_file_nothing_is_served(tmp_path):
    part = files_for(tmp_path / "not-mounted")
    part.cycle()
    assert results(part) == {} and times(part) == {}
    assert skipped(part) == {"prowler": 0, "api-check": 0}


BAD = {
    "half written": prowler_text(RECORDS)[:-40],
    "empty": "",
    "not an array": json.dumps(record("a")),
    "an item that is not an object": "[1, 2]",
    "a record with no check": prowler_text([{"status_code": "FAIL"}]),
    "a record with no status": prowler_text([{"metadata": {"event_code": "a"}}]),
    "no comma": "[" + json.dumps(record("a")) + " " + json.dumps(record("b")) + "]",
    "a comma at the end": "[" + json.dumps(record("a")) + ",]",
    "text after the array": prowler_text([record("a")]) + "x",
    "not UTF-8": b"[\xff]",
}


@pytest.mark.parametrize("content", BAD.values(), ids=BAD.keys())
def test_a_bad_newest_file_gives_no_result_and_no_time_for_its_system_and_is_counted(tmp_path, content, caplog):
    write(tmp_path, f"prowler/aws/account-one.{stamp(90000)}.ocsf.json", prowler_text([record("old")]))
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", content)
    write(tmp_path, f"prowler/aws/account-two.{stamp(3600)}.ocsf.json", prowler_text([record("good")]))
    part = files_for(tmp_path)
    part.cycle()
    # No older file in its place: the system has no time, so the late rule fires.
    assert results(part) == {("prowler", "good", "account-two"): (1, "high")}
    assert times(part) == {("prowler", "account-two"): NOW - 3600}
    assert skipped(part) == {"prowler": 1, "api-check": 0}
    assert f"prowler/aws/account-one.{stamp(3600)}.ocsf.json is skipped" in caplog.text
    assert "invented" not in caplog.text, "no value of a file in a log"


def test_a_file_larger_than_a_limit_is_skipped(tmp_path, monkeypatch):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("a")] * 3))
    monkeypatch.setattr(converter, "MAX_OCSF_BYTES", 100)
    part = files_for(tmp_path)
    part.cycle()
    assert times(part) == {} and skipped(part)["prowler"] == 1


def test_an_item_larger_than_the_limit_is_a_bad_file():
    text = prowler_text([record("a", status_detail="x" * 5000)])
    with pytest.raises(converter.BadFile):
        list(converter.json_array(_Stream(text), chunk=512, limit=2000))
    assert len(list(converter.json_array(_Stream(text), chunk=512, limit=8000))) == 1


class _Stream:
    def __init__(self, text):
        self.text, self.at = text, 0

    def read(self, size):
        part = self.text[self.at:self.at + size]
        self.at += size
        return part


@pytest.mark.parametrize("chunk", [1, 7, 64, 65536])
def test_the_array_is_read_one_item_at_a_time_in_each_size_of_chunk(chunk):
    items = [record(f"check_{index}") for index in range(20)]
    assert list(converter.json_array(_Stream(" \n" + prowler_text(items) + "\n"), chunk=chunk)) == items
    assert list(converter.json_array(_Stream("[ ]"), chunk=chunk)) == []


def test_a_large_file_is_read_with_little_memory(tmp_path):
    """The converter keeps one record in memory, not the file: its limit is 128 MiB."""
    items = [record(f"check_{index % 300}", resource=f"r{index}", unmapped={"compliance": {"x": ["y"] * 200}})
             for index in range(4000)]
    path = write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text(items))
    assert path.stat().st_size > 12 * 1024 * 1024
    part = files_for(tmp_path)
    tracemalloc.start()
    part.cycle()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(results(part)) == 300
    assert peak < 4 * 1024 * 1024, f"{peak / 2**20:.1f} MiB"


def test_a_file_is_read_again_only_when_it_changes(tmp_path, monkeypatch):
    path = write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("a")]))
    calls = []
    real = converter.read_ocsf
    monkeypatch.setattr(converter, "read_ocsf", lambda file: calls.append(file) or real(file))
    part = files_for(tmp_path)
    part.cycle()
    part.cycle()
    assert len(calls) == 1
    path.write_text(prowler_text([record("a"), record("b")]))
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 10**9))
    part.cycle()
    assert len(calls) == 2 and len(results(part)) == 2


def test_a_result_that_no_cycle_replaced_in_two_intervals_goes_away(tmp_path):
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("a")]))
    clock = Clock()
    part = files_for(tmp_path, clock=clock)
    part.cycle()
    assert times(part)
    clock.elapsed = 2 * converter.INTERVAL_SECONDS + 1
    assert times(part) == {} and results(part) == {}


def test_a_file_that_goes_away_gives_no_result_in_the_next_cycle(tmp_path):
    path = write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("a")]))
    part = files_for(tmp_path)
    part.cycle()
    path.unlink()
    part.cycle()
    assert times(part) == {} and results(part) == {}


# --- The result file of the API checks --------------------------------------------------

README_EXAMPLE = re.search(r"### The result file of the API checks.*?```json\n(.*?)```",
                           (ROOT / "security/converter/README.md").read_text(), re.DOTALL)


def test_the_example_of_the_readme_is_read(tmp_path):
    assert README_EXAMPLE
    payload = json.loads(README_EXAMPLE[1])
    payload["time"] = NOW - 7200
    write(tmp_path, "api-check/results.json", json.dumps(payload))
    part = files_for(tmp_path)
    part.cycle()
    expected = {("api-check", entry["check"], entry["system"]): (entry["failed"], entry.get("severity", "unknown"))
                for entry in payload["results"]}
    assert results(part) == expected and len(expected) >= 2
    assert times(part) == {("api-check", system): NOW - 7200 for _, _, system in expected}


def api(*entries, moment=NOW - 600, **more):
    return json.dumps({"version": 1, "source": "api-check", "time": moment, "results": list(entries), **more})


def test_a_system_that_is_not_in_the_registry_has_no_result_and_no_time(tmp_path):
    write(tmp_path, "api-check/results.json", api({"check": "a", "system": "example-system", "failed": 0},
                                                  {"check": "a", "system": "not-registered", "failed": 3}))
    part = files_for(tmp_path)
    part.cycle()
    assert results(part) == {("api-check", "a", "example-system"): (0, "unknown")}
    assert times(part) == {("api-check", "example-system"): NOW - 600}


API_BAD = {
    "half written": api({"check": "a", "system": "example-system", "failed": 0})[:-5],
    "another version": api(version=2),
    "another source": api(source="prowler"),
    "no time": api(moment=None),
    "a time in the future": api(moment=NOW + 3600),
    "a count that is not a number": api({"check": "a", "system": "example-system", "failed": True}),
    "a count below 0": api({"check": "a", "system": "example-system", "failed": -1}),
    "no check": api({"system": "example-system", "failed": 0}),
    "two results of one check": api({"check": "a", "system": "example-system", "failed": 0},
                                    {"check": "a", "system": "example-system", "failed": 1}),
    "a severity of no list": api({"check": "a", "system": "example-system", "failed": 0, "severity": "High"}),
    "findings of another form": api({"check": "a", "system": "example-system", "failed": 1,
                                     "findings": [{"resource": "x"}]}),
}


@pytest.mark.parametrize("content", API_BAD.values(), ids=API_BAD.keys())
def test_a_result_file_that_does_not_fit_gives_no_result_and_no_time(tmp_path, content):
    write(tmp_path, "api-check/results.json", content)
    write(tmp_path, f"prowler/aws/account-one.{stamp(3600)}.ocsf.json", prowler_text([record("a")]))
    part = files_for(tmp_path)
    part.cycle()
    assert {key for key in results(part) if key[0] == "api-check"} == set()
    assert times(part) == {("prowler", "account-one"): NOW - 3600}, "the other source goes on"
    assert skipped(part) == {"prowler": 0, "api-check": 1}


def test_a_result_with_a_severity_has_that_label(tmp_path):
    write(tmp_path, "api-check/results.json",
          api({"check": "a", "system": "example-router", "failed": 1, "severity": "high"}))
    part = files_for(tmp_path)
    part.cycle()
    assert results(part) == {("api-check", "a", "example-router"): (1, "high")}


# --- The map of the registry ------------------------------------------------------------


def test_the_providers_of_the_converter_are_the_providers_of_the_scan_lists():
    registry = generate.load()
    names = json.loads((generate.CONVERTER_CONFIG / "registry.json").read_text())
    assert names == generate.converter_registry(registry)
    assert set(names["prowler"]) == set(generate.scan_lists(registry)["prowler"])
    assert names["series"] == {"result": RESULT, "last_result": LAST, "accepted": generate.ACCEPTED}
    for provider, systems in names["prowler"].items():
        assert systems, provider
        for system in systems:
            assert converter.OCSF_NAME.match(f"{system}.20261113T051000Z.ocsf.json")["system"] == system
