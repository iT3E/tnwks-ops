"""The checks of the agent platform (docs/apps/agent-platform.md, "Checks").
A CronJob in each agents-<name> namespace runs one of:

  platform_checks.py isolation
      This trust domain reaches its own sandbox gateway and no other one,
      and its client certificate works there.
  platform_checks.py probe
      A sandbox of this trust domain reaches nothing that no sandbox policy
      names, and holds no service account token. Its two pods (workload and
      supervisor) run, and each is printed with its node. The run removes a
      sandbox of its own name that an earlier run left, and fails when its
      own sandbox is still there at its end.
  platform_checks.py firefly-probe
      The proxy in front of the finance app holds (finance only:
      kubernetes/apps/ai/agents/finance/checks), with the token that the
      proxy holds. Its two ports are tried: the write port, straight, as
      the MCP server calls it (and with the calls that only a direct client
      can send: a method override, a form body), and the read port from a
      sandbox made with the sandbox policy of the agent. An allowed read
      reaches the app; each delete and each call off the allow list of the
      proxy is refused. The run removes its sandbox at its end, as the probe
      does.
  platform_checks.py mcp-gateway
      The MCP gateway holds for this trust domain (finance only). No key and
      a key that is not in its config get a 401. The probe key lists exactly
      the tools of the domain's rule, its read reaches the app through the
      MCP server and the proxy, and a tool off the rule is refused at the
      gateway. The key with no tool lists nothing and is refused. A pod that
      is not the gateway can not reach the MCP server.
  platform_checks.py dns
      A pod with its own DNS rule, out of the namespace's rule for any name,
      resolves the names of DNS_ALLOWED and none of DNS_REFUSED (finance
      only, as the pods of the finance app).

Each probe prints one JSON line. The exit code is 1 when a probe fails, so
the Job fails. Standard library only; the OpenShell CLI comes from the init
container (/tools/openshell). No secret value is printed.
"""

import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

OWN = os.environ["OWN_NAMESPACE"]
DOMAINS = os.environ["TRUST_DOMAIN_NAMESPACES"].split()
CLI = os.environ.get("OPENSHELL_CLI", "/tools/openshell")
CLIENT_TLS = os.environ.get("CLIENT_TLS_DIR", "/client-tls")
PROBE_IMAGE = os.environ.get("PROBE_IMAGE", "")
SANDBOX = os.environ.get("PROBE_SANDBOX", "platform-probe")
# OpenShell 0.1.8 names the Sandbox record <workspace>--<name>, and the CLI
# creates in the workspace "default". rbac.yaml lets the probe delete this one
# record and no other.
RECORD = f"default--{SANDBOX}"
PORT = 8080
# The model gateway (docs/apps/litellm.md). No sandbox policy names it.
MODEL_GATEWAY = os.environ.get("MODEL_GATEWAY", "litellm-app.ai.svc.cluster.local")
# The cluster DNS Service. The sandbox workload gets no DNS egress: its names
# resolve through its supervisor, by the sandbox policy.
KUBE_DNS = os.environ.get("KUBE_DNS", "kube-dns.kube-system.svc.cluster.local")
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
    return subprocess.run([CLI, *args], capture_output=True, text=True, timeout=timeout,
                          check=False)


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
import json, os, socket, struct, urllib.request
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
# A DNS query for the API server's name, sent straight to the cluster DNS
# Service, not to the sandbox's own resolver. Any answer is a failure.
QUERY = (b"\x4f\x53\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
         + b"".join(bytes([len(p)]) + p.encode() for p in "kubernetes.default.svc.cluster.local".split("."))
         + b"\x00\x00\x01\x00\x01")
def answer(data):
    return "answered: rcode %d, %d records" % (data[3] & 15, struct.unpack(">H", data[6:8])[0])
def dns_udp(ip):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
        s.sendto(QUERY, (ip, 53)); data = s.recv(512); s.close()
        return answer(data) if len(data) >= 12 else "answered: short"
    except Exception as e:
        return "blocked: " + type(e).__name__
def dns_tcp(ip):
    try:
        s = socket.create_connection((ip, 53), timeout=4)
    except Exception as e:
        return "blocked: " + type(e).__name__
    try:
        s.settimeout(4); s.sendall(struct.pack(">H", len(QUERY)) + QUERY); data = s.recv(514)
        return answer(data[2:]) if len(data) >= 14 else "connected: no answer"
    except Exception as e:
        return "connected: " + type(e).__name__
    finally:
        s.close()
out = {
    "Kubernetes API, direct": tcp(os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"), 443),
    "internet, direct": tcp("1.1.1.1", 443),
    "own sandbox gateway, direct": tcp("GATEWAY_IP", 8080),
    "model gateway, direct": tcp("MODEL_GATEWAY_IP", 4000),
    "metadata address, direct": tcp("169.254.169.254", 80),
    "cluster DNS, direct over UDP": dns_udp("KUBE_DNS_IP"),
    "cluster DNS, direct over TCP": dns_tcp("KUBE_DNS_IP"),
    "internet, through the sandbox proxy": http("https://example.com/"),
    "model gateway, through the sandbox proxy": http("http://MODEL_GATEWAY:4000/health/liveliness"),
    "service account token": "present" if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token") else "blocked: absent",
}
print("PROBE " + json.dumps(out))
'''


def kube(method: str, path: str, body: dict | None = None) -> dict:
    """A request to the API server with this pod's service account (rbac.yaml:
    read Sandbox records and pods in this namespace, and delete the probe's
    own Sandbox record)."""
    sa = "/var/run/secrets/kubernetes.io/serviceaccount"
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    with open(f"{sa}/token") as f:
        token = f.read()
    request = urllib.request.Request(
        f"https://{host}:{port}{path}", method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    context = ssl.create_default_context(cafile=f"{sa}/ca.crt")
    with urllib.request.urlopen(request, timeout=15, context=context) as r:
        return json.load(r)


def kube_get(path: str) -> dict:
    return kube("GET", path)


def record_path() -> str:
    return f"/apis/agents.x-k8s.io/v1beta1/namespaces/{OWN}/sandboxes/{RECORD}"


def record() -> dict | None:
    """The probe's Sandbox record, or None when there is none."""
    try:
        return kube_get(record_path())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def wait_gone(seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while record() is not None:
        if time.monotonic() >= deadline:
            return False
        time.sleep(3)
    return True


# The waits of the probe, in seconds. The Job's deadline (cronjobs.yaml) adds
# them up.
DELETE_TIMEOUT = 60
REMOVE_WAIT = 60
CLEANUP_WAIT = 45


def remove_record(seconds: float = REMOVE_WAIT) -> str:
    """Delete the probe's Sandbox record with the API, and wait until it is
    gone. What the record owns goes with it (Foreground: the record stays
    until they are deleted): its workspace volume claim, two Services and a
    NetworkPolicy. A record of this name that OpenShell did not make for this
    probe is left alone. A record that the gateway removes between the read
    and the delete (404) counts as gone; one that was replaced by a new
    record of the same name (409, the uid precondition) is left alone."""
    found = record()
    if found is None:
        return "none"
    meta = found["metadata"]
    labels = meta.get("labels") or {}
    if (labels.get("openshell.ai/managed-by") != "openshell"
            or labels.get("openshell.ai/sandbox-name") != SANDBOX):
        raise LookupError(f"{RECORD} is not the probe's sandbox (labels {labels})")
    try:
        kube("DELETE", record_path(), {
            "apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground",
            "preconditions": {"uid": meta["uid"]}})
    except urllib.error.HTTPError as e:
        if e.code == 409:
            raise LookupError(f"{RECORD} changed between its read and its delete (409)") from e
        if e.code != 404:
            raise
    if not wait_gone(seconds):
        raise TimeoutError(f"{RECORD} is still there {seconds:.0f} s after its delete")
    return f"removed: {RECORD}, made {meta.get('creationTimestamp', 'at an unknown time')}"


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


def remove_leftover() -> bool:
    """Remove a sandbox of the probe's name that an earlier run left. The
    gateway delete removes a sandbox that the gateway knows. OpenShell 0.1.8
    can answer a delete with success and leave the Sandbox record (its lookup
    found no record, and it logs that at debug level only); the gateway then
    forgets the sandbox, so its delete can't remove the record any more, and
    each create of the same name fails with "sandbox already exists". So the
    record goes with the API too. Prints one line; False stops the run."""
    try:
        cli("sandbox", "delete", SANDBOX, timeout=DELETE_TIMEOUT)
    except subprocess.SubprocessError:
        pass  # the record goes with the API below
    try:
        got = remove_record()
    except (OSError, ValueError, LookupError, KeyError) as e:
        report("probe", "leftover sandbox", "none or removed", f"failed: {type(e).__name__}: {e}")
        return False
    print(json.dumps({"check": "probe", "domain": OWN, "target": "leftover sandbox",
                      "got": got, "ok": True}), flush=True)
    return True


def cleanup() -> None:
    """Delete the probe's sandbox, then check that its Sandbox record is gone.
    A record that the gateway delete left, or a gateway delete that failed,
    goes with the API, so that the next run is not blocked. A record that the
    gateway delete left fails the run."""
    try:
        failed = None
        try:
            cli("sandbox", "delete", SANDBOX, timeout=DELETE_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            failed = type(e).__name__
        if failed is None and wait_gone(CLEANUP_WAIT):
            report("probe", "sandbox removed", "gone", "gone")
            return
        got = remove_record()
        if failed is None:
            report("probe", "sandbox removed", "gone",
                   f"left after the gateway delete; {got} with the API")
        elif got == "none":
            report("probe", "sandbox removed", "gone", f"gone (the gateway delete: {failed})")
        else:
            report("probe", "sandbox removed", "gone",
                   f"left after a failed gateway delete ({failed}); {got} with the API")
    except (OSError, ValueError, LookupError, KeyError) as e:
        report("probe", "sandbox removed", "gone", f"left: {type(e).__name__}: {e}")


def run_probe() -> None:
    """Create the sandbox, run the probe in it and check its pods."""
    created = cli("sandbox", "create", "--name", SANDBOX, "--from", PROBE_IMAGE,
                  "--detach", "--no-tty", "--", "sleep", "3600", timeout=300)
    if created.returncode != 0:
        report("probe", "sandbox create", "ok", f"failed: {tail(created)}")
        return
    # The direct probes use addresses: the sandbox resolves no names, so a
    # name would test DNS only. The check pod resolves them.
    code = (PROBE.replace("KUBE_DNS_IP", socket.gethostbyname(KUBE_DNS))
            .replace("MODEL_GATEWAY_IP", socket.gethostbyname(MODEL_GATEWAY))
            .replace("MODEL_GATEWAY", MODEL_GATEWAY)
            .replace("GATEWAY_IP", socket.gethostbyname(gateway(OWN))))
    results = exec_probe("probe", code)
    if results is None:
        return
    for target, got in results.items():
        report("probe", target, "blocked", got)
    # Before cleanup() deletes the sandbox.
    report_pods()


def exec_probe(check: str, code: str) -> dict | None:
    """Run the code in the probe's sandbox until it prints its PROBE line
    (the sandbox can take a while to answer after its create)."""
    result = None
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = cli("sandbox", "exec", "--name", SANDBOX, "--no-tty", "--",
                     "python3", "-c", code, timeout=120)
        if result.returncode == 0 and "PROBE " in result.stdout:
            break
        time.sleep(10)
    line = next((l for l in (result.stdout if result else "").splitlines()
                 if l.startswith("PROBE ")), None)
    if line is None:
        report(check, "sandbox exec", "ok", f"failed: {tail(result)}")
        return None
    return json.loads(line[len("PROBE "):])


def probe() -> None:
    setup_cli()
    if not remove_leftover():
        return
    # From the create on, each way out goes through cleanup(): a create that
    # timed out can still have made the sandbox.
    try:
        run_probe()
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        report("probe", "probe run", "ok", f"failed: {type(e).__name__}: {e}")
    finally:
        cleanup()


# The probe of the finance app. APP_PROXY is the proxy of the app as a
# sandbox calls it (the read port), APP_PROXY_WRITE as the MCP server calls
# it (the write port), and SANDBOX_POLICY the sandbox policy of the agent.
# The proxy holds the token (a placeholder until the owner puts the real one,
# and the checks pass with either). No call here can change data: each write
# or delete names an id that does not exist ("0"), or sends an empty body
# that the app refuses.
APP_PROXY = os.environ.get("APP_PROXY", "")
APP_PROXY_WRITE = os.environ.get("APP_PROXY_WRITE", "")
SANDBOX_POLICY = os.environ.get("SANDBOX_POLICY", "")


# Runs inside the sandbox. Each answer of the proxy names its port in a
# header, and a refusal of the proxy carries a second header; the sandbox
# policy answers 403 with the JSON error "policy_denied" (OpenShell 0.1.8,
# l7/rest.rs). An answer with neither did not come through the proxy.
FIREFLY_PROBE = r'''
import json, socket, urllib.error, urllib.request
APP = "APP_PROXY"
WRITE_PORT = "APP_WRITE"
def http(method, path, base=APP):
    request = urllib.request.Request(base + path, method=method,
        data=b"{}" if method in ("POST", "PUT") else None,
        headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(request, timeout=10); r.read()
        if r.headers.get("X-Firefly-Proxy-Port"):
            return "reached: HTTP %s" % r.status
        return "blocked: HTTP %s, not from the proxy" % r.status
    except urllib.error.HTTPError as e:
        if e.headers.get("X-Firefly-Proxy") == "refused":
            return "refused: proxy"
        if e.headers.get("X-Firefly-Proxy-Port"):
            return "reached: HTTP %s" % e.code
        try:
            error = json.loads(e.read(4000) or b"{}").get("error")
        except Exception:
            error = None
        if e.code == 403 and error == "policy_denied":
            return "refused: sandbox policy"
        return "blocked: HTTP %s %s" % (e.code, error or "")
    except Exception as e:
        return "blocked: " + type(e).__name__
def tcp(host, port):
    try:
        socket.create_connection((host, port), timeout=4).close(); return "connected"
    except Exception as e:
        return "blocked: " + type(e).__name__
out = {"allowed read (GET /api/v1/about)": http("GET", "/api/v1/about")}
for method, path in CALLS:
    out["%s %s" % (method, path)] = http(method, path)
# The probe's policy names the write port too; the network policy must stop
# the supervisor there.
out["write port of the proxy, through the supervisor"] = http("GET", "/api/v1/about", WRITE_PORT)
out["proxy address, direct"] = tcp("PROXY_IP", PROXY_PORT)
print("PROBE " + json.dumps(out))
'''
# Calls that must not reach the app from a sandbox: each write and each
# delete (the read port of the proxy lets through reads only), and calls off
# the allow list of the proxy (firefly-mcp/app/proxy/default.conf.template).
REFUSED_CALLS = [
    ("POST", "/api/v1/tags"),
    ("PUT", "/api/v1/tags/0"),
    ("PUT", "/api/v1/transactions/0"),
    ("PATCH", "/api/v1/transactions/0"),
    ("DELETE", "/api/v1/transactions/0"),
    ("DELETE", "/api/v1/tags/0"),
    ("GET", "/api/v1/preferences"),
    ("GET", "/api/v1/webhooks"),
    ("GET", "/api/v1/cron/probe-not-a-token"),
    ("GET", "/api/v1/configuration"),
    ("GET", "/api/v1/users"),
    ("GET", "/oauth/personal-access-tokens"),
    ("POST", "/api/v1/data/bulk/transactions"),
    ("POST", "/api/v1/rules/0/trigger"),
    ("POST", "/api/v1/rule-groups/0/trigger"),
    ("POST", "/api/v1/recurrences/0/trigger"),
    ("POST", "/api/v1/rules"),
    ("PUT", "/api/v1/rules/0"),
    ("GET", "/api/v1/data/export/transactions"),
    ("GET", "/api/v1/attachments/0/download"),
    ("POST", "/api/v1/attachments/0/upload"),
    ("GET", "/api/v1/accounts/../preferences"),
    ("GET", "/api/v1/accounts/%2e%2e/preferences"),
]


# The sandbox policy of the agent lets through GET only. The probe's sandbox
# gets that policy with each method on the read port, so that the proxy, not
# the policy, must refuse each write there, as it must when a policy is
# widened. It also gets a rule for the write port of the proxy, which the
# network policy must stop at the supervisor. The policy of the agent ends
# with its network_policies.
AGENT_RULE = '''- allow: {method: GET, path: "/api/v1/**"}'''
PROBE_RULE = '''- allow: {method: "*", path: "/api/v1/**"}'''
WRITE_PORT_RULE = """  write_port_probe:
    name: write_port_probe
    endpoints:
      - host: {host}
        port: {port}
        protocol: rest
        tls: ""
        enforcement: enforce
        rules:
          - allow: {{method: GET, path: "/api/v1/**"}}
    binaries:
      - {{path: /usr/local/bin/python3.13}}
      - {{path: /usr/local/bin/python3}}
"""


def probe_policy() -> str:
    """The sandbox policy of the agent, with each method on the read port and
    a rule for the write port."""
    with open(SANDBOX_POLICY) as f:
        text = f.read()
    if text.count(AGENT_RULE) != 1:
        raise ValueError("the rule of the read port is not in the sandbox policy")
    text = text.replace(AGENT_RULE, PROBE_RULE)
    host, port = APP_PROXY_WRITE.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)
    os.makedirs(os.environ["HOME"], exist_ok=True)
    path = os.path.join(os.environ["HOME"], "firefly-probe-policy.yaml")
    with open(path, "w") as f:
        f.write(text.rstrip("\n") + "\n" + WRITE_PORT_RULE.format(host=host, port=port))
    return path


def run_firefly_probe() -> None:
    try:
        policy = probe_policy()
    except (OSError, ValueError) as e:
        report("firefly-probe", "sandbox policy of the probe", "ok", f"failed: {e}")
        return
    created = cli("sandbox", "create", "--name", SANDBOX, "--from", PROBE_IMAGE,
                  "--policy", policy, "--detach", "--no-tty", "--",
                  "sleep", "3600", timeout=300)
    if created.returncode != 0:
        report("firefly-probe", "sandbox create", "ok", f"failed: {tail(created)}")
        return
    writes = {f"{m} {p}" for m, p in REFUSED_CALLS if m != "GET"}
    host, port = APP_PROXY.split("://", 1)[1].split("/", 1)[0].rsplit(":", 1)
    code = (FIREFLY_PROBE.replace("APP_PROXY", APP_PROXY).replace("APP_WRITE", APP_PROXY_WRITE)
            .replace("PROXY_IP", socket.gethostbyname(host)).replace("PROXY_PORT", port)
            .replace("CALLS", json.dumps(REFUSED_CALLS)))
    results = exec_probe("firefly-probe", code)
    if results is None:
        return
    for target, got in results.items():
        # A write must meet the proxy; a read off the list may meet the
        # policy first (a path outside /api/v1, a path trick).
        expected = ("reached" if target.startswith("allowed") else
                    "blocked" if target.endswith(("direct", "supervisor")) else
                    "refused: proxy" if target in writes else "refused")
        report("firefly-probe", f"sandbox: {target}", expected, got)


def direct(base: str, method: str, path: str, body: bytes | None = None,
           headers: dict | None = None) -> str:
    """A call straight to the proxy. The proxy marks its refusal with a
    header. A 405 is the app's answer to a method that a path has no route
    for: here, a method override that reached the app."""
    request = urllib.request.Request(base + path, method=method, data=body,
                                     headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return f"reached: HTTP {r.status}"
    except urllib.error.HTTPError as e:
        if e.headers.get("X-Firefly-Proxy") == "refused":
            return "refused: proxy"
        if e.code == 405:
            return "override applied: HTTP 405"
        return f"reached: HTTP {e.code}"
    except OSError as e:
        return f"failed: {type(e).__name__}"


JSON = {"Content-Type": "application/json"}


def direct_checks() -> None:
    """The calls that neither the MCP server nor a sandbox (reads only) can
    send, on the write port, and the writes on the read port. No call can
    change data: the app refuses an empty body, and ids are "0"."""
    write, read = APP_PROXY_WRITE, APP_PROXY
    for target, expected, args in (
            ("allowed read (GET /api/v1/about)", "reached", (write, "GET", "/api/v1/about")),
            # The proxy drops the header, so the app routes a POST, and
            # refuses its empty body; a PUT on /api/v1/tags has no route.
            ("X-HTTP-Method-Override: PUT on POST /api/v1/tags", "reached",
             (write, "POST", "/api/v1/tags", b"{}", {**JSON, "X-HTTP-Method-Override": "PUT"})),
            ("_method in the query (POST /api/v1/tags?_method=PUT)", "refused",
             (write, "POST", "/api/v1/tags?_method=PUT", b"{}", JSON)),
            ("_method with encoded letters (POST /api/v1/tags?%5F%6Dethod=PUT)", "refused",
             (write, "POST", "/api/v1/tags?%5F%6Dethod=PUT", b"{}", JSON)),
            ("any query on a write (PUT /api/v1/tags/0?x=1)", "refused",
             (write, "PUT", "/api/v1/tags/0?x=1", b"{}", JSON)),
            ("form body (POST /api/v1/tags, _method=PUT)", "refused",
             (write, "POST", "/api/v1/tags", b"_method=PUT",
              {"Content-Type": "application/x-www-form-urlencoded"})),
            ("DELETE /api/v1/tags/0", "refused", (write, "DELETE", "/api/v1/tags/0")),
            ("PATCH /api/v1/transactions/0", "refused",
             (write, "PATCH", "/api/v1/transactions/0", b"{}", JSON)),
            ("GET /api/v1/preferences", "refused", (write, "GET", "/api/v1/preferences")),
            ("GET /api/v1/accounts/%2e%2e/preferences", "refused",
             (write, "GET", "/api/v1/accounts/%2e%2e/preferences")),
            ("read port: allowed read (GET /api/v1/about)", "reached", (read, "GET", "/api/v1/about")),
            ("read port: write (POST /api/v1/tags)", "refused",
             (read, "POST", "/api/v1/tags", b"{}", JSON)),
            ("read port: write (PUT /api/v1/transactions/0)", "refused",
             (read, "PUT", "/api/v1/transactions/0", b"{}", JSON))):
        report("firefly-probe", f"proxy, direct: {target}", expected, direct(*args))


def lookup(name: str) -> str:
    try:
        socket.getaddrinfo(name, None)
        return "resolved"
    except OSError as e:
        return f"blocked: {type(e).__name__}"


def dns() -> None:
    for name in os.environ.get("DNS_ALLOWED", "").split():
        report("dns", f"lookup {name}", "resolved", lookup(name))
    for name in os.environ.get("DNS_REFUSED", "").split():
        report("dns", f"lookup {name}", "blocked", lookup(name))


def firefly_probe() -> None:
    direct_checks()
    setup_cli()
    if not remove_leftover():
        return
    try:
        run_firefly_probe()
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        report("firefly-probe", "probe run", "ok", f"failed: {type(e).__name__}: {e}")
    finally:
        cleanup()


# The gateway probe of a trust domain (finance only:
# kubernetes/apps/ai/agents/finance/checks/app/mcp-gateway-probe.yaml).
# MCP_GATEWAY_URL is the route of the domain at the MCP gateway, as its agent
# calls it; MCP_SERVER the MCP server behind it (host:port), which no pod but
# the gateway may reach; GATEWAY_CONFIG the config of the gateway, whose tool
# list the probe key must see. PROBE_KEY may list the tools of the domain
# and call get_about only; NO_TOOLS_KEY has no tool. Each is a placeholder
# ("pending") until the owner's values step, and a check that needs it says
# so and passes. The probe holds no key of an agent. VAULT_GATEWAY_URL and
# VAULT_SERVER are the vault route of the domain and its vault server; with
# no VAULT_GATEWAY_URL the probe checks no vault. On the vault route,
# PROBE_KEY may list the tools and call get_file_info only, and NO_TOOLS_KEY
# is not a key at all.
MCP_GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "")
MCP_SERVER = os.environ.get("MCP_SERVER", "")
GATEWAY_CONFIG = os.environ.get("GATEWAY_CONFIG", "")
VAULT_GATEWAY_URL = os.environ.get("VAULT_GATEWAY_URL", "")
VAULT_SERVER = os.environ.get("VAULT_SERVER", "")
PENDING = "pending"
SKIPPED = "skipped: the key is pending"


def mcp_request(key: str | None, method: str, params: dict,
                extra: dict | None = None, url: str = "") -> tuple[int, object]:
    """One JSON-RPC request to the gateway (streamable HTTP, stateless: no
    session), with the key as a bearer or with no key, and other headers of
    the client if given. The status and the answer (JSON, or the last
    server-sent event)."""
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream", **(extra or {})}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        url or MCP_GATEWAY_URL, method="POST", headers=headers,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode())
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            status, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode(errors="replace")
    events = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    try:
        return status, json.loads(events[-1] if events else raw)
    except ValueError:
        return status, raw[:200]


def listed(key: str | None, url: str = "") -> str:
    """The tools that a key sees: "tools: <n>", or the refusal."""
    try:
        status, body = mcp_request(key, "tools/list", {}, url=url)
    except OSError as e:
        return f"failed: {type(e).__name__}"
    if status == 401:
        return "refused: HTTP 401"
    try:
        return f"tools: {len(body['result']['tools'])}"
    except (TypeError, KeyError):
        return f"other: HTTP {status}"


def listed_names(key: str, url: str = "") -> list[str] | None:
    try:
        status, body = mcp_request(key, "tools/list", {}, url=url)
        return sorted(t["name"] for t in body["result"]["tools"]) if status == 200 else None
    except (OSError, TypeError, KeyError):
        return None


def called(key: str, name: str, arguments: dict, extra: dict | None = None,
           url: str = "") -> str:
    """The outcome of a tool call through the gateway. The gateway refuses a
    tool that its rule does not name for the key with a 400 and the JSON-RPC
    error "unknown resource" (agentgateway 1.7.0), and sends nothing on. A
    call that it sends on gets the answer of the app through the MCP server
    (fireflyiii-mcp 1.0.1, formatError): the app's 401 with the placeholder
    token, and an answer with the real one."""
    try:
        status, body = mcp_request(key, "tools/call", {"name": name, "arguments": arguments}, extra,
                                   url=url)
    except OSError as e:
        return f"failed: {type(e).__name__}"
    if status == 401:
        return "refused: HTTP 401"
    if not isinstance(body, dict):
        return f"other: HTTP {status}"
    error = body.get("error") or {}
    if status == 400 and error.get("message") == "unknown resource":
        return "refused: gateway"
    result = body.get("result")
    if status != 200 or not isinstance(result, dict):
        return f"other: HTTP {status}"
    text = " ".join(c.get("text", "") for c in result.get("content", []))
    if not result.get("isError"):
        return "reached: ok"
    if text.startswith("Authentication failed"):
        return "reached: HTTP 401"
    if text == "Resource not found.":
        return "reached: HTTP 404"
    return f"other: {text[:120]}"


def rule_tools(name: str = "finance") -> list[str]:
    """The tool list of the rule of a route in the config of the gateway
    (its one `mcp.tool.name in [...]` list, as mcp-gateway/tools/mcp_tools.py
    reads it for the route finance). The route finance-vault has a list of
    its own."""
    with open(GATEWAY_CONFIG) as f:
        route = re.search(rf"(?ms)^  - name: {re.escape(name)}\n.*?(?=^  - name: |\Z)", f.read())
    (names,) = re.findall(r"mcp\.tool\.name in \[(.*?)\]", route.group(0), re.DOTALL)
    return sorted(re.findall(r'"([^"]+)"', names))


def mcp_gateway() -> None:
    check = "mcp-gateway"
    host, port = MCP_SERVER.rsplit(":", 1)
    report(check, "MCP server, direct from a pod that is not the gateway", "blocked",
           tcp(host, int(port)))
    report(check, "no key: tools/list", "refused: HTTP 401", listed(None))
    report(check, "a key that is not in the config: tools/list", "refused: HTTP 401",
           listed(secrets.token_hex(32)))
    probe_key = os.environ.get("PROBE_KEY", PENDING)
    if probe_key == PENDING:
        report(check, "probe key", SKIPPED, SKIPPED)
    else:
        want = rule_tools()
        got = listed_names(probe_key)
        report(check, f"probe key: lists the {len(want)} tools of the rule", "same",
               "same" if got == want else
               f"differs: {'failed' if got is None else sorted(set(got) ^ set(want))[:10]}")
        for target, tool, arguments, expected in (
                ("probe key: allowed read (get_about)", "get_about", {}, "reached"),
                ("probe key: delete (delete_transaction 0), off every rule",
                 "delete_transaction", {"id": "0"}, "refused: gateway"),
                ("probe key: rule trigger (trigger_rule 0), off every rule",
                 "trigger_rule", {"id": "0"}, "refused: gateway"),
                ("probe key: a write of the rule (create_tag), not the probe's",
                 "create_tag", {"tag": "probe"}, "refused: gateway")):
            report(check, target, expected, called(probe_key, tool, arguments))
        # The client is the one of the key: headers that name another client
        # give nothing (the gateway also drops them before the MCP server).
        report(check, "probe key with headers that name the client finance: a write",
               "refused: gateway",
               called(probe_key, "create_tag", {"tag": "probe"},
                      {"X-Client": "finance", "X-Api-Key": "finance", "Cookie": "client=finance",
                       "X-Forwarded-For": "127.0.0.1"}))
    no_tools_key = os.environ.get("NO_TOOLS_KEY", PENDING)
    if no_tools_key == PENDING:
        report(check, "key with no tool", SKIPPED, SKIPPED)
    else:
        report(check, "key with no tool: tools/list", "tools: 0", listed(no_tools_key))
        report(check, "key with no tool: read (get_about)", "refused: gateway",
               called(no_tools_key, "get_about", {}))
    if VAULT_GATEWAY_URL:
        vault(check, probe_key, no_tools_key)


def vault(check: str, probe_key: str, no_tools_key: str) -> None:
    """The vault route of the domain and its vault server. The probe reads
    the facts of the root of the share only (get_file_info /vault): no note
    name and no note text."""
    host, port = VAULT_SERVER.rsplit(":", 1)
    report(check, "vault server, direct from a pod that is not the gateway", "blocked",
           tcp(host, int(port)))
    report(check, "vault: no key: tools/list", "refused: HTTP 401", listed(None, VAULT_GATEWAY_URL))
    report(check, "vault: a key that is not in the config: tools/list", "refused: HTTP 401",
           listed(secrets.token_hex(32), VAULT_GATEWAY_URL))
    if probe_key == PENDING:
        report(check, "vault: probe key", SKIPPED, SKIPPED)
    else:
        want = rule_tools("finance-vault")
        got = listed_names(probe_key, VAULT_GATEWAY_URL)
        report(check, f"vault: probe key: lists the {len(want)} tools of the rule", "same",
               "same" if got == want else
               f"differs: {'failed' if got is None else sorted(set(got) ^ set(want))[:10]}")
        for target, tool, arguments, expected in (
                ("vault: probe key: the facts of the root (get_file_info /vault)",
                 "get_file_info", {"path": "/vault"}, "reached: ok"),
                ("vault: probe key: a write (write_file), not the probe's",
                 "write_file", {"path": "/vault/probe.md", "content": "probe"}, "refused: gateway"),
                ("vault: probe key: a move (move_file), off every rule",
                 "move_file", {"source": "/vault/a", "destination": "/vault/b"},
                 "refused: gateway")):
            report(check, target, expected, called(probe_key, tool, arguments, url=VAULT_GATEWAY_URL))
    if no_tools_key == PENDING:
        report(check, "vault: key with no tool", SKIPPED, SKIPPED)
    else:
        report(check, "vault: key with no tool: tools/list", "refused: HTTP 401",
               listed(no_tools_key, VAULT_GATEWAY_URL))


if __name__ == "__main__":
    {"isolation": isolation, "probe": probe, "firefly-probe": firefly_probe,
     "dns": dns, "mcp-gateway": mcp_gateway}[sys.argv[1]]()
    sys.exit(1 if failures else 0)
