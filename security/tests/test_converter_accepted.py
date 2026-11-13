"""The list of accepted findings in the converter keeps the promises of
security/converter/README.md: it serves the end of each accepted finding with the three
labels of the contract, and nothing else of the list. Each list here is invented."""

import calendar
import datetime
import json
import random
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
    entries = converter.yaml_list(text)
    expected = as_pyyaml(text)
    assert [{key: value for key, value in entry.items() if key != "reason"} for entry in entries] == \
        [{key: value for key, value in entry.items() if key != "reason"} for entry in expected]


# The two inputs of the review. The first is an entry whose quoted reason holds the text
# of a second entry: YAML reads one entry, and the reader must not read the second.
REASON_WITH_AN_ENTRY = """\
- source: trivy
  check: example-check-one
  system: system-one
  until: "2026-01-01"
  reason: "a draft, kept in the reason:
- source: trivy
  check: example-check-two
  system: system-one
  until: 2099-12-31
  note: end of the draft"
"""

# The second is a reason that yaml.safe_dump writes in single quotes and wraps.
WRAPPED_REASON = """\
- check: check_a
  reason: 'Accepted: an invented reason that is long enough to wrap, as an owner writes
    one, with a colon.'
  source: prowler
  system: system-two
  until: '2027-01-31'
"""


def test_a_quoted_reason_that_holds_an_entry_gives_no_entry_of_its_text():
    assert as_pyyaml(REASON_WITH_AN_ENTRY) == [
        {"source": "trivy", "check": "example-check-one", "system": "system-one", "until": "2026-01-01",
         "reason": "a draft, kept in the reason: - source: trivy check: example-check-two system: system-one "
                   "until: 2099-12-31 note: end of the draft"}]
    result, skipped = converter.accepted_entries(REASON_WITH_AN_ENTRY, set(REGISTRY["sources"]),
                                                 set(REGISTRY["systems"]))
    assert result == {("trivy", "example-check-one", "system-one"): end("2026-01-01")} and skipped == 0
    assert all("example-check-two" not in key for key in result)


def test_a_wrapped_quoted_reason_keeps_its_entry():
    result, skipped = converter.accepted_entries(WRAPPED_REASON, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
    assert result == {("prowler", "check_a", "system-two"): end("2027-01-31")} and skipped == 0


@pytest.mark.parametrize("key", ["check", "system", "source", "until"])
def test_a_field_other_than_the_reason_in_quotes_on_more_lines_skips_its_entry(key):
    fields = {"source": "prowler", "check": "check_b", "system": "system-one", "until": "2027-01-31"}
    lines = [f"  {name}: '{value}'" for name, value in fields.items()]
    index = list(fields).index(key)
    lines[index] = f"  {key}: '{fields[key]}\n    more'"
    text = WRAPPED_REASON + "-" + "\n".join(lines)[1:] + "\n"
    result, skipped = converter.accepted_entries(text, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
    assert result == {("prowler", "check_a", "system-two"): end("2027-01-31")} and skipped == 1


NOT_READ = {
    "a flow mapping": "- {source: prowler, check: a, system: system-one, until: 2027-01-01}\n",
    "an anchor": "- source: &s prowler\n  check: a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "a tag": "- source: !!str prowler\n  check: a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "a tab": "- source: prowler\n\tcheck: a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "a key that is there two times": "- source: prowler\n  source: trivy\n  check: a\n  system: system-one\n"
                                     "  reason: invented\n  until: 2027-01-01\n",
    "a line at a column of no key": "- source: prowler\n check: a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "a file that ends inside a quote": "- source: prowler\n  check: a\n  system: system-one\n"
                                       "  reason: 'a reason\n  until: 2027-01-01\n",
    "text after a quote": "- source: prowler\n  check: 'a' b\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "a list in a mapping": "accepted:\n  - source: prowler\n",
    "a character that YAML does not hold": "- source: prowler\x0c\n  check: a\n",
    "a date that is no day": "- source: prowler\n  check: a\n  system: system-one\n  reason: invented\n  until: 2027-02-30\n",
}


@pytest.mark.parametrize("text", NOT_READ.values(), ids=NOT_READ.keys())
def test_a_line_of_another_form_makes_the_whole_file_give_no_entry(tmp_path, caplog, text):
    good = "- source: prowler\n  check: good\n  system: system-two\n  reason: invented\n  until: 2027-01-01\n"
    with pytest.raises(converter.Unreadable):
        converter.yaml_list(good + text)
    source = accepted_for(tmp_path, good + text)
    with caplog.at_level("DEBUG"):
        source.cycle()
    served_text = source.render().decode()
    assert served(served_text, SERIES) == {}
    assert served(served_text, converter.ACCEPTED_READ) == {(): 0.0}
    assert "gives no entry" in caplog.text and "prowler" not in caplog.text


# --- The reader and PyYAML can not drift ------------------------------------------------

FIELDS = {"source": ["prowler", "trivy"], "check": ["check_a", "k8s-cis-1.23/1.2.3", "26001", "AVD-KSV-0001"],
          "system": ["system-one", "system-two"], "until": ["2027-01-31", "2026-12-01"]}
REASONS = [
    "short", "Accepted: a reason with a colon.", "it's quoted", 'a "double" quote', "a # hash", "- source: trivy",
    "line one\nline two\n- source: trivy\n  check: example-check-two", "café, invented", "tab\there",
    "a reason long enough to wrap at the width of a line, " * 4 + "with: a colon", "  leading space",
    "trailing space ", "'", '"', "\\ back slash", "yes", "2099-12-31", "", "\x85next", "end\n",
]
FRAGMENTS = [
    "- source: trivy", "  check: example-check-two", "  system: system-one", "  until: 2099-12-31",
    "  reason: 'open", "  reason: \"open", "close'", "close\"", "  more text", "    deeper: x", "#", "  # c",
    "", "---", "...", "  - nested", "-", "  reason: |", "  reason: >-", "      block line", "\tcheck: x",
    "  check: \"a\\", "  check: 'a''", "  reason: plain", "   odd: column", "  check: 'x' # c",
    "  until: 2027-01-31 # c", "  check: [a]", "  check: yes", "  check: 0x1A", "- check: c", "  ''",
]


def assert_no_drift(text):
    """Each field that the reader takes is the text that PyYAML gives, in an entry of the
    same place; and the reader takes nothing of a file that PyYAML does not read."""
    try:
        expected = yaml.safe_load(text)
    except yaml.YAMLError:
        expected = yaml.YAMLError
    try:
        entries = converter.yaml_list(text)
    except converter.Unreadable:
        return False
    assert expected is not yaml.YAMLError, "the reader takes a file that PyYAML does not read"
    expected = expected or []
    assert isinstance(expected, list) and len(entries) == len(expected)
    for ours, theirs in zip(entries, expected, strict=True):
        if theirs is None:  # a dash with nothing after it
            assert ours == {}
            continue
        assert isinstance(theirs, dict) and set(ours) == set(theirs)
        for key, value in ours.items():
            if isinstance(value, str):
                assert str(theirs[key]) == value, (key, value, theirs[key])
            if value and not (isinstance(value, converter.Undecoded) and not value.text):
                # What the reader takes as a value, the Prowler job takes as one too.
                assert theirs[key], (key, value, theirs[key])
    return True


def test_the_reader_takes_what_yaml_safe_dump_writes_as_pyyaml_reads_it():
    chance = random.Random(2145)
    for width in (20, 80, 200):
        for _ in range(300):
            entries = [{key: chance.choice(values) for key, values in FIELDS.items()} for _ in range(3)]
            for entry in entries:
                entry["reason"] = chance.choice(REASONS) + chance.choice(REASONS)
            text = yaml.safe_dump(entries, width=width, allow_unicode=chance.random() < 0.5,
                                  sort_keys=chance.random() < 0.5)
            assert assert_no_drift(text), text
            result, skipped = converter.accepted_entries(text, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
            kept = [entry for entry in entries if entry["reason"]]
            assert skipped == len(entries) - len(kept) and result == {
                (entry["source"], entry["check"], entry["system"]): end(entry["until"])
                for entry in sorted(kept, key=lambda entry: entry["until"])}


def test_the_reader_and_pyyaml_do_not_drift_on_invented_lists():
    """Lists of yaml.safe_dump with lines put in, lines moved to another column, and
    characters put in."""
    chance = random.Random(2170)
    taken = 0
    for _ in range(6000):
        lines = yaml.safe_dump([{key: chance.choice(values) for key, values in FIELDS.items()}
                                | {"reason": chance.choice(REASONS)} for _ in range(2)],
                               width=chance.choice([20, 80])).splitlines()
        for _ in range(chance.randint(1, 3)):
            change, at = chance.random(), chance.randrange(len(lines))
            if change < 0.6:
                lines.insert(at, chance.choice(FRAGMENTS))
            elif change < 0.8:
                lines[at] = " " * chance.randint(0, 5) + lines[at].lstrip(" ")
            else:
                column = chance.randint(0, len(lines[at]))
                lines[at] = lines[at][:column] + chance.choice("'\"#:- \\|>") + lines[at][column:]
        taken += assert_no_drift("\n".join(lines) + chance.choice(["\n", ""]))
    assert taken > 500, "the corpus has lists that the reader takes"


def test_a_check_that_is_not_a_check_id_is_skipped_and_a_lone_surrogate_stops_nothing(tmp_path):
    text = ('- source: prowler\n  check: "a\\ud800"\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n'
            '- source: prowler\n  check: "a b"\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n'
            '- source: prowler\n  check: good\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n')
    source = accepted_for(tmp_path, text)
    source.cycle()
    rendered = source.render().decode()
    assert len(served(rendered, SERIES)) == 1
    assert served(rendered, converter.ACCEPTED_SKIPPED) == {(): 2.0}
    metrics = converter.Metrics()
    metrics.add("a", 1, check="a\ud800")
    assert metrics.render() == b'a{check="a\\\\ud800"} 1\n'


# --- The entries ------------------------------------------------------------------------


def test_the_end_is_the_start_of_the_day_after_the_end_date():
    assert converter.end_of("2027-01-31") == calendar.timegm((2027, 2, 1, 0, 0, 0))
    assert converter.end_of("2027-12-31") == calendar.timegm((2028, 1, 1, 0, 0, 0))


@pytest.mark.parametrize("entry", [
    "- check: a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "- source: prowler\n  check: ''\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "- source: prowler\n  check: a\n  system: system-one\n  reason: invented\n  until: soon\n",
    "- source: prowler\n  check: a\n  system: system-one\n  reason: invented\n  until: '2027-02-30'\n",
    "- source: prowler\n  check: a\n  system: not-in-the-registry\n  reason: invented\n  until: 2027-01-01\n",
    "- source: not-a-source\n  check: a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "- source: prowler\n  check: >\n    a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "- source: prowler\n  check: a\n    b\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
    "- source: prowler\n  check: " + "a" * 201 + "\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n",
], ids=["no source", "an empty check", "no date", "a day that is not", "a system not in the registry",
        "a source not in the registry", "a check in a block", "a check on two lines", "a long check"])
def test_an_entry_that_does_not_fit_is_skipped_and_counted(entry):
    good = "- source: trivy\n  check: good\n  system: system-two\n  reason: invented\n  until: 2027-01-01\n"
    result, skipped = converter.accepted_entries(good + entry, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
    assert result == {("trivy", "good", "system-two"): end("2027-01-01")}
    assert skipped == 1


NO_REASON = {
    "no reason key": "",
    "an empty reason": "  reason:\n",
    "an empty quoted reason": "  reason: ''\n",
    "an empty double-quoted reason": '  reason: ""\n',
    "a reason of null": "  reason: null\n",
    "a reason of false": "  reason: false\n",
    "a reason of 0": "  reason: 0\n",
    "an empty block": "  reason: |\n",
}


@pytest.mark.parametrize("reason", NO_REASON.values(), ids=NO_REASON.keys())
def test_an_entry_with_no_reason_is_skipped_and_counted(tmp_path, reason):
    """Design Doc 4.1.2: an accepted finding has a reason. The file stays readable."""
    good = "- source: trivy\n  check: good\n  system: system-two\n  reason: invented\n  until: 2027-01-01\n"
    text = good + "- source: prowler\n  check: a\n  system: system-one\n" + reason + "  until: 2027-01-01\n"
    result, skipped = converter.accepted_entries(text, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
    assert result == {("trivy", "good", "system-two"): end("2027-01-01")} and skipped == 1
    assert not (yaml.safe_load(text)[1].get("reason")), "the Prowler job skips it too"
    source = accepted_for(tmp_path, text)
    source.cycle()
    rendered = source.render().decode()
    assert served(rendered, converter.ACCEPTED_SKIPPED) == {(): 1.0}
    assert served(rendered, converter.ACCEPTED_READ) == {(): 1.0}


def test_a_reason_of_space_only_on_two_lines_counts_as_none():
    """PyYAML folds it to one space, which the Prowler job takes. The reader does not fold
    a value, so it takes only a reason with a character that is not space: it mutes less."""
    text = "- source: prowler\n  check: a\n  system: system-one\n  reason: '\n    '\n  until: 2027-01-01\n"
    assert yaml.safe_load(text)[0]["reason"] == " "
    assert converter.accepted_entries(text, set(REGISTRY["sources"]), set(REGISTRY["systems"])) == ({}, 1)


@pytest.mark.parametrize("reason", [
    "  reason: yes\n", "  reason: '0'\n", "  reason: |\n    An invented reason.\n",
    "  reason: 'An invented reason,\n    on two lines.'\n", '  reason: "caf\\xE9, invented"\n',
    "  reason: 26001\n"])
def test_a_reason_in_another_form_counts(reason):
    text = "- source: prowler\n  check: a\n  system: system-one\n" + reason + "  until: 2027-01-01\n"
    result, skipped = converter.accepted_entries(text, set(REGISTRY["sources"]), set(REGISTRY["systems"]))
    assert result == {("prowler", "a", "system-one"): end("2027-01-01")} and skipped == 0
    assert yaml.safe_load(text)[0]["reason"]


def test_two_entries_of_one_finding_keep_the_later_end():
    text = ("- source: prowler\n  check: a\n  system: system-one\n  reason: invented\n  until: 2027-03-01\n"
            "- source: prowler\n  check: a\n  system: system-one\n  reason: invented\n  until: 2027-01-01\n")
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
    assert served(text, converter.ACCEPTED_READ) == {(): 1.0}


@pytest.mark.parametrize("raw", [b"\xff\xfe- source: prowler\n", b"- a: b\n" * 200_000])
def test_a_file_that_can_not_be_read_serves_no_entry_and_is_counted(tmp_path, raw):
    source = accepted_for(tmp_path, raw)
    source.cycle()
    text = source.render().decode()
    assert served(text, SERIES) == {}
    assert served(text, converter.ACCEPTED_READ) == {(): 0.0}


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
