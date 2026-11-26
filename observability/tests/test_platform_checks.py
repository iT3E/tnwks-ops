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
import yaml
from test_hand_written_rules import docker
from test_mcp_gateway import DROPPER

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
    role = next(d for d in yaml.safe_load_all(RBAC.read_text()) if d and d["kind"] == "Role")
    deletes = [r for r in role["rules"] if "delete" in r["verbs"]]
    assert deletes == [{
        "apiGroups": ["agents.x-k8s.io"], "resources": ["sandboxes"],
        "resourceNames": ["default--platform-probe"], "verbs": ["delete"],
    }]


# The straight tries of the isolation check and of the MCP server of
# finance pass on a time out only, as the tries at the read servers and the
# vault servers do (test_main_read_servers.py, test_main_vault.py): a policy
# drop in this cluster is silent. A refusal is what a Service with no ready
# pod gives at once, and a name that does not resolve reaches nothing: both
# prove nothing about the policy. An answer is a hole.

OUTCOMES = [
    ("timeout", True, "blocked: TimeoutError"),
    ("refused", False, "refused: ConnectionRefusedError (no ready backend or a closed port: proves nothing)"),
    ("answer", False, "connected"),
    ("no name", False, "unresolved: gaierror"),
]


def fake_sockets(checks, monkeypatch, outcome, own=None):
    """Each straight try meets `outcome`, but a try at the host `own`
    connects (the own gateway of the isolation check)."""

    def getaddrinfo(host, port, *args, **kwargs):
        if outcome == "no name" and host != own:
            raise checks.socket.gaierror(-2, "Name or service not known")
        return [(2, 1, 6, "", ("192.0.2.1", port))]

    class Answer:
        def close(self):
            pass

    def create_connection(address, timeout=None):
        if address[0] == own or outcome == "answer":
            return Answer()
        if outcome == "refused":
            raise ConnectionRefusedError(111, "Connection refused")
        raise TimeoutError("timed out")

    monkeypatch.setattr(checks.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(checks.socket, "create_connection", create_connection)


@pytest.mark.parametrize(("outcome", "ok", "got"), OUTCOMES)
def test_the_isolation_rows_pass_on_a_time_out_only(checks, monkeypatch, capsys, outcome, ok, got):
    fake_sockets(checks, monkeypatch, outcome, own=checks.gateway("agents-finance"))
    monkeypatch.setattr(checks, "cli", lambda *a, **k: done())
    checks.isolation()
    lines = {line["target"]: line for line in map(json.loads, capsys.readouterr().out.splitlines())}
    other = lines[f"{checks.gateway('agents-main')}:8080"]
    assert (other["expected"], other["ok"], other["got"]) == ("blocked", ok, got)
    own = lines[f"{checks.gateway('agents-finance')}:8080"]
    assert (own["expected"], own["ok"], own["got"]) == ("connected", True, "connected")
    assert lines["own gateway, with the client certificate"]["ok"]
    assert checks.failures == (0 if ok else 1)


@pytest.mark.parametrize(("outcome", "ok", "got"), OUTCOMES)
def test_the_try_at_the_mcp_server_of_finance_passes_on_a_time_out_only(checks, monkeypatch, capsys,
                                                                       outcome, ok, got):
    fake_sockets(checks, monkeypatch, outcome)
    monkeypatch.setattr(checks, "MCP_SERVER", "firefly-mcp.agents-finance.svc.cluster.local:3000")
    # The calls through the gateway are not this test's: each gets its 401.
    monkeypatch.setattr(checks, "listed", lambda *a, **k: "refused: HTTP 401")
    checks.domain("mcp-gateway", checks.PENDING, checks.PENDING)
    lines = {line["target"]: line for line in map(json.loads, capsys.readouterr().out.splitlines())}
    row = lines["MCP server, direct from a pod that is not the gateway"]
    assert (row["expected"], row["ok"], row["got"]) == ("blocked", ok, got)
    assert all(line["ok"] for t, line in lines.items() if t != row["target"])
    assert checks.failures == (0 if ok else 1)


# The same rows on real sockets: the script runs in the pinned image of the
# checks, and the names of its tries lead, by hosts lines, to a container
# that drops each new SYN (a time out), to one that listens on no port (a
# refusal), and to one that answers; one name is in no hosts line.
ANSWER = r'''
import socket, threading, time
def serve(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(16)
    while True:
        c, _ = s.accept()
        c.close()
for port in (8080, 3000):
    threading.Thread(target=serve, args=(port,), daemon=True).start()
time.sleep(3600)
'''

# Waits up to 60 s until each port of the lab is in its state.
READY = r'''
import socket, sys, time
answer, drop, refuse = sys.argv[1:4]
want = [((answer, 8080), "connected"), ((answer, 3000), "connected"), ((drop, 8080), "timeout"),
        ((drop, 3000), "timeout"), ((refuse, 8080), "refused")]
def state(address):
    try:
        socket.create_connection(address, timeout=1).close()
        return "connected"
    except TimeoutError:
        return "timeout"
    except ConnectionRefusedError:
        return "refused"
    except OSError as e:
        return type(e).__name__
end = time.monotonic() + 60
while True:
    got = [state(a) for a, _ in want]
    if got == [w for _, w in want]:
        break
    if time.monotonic() > end:
        raise SystemExit("lab not ready: %s" % got)
    time.sleep(0.5)
'''

DRIVER = r'''
import importlib.util, json, subprocess, sys
spec = importlib.util.spec_from_file_location("platform_checks", "/scripts/platform_checks.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
checks.setup_cli = lambda: None
checks.cli = lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="", stderr="")
checks.listed = lambda *a, **k: "refused: HTTP 401"
checks.isolation()
for name in sys.argv[1:]:
    checks.MCP_SERVER = name + ":3000"
    checks.domain("mcp-gateway", checks.PENDING, checks.PENDING)
'''


def checks_image() -> str:
    """The image of the gateway probe of finance."""
    path = ROOT / "kubernetes/apps/ai/agents/finance/checks/app/mcp-gateway-probe.yaml"
    cron = next(d for d in yaml.safe_load_all(path.read_text()) if d and d["kind"] == "CronJob")
    return cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["image"]


def test_the_changed_rows_on_real_sockets(tmp_path):
    tool = docker()
    image = checks_image()
    tag = tmp_path.name[-8:].replace("_", "x").lower()
    net = f"pcheck-{tag}"
    names = {k: f"pcheck-{k}-{tag}" for k in ("drop", "refuse", "answer")}

    def sh(*args, check=True):
        result = subprocess.run([tool, *args], capture_output=True, text=True, timeout=300, check=False)
        assert not check or result.returncode == 0, result.stdout + result.stderr
        return result

    try:
        sh("network", "create", "--internal", net)
        common = ["run", "-d", "--network", net, "--read-only", "--cap-drop", "ALL",
                  "--user", "65534"]
        sh(*common, "--name", names["drop"], image, "python3", "-I", "-c", DROPPER)
        sh(*common, "--name", names["refuse"], image, "python3", "-I", "-c", "import time; time.sleep(3600)")
        sh(*common, "--name", names["answer"], image, "python3", "-I", "-c", ANSWER)
        ip = {k: sh("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", v).stdout.strip()
              for k, v in names.items()}
        gw = "openshell.{}.svc.cluster.local"
        hosts = {gw.format("agents-own"): ip["answer"], gw.format("agents-drop"): ip["drop"],
                 gw.format("agents-refuse"): ip["refuse"], gw.format("agents-answer"): ip["answer"],
                 "server-drop.test": ip["drop"], "server-refuse.test": ip["refuse"],
                 "server-answer.test": ip["answer"]}
        # Each port in its state before the run: the answer accepts, the
        # dropper's full queue makes a connect time out, and the empty
        # container refuses.
        sh("run", "--rm", "--network", net, "--read-only", "--cap-drop", "ALL", "--user", "65534", image,
           "python3", "-I", "-c", READY, ip["answer"], ip["drop"], ip["refuse"])
        result = sh("run", "--rm", "--network", net, "--read-only", "--cap-drop", "ALL",
                    "--user", "65534", *[x for n, a in hosts.items() for x in ("--add-host", f"{n}:{a}")],
                    "-e", "OWN_NAMESPACE=agents-own",
                    "-e", "TRUST_DOMAIN_NAMESPACES=agents-own agents-drop agents-refuse agents-answer agents-none",
                    "-v", f"{SCRIPT.parent}:/scripts:ro", image, "python3", "-I", "-c", DRIVER,
                    "server-drop.test", "server-refuse.test", "server-answer.test", "server-none.test")
        lines = [json.loads(line) for line in result.stdout.splitlines()]
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)
    isolation = {line["target"].split(".")[1]: (line["ok"], line["got"].split(" (")[0])
                 for line in lines if line["check"] == "isolation" and line["target"].startswith("openshell.")}
    assert isolation == {
        "agents-own": (True, "connected"),
        "agents-drop": (True, "blocked: TimeoutError"),
        "agents-refuse": (False, "refused: ConnectionRefusedError"),
        "agents-answer": (False, "connected"),
        "agents-none": (False, "unresolved: gaierror"),
    }
    server = [(line["ok"], line["got"].split(" (")[0]) for line in lines
              if line["target"] == "MCP server, direct from a pod that is not the gateway"]
    assert server == [(True, "blocked: TimeoutError"), (False, "refused: ConnectionRefusedError"),
                      (False, "connected"), (False, "unresolved: gaierror")]
