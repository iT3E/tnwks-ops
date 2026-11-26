"""The sign-in check in front of the Control UI of main (task 6.57,
kubernetes/apps/ai/agents/main/agent/app/ui-proxy).

The gateway of main trusts a user header from its trusted proxies. So that
header must come from one pod only, which sets it from the answer of the
sign-in server and never from the client: the proxy pod in agents-main. The
static tests hold the pieces to that: the Ingress points at the proxy, the
proxy asks the same URL as the Ingress, the network policy of the agent
admits the proxy alone to the gateway port, no other manifest can make a
pod with the label of the proxy, and the internal controller takes no
Service of type ExternalName as a backend.

The container test runs the pinned proxy image with the template of the
pod, as its user and with a read-only root, between a fake sign-in server
(it says yes for one cookie only, and gives the email) and a fake gateway
(it records the headers of each request, and answers a WebSocket upgrade).
No real credential: fake values only. It needs Docker: without it the test
is skipped, except in CI, where it fails.
"""

import json
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/agents/main/agent/app"
CONFIG = ROOT / "kubernetes/apps/ai/agents/main/agent/config/openclaw.json"
UI = APP / "ui-proxy"
PROXY = UI / "ui-proxy.yaml"
TEMPLATE = UI / "default.conf.template"
CONTROLLER = ROOT / "kubernetes/apps/networking/ingress-nginx-internal/app/helmrelease.yaml"
PYTHON = "docker.io/library/python:3.13-alpine"
LABEL = "ui-proxy"
# The identity headers that the proxy drops: those of the sign-in server and
# those that other proxies use. The user header of the gateway is one of
# them; the proxy sets it from the answer of the sign-in server.
IDENTITY = ["X-Auth-Request-Email", "X-Auth-Request-User", "X-Auth-Request-Groups",
            "X-Auth-Request-Preferred-Username", "X-Auth-Request-Access-Token", "X-Forwarded-User",
            "X-Forwarded-Email", "X-Forwarded-Groups", "X-Forwarded-Preferred-Username",
            "X-Forwarded-Access-Token", "Remote-User", "X-Remote-User", "X-User", "X-Email"]


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def one(path: Path, kind: str) -> dict:
    (found,) = [d for d in docs(path) if d["kind"] == kind]
    return found


def user_header() -> str:
    return json.loads(CONFIG.read_text())["gateway"]["auth"]["trustedProxy"]["userHeader"]


def settings() -> dict[str, str]:
    kustomization = yaml.safe_load((UI / "kustomization.yaml").read_text())
    (generator,) = [g for g in kustomization["configMapGenerator"] if g["name"] == "ui-proxy-settings"]
    return dict(literal.split("=", 1) for literal in generator["literals"])


def proxy() -> dict:
    (container,) = one(PROXY, "Deployment")["spec"]["template"]["spec"]["containers"]
    return container


# The static tests

def test_the_ingress_sends_the_ui_to_the_proxy_after_the_same_check():
    ingress = one(APP / "ingress.yaml", "Ingress")
    annotations = ingress["metadata"]["annotations"]
    (rule,) = ingress["spec"]["rules"]
    (route,) = rule["http"]["paths"]
    assert route["backend"]["service"] == {"name": LABEL, "port": {"name": "ui"}}
    service = one(PROXY, "Service")
    assert service["metadata"]["name"] == LABEL
    assert service["spec"]["ports"] == [{"name": "ui", "port": 8080, "targetPort": "ui", "protocol": "TCP"}]
    assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    # The proxy asks the URL of the Ingress, with its Flux variable, default
    # and group, and sends on to the gateway Service.
    assert settings()["UI_PROXY_AUTH_URL"] == annotations["nginx.ingress.kubernetes.io/auth-url"]
    gateway = json.loads(CONFIG.read_text())["gateway"]
    assert settings()["UI_PROXY_UPSTREAM"] == f"agent.agents-main.svc.cluster.local:{gateway['port']}"
    # In the apply of the agent, with its own ConfigMaps.
    assert "./ui-proxy" in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]
    own = yaml.safe_load((UI / "kustomization.yaml").read_text())
    assert own["resources"] == ["./ui-proxy.yaml"]
    assert {g["name"] for g in own["configMapGenerator"]} == {"ui-proxy", "ui-proxy-settings"}


def test_the_proxy_sets_the_user_header_from_the_answer_and_drops_each_identity_header():
    text = TEMPLATE.read_text()
    header = user_header()
    assert header in [h.lower() for h in IDENTITY]
    assert "auth_request /_ui_proxy_auth;" in text
    assert f"auth_request_set $ui_proxy_email $upstream_http_{header.replace('-', '_')};" in text
    lines = [line.strip() for line in text.splitlines()]
    for name in IDENTITY:
        value = "$ui_proxy_email" if name.lower() == header else '""'
        assert f"proxy_set_header {name} {value};" in lines, name
    # The gateway requires the forwarded headers of the controller; the
    # proxy passes them on as the controller set them.
    assert "proxy_set_header X-Forwarded-Proto" not in text
    assert "proxy_set_header X-Forwarded-Host" not in text
    # The only names that the entrypoint fills.
    assert {e["name"]: e["value"] for e in proxy()["env"]} == {"NGINX_ENVSUBST_FILTER": "^UI_PROXY_"}
    assert proxy()["envFrom"] == [{"configMapRef": {"name": "ui-proxy-settings"}}]
    assert set(settings()) == {"UI_PROXY_AUTH_URL", "UI_PROXY_UPSTREAM"}


def test_only_the_proxy_reaches_the_gateway_port():
    """The policy of the agent admits the proxy pod alone to the gateway
    port: the ingress controllers, and any route of any Ingress through them,
    reach the proxy and never the gateway."""
    gateway = json.loads(CONFIG.read_text())["gateway"]
    policy = one(APP / "networkpolicy.yaml", "CiliumNetworkPolicy")
    assert policy["spec"]["ingress"] == [{
        "fromEndpoints": [{"matchLabels": {
            "io.kubernetes.pod.namespace": "agents-main",
            "io.cilium.k8s.policy.cluster": "${CLUSTER_NAME}",
            "app.kubernetes.io/name": LABEL,
        }}],
        "toPorts": [{"ports": [{"port": str(gateway["port"]), "protocol": "TCP"}]}],
    }]
    # No other policy of the namespace opens the gateway port.
    for path in (ROOT / "kubernetes/apps/ai/agents/main").rglob("*.yaml"):
        for doc in docs(path):
            if doc.get("kind") not in ("CiliumNetworkPolicy", "NetworkPolicy") or doc is None:
                continue
            if doc["metadata"]["name"] == "agent" and path.name == "networkpolicy.yaml" and path.parent == APP:
                continue
            assert str(gateway["port"]) not in json.dumps(doc.get("spec", {}).get("ingress", [])), path


def test_the_proxy_policy_lets_in_the_internal_controller_and_out_two_servers():
    policy = one(PROXY, "CiliumNetworkPolicy")
    assert policy["spec"]["endpointSelector"] == {"matchLabels": {"app.kubernetes.io/name": LABEL}}
    (controller, kubelet) = policy["spec"]["ingress"]
    assert controller["fromEndpoints"][0]["matchLabels"] == {
        "io.kubernetes.pod.namespace": "networking", "io.cilium.k8s.policy.cluster": "${CLUSTER_NAME}",
        "app.kubernetes.io/name": "ingress-nginx", "app.kubernetes.io/instance": "ingress-nginx-internal",
        "app.kubernetes.io/component": "controller"}
    assert controller["toPorts"] == [{"ports": [{"port": "8080", "protocol": "TCP"}]}]
    assert kubelet == {"fromEntities": ["host"], "toPorts": [{"ports": [{"port": "8081", "protocol": "TCP"}]}]}
    out = {rule["toEndpoints"][0]["matchLabels"]["io.kubernetes.pod.namespace"]:
           [p["port"] for tp in rule["toPorts"] for p in tp["ports"]] for rule in policy["spec"]["egress"]}
    assert out == {"auth": ["4180"], "agents-main": ["18789"], "kube-system": ["53"]}
    dns = policy["spec"]["egress"][2]["toPorts"][0]["rules"]["dns"]
    assert dns == [{"matchName": "oauth2-proxy.auth.svc.cluster.local"},
                   {"matchName": "agent.agents-main.svc.cluster.local"}]
    template = one(PROXY, "Deployment")["spec"]["template"]
    assert template["metadata"]["labels"]["agents.home.arpa/dns"] == "own-rule"
    assert {"name": "ndots", "value": "1"} in template["spec"]["dnsConfig"]["options"]


def pod_labels(doc: dict) -> dict:
    spec = doc.get("spec") or {}
    if doc.get("kind") == "Pod":
        return doc.get("metadata", {}).get("labels") or {}
    template = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get("template") or {}
    return (template.get("metadata") or {}).get("labels") or {}


def test_no_other_manifest_makes_a_pod_with_the_label_of_the_proxy():
    """A pod with the label of the proxy in agents-main would reach the
    gateway port. Only the Deployment of the proxy makes one, and no sandbox
    can carry a label of a caller (the main sandbox gateway sets no
    allowed_pod_labels, OpenShell's default is none)."""
    found = []
    for path in (ROOT / "kubernetes").rglob("*.yaml"):
        if path.name.endswith(".sops.yaml") or LABEL not in path.read_text(errors="replace"):
            continue
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and pod_labels(doc).get("app.kubernetes.io/name") == LABEL:
                found.append((str(path.relative_to(ROOT)), doc["kind"], doc["metadata"]["name"]))
    assert found == [(str(PROXY.relative_to(ROOT)), "Deployment", LABEL)]
    release = ROOT / "kubernetes/apps/ai/agents/main/sandbox-gateway/app/helmrelease.yaml"
    assert "allowed_pod_labels" not in release.read_text()


def test_the_internal_controller_takes_no_external_name_backend():
    release = yaml.safe_load(CONTROLLER.read_text())
    assert release["spec"]["values"]["controller"]["extraArgs"]["disable-svc-external-name"] == "true"
    for path in (ROOT / "kubernetes").rglob("*.yaml"):
        text = path.read_text(errors="replace")
        if "ExternalName" not in text or path.name.endswith(".sops.yaml"):
            continue
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") == "Service":
                assert doc.get("spec", {}).get("type") != "ExternalName", path


# The lab

# The fake sign-in server: a 202 with the email for the cookie of a member
# of the group, a 403 for one that is not, a 202 with no email for one more,
# and a 401 for each other request. It records what it was asked.
AUTH = r'''
import http.server, json
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        cookie = self.headers.get("Cookie") or ""
        with open("/rec/auth.jsonl", "a") as f:
            f.write(json.dumps({"path": self.path, "cookie": cookie, "length": self.headers.get("Content-Length"),
                                "upgrade": self.headers.get("Upgrade")}) + "\n")
        if "_oauth2_proxy=member" in cookie:
            self.send_response(202)
            self.send_header("X-Auth-Request-Email", "owner@example.invalid")
            self.send_header("X-Auth-Request-User", "owner-sub")
            self.send_header("X-Auth-Request-Groups", "admins")
        elif "_oauth2_proxy=nomail" in cookie:
            self.send_response(202)
        elif "_oauth2_proxy=other" in cookie:
            self.send_response(403)
        else:
            self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_POST = do_GET
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", 4180), Handler).serve_forever()
'''

# The fake gateway: it records the headers of each request, answers a plain
# request with 200, and a WebSocket upgrade with 101 and one text frame.
GATEWAY = r'''
import base64, hashlib, http.server, json
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_GET(self):
        with open("/rec/gateway.jsonl", "a") as f:
            f.write(json.dumps({k.lower(): v for k, v in self.headers.items()}) + "\n")
        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept.decode())
            self.end_headers()
            self.wfile.write(b"\x81\x05hello")
            self.wfile.flush()
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open("/rec/gateway.jsonl", "a") as f:
            f.write(json.dumps({**{k.lower(): v for k, v in self.headers.items()}, "body": body.decode()}) + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", 18789), Handler).serve_forever()
'''

# The client: each case as the controller would send it, with the forwarded
# headers that the gateway requires. It prints one JSON line per case.
CLIENT = r'''
import base64, json, os, socket, sys, time
FORGED = {name: "forged@example.invalid" for name in json.loads(sys.argv[1])}
def ask(path="/", cookie=None, forged=True, upgrade=False, port=8080, body=None):
    h = {"Host": "ui.example.invalid", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "ui.example.invalid"}
    if forged:
        h.update(FORGED)
    if cookie:
        h["Cookie"] = "_oauth2_proxy=" + cookie
    if upgrade:
        h.update({"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
                  "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode()})
    if body is not None:
        h["Content-Length"] = str(len(body))
    method = "GET" if body is None else "POST"
    request = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items())
    if not upgrade:
        request += "Connection: close\r\n"
    request += "\r\n" + (body or "")
    for _ in range(60):
        try:
            s = socket.create_connection(("proxy", port), timeout=10)
            break
        except OSError:
            time.sleep(0.5)
    s.sendall(request.encode())
    data = b""
    s.settimeout(5)
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if upgrade and b"hello" in data:
                break
    except socket.timeout:
        pass
    s.close()
    status = int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/") else 0
    return {"status": status, "frame": upgrade and data.endswith(b"\x81\x05hello")}
out = {
    "health": ask("/healthz", forged=False, port=8081)["status"],
    "health, other path": ask("/", forged=False, port=8081)["status"],
    "forged, no sign-in": ask()["status"],
    "forged, not in the group": ask(cookie="other")["status"],
    "forged, an answer with no email": ask(cookie="nomail")["status"],
    "forged, signed in": ask(cookie="member")["status"],
    "a post, signed in": ask(cookie="member", body="lab-body")["status"],
    "upgrade, forged, no sign-in": ask(upgrade=True),
    "upgrade, forged, signed in": ask(cookie="member", upgrade=True),
}
print(json.dumps(out))
'''


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return result.stdout


def records(work: Path, name: str) -> list[dict]:
    path = work / "rec" / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    for name, text in (("auth.py", AUTH), ("gateway.py", GATEWAY), ("client.py", CLIENT)):
        (work / name).write_text(text)
        (work / name).chmod(0o644)
    (work / "rec").mkdir(mode=0o777)
    (work / "rec").chmod(0o777)
    pod = one(PROXY, "Deployment")["spec"]["template"]["spec"]
    user, gid = pod["securityContext"]["runAsUser"], pod["securityContext"]["runAsGroup"]
    env = {e["name"]: e["value"] for e in proxy()["env"]}
    env["UI_PROXY_AUTH_URL"] = "http://auth:4180/oauth2/auth?allowed_groups=admins"
    env["UI_PROXY_UPSTREAM"] = "gateway:18789"
    tag = uuid.uuid4().hex[:8]
    net = f"uiproxy-{tag}"
    names = {part: f"uiproxy-{part}-{tag}" for part in ("auth", "gateway", "proxy")}
    try:
        run([tool, "network", "create", "--internal", net])
        for part, script in (("auth", "auth.py"), ("gateway", "gateway.py")):
            run([tool, "run", "-d", "--name", names[part], "--network", net, "--network-alias", part,
                 "--read-only", "--cap-drop", "ALL", "--user", "65534", "-v", f"{work}/{script}:/{script}:ro",
                 "-v", f"{work}/rec:/rec", PYTHON, "python3", "-I", f"/{script}"])
        tmpfs = [x for p in ("/tmp", "/etc/nginx/conf.d", "/var/cache/nginx")
                 for x in ("--tmpfs", f"{p}:uid={user},gid={gid}")]
        run([tool, "run", "-d", "--name", names["proxy"], "--network", net, "--network-alias", "proxy",
             "--read-only", *tmpfs, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--user", f"{user}:{gid}", *[x for k, v in env.items() for x in ("-e", f"{k}={v}")],
             "-v", f"{TEMPLATE}:/etc/nginx/templates/default.conf.template:ro", proxy()["image"]])
        answer = run([tool, "run", "--rm", "--network", net, "--read-only", "--cap-drop", "ALL", "--user", "65534",
                      "-v", f"{work}/client.py:/client.py:ro", PYTHON, "python3", "-I", "/client.py",
                      json.dumps(IDENTITY)])
        logs = subprocess.run([tool, "logs", names["proxy"]], capture_output=True, text=True, check=False)
        yield {"answer": json.loads(answer.strip().splitlines()[-1]), "work": work,
               "logs": logs.stdout + logs.stderr}
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def test_the_lab_answers(lab):
    answer = lab["answer"]
    assert answer == {
        "health": 200,
        "health, other path": 404,
        # The check refuses: the status of the sign-in server.
        "forged, no sign-in": 401,
        "forged, not in the group": 403,
        # The check says yes, but with no email the gateway gets no user
        # header (the fake gateway answers 200 to any request).
        "forged, an answer with no email": 200,
        "forged, signed in": 200,
        "a post, signed in": 200,
        "upgrade, forged, no sign-in": {"status": 401, "frame": False},
        "upgrade, forged, signed in": {"status": 101, "frame": True},
    }, (answer, lab["logs"][-3000:])


def test_the_gateway_gets_the_user_header_of_the_sign_in_server_only(lab):
    seen = records(lab["work"], "gateway")
    # Only the four requests that the check let through reached it.
    assert len(seen) == 4, seen
    nomail, member, post, upgrade = seen
    # The body of a request reaches the gateway, not the check.
    assert post["body"] == "lab-body"
    names = [name.lower() for name in IDENTITY]
    # With no email in the answer: no identity header at all.
    assert not [name for name in names if name in nomail], nomail
    # Signed in: the email of the answer, and no other identity header.
    for headers in (member, post, upgrade):
        assert headers[user_header()] == "owner@example.invalid", headers
        assert not [name for name in names if name in headers and name != user_header()], headers
        assert "forged@example.invalid" not in json.dumps(headers)
        # The forwarded headers of the controller and the host reach it.
        assert headers["x-forwarded-proto"] == "https" and headers["x-forwarded-host"] == "ui.example.invalid"
        assert headers["host"] == "ui.example.invalid"
    assert upgrade["upgrade"] == "websocket" and upgrade["connection"].lower() == "upgrade"


def test_the_sign_in_server_gets_the_cookie_and_the_url_of_the_ingress(lab):
    asked = records(lab["work"], "auth")
    # One check for each request on the UI port, the upgrade too.
    assert len(asked) == 7, asked
    assert {a["path"] for a in asked} == {"/oauth2/auth?allowed_groups=admins"}
    assert sorted(a["cookie"] for a in asked) == sorted(
        ["", "_oauth2_proxy=other", "_oauth2_proxy=nomail", "_oauth2_proxy=member", "_oauth2_proxy=member", "",
         "_oauth2_proxy=member"])
    # No body, and no upgrade, in the check itself.
    assert all(a["length"] in (None, "0") and a["upgrade"] is None for a in asked), asked
