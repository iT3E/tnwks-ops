"""The checks of the agent platform (docs/apps/agent-platform.md, "Checks").
A CronJob in each agents-<name> namespace runs one of:

  platform_checks.py isolation
      This trust domain reaches its own sandbox gateway and no other one,
      and its client certificate works there.
  platform_checks.py probe
      A sandbox of this trust domain reaches nothing that no sandbox policy
      names, and holds no service account token. Its two pods (workload and
      supervisor) run, and each is printed with its node.

Each probe prints one JSON line. The exit code is 1 when a probe fails, so
the Job fails. Standard library only; the OpenShell CLI comes from the init
container (/tools/openshell). No secret value is printed.
"""

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

OWN = os.environ["OWN_NAMESPACE"]
DOMAINS = os.environ["TRUST_DOMAIN_NAMESPACES"].split()
CLI = os.environ.get("OPENSHELL_CLI", "/tools/openshell")
CLIENT_TLS = os.environ.get("CLIENT_TLS_DIR", "/client-tls")
PROBE_IMAGE = os.environ.get("PROBE_IMAGE", "")
SANDBOX = os.environ.get("PROBE_SANDBOX", "platform-probe")
PORT = 8080
# The model gateway (docs/apps/litellm.md). No sandbox policy names it.
MODEL_GATEWAY = os.environ.get("MODEL_GATEWAY", "litellm-app.ai.svc.cluster.local")
failures = 0


def gateway(namespace: str) -> str:
    return f"openshell.{namespace}.svc.cluster.local"


def report(check: str, target: str, expected: str, got: str) -> None:
    global failures
    ok = got.startswith(expected)
    failures += not ok
    print(json.dumps({"check": check, "domain": OWN, "target": target,
                      "expected": expected, "got": got, "ok": ok}), flush=True)


def tcp(host: str, port: int, timeout: float = 5) -> str:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return "connected"
    except OSError as e:
        return f"blocked: {type(e).__name__}"


def cli(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([CLI, *args], capture_output=True, text=True, timeout=timeout)


def tail(result: subprocess.CompletedProcess) -> str:
    return " ".join((result.stderr or result.stdout or "").split())[-300:]


def setup_cli() -> None:
    """Register this domain's gateway with its client certificate, as the
    agent pod of the domain does (CLI config under XDG_CONFIG_HOME)."""
    mtls = os.path.join(os.environ["XDG_CONFIG_HOME"], "openshell", "gateways",
                        os.environ["OPENSHELL_GATEWAY"], "mtls")
    os.makedirs(mtls, exist_ok=True)
    for name in ("ca.crt", "tls.crt", "tls.key"):
        shutil.copyfile(os.path.join(CLIENT_TLS, name), os.path.join(mtls, name))
    os.chmod(os.path.join(mtls, "tls.key"), 0o600)
    result = cli("gateway", "add", f"https://{gateway(OWN)}:{PORT}", "--local",
                 "--name", os.environ["OPENSHELL_GATEWAY"])
    if result.returncode != 0:
        sys.exit(f"gateway add failed: {tail(result)}")


def isolation() -> None:
    for namespace in DOMAINS:
        expected = "connected" if namespace == OWN else "blocked"
        report("isolation", f"{gateway(namespace)}:{PORT}", expected,
               tcp(gateway(namespace), PORT))
    setup_cli()
    result = cli("sandbox", "list")
    report("isolation", "own gateway, with the client certificate", "ok",
           "ok" if result.returncode == 0 else f"failed: {tail(result)}")


# Runs inside the sandbox. Every connection must fail: a sandbox reaches only
# the addresses that its policy names, and this one has none.
PROBE = r'''
import json, os, socket, urllib.request
def tcp(host, port):
    try:
        socket.create_connection((host, port), timeout=4).close(); return "connected"
    except Exception as e:
        return "blocked: " + type(e).__name__
def http(url):
    try:
        r = urllib.request.urlopen(url, timeout=6); return "HTTP %s" % r.status
    except Exception as e:
        return "blocked: " + type(e).__name__
out = {
    "Kubernetes API, direct": tcp(os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"), 443),
    "internet, direct": tcp("1.1.1.1", 443),
    "own sandbox gateway, direct": tcp("GATEWAY_IP", 8080),
    "model gateway, direct": tcp("MODEL_GATEWAY_IP", 4000),
    "metadata address, direct": tcp("169.254.169.254", 80),
    "internet, through the sandbox proxy": http("https://example.com/"),
    "model gateway, through the sandbox proxy": http("http://MODEL_GATEWAY:4000/health/liveliness"),
    "service account token": "present" if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token") else "blocked: absent",
}
print("PROBE " + json.dumps(out))
'''


def kube_get(path: str) -> dict:
    """GET from the API server with this pod's service account (rbac.yaml:
    read Sandbox records and pods in this namespace only)."""
    sa = "/var/run/secrets/kubernetes.io/serviceaccount"
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    with open(f"{sa}/token") as f:
        token = f.read()
    request = urllib.request.Request(f"https://{host}:{port}{path}",
                                     headers={"Authorization": f"Bearer {token}"})
    context = ssl.create_default_context(cafile=f"{sa}/ca.crt")
    with urllib.request.urlopen(request, timeout=15, context=context) as r:
        return json.load(r)


def sandbox_pods() -> list[dict]:
    """The pods of the probe sandbox, while it runs.

    OpenShell 0.1.7 labels the Sandbox record (agents.x-k8s.io) with the
    sandbox name and id, and each of the sandbox's two pods with
    openshell.ai/boundary-pair (the id in lower case) and
    openshell.ai/boundary-role (workload or supervisor). The pods carry no
    name label, so the id comes from the record.
    """
    records = kube_get(f"/apis/agents.x-k8s.io/v1beta1/namespaces/{OWN}/sandboxes"
                       f"?labelSelector=openshell.ai/sandbox-name%3D{SANDBOX}")["items"]
    if len(records) != 1:
        raise LookupError(f"{len(records)} Sandbox records named {SANDBOX}")
    pair = records[0]["metadata"]["labels"]["openshell.ai/sandbox-id"].lower()
    pods = kube_get(f"/api/v1/namespaces/{OWN}/pods"
                    f"?labelSelector=openshell.ai/boundary-pair%3D{pair}")["items"]
    return [{"pod": p["metadata"]["name"],
             "role": p["metadata"].get("labels", {}).get("openshell.ai/boundary-role"),
             "node": p["spec"].get("nodeName"),
             "phase": p.get("status", {}).get("phase")}
            for p in pods]


def report_pods() -> None:
    """Both pods of the sandbox exist and run, each on a node. The nodes are
    printed; whether they differ is up to the scheduler (no rule asks for it)."""
    try:
        pods = sandbox_pods()
    except (OSError, ValueError, LookupError, KeyError) as e:
        report("probe", "sandbox pods", "found", f"failed: {type(e).__name__}: {e}")
        return
    for pod in pods:
        print(json.dumps({"check": "probe", "domain": OWN, "sandbox_pod": pod}), flush=True)
    roles = sorted(p["role"] or "" for p in pods)
    running = all(p["phase"] == "Running" and p["node"] for p in pods)
    report("probe", "sandbox pods", "found",
           "found: workload and supervisor, running"
           if roles == ["supervisor", "workload"] and running
           else f"missing: roles {roles}, phases {[p['phase'] for p in pods]}")
    nodes = sorted({p["node"] for p in pods if p["node"]})
    print(json.dumps({"check": "probe", "domain": OWN, "nodes": nodes,
                      "different_nodes": len(nodes) > 1}), flush=True)


def probe() -> None:
    setup_cli()
    cli("sandbox", "delete", SANDBOX)  # a run that stopped can leave one
    created = cli("sandbox", "create", "--name", SANDBOX, "--from", PROBE_IMAGE,
                  "--detach", "--no-tty", "--", "sleep", "3600", timeout=300)
    if created.returncode != 0:
        report("probe", "sandbox create", "ok", f"failed: {tail(created)}")
        return
    try:
        result = None
        deadline = time.monotonic() + 300
        # The direct probes use addresses: the sandbox resolves no names, so a
        # name would test DNS only. The check pod resolves them.
        code = (PROBE.replace("MODEL_GATEWAY_IP", socket.gethostbyname(MODEL_GATEWAY))
                .replace("MODEL_GATEWAY", MODEL_GATEWAY)
                .replace("GATEWAY_IP", socket.gethostbyname(gateway(OWN))))
        while time.monotonic() < deadline:
            result = cli("sandbox", "exec", "--name", SANDBOX, "--no-tty", "--",
                         "python3", "-c", code, timeout=120)
            if result.returncode == 0 and "PROBE " in result.stdout:
                break
            time.sleep(10)
        line = next((l for l in (result.stdout if result else "").splitlines()
                     if l.startswith("PROBE ")), None)
        if line is None:
            report("probe", "sandbox exec", "ok", f"failed: {tail(result)}")
            return
        for target, got in json.loads(line[len("PROBE "):]).items():
            report("probe", target, "blocked", got)
        # Before the finally below deletes the sandbox.
        report_pods()
    finally:
        cli("sandbox", "delete", SANDBOX)


if __name__ == "__main__":
    {"isolation": isolation, "probe": probe}[sys.argv[1]]()
    sys.exit(1 if failures else 0)
