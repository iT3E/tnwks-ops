"""The list of accepted findings in the converter keeps the promises of
security/converter/README.md: it serves the end of each accepted finding with the three
labels of the contract, and nothing else of the list. Each list here is invented."""

import calendar
import datetime
import json
import re
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
import converter
import generate

ROOT = Path(__file__).resolve().parents[2]
SERIES = generate.ACCEPTED
REGISTRY = {"series": {"accepted": SERIES}, "sources": ["prowler", "trivy", "api-check"],
            "systems": ["system-one", "system-two"]}


def end(day):
    """The start of the next day, UTC, counted in days: a date has no day after 9999-12-31."""
    return ((datetime.date.fromisoformat(day) - datetime.date(1970, 1, 1)).days + 1) * 86400


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def time(self):
        return 1_800_000_000.0

    def monotonic(self):
        return self.elapsed


def accepted_for(tmp_path, text, clock=None):
    path = tmp_path / "accepted.json"
    if text is not None:
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
    clock = clock or Clock()
    return converter.AcceptedList(path, REGISTRY, clock=clock.time, monotonic=clock.monotonic)


def served(text, name):
    found = {}
    for line in text.splitlines():
        if line.startswith((name + "{", name + " ")):
            labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', line))
            found[tuple(sorted(labels.items()))] = float(line.rsplit(" ", 1)[1])
    return found


def entry(**changes):
    """One invented entry that fits, with `changes`; a value of None takes the key away."""
    item = {"source": "prowler", "check": "check_a", "system": "system-one", "until": "2027-01-31",
            "reason": "An invented reason."} | changes
    return {key: value for key, value in item.items() if value is not None}


def read(items_or_text):
    text = items_or_text if isinstance(items_or_text, str) else json.dumps(items_or_text)
    return converter.accepted_entries(text, set(REGISTRY["sources"]), set(REGISTRY["systems"]))


# --- The reader: one JSON array, read with json.loads -----------------------------------

README_EXAMPLE = re.search(r"  accepted\.json: \|\n((?:    .*\n)+)", (ROOT / "security/README.md").read_text())


def test_the_example_of_the_readme_is_read():
    text = "\n".join(line[4:] for line in README_EXAMPLE[1].splitlines())
    result, skipped = converter.accepted_entries(text, {"prowler"}, {"example-system"})
    assert result == {("prowler", "example_check_id", "example-system"): end("2027-01-31")} and skipped == 0


def test_an_entry_that_fits_ends_at_the_start_of_the_next_day():
    assert read([entry()]) == ({("prowler", "check_a", "system-one"): end("2027-01-31")}, 0)
    assert converter.end_of("2027-12-31") == calendar.timegm((2028, 1, 1, 0, 0, 0))


def test_two_entries_of_one_finding_keep_the_later_end():
    assert read([entry(until="2027-03-01"), entry(until="2027-01-01")]) == (
        {("prowler", "check_a", "system-one"): end("2027-03-01")}, 0)


def test_an_empty_array_gives_no_entry_and_is_read(tmp_path):
    assert read("[]") == ({}, 0)
    source = accepted_for(tmp_path, " [ ]\n")
    source.cycle()
    assert served(source.render().decode(), converter.ACCEPTED_READ) == {(): 1.0}


NOT_READ = {
    "a comma at the end": '[{"source": "prowler"},]',
    "a comment": '// an invented comment\n[]',
    "a byte order mark": "﻿[]",
    "a key two times": '[{"source": "prowler", "check": "check_a", "system": "system-one", "until": "2027-01-31",'
                       ' "reason": "An invented reason.", "check": "check_b"}]',
    "an object at the top": json.dumps(entry()),
    "a YAML list": "- source: prowler\n  check: check_a\n",
    "two arrays": "[] []",
    "an array that does not end": json.dumps([entry()])[:-1],
    "an empty file": "",
    "arrays in arrays too deep for the decoder": "[" * 100_000 + "]" * 100_000,
}


@pytest.mark.parametrize("text", NOT_READ.values(), ids=NOT_READ.keys())
def test_a_list_that_is_not_one_json_array_gives_no_entry(tmp_path, caplog, text):
    with pytest.raises(converter.Unreadable):
        read(text)
    source = accepted_for(tmp_path, text)
    with caplog.at_level("DEBUG"):
        source.cycle()
    rendered = source.render().decode()
    assert served(rendered, SERIES) == {}
    assert served(rendered, converter.ACCEPTED_READ) == {(): 0.0}
    assert "gives no entry" in caplog.text and "invented" not in caplog.text and "check_b" not in caplog.text


SKIPPED = {
    "not an object": ["check_a"],
    "a nested array": [[entry()]],
    "a field missing": entry(reason=None),
    "a key that is not a field": entry(note="An invented note."),
    "a number": entry(check=26001),
    "null": entry(reason=None) | {"reason": None},
    "true": entry(reason=True),
    "NaN": '{"source": "prowler", "check": "check_a", "system": "system-one", "until": "2027-01-31", "reason": NaN}',
    "Infinity": '{"source": "prowler", "check": "check_a", "system": "system-one", "until": Infinity,'
                ' "reason": "An invented reason."}',
    "an empty reason": entry(reason=""),
    "a reason of space": entry(reason=" \t\n "),
    "an empty check": entry(check=""),
    "a source not in the registry": entry(source="not-a-source"),
    "a system not in the registry": entry(system="not-in-the-registry"),
    "a check that is not a check ID": entry(check="a b"),
    "a long check": entry(check="a" * 201),
    "a lone surrogate escape in the check": '{"source": "prowler", "check": "check_\\ud800", "system": "system-one",'
                                            ' "until": "2027-01-31", "reason": "An invented reason."}',
    "a date that is no day": entry(until="2027-02-30"),
    "a date that is not a date": entry(until="soon"),
    "a date with a time": entry(until="2027-01-31T10:00:00"),
    "a date without dashes": entry(until="20271231"),
    "a date of a week": entry(until="2027-W05-1"),
    "a date with a line end": entry(until="2027-01-31\n"),
    "a date of other digits": entry(until="\u0662\u0660\u0662\u0667-\u0660\u0661-\u0663\u0661"),
    "a date with space": entry(until=" 2027-01-31"),
    "a year 0": entry(until="0000-01-01"),
    "a day 29 of a year with no leap day": entry(until="2027-02-29"),
    "a check that ends in a line feed": entry(check="check_a\n"),
}


@pytest.mark.parametrize("item", SKIPPED.values(), ids=SKIPPED.keys())
def test_an_item_that_does_not_fit_is_skipped_and_counted(item):
    text = "[" + json.dumps(entry(check="good")) + ", " + (item if isinstance(item, str) else json.dumps(item)) + "]"
    result, skipped = read(text)
    assert result == {("prowler", "good", "system-one"): end("2027-01-31")} and skipped == 1


@pytest.mark.parametrize("day, ends", [
    ("9999-12-31", 253_402_300_800), ("0001-01-01", -62_135_510_400), ("2028-02-29", 1_835_481_600),
    ("1970-01-01", 86_400)])
def test_each_day_that_a_date_can_hold_ends_at_the_start_of_the_next_day(tmp_path, day, ends):
    """The day is added in seconds: 9999-12-31 has no next date, and once ended the whole
    list (OverflowError) with no series to alert on."""
    assert converter.end_of(day) == end(day) == ends
    source = accepted_for(tmp_path, json.dumps([entry(until=day), entry(check="check_b")]))
    source.cycle()
    text = source.render().decode()
    assert served(text, SERIES) == {
        (("check", "check_a"), ("source", "prowler"), ("system", "system-one")): float(ends),
        (("check", "check_b"), ("source", "prowler"), ("system", "system-one")): float(end("2027-01-31"))}
    assert served(text, converter.ACCEPTED_READ) == {(): 1.0}


def test_a_value_that_is_not_a_string_is_skipped_also_when_it_is_nan_or_infinity():
    """json.loads reads NaN and Infinity as numbers, as the Prowler job does: their entry
    has a value that is not a string, so it is skipped and the file stays readable."""
    text = '[{"source": "prowler", "check": "check_a", "system": "system-one", "until": "2027-01-31", "reason": NaN}]'
    assert read(text) == ({}, 1)
    assert read(text.replace("NaN", "-Infinity")) == ({}, 1)


@pytest.mark.parametrize("reason", [
    "An invented reason. " * 2000, "Ein erfundener Grund, café, 日本語の理由.", 'With "quotes", a \\ and a\nnew line.',
    "\ud800 a lone surrogate in an invented reason", "x"])
def test_a_reason_of_each_text_counts_and_stays_in_the_file(tmp_path, reason):
    items = [entry(reason=reason)]
    for text in (json.dumps(items), json.dumps(items, ensure_ascii=False)):
        if "\ud800" in reason and "\ud800" in text:
            continue  # a lone surrogate can not be UTF-8: a list holds it as an escape only
        assert read(text) == ({("prowler", "check_a", "system-one"): end("2027-01-31")}, 0)
    source = accepted_for(tmp_path, json.dumps(items))
    source.cycle()
    rendered = source.render().decode()
    assert len(served(rendered, SERIES)) == 1
    assert "invented" not in rendered and "café" not in rendered


def test_a_list_larger_than_the_limit_gives_no_entry(tmp_path):
    source = accepted_for(tmp_path, json.dumps([entry(reason="x" * converter.MAX_LIST_BYTES)]))
    source.cycle()
    assert served(source.render().decode(), converter.ACCEPTED_READ) == {(): 0.0}


# --- What the converter serves ----------------------------------------------------------

LIST = json.dumps([
    entry(check="example_check_id", reason="An invented reason that names-a-resource."),
    entry(source="trivy", check="k8s-cis-1.23/1.2.3", system="system-two", reason="Another invented reason.",
          until="2026-11-01"),
    entry(check="other", system="not-in-the-registry"),
], indent=2)


def test_the_end_of_each_accepted_finding_with_the_three_labels_and_no_reason(tmp_path, caplog):
    source = accepted_for(tmp_path, LIST)
    with caplog.at_level("DEBUG"):
        source.cycle()
    text = source.render().decode()
    assert served(text, SERIES) == {
        (("check", "example_check_id"), ("source", "prowler"), ("system", "system-one")): end("2027-01-31"),
        (("check", "k8s-cis-1.23/1.2.3"), ("source", "trivy"), ("system", "system-two")): end("2026-11-01"),
    }
    assert f'{SERIES}{{check="example_check_id",source="prowler",system="system-one"}} {end("2027-01-31")}' in text
    assert served(text, converter.ACCEPTED_SKIPPED) == {(): 1.0}
    # No reason, and no value of the list in a log.
    assert "reason" not in text and "invented" not in text and "names-a-resource" not in text
    assert "invented" not in caplog.text and "not-in-the-registry" not in caplog.text
    assert "entry 3 of the list of accepted findings is skipped" in caplog.text


def test_with_no_secret_the_list_is_empty(tmp_path):
    source = accepted_for(tmp_path, None)
    source.cycle()
    text = source.render().decode()
    assert served(text, SERIES) == {}
    assert served(text, converter.ACCEPTED_SKIPPED) == {(): 0.0}
    assert served(text, converter.ACCEPTED_READ) == {(): 1.0}


@pytest.mark.parametrize("raw", [b"[\xff]", b"[" + b"[]," * 400_000 + b"[]]"])
def test_a_file_that_can_not_be_read_serves_no_entry_and_is_counted(tmp_path, raw):
    source = accepted_for(tmp_path, raw)
    source.cycle()
    text = source.render().decode()
    assert served(text, SERIES) == {}
    assert served(text, converter.ACCEPTED_READ) == {(): 0.0}


@pytest.mark.parametrize("fault", [OverflowError, RuntimeError, KeyError, MemoryError])
def test_a_fault_of_the_reader_is_a_list_not_read_never_no_list(tmp_path, monkeypatch, caplog, fault):
    """Each fault in the reader serves read 0, so SecurityAcceptedListUnread
    (read == 0) fires. Without a series it would stay quiet."""
    def broken(*_):
        raise fault("an invented fault")
    monkeypatch.setattr(converter, "accepted_entries", broken)
    clock = Clock()
    whole = converter.Converter([accepted_for(tmp_path, LIST, clock=clock)], clock=clock.time,
                                monotonic=clock.monotonic)
    whole.cycle()
    text = whole.render().decode()
    assert served(text, SERIES) == {}
    assert served(text, converter.ACCEPTED_READ) == {(): 0.0}
    assert served(text, converter.ACCEPTED_SKIPPED) == {(): 0.0}
    assert "an invented fault" not in caplog.text and fault.__name__ in caplog.text


def test_a_list_in_the_old_key_only_is_a_list_not_read(tmp_path, caplog):
    """A Secret with accepted.yaml and no accepted.json: no reader reads it, so it must
    alert, not look like no list."""
    (tmp_path / "accepted.yaml").write_text("- source: prowler\n")
    source = accepted_for(tmp_path, None)
    source.cycle()
    assert served(source.render().decode(), converter.ACCEPTED_READ) == {(): 0.0}
    assert "accepted.yaml" in caplog.text
    (tmp_path / "accepted.json").write_text(LIST)
    source.cycle()
    text = source.render().decode()
    assert served(text, converter.ACCEPTED_READ) == {(): 1.0} and len(served(text, SERIES)) == 2


def test_a_new_list_counts_from_the_next_cycle_and_an_old_result_goes_away(tmp_path):
    clock = Clock()
    source = accepted_for(tmp_path, LIST, clock=clock)
    source.cycle()
    assert len(served(source.render().decode(), SERIES)) == 2
    (tmp_path / "accepted.json").write_text(json.dumps(json.loads(LIST)[:1]))
    source.cycle()
    assert len(served(source.render().decode(), SERIES)) == 1
    clock.elapsed = 2 * converter.INTERVAL_SECONDS + 1
    assert SERIES not in source.render().decode(), "a loop that stopped serves no old list"


# --- The parts in one loop --------------------------------------------------------------


class Broken(converter.Loop):
    name = "broken"

    def step(self):
        raise RuntimeError("a fault")

    def collect(self, metrics, now):
        pass


def test_a_fault_of_one_part_does_not_stop_the_others(tmp_path):
    clock = Clock()
    accepted = accepted_for(tmp_path, LIST, clock=clock)
    whole = converter.Converter([Broken(), accepted], clock=clock.time, monotonic=clock.monotonic)
    whole.cycle()
    text = whole.render().decode()
    assert len(served(text, SERIES)) == 2
    assert text.count(converter.LAST_CYCLE) == 1
    assert whole.healthy()


class FailsLater(converter.AcceptedList):
    name = "fails later"
    fail = False

    def step(self):
        if self.fail:
            raise RuntimeError("a fault")
        super().step()


def test_a_part_whose_cycle_failed_serves_nothing_of_an_older_cycle(tmp_path):
    clock = Clock()
    path = tmp_path / "accepted.json"
    path.write_text(LIST)
    part = FailsLater(path, REGISTRY, clock=clock.time, monotonic=clock.monotonic)
    whole = converter.Converter([part], clock=clock.time, monotonic=clock.monotonic)
    whole.cycle()
    assert len(served(whole.render().decode(), SERIES)) == 2
    part.fail = True
    whole.cycle()
    assert served(whole.render().decode(), SERIES) == {}


def test_the_lines_of_one_series_stay_together():
    metrics = converter.Metrics()
    metrics.add("a", 1, x="1")
    metrics.add("b", 2)
    metrics.add("a", 3, x="2")
    assert metrics.render().decode().splitlines() == ['a{x="1"} 1', 'a{x="2"} 3', "b 2"]


def test_the_service_serves_the_list(tmp_path):
    clock = Clock()
    whole = converter.Converter([accepted_for(tmp_path, LIST, clock=clock)], clock=clock.time,
                                monotonic=clock.monotonic)
    whole.cycle()
    server = ThreadingHTTPServer(("127.0.0.1", 0), converter.handler(whole))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(url + "/metrics", timeout=5) as response:
            assert len(served(response.read().decode(), SERIES)) == 2
        with urllib.request.urlopen(url + "/healthz", timeout=5) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()


# --- The names of the registry ----------------------------------------------------------


def test_the_converter_reads_the_names_of_the_registry_in_git():
    registry = generate.load()
    names = json.loads((generate.CONVERTER_CONFIG / "registry.json").read_text())
    assert names == generate.converter_registry(registry)
    assert names["series"]["accepted"] == SERIES
    assert set(names["systems"]) == {system["name"] for system in registry["systems"]}
    assert set(names["sources"]) == {source["name"] for source in registry["sources"]}
