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
  platform_checks.py provider-probe
      The rule of a provider profile holds, before the provider has a real
      credential (finance only: kubernetes/apps/ai/agents/finance/checks).
      The run imports a copy of the profile (<id>-probe), makes a provider
      of it with a dummy credential, and starts a sandbox with that
      provider. From the sandbox, an allowed read reaches the app, and each
      other call is refused: a write, a read outside the rule, a read that
      changes data, two paths
      that leave the rule with "..", the address of the Service in place of
      its name, and the Service without the proxy. The run removes the
      sandbox, the provider and the copy at its end.

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


def cli(*args: str, timeout: int = 120, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([CLI, *args], capture_output=True, text=True, timeout=timeout, check=False,
                          env=None if env is None else {**os.environ, **env})


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


# The provider probe. The profile file is the one in git that the providers
# job imports (PROVIDER_PROFILE); the run imports a copy with the id
# <id>-probe and makes the provider PROBE_PROVIDER of it, with the value
# DUMMY in the env var of its credential, so no real credential is used, and
# the providers job of the domain is not touched. APP_URL is the address of
# the app as the agent uses it (http://<Service name>:<port>).
PROFILE_FILE = os.environ.get("PROVIDER_PROFILE", "")
PROBE_PROVIDER = os.environ.get("PROBE_PROVIDER", "provider-probe")
APP_URL = os.environ.get("APP_URL", "")
DUMMY = "probe-not-a-credential"

# Runs inside the sandbox. A proxy refusal is an answer with a JSON "error"
# of the proxy (policy_denied, ssrf_denied, ...) or a closed connection; any
# other answer came from the app. With the dummy credential, the app answers
# an allowed read with 401.
PROVIDER_PROBE = r'''
import json, socket, urllib.error, urllib.request
APP = "APP_URL"
def http(path, method="GET", base=APP):
    request = urllib.request.Request(base + path, method=method,
        data=b"{}" if method != "GET" else None,
        headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(request, timeout=10); return "reached: HTTP %s" % r.status
    except urllib.error.HTTPError as e:
        try:
            error = json.loads(e.read(4000) or b"{}").get("error")
        except Exception:
            error = None
        if isinstance(error, str):
            return "blocked: HTTP %s %s" % (e.code, error)
        return "reached: HTTP %s" % e.code
    except Exception as e:
        return "blocked: " + type(e).__name__
def tcp(host, port):
    try:
        socket.create_connection((host, port), timeout=4).close(); return "connected"
    except Exception as e:
        return "blocked: " + type(e).__name__
out = {
    "allowed read (GET ALLOWED_PATH)": http("ALLOWED_PATH"),
    "write (POST WRITE_PATH)": http("WRITE_PATH", "POST"),
    "read outside the rule (GET OUTSIDE_PATH)": http("OUTSIDE_PATH"),
    "read that changes data (GET CRON_PATH)": http("CRON_PATH"),
    "path out of the rule with .. (GET TRAVERSAL_PATH)": http("TRAVERSAL_PATH"),
    "path out of the rule with %2e%2e (GET ENCODED_PATH)": http("ENCODED_PATH"),
    "Service address in place of its name, through the proxy": http("ALLOWED_PATH", base="APP_IP_URL"),
    "Service address, direct": tcp("APP_IP", APP_PORT),
}
print("PROBE " + json.dumps(out))
'''
# The paths of the provider probe: an allowed read, and calls that the rule
# of the profile must refuse.
ALLOWED_PATH = os.environ.get("PROBE_ALLOWED_PATH", "/api/v1/about")
WRITE_PATH = os.environ.get("PROBE_WRITE_PATH", "/api/v1/transactions")
OUTSIDE_PATH = os.environ.get("PROBE_OUTSIDE_PATH", "/api/v1/preferences")
CRON_PATH = os.environ.get("PROBE_CRON_PATH", f"/api/v1/cron/{DUMMY}")
TRAVERSAL_PATH = os.environ.get("PROBE_TRAVERSAL_PATH", "/api/v1/accounts/../../../oauth/clients")
ENCODED_PATH = os.environ.get("PROBE_ENCODED_PATH", "/api/v1/accounts/%2e%2e/preferences")


def probe_profile() -> tuple[str, str, str]:
    """The copy of the profile (id <id>-probe), the id of the copy, and the
    env var of its credential."""
    with open(PROFILE_FILE) as f:
        text = f.read()
    ids = [l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("id:")]
    env = [l.split("[", 1)[1].split("]", 1)[0].strip() for l in text.splitlines()
           if l.strip().startswith("env_vars:")]
    if len(ids) != 1 or len(env) != 1 or "," in env[0]:
        raise ValueError(f"{PROFILE_FILE}: one id and one env var expected")
    copy_id = f"{ids[0]}-probe"
    lines = [f"id: {copy_id}" if l.startswith("id:") else l for l in text.splitlines()]
    return "\n".join(lines) + "\n", copy_id, env[0]


def remove_provider_probe(copy_id: str) -> None:
    """The provider and the copy of the profile, if a run left them. A
    missing one is not an error."""
    cli("provider", "delete", PROBE_PROVIDER, timeout=60)
    cli("profile", "delete", copy_id, timeout=60)


def run_provider_probe(copy_id: str, text: str, env_var: str) -> None:
    path = os.path.join(os.environ["HOME"], "provider-probe-profile.yaml")
    with open(path, "w") as f:
        f.write(text)
    step = cli("profile", "import", "-f", path)
    if step.returncode != 0:
        report("provider-probe", "profile import", "ok", f"failed: {tail(step)}")
        return
    step = cli("provider", "create", "--name", PROBE_PROVIDER, "--type", copy_id,
               "--credential", env_var, env={env_var: DUMMY})
    if step.returncode != 0:
        report("provider-probe", "provider create", "ok", f"failed: {tail(step)}")
        return
    created = cli("sandbox", "create", "--name", SANDBOX, "--from", PROBE_IMAGE,
                  "--provider", PROBE_PROVIDER, "--detach", "--no-tty", "--",
                  "sleep", "3600", timeout=300)
    if created.returncode != 0:
        report("provider-probe", "sandbox create", "ok", f"failed: {tail(created)}")
        return
    host_port = APP_URL.split("://", 1)[1].split("/", 1)[0]
    host, port = host_port.rsplit(":", 1)
    ip = socket.gethostbyname(host)
    code = (PROVIDER_PROBE.replace("APP_IP_URL", f"http://{ip}:{port}")
            .replace("APP_URL", APP_URL).replace("APP_IP", ip).replace("APP_PORT", port)
            .replace("ALLOWED_PATH", ALLOWED_PATH).replace("WRITE_PATH", WRITE_PATH)
            .replace("OUTSIDE_PATH", OUTSIDE_PATH).replace("CRON_PATH", CRON_PATH).replace("TRAVERSAL_PATH", TRAVERSAL_PATH)
            .replace("ENCODED_PATH", ENCODED_PATH))
    results = exec_probe("provider-probe", code)
    if results is None:
        return
    for target, got in results.items():
        report("provider-probe", target,
               "reached" if target.startswith("allowed read") else "blocked", got)


def provider_probe() -> None:
    try:
        text, copy_id, env_var = probe_profile()
    except (OSError, ValueError) as e:
        report("provider-probe", "profile file", "ok", f"failed: {type(e).__name__}: {e}")
        return
    setup_cli()
    if not remove_leftover():
        return
    remove_provider_probe(copy_id)
    try:
        run_provider_probe(copy_id, text, env_var)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        report("provider-probe", "probe run", "ok", f"failed: {type(e).__name__}: {e}")
    finally:
        cleanup()
        remove_provider_probe(copy_id)
        left = cli("provider", "get", PROBE_PROVIDER, timeout=60)
        report("provider-probe", "dummy provider removed", "gone",
               "gone" if left.returncode != 0 else "left")


if __name__ == "__main__":
    {"isolation": isolation, "probe": probe, "provider-probe": provider_probe}[sys.argv[1]]()
    sys.exit(1 if failures else 0)
