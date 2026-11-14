"""The converter and the Prowler runs share one volume and one list of accepted findings
(security/converter/README.md, "The files of results"): what a run leaves on the volume
is what the converter reads, and both read the list the same way. Each value is invented."""

import calendar
import datetime
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
import converter
import generate

ROOT = Path(__file__).resolve().parents[2]
CONVERTER_APP = ROOT / "kubernetes/apps/security/converter/app"
PROWLER_APP = ROOT / "kubernetes/apps/security/prowler/app"
UTC = datetime.UTC
CLAIM = "security-results"


@pytest.fixture(scope="module")
def run():
    spec = importlib.util.spec_from_file_location("prowler_run", PROWLER_APP / "scripts/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def documents(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


# --- The claim --------------------------------------------------------------------------


def test_the_converter_mounts_the_claim_of_its_app_read_only():
    resources = yaml.safe_load((CONVERTER_APP / "kustomization.yaml").read_text())["resources"]
    claims = [item for name in resources for item in documents(CONVERTER_APP / name)
              if item["kind"] == "PersistentVolumeClaim"]
    assert [claim["metadata"]["name"] for claim in claims] == [CLAIM]
    values = documents(CONVERTER_APP / "helmrelease.yaml")[0]["spec"]["values"]
    volume = values["persistence"]["results"]
    assert (volume["type"], volume["existingClaim"]) == ("persistentVolumeClaim", CLAIM)
    assert volume["globalMounts"] == [{"path": "/results", "readOnly": True}]
    assert values["defaultPodOptions"]["nodeSelector"] == {"kubernetes.io/hostname": "${SECURITY_NODE}"}


def test_each_pod_that_mounts_the_claim_is_on_the_security_node():
    """The class puts the volume on the node of its first pod."""
    mounts = 0
    for path in PROWLER_APP.glob("cronjob-*.yaml"):
        for job in documents(path):
            spec = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            if any(volume.get("persistentVolumeClaim", {}).get("claimName") == CLAIM for volume in spec["volumes"]):
                mounts += 1
                assert spec["nodeSelector"] == {"kubernetes.io/hostname": "${SECURITY_NODE}"}
    assert mounts >= 1


# --- What a run leaves is what the converter reads --------------------------------------


def record(check, status_code, status="New"):
    return {"metadata": {"event_code": check}, "status_code": status_code, "status": status, "severity": "High",
            "finding_info": {"uid": f"example-{check}"}, "resources": [{"uid": "example-resource"}]}


def test_the_file_of_a_run_is_the_newest_result_of_its_system(run, tmp_path):
    ended = datetime.datetime(2026, 11, 13, 5, 10, 0, tzinfo=UTC)
    produced = tmp_path / "produced.ocsf.json"
    produced.write_text(json.dumps([record("check_a", "FAIL"), record("check_b", "FAIL", "Suppressed"),
                                    record("check_c", "PASS")], indent=4))
    results = tmp_path / "volume"
    final = run.publish(produced, results, "aws", "aws-prod", ended)
    assert final.parent == results / "prowler/aws"
    part = converter.ResultFiles(results, generate.converter_registry(generate.load()),
                                 clock=lambda: ended.timestamp() + 60)
    part.cycle()
    text = part.render().decode()
    assert f'{generate.LAST_RESULT}{{source="prowler",system="aws-prod"}} {ended.timestamp():.3f}' in text
    found = {re.search(r'check="([^"]+)"', line)[1]: line.rsplit(" ", 1)[1]
             for line in text.splitlines() if line.startswith(generate.RESULT + "{")}
    assert found == {"check_a": "1", "check_b": "0", "check_c": "0"}


# --- One list, read the same way --------------------------------------------------------

README_EXAMPLE = re.search(r"  accepted\.json: \|\n((?:    .*\n)+)", (ROOT / "security/README.md").read_text())
DAYS = (datetime.date(2026, 11, 13), datetime.date(2027, 1, 31), datetime.date(2027, 2, 1))
SYSTEMS = ("aws-prod", "aws-management")
# Reasons as an owner writes them: long, not ASCII, with quotes, escapes and new lines,
# and with the text of an entry, which is only text in JSON.
REASONS = ["An invented reason. " * 500, "Ein erfundener Grund, café, 日本語の理由.",
           'With "quotes", a \\ and a\nnew line.', '{"source": "prowler", "check": "check_injected"}',
           "\ud800 a lone surrogate escape in an invented reason"]


def entry(**changes):
    item = {"source": "prowler", "check": "check_open", "system": "aws-prod", "until": "2026-11-13",
            "reason": "An invented reason."} | changes
    return {key: value for key, value in item.items() if value is not None}


def lists():
    example = "\n".join(line[4:] for line in README_EXAMPLE[1].splitlines()) + "\n"
    entries = [
        entry(), entry(check="check_ended", until="2026-11-12"),
        entry(check="check_other", system="aws-management", until="2027-01-31"),
        entry(check="check_late", until="2027-02-01"),
        entry(source="trivy", check="k8s-cis-1.23/1.2.1", until="2027-01-31"),
    ]
    texts = [json.dumps([item | {"reason": REASONS[(index + shift) % len(REASONS)]}
                         for index, item in enumerate(entries)], indent=indent)
             for shift, indent in enumerate([None, 2, 4, None, 2])]
    return [example.replace("example-system", "aws-prod"), *texts]


def readers(run, tmp_path, text):
    """What each reader gives for one text, on each day and system: the checks of
    `prowler` that it accepts, or "refused" when the list gives no entry."""
    path = tmp_path / "accepted.json"
    path.write_bytes(text.encode("utf-8", "surrogatepass") if isinstance(text, str) else text)
    registry = generate.converter_registry(generate.load())
    try:
        served, skipped = converter.accepted_entries(path.read_bytes().decode("utf-8"),
                                                     set(registry["sources"]), set(registry["systems"]))
    except (converter.Unreadable, UnicodeDecodeError):
        served, skipped = "refused", None
    found_run, found_converter, run_skipped = {}, {}, None
    for day in DAYS:
        start = calendar.timegm(day.timetuple())
        for system in SYSTEMS:
            try:
                accepted, run_skipped = run.accepted_checks(path, system, day)
                found_run[(day, system)] = set(accepted)
            except Exception:  # noqa: BLE001 - each fault of the run is a run that fails
                found_run[(day, system)], run_skipped = "refused", None
            found_converter[(day, system)] = "refused" if served == "refused" else {
                check for (source, check, name), end in served.items()
                if source == "prowler" and name == system and end > start}
    return found_run, found_converter, run_skipped, skipped


@pytest.mark.parametrize("index", range(len(lists())))
def test_a_run_mutes_what_the_converter_serves_as_accepted_on_that_day(run, tmp_path, index):
    found_run, found_converter, run_skipped, skipped = readers(run, tmp_path, lists()[index])
    assert found_run == found_converter
    assert run_skipped == skipped == 0
    assert all("check_injected" not in found for found in found_converter.values())


# One item after a good entry, in each form that one reader or the other skips. The
# readers must agree, or the converter must be the one that accepts less.
ITEMS = {
    "no reason": entry(reason=None), "an empty reason": entry(reason=""), "a reason of space": entry(reason=" \t"),
    "null": {**entry(), "reason": None}, "false": entry(reason=False), "0": entry(reason=0),
    "an empty string as until": entry(until=""), "a number as check": entry(check=26001),
    "NaN": "NaN", "a key that is not a field": entry(note="An invented note."), "a nested array": [entry()],
    "not an object": "check_open", "a date that is no day": entry(until="2026-02-30"),
    "a date with a time": entry(until="2027-01-31T10:00:00"), "a date without dashes": entry(until="20271231"),
    "a date of a week": entry(until="2027-W05-1"), "a date with a line end": entry(until="2027-01-31\n"),
    "a date of other digits": entry(until="\u0662\u0660\u0662\u0667-\u0660\u0661-\u0663\u0661"),
    "a source not in the registry": entry(source="not-a-source"),
    "a system not in the registry": entry(system="not-in-the-registry"),
    "a check that is not a check ID": entry(check="check open"),
    "a check that ends in a line feed": entry(check="check_open\n"),
    "the last day a date holds": entry(until="9999-12-31"),
}
# The run does not know the registry or the form of a check ID: there the converter
# skips an entry that the run takes, which mutes less. A run mutes only its own system
# and `prowler`, so only the check shows the difference in what is muted.
CONVERTER_ACCEPTS_LESS = {"a source not in the registry", "a system not in the registry",
                          "a check that is not a check ID", "a check that ends in a line feed"}
# An entry that fits: both take it, and add one more accepted finding to the good one.
BOTH_ACCEPT = {"the last day a date holds"}


@pytest.mark.parametrize("name", ITEMS)
def test_the_run_and_the_converter_skip_the_same_items(run, tmp_path, name):
    item = ITEMS[name]
    text = "[" + json.dumps(entry(check="check_late", until="2027-02-01")) + ", " + (
        '{"source": "prowler", "check": "check_open", "system": "aws-prod", "until": "2026-11-13", "reason": NaN}'
        if item == "NaN" else json.dumps(item)) + "]"
    found_run, found_converter, run_skipped, skipped = readers(run, tmp_path, text)
    for key, found in found_converter.items():
        assert found <= found_run[key], "the converter never accepts what the run does not"
    if name in CONVERTER_ACCEPTS_LESS:
        assert run_skipped == 0 and skipped == 1
        if name in {"a check that is not a check ID", "a check that ends in a line feed"}:
            assert any(found_converter[key] != found_run[key] for key in found_run)
    elif name in BOTH_ACCEPT:
        assert found_run == found_converter and run_skipped == skipped == 0
        assert any("check_open" in found for found in found_converter.values())
    else:
        assert found_run == found_converter and run_skipped == skipped == 1


LISTS_THAT_GIVE_NO_ENTRY = {
    "a comma at the end": '[{"source": "prowler"},]', "a comment": "// an invented comment\n[]",
    "a byte order mark": "﻿[]", "an object at the top": json.dumps(entry()), "two arrays": "[] []",
    "a key two times": '[{"source": "prowler", "check": "check_open", "system": "aws-prod", "until": "2026-11-13",'
                       ' "reason": "An invented reason.", "check": "check_late"}]',
    "an array that does not end": "[" + json.dumps(entry()), "an empty file": "",
    "not UTF-8": b"[\xff]", "arrays too deep": "[" * 100_000 + "]" * 100_000,
}


@pytest.mark.parametrize("name", LISTS_THAT_GIVE_NO_ENTRY)
def test_a_list_that_one_reader_refuses_the_other_refuses_too(run, tmp_path, name):
    found_run, found_converter, _, _ = readers(run, tmp_path, LISTS_THAT_GIVE_NO_ENTRY[name])
    assert set(found_run.values()) == set(found_converter.values()) == {"refused"}


def test_an_empty_array_and_no_file_accept_nothing_in_both(run, tmp_path):
    found_run, found_converter, run_skipped, skipped = readers(run, tmp_path, "[]")
    assert all(found == set() for found in [*found_run.values(), *found_converter.values()])
    assert run_skipped == skipped == 0
    assert run.accepted_checks(tmp_path / "no-list.json", "aws-prod", DAYS[0]) == ({}, 0)
