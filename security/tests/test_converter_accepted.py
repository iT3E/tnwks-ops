"""The list of accepted findings in the converter keeps the promises of
security/converter/README.md: it serves the end of each accepted finding with the three
labels of the contract, and nothing else of the list. Each list here is invented."""

import calendar
import datetime
import json
import re
import sys
import textwrap
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
import converter
import generate

ROOT = Path(__file__).resolve().parents[2]
SERIES = generate.ACCEPTED
REGISTRY = {"series": {"accepted": SERIES}, "sources": ["prowler", "trivy", "api-check"],
            "systems": ["system-one", "system-two"]}


def end(day):
    return calendar.timegm((datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).timetuple())


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def time(self):
        return 1_800_000_000.0

    def monotonic(self):
        return self.elapsed


def accepted_for(tmp_path, text, clock=None):
    path = tmp_path / "accepted.yaml"
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


def as_pyyaml(text):
    """What PyYAML reads, with each value as text: the form that the scanners read."""
    return [{key: None if value is None else str(value) for key, value in entry.items()}
            for entry in yaml.safe_load(text) or []]


# --- The reader takes the block form, as PyYAML reads it --------------------------------

README_EXAMPLE = re.search(r"  accepted\.yaml: \|\n((?:    .*\n)+)", (ROOT / "security/README.md").read_text())

TAKEN = [
    textwrap.dedent(README_EXAMPLE[1]) if README_EXAMPLE else "",
    """\
---
# Invented entries.
- source: prowler
  check: example_check_id   # a comment
  system: system-one
  reason: 'An invented reason, with a quote: ''here''.'
  until: 2027-01-31

- source: "trivy"
  check: "k8s-cis-1.23/1.2.3"
  system: system-two
  reason: >
    An invented reason
    on two lines.
  until: "2027-02-28"
-
  source: api-check
  check: 26001
  system: system-one
  reason: |
    An invented reason.
  until: '2027-03-01'
""",
    """\
  - source: prowler
    check: other_check
    system: system-two
    reason: An invented reason
      that goes on.
    until: "2027-01-01"
""",
]


@pytest.mark.parametrize("text", TAKEN)
def test_the_reader_takes_the_block_form_as_pyyaml_reads_it(text):
    assert text, "the README has the example of the list"
    entries, skipped = converter.yaml_list(text)
    expected = as_pyyaml(text)
    assert skipped == 0
    assert [{key: value for key, value in entry.items() if key != "reason"} for entry in entries] == \
        [{key: value for key, value in entry.items() if key != "reason"} for entry in expected]


NOT_TAKEN = {
    "a flow mapping": "- {source: prowler, check: a, system: system-one, until: 2027-01-01}\n",
    "an anchor": "- source: &s prowler\n  check: a\n  system: system-one\n  until: 2027-01-01\n",
    "a tag": "- source: !!str prowler\n  check: a\n  system: system-one\n  until: 2027-01-01\n",
    "a tab": "- source: prowler\n\tcheck: a\n  system: system-one\n  until: 2027-01-01\n",
    "a key that is there two times": "- source: prowler\n  source: trivy\n  check: a\n  system: system-one\n"
                                     "  until: 2027-01-01\n",
    "a line at a column of no key": "- source: prowler\n check: a\n  system: system-one\n  until: 2027-01-01\n",
    "a quote that does not end": "- source: prowler\n  check: \"a\n  system: system-one\n  until: 2027-01-01\n",
}


@pytest.mark.parametrize("text", NOT_TAKEN.values(), ids=NOT_TAKEN.keys())
def test_an_entry_of_another_form_is_not_taken_and_is_counted(text):
    good = "- source: prowler\n  check: good\n  system: system-two\n  until: 2027-01-01\n"
    entries, skipped = converter.yaml_list(good + text)
    assert [entry["check"] for entry in entries] == ["good"]
    assert skipped == 1


def test_a_list_that_is_not_a_list_is_counted():
    entries, skipped = converter.yaml_list("accepted:\n  source: prowler\n")
    assert entries == [] and skipped == 2


# --- The entries ------------------------------------------------------------------------


def test_the_end_is_the_start_of_the_day_after_the_end_date():
    assert converter.end_of("2027-01-31") == calendar.timegm((2027, 2, 1, 0, 0, 0))
    assert converter.end_of("2027-12-31") == calendar.timegm((2028, 1, 1, 0, 0, 0))


@pytest.mark.parametrize("entry", [
    "- check: a\n  system: system-one\n  until: 2027-01-01\n",
    "- source: prowler\n  check: ''\n  system: system-one\n  until: 2027-01-01\n",
    "- source: prowler\n  check: a\n  system: system-one\n  until: soon\n",
    "- source: prowler\n  check: a\n  system: system-one\n  until: 2027-02-30\n",
    "- source: prowler\n  check: a\n  system: not-in-the-registry\n  until: 2027-01-01\n",
    "- source: not-a-source\n  check: a\n  system: system-one\n  until: 2027-01-01\n",
    "- source: prowler\n  check: >\n    a\n  system: system-one\n  until: 2027-01-01\n",
    "- source: prowler\n  check: a\n    b\n  system: system-one\n  until: 2027-01-01\n",
    "- source: prowler\n  check: " + "a" * 201 + "\n  system: system-one\n  until: 2027-01-01\n",
], ids=["no source", "an empty check", "no date", "a day that is not", "a system not in the registry",
        "a source not in the registry", "a check in a block", "a check on two lines", "a long check"])
def test_an_entry_that_does_not_fit_is_skipped_and_counted(entry):
    good = "- source: trivy\n  check: good\n  system: system-two\n  until: 2027-01-01\n"
    result, skipped = converter.accepted_entries(good + entry, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
    assert result == {("trivy", "good", "system-two"): end("2027-01-01")}
    assert skipped == 1


def test_two_entries_of_one_finding_keep_the_later_end():
    text = ("- source: prowler\n  check: a\n  system: system-one\n  until: 2027-03-01\n"
            "- source: prowler\n  check: a\n  system: system-one\n  until: 2027-01-01\n")
    result, skipped = converter.accepted_entries(text, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
    assert result == {("prowler", "a", "system-one"): end("2027-03-01")} and skipped == 0


# --- What the converter serves ----------------------------------------------------------

LIST = """\
- source: prowler
  check: example_check_id
  system: system-one
  reason: An invented reason that names-a-resource.
  until: "2027-01-31"
- source: trivy
  check: k8s-cis-1.23/1.2.3
  system: system-two
  reason: Another invented reason.
  until: "2026-11-01"
- source: prowler
  check: other
  system: not-in-the-registry
  reason: An invented reason.
  until: "2027-01-31"
"""


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


@pytest.mark.parametrize("raw", [b"\xff\xfe- source: prowler\n", b"- a: b\n" * 200_000])
def test_a_file_that_can_not_be_read_serves_no_entry_and_is_counted(tmp_path, raw):
    source = accepted_for(tmp_path, raw)
    source.cycle()
    text = source.render().decode()
    assert served(text, SERIES) == {}
    assert served(text, converter.ACCEPTED_SKIPPED) == {(): 1.0}


def test_a_new_list_counts_from_the_next_cycle_and_an_old_result_goes_away(tmp_path):
    clock = Clock()
    source = accepted_for(tmp_path, LIST, clock=clock)
    source.cycle()
    assert len(served(source.render().decode(), SERIES)) == 2
    (tmp_path / "accepted.yaml").write_text(LIST.split("- source: trivy")[0])
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
    path = tmp_path / "accepted.yaml"
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
