"""The Prowler runs of the security platform keep their contract.

kubernetes/apps/security/prowler: the script turns the list of accepted findings
into the mutelist of a run, runs Prowler with nothing for its vendor, and leaves
one whole OCSF file for the converter (security/converter/README.md, "The files
of results"). The CronJobs name systems of the registry, the scan list of their
provider, the volume of results and the network policy of their provider.
"""

import datetime
import importlib.util
import json
import stat
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/security/prowler/app"
UTC = datetime.UTC
NOW = datetime.datetime(2026, 11, 13, 4, 30, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def run():
    spec = importlib.util.spec_from_file_location("prowler_run", APP / "scripts/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def accepted_file(tmp_path, entries):
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps(entries))
    return path


def entry(check, until, source="prowler", system="aws-prod"):
    return {"source": source, "check": check, "system": system, "reason": "An invented reason.", "until": until}


def test_only_the_open_entries_of_prowler_for_the_system_are_accepted(run, tmp_path):
    path = accepted_file(tmp_path, [
        entry("check_open", "2026-11-13"),
        entry("check_later", "2027-01-31"),
        entry("check_ended", "2026-11-12"),
        entry("check_other_system", "2027-01-31", system="aws-management"),
        entry("k8s-cis-1.23/1.2.1", "2027-01-31", source="trivy", system="aws-prod"),
    ])
    assert run.accepted_checks(path, "aws-prod", NOW.date()) == ({
        "check_open": "2026-11-13", "check_later": "2027-01-31"}, 0)


def test_no_list_accepts_nothing(run, tmp_path):
    assert run.accepted_checks(tmp_path / "absent.json", "aws-prod", NOW.date()) == ({}, 0)
    assert run.accepted_checks(accepted_file(tmp_path, []), "aws-prod", NOW.date()) == ({}, 0)


def test_a_bad_entry_is_skipped_and_counted_and_its_values_stay_out_of_the_log(run, tmp_path, capsys):
    good = entry("check_open", "2027-01-31")
    bad = [
        "a line",
        entry("check_bad_date", "next spring"),
        entry("check_with_time", "2027-01-31T00:00:00"),
        # date.fromisoformat takes these two; the form YYYY-MM-DD does not.
        entry("check_basic_date", "20270131"),
        entry("check_week_date", "2027-W05-1"),
        entry("check_other_digits", "\u0662\u0660\u0662\u0667-01-31"),
        entry("check_newline", "2027-01-31\n"),
        # Prowler reads a key of the mutelist as a regex: none of these is a check ID.
        entry("*", "2027-01-31"),
        entry(".", "2027-01-31"),
        entry(".*", "2027-01-31"),
        entry("x|.", "2027-01-31"),
        entry("_check", "2027-01-31"),
        entry("a" * 201, "2027-01-31"),
        entry("", "2027-01-31"),
        entry("check_blank_reason", "2027-01-31") | {"reason": "   "},
        {key: value for key, value in entry("check_no_reason", "2027-01-31").items() if key != "reason"},
        entry("check_extra_key", "2027-01-31") | {"owner": "adult1"},
        entry("check_not_a_string", "2027-01-31") | {"reason": 7},
        entry("check_until_a_number", 20270131),
        entry("check_null", "2027-01-31") | {"system": None},
    ]
    path = accepted_file(tmp_path, [*bad, good])
    assert run.accepted_checks(path, "aws-prod", NOW.date()) == ({"check_open": "2027-01-31"}, len(bad))
    out = capsys.readouterr().out
    assert all(f"entry {index} " in out for index in range(1, len(bad) + 1))
    assert f"entry {len(bad) + 1} " not in out
    assert "next spring" not in out and "invented reason" not in out and "a line" not in out and "adult1" not in out


@pytest.mark.parametrize("text", [
    '{"source": "prowler"}',
    '[{"source": "prowler", "reason": "An invented reason."',
    "- source: prowler\n  reason: An invented reason.\n",
    ('[{"source": "prowler", "check": "check_open", "system": "aws-prod", "reason": "An invented reason.",'
     ' "until": "2027-01-31", "check": "check_other"}]'),
    "",
    # Nested too deep for the parser: RecursionError, not a crash.
    "[" * 100_000 + "]" * 100_000,
    # A number longer than the int limit of Python (4300 digits): ValueError.
    "[" + "1" * 5000 + "]",
])
def test_a_list_that_can_not_be_read_stops_the_run_and_its_text_stays_out_of_the_log(run, tmp_path, capsys, text):
    """Not JSON (YAML is not read), not an array, nested too deep, or a key twice in one object."""
    (tmp_path / "accepted.json").write_text(text)
    with pytest.raises(run.UnreadableList):
        run.accepted_checks(tmp_path / "accepted.json", "aws-prod", NOW.date())
    call, calls = fake_prowler()
    assert run.run(args(run, tmp_path), now=lambda: NOW, call=call) == 2
    assert calls == []
    assert "invented reason" not in capsys.readouterr().out


def test_the_mutelist_mutes_each_accepted_check_and_holds_no_reason(run):
    muted = run.mutelist({"check_b": "2027-01-31", "check_a": "2026-12-01"})
    checks = muted["Mutelist"]["Accounts"]["*"]["Checks"]
    assert list(checks) == ["^check_a$", "^check_b$"]
    assert checks["^check_a$"] == {"Regions": ["*"], "Resources": ["*"], "Description": "Accepted until 2026-12-01"}
    assert "reason" not in json.dumps(muted).lower()
    # The run always passes its own list, so the default list of Prowler is off too.
    assert run.mutelist({}) == {"Mutelist": {"Accounts": {}}}


def test_the_command_sends_nothing_to_the_vendor(run, tmp_path):
    command = run.prowler_command("aws", tmp_path / "c.json", tmp_path / "m.yaml", tmp_path / "out", "aws-prod.run",
                                  ["--role=arn:aws:iam::123456789012:role/example"])
    assert command[:2] == [run.PROWLER, "aws"]
    for flag in ("--push-to-cloud", "-v", "--version", "--slack", "--send-sh-only-fails", "--shodan"):
        assert flag not in command
    assert command[command.index("--output-formats") + 1] == "json-ocsf"
    assert "--ignore-exit-code-3" in command
    assert command[-1] == "--role=arn:aws:iam::123456789012:role/example"


def fake_prowler(code=0, write=True, content='[{"status_code": "FAIL"}]'):
    calls = []

    def call(command):
        calls.append(command)
        if write:
            output = Path(command[command.index("--output-directory") + 1])
            output.mkdir(parents=True, exist_ok=True)
            name = command[command.index("--output-filename") + 1]
            (output / f"{name}.ocsf.json").write_text(content)
            (output / "compliance").mkdir()
            (output / "compliance" / f"{name}_cis.csv").write_text("a table")
        return code
    return call, calls


def args(run, tmp_path, *extra):
    (tmp_path / "work").mkdir(exist_ok=True)
    return run.parse(["--provider=aws", "--system=aws-prod", f"--checks={tmp_path / 'checks.json'}",
                      f"--results={tmp_path / 'results'}", f"--accepted={tmp_path / 'accepted.json'}",
                      f"--work={tmp_path / 'work'}", "--", *extra])


def test_a_run_leaves_one_whole_file_with_the_time_of_its_end(run, tmp_path, capsys):
    accepted_file(tmp_path, [entry("check_open", "2027-01-31")])
    times = iter([NOW, NOW + datetime.timedelta(minutes=40), NOW + datetime.timedelta(minutes=40)])
    call, calls = fake_prowler()
    code = run.run(args(run, tmp_path, "--role=arn:aws:iam::123456789012:role/example"), now=lambda: next(times), call=call)
    assert code == 0
    directory = tmp_path / "results/prowler/aws"
    files = sorted(path.name for path in directory.iterdir())
    assert files == ["aws-prod.20261113T051000Z.ocsf.json", "tmp"]
    assert list((directory / "tmp").iterdir()) == []
    assert stat.S_IMODE((directory / files[0]).stat().st_mode) == 0o644
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert stat.S_IMODE((tmp_path / "results/prowler").stat().st_mode) == 0o755
    # The mutelist of the run, and nothing of Prowler's own output stays in the scratch space.
    command = calls[0]
    assert command[-1] == "--role=arn:aws:iam::123456789012:role/example"
    assert list((tmp_path / "work").iterdir()) == []
    out = capsys.readouterr().out
    assert "1 accepted checks muted" in out and "check_open" not in out
    assert "memory peak of the container" in out


def test_the_mutelist_of_the_run_has_the_accepted_checks(run, tmp_path):
    accepted_file(tmp_path, [entry("check_open", "2027-01-31")])
    seen = {}

    def call(command):
        seen.update(json.loads(Path(command[command.index("--mutelist-file") + 1]).read_text()))
        return 1
    run.run(args(run, tmp_path), now=lambda: NOW, call=call)
    assert list(seen["Mutelist"]["Accounts"]["*"]["Checks"]) == ["^check_open$"]


@pytest.mark.parametrize("code,write,content", [
    (1, True, "[]"), (2, False, "[]"), (0, False, "[]"),
    # An empty file is a bad file for the converter: the run fails.
    (0, True, "")])
def test_a_run_that_fails_moves_no_file(run, tmp_path, code, write, content):
    call, _ = fake_prowler(code=code, write=write, content=content)
    assert run.run(args(run, tmp_path), now=lambda: NOW, call=call) != 0
    directory = tmp_path / "results/prowler/aws"
    assert not directory.exists() or not any(path.is_file() for path in directory.rglob("*"))


def test_files_older_than_14_days_and_leftovers_are_deleted(run, tmp_path):
    directory = tmp_path / "results/prowler/aws"
    (directory / "tmp").mkdir(parents=True)
    names = {
        "aws-prod.20261029T031700Z.ocsf.json": False,  # 15 days
        "aws-prod.20261031T031700Z.ocsf.json": True,  # 13 days
        "aws-prod.20261112T031700Z.ocsf.json": True,
        "aws-management.20261001T031700Z.ocsf.json": True,  # another system
        "notes.txt": True,
    }
    for name in names:
        (directory / name).write_text("[]")
    (directory / "tmp/aws-prod.20261112T031700Z.ocsf.json.partial").write_text("[")
    removed = run.prune(tmp_path / "results", "aws", "aws-prod", NOW)
    assert sorted(path.name for path in removed) == [
        "aws-prod.20261029T031700Z.ocsf.json", "aws-prod.20261112T031700Z.ocsf.json.partial"]
    assert {path.name for path in directory.iterdir() if path.is_file()} == {name for name, kept in names.items() if kept}


def cronjobs():
    documents = []
    for path in sorted(APP.glob("cronjob-*.yaml")):
        documents += [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
    return documents


def test_each_cronjob_runs_one_system_of_the_registry_with_its_scan_list():
    systems = {item["name"]: item["type"] for item in yaml.safe_load((ROOT / "security/registry/systems.yaml").read_text())}
    providers = {"aws-account": "aws", "cloudflare-account": "cloudflare", "github-organization": "github"}
    kustomization = yaml.safe_load((APP / "kustomization.yaml").read_text())
    config = next(item for item in kustomization["configMapGenerator"] if item["name"] == "prowler-config")
    jobs = cronjobs()
    assert jobs
    for job in jobs:
        pod = job["spec"]["jobTemplate"]["spec"]["template"]
        (container,) = pod["spec"]["containers"]
        own = container["args"][:container["args"].index("--")] if "--" in container["args"] else container["args"]
        flags = dict(arg.removeprefix("--").split("=", 1) for arg in own)
        provider, system = flags["provider"], flags["system"]
        assert providers[systems[system]] == provider, job["metadata"]["name"]
        assert flags["checks"] == f"/config/checks-{provider}.json"
        assert f"./config/checks-{provider}.json" in config["files"]
        assert pod["metadata"]["labels"]["security.tnwks.us/prowler-provider"] == provider
        assert "@sha256:" in container["image"]
        assert container["resources"]["limits"]["memory"] == "768Mi"
        assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "${SECURITY_NODE}"}
        volumes = {volume["name"]: volume for volume in pod["spec"]["volumes"]}
        assert volumes["results"]["persistentVolumeClaim"]["claimName"] == "security-results"
        assert volumes["accepted"]["secret"] == {"secretName": "security-accepted-findings", "optional": True}
        mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
        assert "subPath" not in mounts["results"] and mounts["results"]["mountPath"] == "/results"
        assert mounts["accepted"]["mountPath"] == "/accepted"


def test_each_provider_has_its_network_policy():
    policies = [doc for doc in yaml.safe_load_all((APP / "networkpolicy.yaml").read_text()) if doc]
    selected = {doc["spec"]["endpointSelector"]["matchLabels"]["security.tnwks.us/prowler-provider"] for doc in policies}
    for job in cronjobs():
        labels = job["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
        assert labels["security.tnwks.us/prowler-provider"] in selected
    for doc in policies:
        assert "ingress" not in doc["spec"]
        hosts = [item for rule in doc["spec"]["egress"] for item in rule.get("toFQDNs", [])]
        assert hosts, doc["metadata"]["name"]
        assert not any("prowler" in str(value) for host in hosts for value in host.values())


def test_the_claim_of_results_is_in_the_app_of_the_converter():
    claim = yaml.safe_load((ROOT / "kubernetes/apps/security/converter/app/pvc.yaml").read_text())
    assert claim["metadata"]["name"] == "security-results"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"]["requests"]["storage"] == "1Gi"
    assert "./pvc.yaml" in yaml.safe_load((ROOT / "kubernetes/apps/security/converter/app/kustomization.yaml").read_text())["resources"]


def prowler_matches(key, check):
    """Whether Prowler 5.49.0 mutes `check` with the mutelist key `key`: a faithful copy
    of prowler/lib/mutelist/mutelist.py, Mutelist.is_muted_in_check (lines 248 to 254:
    the rename of "lambda" and check_match) and Mutelist.is_item_matched (lines 431 to
    440, for a list of one item that is not a tag). The image has Prowler; the tests
    do not. The real functions of the release gave the same answer for the two
    pairs of AWS checks below."""
    import re
    key = re.sub("^lambda", "awslambda", key)
    item = key.replace("*", ".*") if "*" in key else key
    return "*" == key or check == key or bool(re.search(item, check))


@pytest.mark.parametrize("accepted,other", [
    ("cloudtrail_multi_region_enabled", "cloudtrail_multi_region_enabled_logging_management_events"),
    ("ec2_securitygroup_allow_ingress_from_internet_to_any_port",
     "ec2_securitygroup_allow_ingress_from_internet_to_any_port_from_ip"),
    ("s3_bucket_public_access", "s3_bucket_public_access_block"),
    ("k8s-cis-1.23/1.2.1", "k8s-cis-1.23/1.2.10"),
    ("k8s-cis-1.23/1.2.1", "k8s-cis-1.23x1.2.1"),
])
def test_the_key_of_an_accepted_check_mutes_that_check_only(run, accepted, other):
    key = next(iter(run.mutelist({accepted: "2027-01-31"})["Mutelist"]["Accounts"]["*"]["Checks"]))
    assert prowler_matches(key, accepted)
    assert not prowler_matches(key, other) and not prowler_matches(key, "x" + accepted)
    # The bare check, as the key was before, mutes the longer check too.
    assert prowler_matches(accepted, accepted + "_x")


def test_each_check_of_the_scan_lists_is_a_check_id_that_mutes_only_itself(run):
    checks = sorted({check for path in (APP / "config").glob("checks-*.json")
                     for checks in json.loads(path.read_text()).values() for check in checks})
    assert checks
    for check in checks:
        assert run.CHECK.fullmatch(check), check
        key = run.mutelist_key(check)
        assert [other for other in checks if prowler_matches(key, other)] == [check]
