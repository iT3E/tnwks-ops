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

README_EXAMPLE = re.search(r"  accepted\.yaml: \|\n((?:    .*\n)+)", (ROOT / "security/README.md").read_text())


# Reasons as an owner writes them: long, with ": " (yaml.safe_dump puts such a reason
# in single quotes and wraps it over lines), with quotes, and with the text of an entry.
LONG = ("Accepted: an invented reason that is long enough to wrap, as an owner writes one, with a colon, "
        "and with more words: the line goes on past the width of yaml.safe_dump.")
REASONS = [LONG, LONG + " It's 'quoted', \"too\".", "Invented.\n- source: prowler\n  check: check_injected",
           "Invented: café, " * 12]


def lists():
    example = "\n".join(line[4:] for line in README_EXAMPLE[1].splitlines()) + "\n"
    entries = [
        {"source": "prowler", "check": "check_open", "system": "aws-prod", "until": "2026-11-13"},
        {"source": "prowler", "check": "check_ended", "system": "aws-prod", "until": "2026-11-12"},
        {"source": "prowler", "check": "check_other", "system": "aws-management", "until": "2027-01-31"},
        {"source": "prowler", "check": "check_late", "system": "aws-prod", "until": "2027-02-01"},
        {"source": "trivy", "check": "k8s-cis-1.23/1.2.1", "system": "aws-prod", "until": "2027-01-31"},
    ]
    dumped = [yaml.safe_dump([entry | {"reason": REASONS[(index + shift) % len(REASONS)]}
                              for index, entry in enumerate(entries)], width=width, allow_unicode=unicode)
              for shift, (width, unicode) in enumerate([(80, False), (80, True), (40, False), (120, True)])]
    return [example.replace("example-system", "aws-prod"), *dumped]


def test_the_lists_hold_wrapped_quoted_reasons():
    """The comparison below proves something only when a reason goes on over more lines."""
    dumped = lists()[1:]
    assert all(re.search(r"reason: '[^\n]*\n {4}\S", text) for text in dumped)
    assert all("check_injected" in text for text in dumped)


@pytest.mark.parametrize("text", lists())
def test_a_run_mutes_what_the_converter_serves_as_accepted_on_that_day(run, tmp_path, text):
    path = tmp_path / "accepted.yaml"
    path.write_text(text)
    registry = generate.converter_registry(generate.load())
    served, skipped = converter.accepted_entries(text, set(registry["sources"]), set(registry["systems"]))
    assert skipped == 0
    assert all(check != "check_injected" for _, check, _ in served)
    for day in (datetime.date(2026, 11, 13), datetime.date(2027, 1, 31), datetime.date(2027, 2, 1)):
        start = calendar.timegm(day.timetuple())
        for system in ("aws-prod", "aws-management"):
            accepted, not_fit = run.accepted_checks(path, system, day)
            muted = set(accepted)
            assert not_fit == 0
            open_ = {check for (source, check, name), end in served.items()
                     if source == "prowler" and name == system and end > start}
            assert muted == open_, (day, system)
