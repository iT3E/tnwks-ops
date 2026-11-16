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
import sys
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


def record(check):
    return {"status_code": "FAIL", "metadata": {"event_code": check}}


def fake_prowler(code=0, write=True, content=None, peak=2 * 2**20):
    """A Prowler for each part. `code`, `write` and `content` are one value for each
    part, or a dict from the service of the part to its value. The default content is
    one record for each check of the part."""
    calls = []

    def of(value, name, default):
        return value.get(name, default) if isinstance(value, dict) else value

    def call(command):
        calls.append(command)
        checks = json.loads(Path(command[command.index("--checks-file") + 1]).read_text())["aws"]
        name = checks[0].split("_")[0]
        if of(write, name, True):
            output = Path(command[command.index("--output-directory") + 1])
            output.mkdir(parents=True, exist_ok=True)
            prefix = command[command.index("--output-filename") + 1]
            text = of(content, name, None)
            if text is None:
                text = "[" + ",".join(json.dumps(record(check), indent=4) for check in checks) + "]"
            (output / f"{prefix}.ocsf.json").write_text(text)
            (output / "compliance").mkdir()
            (output / "compliance" / f"{prefix}_cis.csv").write_text("a table")
        return of(code, name, 0), peak
    return call, calls


CHECKS = ["s3_bucket_one", "ec2_instance_one", "iam_user_one", "ec2_instance_two"]


def args(run, tmp_path, *extra, checks=CHECKS):
    (tmp_path / "work").mkdir(exist_ok=True)
    (tmp_path / "checks.json").write_text(json.dumps({"aws": checks}))
    return run.parse(["--provider=aws", "--system=aws-prod", f"--checks={tmp_path / 'checks.json'}",
                      f"--results={tmp_path / 'results'}", f"--accepted={tmp_path / 'accepted.json'}",
                      f"--work={tmp_path / 'work'}", "--", *extra])


def test_the_scan_list_runs_in_one_part_for_each_service(run, tmp_path):
    args(run, tmp_path)
    assert run.parts(tmp_path / "checks.json", "aws") == [
        ("ec2", ["ec2_instance_one", "ec2_instance_two"]), ("iam", ["iam_user_one"]), ("s3", ["s3_bucket_one"])]


@pytest.mark.parametrize("provider", ["aws", "cloudflare"])
def test_each_check_of_a_scan_list_is_in_one_part_with_its_service(run, provider):
    path = APP / f"config/checks-{provider}.json"
    checks = json.loads(path.read_text())[provider]
    pieces = run.parts(path, provider)
    assert sorted(check for _, part in pieces for check in part) == sorted(checks)
    assert len({name for name, _ in pieces}) == len(pieces)
    for name, part in pieces:
        assert part and all(run.service(check) == name for check in part)


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
    # One JSON array with the records of each part, in the order of the parts.
    joined = json.loads((directory / files[0]).read_text())
    assert joined == [record(check) for check in ["ec2_instance_one", "ec2_instance_two", "iam_user_one", "s3_bucket_one"]]
    # One process for each part, each with the checks of its part, the mutelist of the
    # run and the arguments after --, and nothing of Prowler's own output stays in the
    # scratch space.
    assert len(calls) == 3
    assert all(command[-1] == "--role=arn:aws:iam::123456789012:role/example" for command in calls)
    assert len({command[command.index("--mutelist-file") + 1] for command in calls}) == 1
    assert list((tmp_path / "work").iterdir()) == []
    out = capsys.readouterr().out
    assert "1 accepted checks muted" in out and "check_open" not in out
    assert "3 parts" in out
    assert "part 1 of 3: ec2, 2 checks: starts" in out
    assert "part 3 of 3: s3, 1 checks: ends, memory peak of the part: 2 MiB" in out
    assert "memory peak of the container" in out
    assert "FAIL" not in out


def test_each_part_gets_the_checks_of_its_service_only(run, tmp_path):
    seen = []

    def call(command):
        seen.append(json.loads(Path(command[command.index("--checks-file") + 1]).read_text()))
        return 1, None
    run.run(args(run, tmp_path), now=lambda: NOW, call=call)
    assert seen == [{"aws": ["ec2_instance_one", "ec2_instance_two"]}, {"aws": ["iam_user_one"]}, {"aws": ["s3_bucket_one"]}]


def test_the_mutelist_of_the_run_has_the_accepted_checks(run, tmp_path):
    accepted_file(tmp_path, [entry("check_open", "2027-01-31")])
    seen = {}

    def call(command):
        seen.update(json.loads(Path(command[command.index("--mutelist-file") + 1]).read_text()))
        return 1, None
    run.run(args(run, tmp_path), now=lambda: NOW, call=call)
    assert list(seen["Mutelist"]["Accounts"]["*"]["Checks"]) == ["^check_open$"]


@pytest.mark.parametrize("code,write,content", [
    # A part that fails: Prowler ends with an error, or a signal ends it.
    ({"iam": 1}, True, None), ({"iam": 2}, False, None), ({"iam": -9}, False, None),
    # An empty file is a bad file for the converter: the part fails.
    (0, True, {"iam": ""}),
    # A file that is not one JSON array.
    (0, True, {"iam": "{}"}), (0, True, {"iam": "[{}"}), (0, True, {"iam": "{}]"}), (0, True, {"iam": "  ]  "}),
    (0, True, {"iam": "["}), (0, True, {"iam": "x"}), (0, True, {"iam": "   "}),
    # No part leaves a record.
    (0, False, None), (0, True, "[]"), (1, True, "[]")])
def test_a_run_in_which_a_part_fails_moves_no_file(run, tmp_path, capsys, code, write, content):
    call, calls = fake_prowler(code=code, write=write, content=content)
    assert run.run(args(run, tmp_path), now=lambda: NOW, call=call) != 0
    directory = tmp_path / "results/prowler/aws"
    assert not directory.exists() or not any(path.is_file() for path in directory.rglob("*"))
    # Each part runs, so the log names each part that fails.
    assert len(calls) == 3
    out = capsys.readouterr().out
    if isinstance(code, dict) or isinstance(content, dict):
        assert "1 of 3 parts failed (iam): no file for the converter" in out
        assert "part 2 of 3: iam, 1 checks: fails (" in out


@pytest.mark.parametrize("content", [
    # Prowler writes no file for a part with no finding, and the run goes on.
    None, "[]", " [ \n ] \n"])
def test_a_part_with_no_record_adds_nothing(run, tmp_path, content):
    call, _ = fake_prowler(write={"iam": content is not None}, content={"iam": content})
    assert run.run(args(run, tmp_path), now=lambda: NOW, call=call) == 0
    (path,) = (tmp_path / "results/prowler/aws").glob("*.ocsf.json")
    assert json.loads(path.read_text()) == [record(c) for c in ["ec2_instance_one", "ec2_instance_two", "s3_bucket_one"]]


def test_the_records_are_copied_in_chunks_with_the_space_around_them(run, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "CHUNK", 3)
    parts = ["  \n[\n" + json.dumps(record("a_one"), indent=4) + ",\n" + json.dumps(record("a_two")) + "\n]\n\n  ",
             "[" + json.dumps(record("b_one")) + "]", "[ ]", "[" + " " * 10 + json.dumps(record("c_one")) + " " * 10 + "]"]
    joined_path = tmp_path / "joined.json"
    records = False
    with joined_path.open("wb") as joined:
        joined.write(b"[")
        for index, text in enumerate(parts):
            path = tmp_path / f"part{index}.json"
            path.write_text(text)
            records = run.append_records(path, joined, records)
        joined.write(b"]")
    assert records
    assert json.loads(joined_path.read_text()) == [record(c) for c in ["a_one", "a_two", "b_one", "c_one"]]


def test_a_part_reports_its_code_and_the_peak_of_its_process(run):
    code, peak = run.child([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert code == 3 and peak > 2**20
    code, _ = run.child([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"])
    assert code == -9


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


# The runs of the whole AWS scan list in one process ended at 768 MiB, then at
# 1536 MiB: a run is now one process for each service (prowler/README.md,
# "Memory"). A Cloudflare run has a few clients in one place.
MEMORY_LIMITS = {"aws": "1536Mi", "cloudflare": "768Mi"}
MEMORY_REQUESTS = {"aws": "1024Mi", "cloudflare": "512Mi"}


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
        # The limit of each provider (kubernetes/apps/security/README.md, "Memory").
        assert container["resources"]["limits"]["memory"] == MEMORY_LIMITS[provider], job["metadata"]["name"]
        assert container["resources"]["requests"]["memory"] == MEMORY_REQUESTS[provider], job["metadata"]["name"]
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


def test_each_token_has_a_lease_that_the_token_alert_reads():
    import re
    import sys
    sys.path.insert(0, str(ROOT / "observability"))
    import rules
    alert = next(rule for rule in rules.security_alerts() if rule["alert"] == "SecurityTokenExpiring")
    assert rules.SECURITY_TOKEN_LEASES in alert["expr"]
    tokens = {"cloudflare": "CLOUDFLARE_API_TOKEN"}
    assert sorted(path.name for path in APP.glob("lease-*.yaml")) == [f"lease-{provider}-token.yaml" for provider in tokens]
    for provider, variable in tokens.items():
        lease = yaml.safe_load((APP / f"lease-{provider}-token.yaml").read_text())
        assert lease["kind"] == "Lease"
        assert re.fullmatch(rules.SECURITY_TOKEN_LEASES, lease["metadata"]["name"])
        # The form of a MicroTime, which the API server takes; not its value, as
        # the owner writes the end date of each token there.
        renew = lease["spec"]["renewTime"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", renew), renew
        datetime.datetime.fromisoformat(renew)
        cronjob = (APP / f"cronjob-{provider}.yaml").read_text()
        assert variable in (APP.parent / "README.md").read_text() and f"prowler-{provider}" in cronjob


def test_a_listed_run_with_a_token_lists_its_lease_and_its_secret():
    """Without its Lease, SecurityTokenExpiring has no series for a token, and the
    run fails on the day the token ends with no warning before."""
    # By the file name, so "cronjob-cloudflare.yaml" and "./cronjob-cloudflare.yaml" are the same entry.
    resources = {Path(item).name for item in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]}
    for provider in ("cloudflare", "github"):
        parts = {f"cronjob-{provider}.yaml", f"lease-{provider}-token.yaml", f"secret-{provider}.sops.yaml"}
        listed = parts & resources
        assert listed in (set(), parts), f"{provider}: list the Secret, the Lease and the CronJob together, not {sorted(listed)}"


def test_github_has_no_run_and_each_run_has_its_scan_list():
    """A Prowler run for GitHub would fail each day (a token of a personal account
    reads no check of the organization): the API checks take its lines."""
    import sys
    sys.path.insert(0, str(ROOT / "security"))
    import generate
    assert "github" not in generate.PROWLER_RUNS
    assert sorted(path.name for path in (APP / "config").glob("checks-*.json")) == sorted(
        f"checks-{provider}.json" for provider in generate.PROWLER_RUNS)
    assert not list(APP.glob("*github*"))
    policies = (APP / "networkpolicy.yaml").read_text()
    assert "github" not in policies


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
