"""The API checks (kubernetes/apps/security/api-checks) write a result file that the
converter takes, and a test that does not run has no entry, never a pass.

Each call gets a fake answer: no test reaches Prometheus, the feed of the vendor or
HCP Terraform. The versions and the dates are invented.
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
APP = ROOT / "kubernetes/apps/security/api-checks"
REGISTRY = ROOT / "security/registry"
sys.path.insert(0, str(ROOT / "security/converter"))
import converter

UTC = datetime.UTC
NOW = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
PROMETHEUS = "http://192.0.2.10:9090"
FEED = "https://cdn.mikrotik.com/routeros/latest-stable.rss"
ORG = "https://app.terraform.io/api/v2/organizations/tnwks-ops"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checks = load("api_checks", APP / "app/scripts/api_checks.py")
prowler = load("prowler_run_for_api_checks", ROOT / "kubernetes/apps/security/prowler/app/scripts/run.py")


def prometheus_answer(*versions: str) -> bytes:
    return json.dumps({"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {"version": version}, "value": [1794571200, "1"]} for version in versions]}}).encode()


def feed_answer(version: str, published: datetime.datetime) -> bytes:
    date = published.strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>RouterOS</title>'
            f"<item><title>RouterOS {version} [stable]</title><pubDate>{date}</pubDate></item>"
            f"</channel></rss>").encode()


def tfc_answer(policy: str) -> bytes:
    return json.dumps({"data": {"type": "organizations", "attributes": {
        "name": "tnwks-ops", "collaborator-auth-policy": policy}}}).encode()


class Fake:
    """Answers by the start of the URL; each call is kept."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append((url, headers or {}))
        for start, answer in self.answers.items():
            if url.startswith(start):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"no fake for {url}")


def fake(running="7.90.1 (stable)", newest="7.90.1", age_days=30, tfc=None):
    answers = {
        PROMETHEUS: (200, prometheus_answer(running)) if isinstance(running, str) else running,
        FEED: (200, feed_answer(newest, NOW - datetime.timedelta(days=age_days))),
    }
    if tfc is not None:
        answers[ORG] = tfc
    return Fake(answers)


def args(tmp_path, *extra):
    return checks.parse([f"--prometheus={PROMETHEUS}", f"--results={tmp_path / 'results'}",
                         f"--accepted={tmp_path / 'accepted.json'}", *extra])


def run(tmp_path, monkeypatch, get, token=None, *extra):
    if token is None:
        monkeypatch.delenv("TFE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TFE_TOKEN", token)
    (tmp_path / "results").mkdir(exist_ok=True)
    code = checks.run(args(tmp_path, *extra), now=lambda: NOW, get=get)
    path = tmp_path / "results/api-check/results.json"
    return code, (json.loads(path.read_text()) if path.exists() else None)


def by_check(document):
    return {entry["check"]: entry for entry in document["results"]}


# --- The firmware of the router ---------------------------------------------------------

@pytest.mark.parametrize(("running", "newest", "age_days", "failed"), [
    ("7.90.1 (stable)", "7.90.1", 400, 0),     # runs the newest release
    ("7.90.1 (stable)", "7.90.2", 15, 1),      # older, and the release is 15 days out
    ("7.90.1 (stable)", "7.90.2", 14, 0),      # older, and the release is 14 days out: the grace
    ("7.90.1 (stable)", "7.91", 30, 1),        # a minor release
    ("7.90.10 (stable)", "7.90.9", 30, 0),     # numbers, not text: 10 is after 9
    ("7.91.1 (testing)", "7.90.2", 30, 0),     # newer than the newest stable
])
def test_the_router_fails_when_a_release_it_does_not_run_is_out_past_the_grace(
        tmp_path, monkeypatch, running, newest, age_days, failed):
    code, document = run(tmp_path, monkeypatch, fake(running, newest, age_days))
    assert code == 0
    entry = by_check(document)["routeros-firmware"]
    assert (entry["system"], entry["failed"], entry["severity"]) == ("sce-rtr01", failed, "high")
    assert ("findings" in entry) == bool(failed)
    if failed:
        assert entry["findings"][0]["resource"] == "sce-rtr01"


def test_the_version_comes_from_the_series_of_mktxp_for_the_router():
    get = fake()
    assert checks.running_version(PROMETHEUS, "sce-rtr01", get) == (7, 90, 1)
    url = get.calls[0][0]
    assert url.startswith(f"{PROMETHEUS}/api/v1/query?query=")
    assert "mktxp_system_uptime" in url and "sce-rtr01" in url


BROKEN_ROUTER = {
    "no series": (200, prometheus_answer()),
    "two versions": (200, prometheus_answer("7.90.1 (stable)", "7.90.2 (stable)")),
    "a version that is not one": (200, prometheus_answer("stable")),
    "Prometheus answers 503": (503, b""),
    "an answer that is not JSON": (200, b"<html>"),
    "a query error": (200, json.dumps({"status": "error", "error": "x"}).encode()),
    "no answer": checks.QueryError("no answer from 192.0.2.10: URLError"),
}


@pytest.mark.parametrize("case", BROKEN_ROUTER)
def test_a_router_query_that_fails_gives_no_entry_and_fails_the_run(tmp_path, monkeypatch, case):
    code, document = run(tmp_path, monkeypatch, fake(running=BROKEN_ROUTER[case]))
    assert code == 1
    assert document["results"] == []


@pytest.mark.parametrize("answer", [
    (200, b"not xml"),
    (404, b""),
    (200, (b'<rss><channel><item><title>RouterOS 7.90.1 [testing]</title>'
          b"<pubDate>Fri, 13 Nov 2026 00:00:00 +0000</pubDate></item></channel></rss>")),
    (200, b"<rss><channel><item><title>RouterOS 7.90.1 [stable]</title></item></channel></rss>"),
    (200, (b"<rss><channel><item><title>RouterOS 7.90.1 [stable]</title>"
          b"<pubDate>not a date</pubDate></item></channel></rss>")),
])
def test_a_feed_with_no_dated_stable_release_gives_no_entry(tmp_path, monkeypatch, answer):
    get = fake()
    get.answers[FEED] = answer
    code, document = run(tmp_path, monkeypatch, get)
    assert code == 1
    assert document["results"] == []


def test_the_newest_of_several_stable_releases_counts():
    body = (b"<rss><channel>"
            b"<item><title>RouterOS 7.90.2 [stable]</title><pubDate>Mon, 02 Nov 2026 00:00:00 +0000</pubDate></item>"
            b"<item><title>RouterOS 7.89.9 [stable]</title><pubDate>Thu, 01 Oct 2026 00:00:00 +0000</pubDate></item>"
            b"</channel></rss>")
    newest, published = checks.newest_release(lambda url, headers=None: (200, body))
    assert newest == (7, 90, 2)
    assert published == datetime.datetime(2026, 11, 2, tzinfo=UTC)


# --- Terraform Cloud --------------------------------------------------------------------

@pytest.mark.parametrize(("policy", "failed"), [("two_factor_mandatory", 0), ("password", 1)])
def test_terraform_cloud_fails_when_the_organization_does_not_require_two_factors(
        tmp_path, monkeypatch, capsys, policy, failed):
    get = fake(tfc=(200, tfc_answer(policy)))
    code, document = run(tmp_path, monkeypatch, get, token="fake-token-value")
    assert code == 0
    entry = by_check(document)["tfc-mfa-required"]
    assert (entry["system"], entry["failed"], entry["severity"]) == ("tnwks-ops", failed, "medium")
    url, headers = get.calls[-1]
    assert url == ORG
    assert headers["Authorization"] == "Bearer fake-token-value"
    text = (tmp_path / "results/api-check/results.json").read_text() + capsys.readouterr().out
    assert "fake-token-value" not in text, "no token in the file or the log"


def test_with_no_token_the_test_of_terraform_cloud_does_not_run_and_the_run_passes(tmp_path, monkeypatch):
    get = fake()
    code, document = run(tmp_path, monkeypatch, get)
    assert code == 0
    assert set(by_check(document)) == {"routeros-firmware"}
    assert not any(url.startswith(ORG) for url, _ in get.calls)


@pytest.mark.parametrize("answer", [(401, b""), (404, b""), (200, b"{}"), (200, tfc_answer("")),
                                    checks.QueryError("no answer from app.terraform.io: URLError")])
def test_a_terraform_cloud_query_that_fails_gives_no_entry_and_fails_the_run(tmp_path, monkeypatch, answer):
    code, document = run(tmp_path, monkeypatch, fake(tfc=answer), token="fake-token-value")
    assert code == 1
    assert set(by_check(document)) == {"routeros-firmware"}


def test_a_redirect_is_refused_so_the_token_never_leaves_for_another_host():
    handler = checks._NoRedirect()
    request = checks.urllib.request.Request(ORG, headers={"Authorization": "Bearer x"})
    with pytest.raises(checks.urllib.error.HTTPError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://example.com/")


# --- The list of accepted findings ------------------------------------------------------

def accepted(tmp_path, entries):
    (tmp_path / "accepted.json").write_text(json.dumps(entries))


def entry(check="routeros-firmware", system="sce-rtr01", until="2026-11-13", source="api-check"):
    return {"source": source, "check": check, "system": system, "reason": "a reason", "until": until}


def test_an_accepted_test_counts_no_resource_and_lists_no_finding(tmp_path, monkeypatch):
    accepted(tmp_path, [entry()])
    _, document = run(tmp_path, monkeypatch, fake(newest="7.90.2", age_days=30))
    result = by_check(document)["routeros-firmware"]
    assert result["failed"] == 0 and "findings" not in result


@pytest.mark.parametrize("other", [entry(until="2026-11-12"), entry(source="prowler"), entry(system="other-router")])
def test_an_entry_that_has_ended_or_is_of_another_test_accepts_nothing(tmp_path, monkeypatch, other):
    accepted(tmp_path, [other])
    _, document = run(tmp_path, monkeypatch, fake(newest="7.90.2", age_days=30))
    assert by_check(document)["routeros-firmware"]["failed"] == 1


@pytest.mark.parametrize("text", ["not json", "{}", '[{"source": "a", "source": "b"}]', "﻿[]"])
def test_a_list_that_can_not_be_read_stops_the_run_and_writes_nothing(tmp_path, monkeypatch, text):
    (tmp_path / "accepted.json").write_text(text, encoding="utf-8")
    code, document = run(tmp_path, monkeypatch, fake())
    assert code == 2
    assert document is None


ITEMS = [
    entry(),
    "not an object",
    {**entry(), "extra": "x"},
    {**entry(), "until": 20271231},
    {**entry(), "reason": "   "},
    entry(check="*"),
    entry(until="2027-02-30"),
    entry(until="20271231"),
    entry(check="routeros-firmware\n"),
]


def test_the_job_reads_the_list_as_the_prowler_job_and_the_converter_do(tmp_path):
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps(ITEMS))
    today = NOW.date()
    _, skipped_here = checks.accepted_tests(path, today)
    _, skipped_prowler = prowler.accepted_checks(path, "sce-rtr01", today)
    _, skipped_converter = converter.accepted_entries(path.read_text(), {"api-check"}, {"sce-rtr01"})
    assert skipped_here == skipped_prowler == skipped_converter == len(ITEMS) - 1


# --- The file ---------------------------------------------------------------------------

def test_the_file_fits_the_converter_and_is_readable_by_another_user(tmp_path, monkeypatch):
    code, _ = run(tmp_path, monkeypatch, fake(newest="7.90.2", age_days=30, tfc=(200, tfc_answer("password"))),
                  token="fake-token-value")
    assert code == 0
    directory = tmp_path / "results/api-check"
    assert sorted(path.name for path in directory.iterdir()) == ["results.json"], "no .tmp is left"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert stat.S_IMODE((directory / "results.json").stat().st_mode) == 0o644
    with (directory / "results.json").open("rb") as file:
        moment, results = converter.read_api(file, NOW.timestamp())
    assert moment == NOW.timestamp()
    assert {(r["check"], r["system"], r["failed"]) for r in results} == {
        ("routeros-firmware", "sce-rtr01", 1), ("tfc-mfa-required", "tnwks-ops", 1)}


def test_each_check_of_the_job_is_a_line_of_the_registry_for_its_system():
    controls = yaml.safe_load((REGISTRY / "controls.yaml").read_text())
    systems = {system["name"]: system for system in yaml.safe_load((REGISTRY / "systems.yaml").read_text())}
    lines = {}
    for control in controls:
        for system_type, items in control["checks"].items():
            for item in items:
                if item["source"] == "api-check":
                    lines.setdefault((item["check"], system_type), []).append(control["id"])
    assert lines[("routeros-firmware", systems[checks.ROUTER]["type"])] == ["12.1"]
    assert lines[("tfc-mfa-required", systems[checks.TFC_ORGANIZATION]["type"])] == ["6.3", "6.5"]
    assert "api-check" in systems[checks.ROUTER]["sources"]


# --- The manifests ----------------------------------------------------------------------

def manifests(name):
    return list(yaml.safe_load_all((APP / "app" / name).read_text()))


def test_the_job_runs_with_no_api_token_no_root_and_the_limit_of_the_budget():
    (job,) = manifests("cronjob.yaml")
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsUser"] == 65534 and pod["securityContext"]["runAsNonRoot"] is True
    assert pod["nodeSelector"] == {"kubernetes.io/hostname": "${SECURITY_NODE}"}
    assert container["resources"]["limits"]["memory"] == "128Mi", "kubernetes/apps/security/README.md, Memory"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert "@sha256:" in container["image"]
    token = next(env for env in container["env"] if env["name"] == "TFE_TOKEN")
    assert token["valueFrom"]["secretKeyRef"] == {"name": "api-check-terraform-cloud", "key": "TFE_TOKEN",
                                                  "optional": True}
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["results"]["persistentVolumeClaim"]["claimName"] == "security-results"
    assert volumes["accepted"]["secret"] == {"secretName": "security-accepted-findings", "optional": True}
    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    assert "subPath" not in mounts["results"]


def test_the_policy_names_only_the_hosts_of_the_tests():
    (policy,) = manifests("networkpolicy.yaml")
    egress = policy["spec"]["egress"]
    names = {item["matchName"] for rule in egress for item in rule.get("toFQDNs", [])}
    assert names == {"cdn.mikrotik.com", "app.terraform.io"}
    cidrs = [cidr for rule in egress for cidr in rule.get("toCIDR", [])]
    assert cidrs == ["${PEER_WINDOWS_HOST_ADDR}/32"]
    (job,) = manifests("cronjob.yaml")
    container = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert "--prometheus=http://${PEER_WINDOWS_HOST_ADDR}:9090" in container["args"]


def test_no_secret_is_in_the_app_until_the_owner_adds_it():
    kustomization = yaml.safe_load((APP / "app/kustomization.yaml").read_text())
    assert not [name for name in kustomization["resources"] if "secret" in name]
    assert not list((APP / "app").glob("*.sops.yaml"))


def test_site_2_runs_the_app_and_site_1_does_not():
    site2 = (ROOT / "kubernetes/clusters/wsl-02/apps/kustomization.yaml").read_text()
    site1 = (ROOT / "kubernetes/clusters/wsl/apps/kustomization.yaml").read_text()
    assert "apps/security/api-checks/ks.yaml" in site2
    assert "api-checks" not in site1
