"""The sandbox probe can't block itself with a sandbox that a run left behind.

kubernetes/apps/ai/agents/checks/app/scripts/platform_checks.py runs once a day
in each trust domain. On 2026-11-14 the sandbox gateway of agents-finance
answered the probe's delete with success and left the Sandbox record; the next
day's create of the same name failed with "sandbox already exists". These cases
run the probe with a fake CLI and a fake API server.
"""

import importlib.util
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "kubernetes/apps/ai/agents/checks/app/scripts/platform_checks.py"
RBAC = ROOT / "kubernetes/apps/ai/agents/checks/app/rbac.yaml"
PATH = "/apis/agents.x-k8s.io/v1beta1/namespaces/agents-finance/sandboxes/default--platform-probe"
OURS = {"openshell.ai/managed-by": "openshell", "openshell.ai/sandbox-name": "platform-probe"}


def done(code=0, stdout=""):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


class Cluster:
    """A fake CLI and API server. `record` is the Sandbox record or None;
    `gateway_delete_works` says if the CLI delete removes it."""

    def __init__(self, record=None, gateway_delete_works=True, create_code=0, exec_out=None,
                 delete_answer=None, stuck=False, gateway_delete_times_out=False):
        self.record = record
        self.gateway_delete_works = gateway_delete_works
        self.gateway_delete_times_out = gateway_delete_times_out
        self.create_code = create_code
        self.exec_out = exec_out
        # The answer of the API to the DELETE: None (accepted), 404 (the
        # gateway removed the record just before), 409 (a new record).
        self.delete_answer = delete_answer
        # A record that stays in deletion (a finalizer that never ends).
        self.stuck = stuck
        self.created = False
        self.calls = []

    def cli(self, *args, timeout=120):
        self.calls.append(("cli",) + args[:2])
        if args[:2] == ("sandbox", "delete"):
            if self.gateway_delete_times_out:
                raise subprocess.TimeoutExpired(["openshell"], timeout)
            if self.gateway_delete_works:
                self.record = None
        if args[:2] == ("sandbox", "create"):
            if self.record is not None:
                return done(1, "sandbox already exists")
            if self.create_code == 0:
                self.record = {"metadata": {"uid": "new", "labels": dict(OURS)}}
                self.created = True
            return done(self.create_code)
        if args[:2] == ("sandbox", "exec"):
            if isinstance(self.exec_out, Exception):
                raise self.exec_out
            return done(0, self.exec_out or "")
        return done()

    def kube(self, method, path, body=None):
        assert path == PATH, path
        self.calls.append(("api", method))
        if self.record is None:
            raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
        if method == "DELETE":
            assert body["propagationPolicy"] == "Foreground"
            assert body["preconditions"] == {"uid": self.record["metadata"]["uid"]}
            if self.delete_answer == 404:
                self.record = None
                raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
            if self.delete_answer == 409:
                raise urllib.error.HTTPError(path, 409, "Conflict", None, None)
            if self.stuck:
                self.record["metadata"]["deletionTimestamp"] = "2026-11-15T04:47:05Z"
                return {}
            self.record = None
            return {}
        return self.record


class Clock:
    """A clock that a sleep moves on, so that no case waits."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def checks(monkeypatch):
    monkeypatch.setenv("OWN_NAMESPACE", "agents-finance")
    monkeypatch.setenv("TRUST_DOMAIN_NAMESPACES", "agents-main agents-finance")
    spec = importlib.util.spec_from_file_location("platform_checks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "setup_cli", lambda: None)
    monkeypatch.setattr(module, "time", Clock())
    monkeypatch.setattr(module.socket, "gethostbyname", lambda name: "10.0.0.1")
    monkeypatch.setattr(module, "report_pods", lambda: None)
    return module


def run(checks, monkeypatch, capsys, cluster):
    monkeypatch.setattr(checks, "cli", cluster.cli)
    # raising=False: the script before the fix had no kube(), and these cases
    # must fail on it for the fault itself, not on a missing name.
    monkeypatch.setattr(checks, "kube", cluster.kube, raising=False)
    checks.probe()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return {line["target"]: line for line in lines if "target" in line}


PASS = "PROBE " + json.dumps({"internet, direct": "blocked: PermissionError"})


def test_a_left_record_is_removed_before_the_create(checks, monkeypatch, capsys):
    left = {"metadata": {"uid": "old", "labels": dict(OURS), "creationTimestamp": "2026-11-14T04:47:03Z"}}
    cluster = Cluster(record=left, gateway_delete_works=False, exec_out=PASS)
    # The gateway delete of the end works again, as it did on most days.
    real_cli = cluster.cli

    def cli(*args, timeout=120):
        if args[:2] == ("sandbox", "create"):
            cluster.gateway_delete_works = True
        return real_cli(*args, timeout=timeout)

    cluster.cli = cli
    lines = run(checks, monkeypatch, capsys, cluster)
    # The fault of 2026-11-15: the create failed with "sandbox already exists".
    assert "sandbox create" not in lines, lines.get("sandbox create")
    assert cluster.created
    assert lines["leftover sandbox"]["ok"]
    assert lines["leftover sandbox"]["got"].startswith("removed: default--platform-probe, made 2026-11-14")
    assert ("api", "DELETE") in cluster.calls
    assert cluster.calls.index(("api", "DELETE")) < cluster.calls.index(("cli", "sandbox", "create"))
    assert lines["internet, direct"]["ok"]
    assert lines["sandbox removed"] == {**lines["sandbox removed"], "got": "gone", "ok": True}
    assert checks.failures == 0


def test_no_leftover_says_so_and_passes(checks, monkeypatch, capsys):
    cluster = Cluster(exec_out=PASS)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert cluster.created
    assert lines["leftover sandbox"]["got"] == "none"
    assert ("api", "DELETE") not in cluster.calls
    assert checks.failures == 0


def test_a_record_that_is_not_the_probes_is_left_alone(checks, monkeypatch, capsys):
    other = {"metadata": {"uid": "x", "labels": {"openshell.ai/managed-by": "someone"}}}
    cluster = Cluster(record=other, gateway_delete_works=False, exec_out=PASS)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert not lines["leftover sandbox"]["ok"]
    assert ("api", "DELETE") not in cluster.calls
    assert ("cli", "sandbox", "create") not in cluster.calls
    assert checks.failures == 1


def test_a_record_left_at_the_end_fails_the_run_and_is_removed(checks, monkeypatch, capsys):
    # The day of the fault: the gateway answers the delete and leaves the record.
    cluster = Cluster(gateway_delete_works=False, exec_out=PASS)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert lines["internet, direct"]["ok"]
    removed = lines["sandbox removed"]
    assert not removed["ok"]
    assert removed["got"].startswith("left after the gateway delete; removed: default--platform-probe")
    assert cluster.record is None
    assert checks.failures == 1


def test_a_failure_after_the_create_still_cleans_up(checks, monkeypatch, capsys):
    cluster = Cluster(exec_out=subprocess.TimeoutExpired(["openshell"], 120))
    lines = run(checks, monkeypatch, capsys, cluster)
    assert not lines["probe run"]["ok"]
    assert lines["sandbox removed"]["got"] == "gone"
    assert cluster.record is None
    assert checks.failures == 1


def test_a_failed_create_still_cleans_up(checks, monkeypatch, capsys):
    cluster = Cluster(create_code=1)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert not lines["sandbox create"]["ok"]
    assert lines["sandbox removed"]["got"] == "gone"
    assert checks.failures == 1


LEFT = {"metadata": {"uid": "old", "labels": dict(OURS), "creationTimestamp": "2026-11-14T04:47:03Z"}}


def test_a_404_on_the_delete_counts_as_gone(checks, monkeypatch, capsys):
    # The gateway removed the record between the read and the delete.
    cluster = Cluster(record=dict(LEFT), gateway_delete_works=False, delete_answer=404,
                      exec_out=PASS)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert cluster.created
    assert lines["leftover sandbox"]["ok"]
    assert lines["leftover sandbox"]["got"].startswith("removed: default--platform-probe")


def test_a_409_on_the_delete_stops_the_run_with_no_create(checks, monkeypatch, capsys):
    # The record was replaced by a new one of the same name.
    cluster = Cluster(record=dict(LEFT), gateway_delete_works=False, delete_answer=409,
                      exec_out=PASS)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert not lines["leftover sandbox"]["ok"]
    assert "409" in lines["leftover sandbox"]["got"]
    assert not cluster.created
    assert checks.failures == 1


def test_a_record_stuck_in_deletion_stops_the_run_with_no_create(checks, monkeypatch, capsys):
    cluster = Cluster(record=dict(LEFT), gateway_delete_works=False, stuck=True, exec_out=PASS)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert not lines["leftover sandbox"]["ok"]
    assert "still there" in lines["leftover sandbox"]["got"]
    assert not cluster.created
    assert checks.failures == 1


def test_a_record_stuck_in_deletion_at_the_end_fails_the_run(checks, monkeypatch, capsys):
    cluster = Cluster(gateway_delete_works=False, stuck=True, exec_out=PASS)
    lines = run(checks, monkeypatch, capsys, cluster)
    assert cluster.created
    removed = lines["sandbox removed"]
    assert not removed["ok"]
    assert removed["got"].startswith("left: TimeoutError")


def test_a_gateway_delete_that_times_out_at_the_end_still_removes_the_record(
        checks, monkeypatch, capsys):
    cluster = Cluster(exec_out=PASS)
    real_cli = cluster.cli

    def cli(*args, timeout=120):
        # The gateway delete works before the create and times out after it.
        if args[:2] == ("sandbox", "delete") and cluster.created:
            cluster.gateway_delete_times_out = True
        return real_cli(*args, timeout=timeout)

    cluster.cli = cli
    lines = run(checks, monkeypatch, capsys, cluster)
    assert cluster.created
    removed = lines["sandbox removed"]
    assert not removed["ok"]
    assert removed["got"].startswith("left after a failed gateway delete (TimeoutExpired); removed:")
    assert cluster.record is None


def test_the_role_deletes_only_the_probes_record():
    import yaml

    role = next(d for d in yaml.safe_load_all(RBAC.read_text()) if d and d["kind"] == "Role")
    deletes = [r for r in role["rules"] if "delete" in r["verbs"]]
    assert deletes == [{
        "apiGroups": ["agents.x-k8s.io"], "resources": ["sandboxes"],
        "resourceNames": ["default--platform-probe"], "verbs": ["delete"],
    }]
