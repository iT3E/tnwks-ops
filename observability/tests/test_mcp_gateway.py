"""The MCP gateway (kubernetes/apps/ai/mcp-gateway, docs/apps/mcp-gateway.md).

The static tests read the files: the tool rule of finance names exactly the
kept tools of the dump of the pinned MCP server, the agent sees the same
tools, the keys are hashes and the placeholders match no value, each route
denies by default, the admin port is on loopback, and the network policies
let the agent pod reach the gateway, the gateway reach the MCP server, and
no other pod reach the MCP server.

The container test runs the pinned gateway with the config of the repo (the
hash of each client swapped for the hash of a test key, placeholder or
not), the pinned MCP server,
and a fake app that records each request. The gateway probe of the platform
checks (scripts/platform_checks.py mcp-gateway) runs against it, and the
finance key is tried as the agent uses it. It needs Docker: without it the
test is skipped, except in CI, where it fails.
"""

import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_firefly_proxy import TOOLS, agent_sees, mcp_groups
from test_hand_written_rules import PROMETHEUS, docker, scratch

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "kubernetes/apps/ai/mcp-gateway"
CONFIG = GATEWAY / "app/config/config.yaml"
DUMP = GATEWAY / "tools/firefly.json"
FINANCE = ROOT / "kubernetes/apps/ai/agents/finance"
OPENCLAW = FINANCE / "agent/config/openclaw.json"
MCP = FINANCE / "firefly-mcp/app/mcp.yaml"
PROBE = FINANCE / "checks/app/mcp-gateway-probe.yaml"
CHECKS = ROOT / "kubernetes/apps/ai/agents/checks/app/scripts/platform_checks.py"

sys.path.insert(0, str(GATEWAY / "tools"))
import mcp_tools

PLACEHOLDER = re.compile(r"\Asha256:0{63}[0-9]\Z")
HASH = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def route(name: str) -> dict:
    (found,) = [r for r in config()["routes"] if r["name"] == name]
    return found


def dump() -> dict:
    return json.loads(DUMP.read_text())


def rule() -> list[str]:
    return mcp_tools.rule_names(CONFIG.read_text())


def kind(path: Path, k: str) -> dict:
    (found,) = [d for d in docs(path) if d["kind"] == k]
    return found


def mcp_image() -> str:
    return kind(MCP, "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"]


def gateway_image() -> str:
    return kind(GATEWAY / "app/deployment.yaml", "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"]


def probe_env() -> dict:
    container = kind(PROBE, "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


# The tool rule


def test_the_dump_is_of_the_pinned_image_and_its_groups():
    """A new image of the MCP server (a Renovate PR) fails here until a new
    dump of it is in git, and the dump's diff shows each new, changed or
    removed tool, its description and its schema, for the review."""
    data = dump()
    assert data["image"] == mcp_image()
    assert data["groups"].split(",") == mcp_groups()
    names = [t["name"] for t in data["tools"]]
    assert names == sorted(set(names))
    assert set(names) == {t for g in mcp_groups() for t in TOOLS[g]}


def test_the_rule_names_exactly_the_kept_tools_of_the_dump():
    names = [t["name"] for t in dump()["tools"]]
    assert rule() == mcp_tools.kept(names)
    assert len(rule()) == 73
    assert not [n for n in rule()
                if n.startswith(("delete_", "bulk_", "trigger_", "create_rule", "update_rule"))
                or "export" in n or "attachment" in n]


def test_the_agent_sees_the_tools_of_the_rule():
    """The agent's filter and deny list (openclaw.json) are a second wall:
    they give the same tools as the rule, so neither drifts alone."""
    assert sorted(agent_sees([t["name"] for t in dump()["tools"]])) == rule()


def test_the_rule_list_is_written_by_the_script(tmp_path):
    copy = tmp_path / "config.yaml"
    copy.write_text(CONFIG.read_text())
    subprocess.run([sys.executable, "-I", str(GATEWAY / "tools/mcp_tools.py"), "write",
                    str(DUMP), str(copy)], check=True)
    assert copy.read_text() == CONFIG.read_text()


# Keys, clients and rules


def key_hashes(text: str) -> dict[str, str]:
    """The hash of each client of the finance route, by its metadata.client."""
    (finance,) = [r for r in yaml.safe_load(text)["routes"] if r["name"] == "finance"]
    return {k["metadata"]["client"]: k["keyHash"] for k in finance["policies"]["apiKey"]["keys"]}


def test_each_key_is_a_hash_and_each_placeholder_matches_no_value():
    """Before the owner's values step a key is a placeholder, after it the
    hash of a real key: either passes. No value is in the file, and no hash
    is the hash of a placeholder value."""
    seen = {}
    for r in config()["routes"]:
        keys = r["policies"]["apiKey"]["keys"]
        for k in keys:
            assert set(k) == {"keyHash", "metadata"}, "no key value in the config"
            assert PLACEHOLDER.match(k["keyHash"]) or HASH.match(k["keyHash"]), k["keyHash"]
            # One client may have its key on two routes (the finance agent on
            # finance and finance-vault); two clients never share a hash.
            client = k["metadata"]["client"]
            assert seen.setdefault(k["keyHash"], client) == client, "each client has a hash of its own"
            for value in ("", "pending", "none", "SET-ME", "changeme"):
                assert k["keyHash"] != "sha256:" + hashlib.sha256(value.encode()).hexdigest()
        clients = [k["metadata"]["client"] for k in keys]
        assert len(clients) == len(set(clients))


def test_each_route_denies_by_default():
    for r in config()["routes"]:
        policies = r["policies"]
        assert policies["apiKey"]["mode"] == "strict"
        assert policies["apiKey"]["location"] == {"header": {"name": "Authorization", "prefix": "Bearer "}}
        rules = policies["mcpAuthorization"]["rules"]
        # Allow rules only: a deny rule whose expression fails does not deny.
        assert all(set(x) == {"allow"} for x in rules)
        for x in rules:
            assert "apiKey.client ==" in x["allow"]


def test_the_finance_route_has_its_clients_only():
    keys = route("finance")["policies"]["apiKey"]["keys"]
    assert [k["metadata"]["client"] for k in keys] == ["finance", "finance-probe", "no-tools"]
    text = route("finance")["policies"]["mcpAuthorization"]["rules"][0]["allow"]
    assert set(re.findall(r'apiKey\.client == "([^"]+)"', text)) == {"finance", "finance-probe"}
    # The probe key may list, and call get_about only.
    assert '(mcp.methodName == "tools/list" || mcp.tool.name == "get_about")' in text


def test_the_target_is_the_mcp_server_with_a_bearer_that_is_no_secret():
    (backend,) = route("finance")["backends"]
    assert backend["mcp"]["prefixMode"] == "conditional"
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == "http://firefly-mcp.agents-finance.svc.cluster.local:3000/mcp"
    # The 60 s of the route: an MCP target of the gateway waits 10 s unless
    # it sets a time of its own.
    assert target["policies"] == {"backendAuth": {"key": "none"},
                                  "http": {"requestTimeout": route("finance")["policies"]["timeout"]["requestTimeout"]}}
    assert route("finance")["policies"]["timeout"]["requestTimeout"] == "60s"
    assert route("finance")["matches"] == [{"path": {"pathPrefix": "/finance/"}}]



def test_no_alert_rule_counts_the_requests_of_the_gateway():
    """The client of the agent opens the optional stream with a GET after
    each initialize, and the gateway logs its 405 at level error
    (docs/apps/mcp-gateway.md, "Logs and metrics"). No rule may count it:
    no rule reads the request series of the gateway."""
    rules = [p for p in (ROOT / "kubernetes").rglob("*.yaml")
             if re.search(r"(?m)^kind: PrometheusRule$", p.read_text(errors="replace"))]
    assert rules
    for path in rules:
        assert "agentgateway_" not in path.read_text(), path

def test_the_agent_and_the_probe_call_the_finance_route():
    entry = json.loads(OPENCLAW.read_text())["mcp"]["servers"]["firefly"]
    assert entry["url"] == "http://mcp-gateway.ai.svc.cluster.local:3000/finance/mcp"
    assert entry["headers"] == {"Authorization": "Bearer ${MCP_GATEWAY_KEY}"}
    env = probe_env()
    assert env["MCP_GATEWAY_URL"] == entry["url"]
    (target,) = route("finance")["backends"][0]["mcp"]["targets"]
    assert "http://" + env["MCP_SERVER"] + "/mcp" == target["mcp"]["host"]


def test_the_log_adds_the_client_and_nothing_of_a_call():
    cfg = config()
    assert cfg["frontendPolicies"]["accessLog"] == {"add": {"client": "apiKey.client"}}
    text = CONFIG.read_text()
    for word in ("arguments", "result", "request.body", "response.body", "unredacted"):
        assert word not in re.sub(r"(?m)^\s*#.*$", "", text), word


def test_only_the_headers_of_mcp_go_on_and_no_tool_name_is_a_metric():
    """The route lets only the headers of MCP through to the MCP server, and
    the counter whose label a client sets is not made and not scraped (the
    container tests show both)."""
    allowed = ["accept", "content-type", "mcp-protocol-version", "mcp-session-id", "last-event-id"]
    for r in config()["routes"]:
        replace = r["policies"]["transformations"]["request"]["replace"]
        assert replace == f"request.headers.filterKeys(k, k in {json.dumps(allowed)})"
    assert config()["config"]["metrics"]["remove"] == ["agentgateway_mcp_requests_total"]
    (endpoint,) = kind(GATEWAY / "app/podmonitor.yaml", "PodMonitor")["spec"]["podMetricsEndpoints"]
    assert endpoint["metricRelabelings"][0] == {
        "sourceLabels": ["__name__"], "regex": "agentgateway_mcp_requests_total", "action": "drop"}


def test_flux_finds_no_variable_in_the_config():
    assert "${" not in CONFIG.read_text()


# The pod


def test_the_admin_port_is_on_loopback_and_named_nowhere():
    assert config()["config"]["adminAddr"] == "localhost:15000"
    deployment = kind(GATEWAY / "app/deployment.yaml", "Deployment")
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    assert {p["containerPort"] for p in container["ports"]} == {3000, 15020, 15021}
    service = kind(GATEWAY / "app/service.yaml", "Service")
    assert [p["targetPort"] for p in service["spec"]["ports"]] == ["mcp"]


def test_the_pod_is_restricted_and_holds_no_token():
    spec = kind(GATEWAY / "app/deployment.yaml", "Deployment")["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    (container,) = spec["containers"]
    assert container["securityContext"] == {"allowPrivilegeEscalation": False,
                                            "readOnlyRootFilesystem": True,
                                            "capabilities": {"drop": ["ALL"]}}
    assert re.search(r":v[0-9.]+@sha256:[0-9a-f]{64}\Z", container["image"])
    assert "env" not in container and "envFrom" not in container
    assert not [v for v in spec["volumes"] if "secret" in v]


# The network policies


def peers(rule: dict) -> list[dict]:
    return [p["matchLabels"] for p in rule.get("fromEndpoints", rule.get("toEndpoints", []))]


def ports(rule: dict) -> list[str]:
    return [p["port"] for tp in rule["toPorts"] for p in tp["ports"]]


def who(labels: dict) -> tuple:
    return (labels["io.kubernetes.pod.namespace"],
            labels.get("app.kubernetes.io/component") or labels.get("app.kubernetes.io/name")
            or labels.get("k8s-app"))


def test_the_gateway_takes_the_agent_and_its_probe_and_reaches_the_mcp_server_only():
    policy = kind(GATEWAY / "app/networkpolicy.yaml", "CiliumNetworkPolicy")["spec"]
    into = {}
    for rule in policy["ingress"]:
        sources = [who(p) for p in peers(rule)] or [("entity", e) for e in rule["fromEntities"]]
        for s in sources:
            into[s] = ports(rule)
    assert into == {("agents-finance", "agent"): ["3000"],
                    ("agents-finance", "mcp-gateway-probe"): ["3000"],
                    ("monitoring", "prometheus"): ["15020"],
                    ("entity", "host"): ["15021"]}
    text = (GATEWAY / "app/networkpolicy.yaml").read_text()
    assert "openshell.ai" not in text and "supervisor" not in re.sub(r"(?m)^\s*#.*$", "", text)
    out = {}
    for rule in policy["egress"]:
        for p in peers(rule):
            out[who(p)] = ports(rule)
    assert out == {("agents-finance", "firefly-mcp"): ["3000"],
                   ("agents-finance", "vault-server"): ["8080"],
                   ("kube-system", "kube-dns"): ["53"]}
    (dns,) = [r for r in policy["egress"] if peers(r)[0].get("k8s-app") == "kube-dns"]
    names = [d for tp in dns["toPorts"] for d in tp["rules"]["dns"]]
    targets = [t["mcp"]["host"].split("//")[1].split(":")[0]
               for r in config()["routes"] for b in r["backends"] for t in b["mcp"]["targets"]]
    assert names == [{"matchName": t} for t in targets]


def test_only_the_gateway_reaches_the_mcp_server():
    (policy,) = [d for d in docs(FINANCE / "firefly-mcp/app/networkpolicy.yaml")
                 if d["metadata"]["name"] == "firefly-mcp"]
    (rule,) = policy["spec"]["ingress"]
    assert [who(p) for p in peers(rule)] == [("ai", "mcp-gateway")]
    assert ports(rule) == ["3000"]


def test_the_agent_reaches_the_gateway_and_not_the_mcp_server():
    policy = kind(FINANCE / "agent/app/networkpolicy.yaml", "CiliumNetworkPolicy")["spec"]
    out = [who(p) for r in policy["egress"] for p in peers(r)]
    assert ("ai", "mcp-gateway") in out
    assert ("agents-finance", "firefly-mcp") not in out


def test_no_other_check_reaches_the_mcp_server():
    (firefly_probe,) = [d for d in docs(FINANCE / "checks/app/firefly-probe.yaml")
                        if d["kind"] == "CiliumNetworkPolicy"
                        and d["metadata"]["name"] == "platform-check-firefly-probe"]
    out = [who(p) for r in firefly_probe["spec"]["egress"] for p in peers(r)]
    assert ("agents-finance", "firefly-mcp") not in out


# The container test


def run(args: list[str], check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check:
        assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return result


FAKE_APP = r'''
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def answer(self, method):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n).decode(errors="replace") if n else ""
        with open("/rec/app.jsonl", "a") as f:
            f.write(json.dumps({"method": method, "path": self.path,
                                "authorization": self.headers.get("Authorization"),
                                "body": body}) + "\n")
        data = json.dumps({"data": {"version": "test"}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/vnd.api+json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self): self.answer("GET")
    def do_POST(self): self.answer("POST")
    def do_PUT(self): self.answer("PUT")
    def do_DELETE(self): self.answer("DELETE")
ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
'''

# A recorder in front of the MCP server: it writes the headers of each
# request that the gateway sends on, then forwards it to the server.
RECORDER = r'''
import json, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
UP = sys.argv[1]
HOP = {"host", "content-length", "connection", "transfer-encoding"}
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass
    def forward(self, method):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        with open("/rec/mcp.jsonl", "a") as f:
            f.write(json.dumps({"method": method,
                                "headers": {k.lower(): v for k, v in self.headers.items()}}) + "\n")
        request = urllib.request.Request(UP + self.path, data=body, method=method,
                                         headers={k: v for k, v in self.headers.items() if k.lower() not in HOP})
        try:
            r = urllib.request.urlopen(request, timeout=30)
            status, headers, data = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, headers, data = e.code, e.headers, e.read()
        self.send_response(status)
        for k in ("content-type", "mcp-session-id"):
            if headers.get(k):
                self.send_header(k, headers.get(k))
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self): self.forward("POST")
    def do_GET(self): self.forward("GET")
    def do_DELETE(self): self.forward("DELETE")
ThreadingHTTPServer(("0.0.0.0", 3000), H).serve_forever()
'''

# Headers that a client sets, which must not reach the MCP server.
CLIENT_HEADERS = {"Cookie": "session=gateway-test-header", "X-Api-Key": "gateway-test-header",
                  "X-Client": "finance", "X-Forwarded-For": "192.0.2.1",
                  "Traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                  # Allowed, but stateless: the gateway opens its own session
                  # with the server for each request, so it never arrives.
                  "Mcp-Session-Id": "gateway-test-header-session"}
# What reaches the MCP server: the headers of MCP that the gateway lets
# through, the ones it sets itself, and the bearer of the server.
UPSTREAM_HEADERS = {"accept", "content-type", "content-length", "host", "authorization",
                    "mcp-method", "mcp-name", "mcp-protocol-version", "mcp-session-id"}

# Calls of the finance key, as the agent makes them, through the probe's
# own functions.
FINANCE_CALLS = r'''
import json, sys
sys.path.insert(0, "/scripts")
import platform_checks as p
key = sys.argv[1]
print(json.dumps({
    "list": p.listed_names(key),
    "get_about": p.called(key, "get_about", {}),
    "create_tag": p.called(key, "create_tag", {"tag": "gateway-test-argument"}),
    "delete_transaction": p.called(key, "delete_transaction", {"id": "0"}),
    "update_rule": p.called(key, "update_rule", {"id": "0", "title": "gateway-test-argument"}),
    "with_headers": p.called(key, "get_about", {}, json.loads(sys.argv[2])),
    "made_up": p.called(key, "zz_made_up_tool_name", {}),
}))
'''


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    (work / "rec").mkdir(mode=0o777)
    (work / "rec").chmod(0o777)
    (work / "app.py").write_text(FAKE_APP)
    (work / "recorder.py").write_text(RECORDER)
    keys = {client: f"test-{client}-{uuid.uuid4().hex}" for client in ("finance", "finance-probe", "no-tools")}
    text = CONFIG.read_text()
    # The hash of each client, a placeholder or a real one, becomes the hash
    # of its test key.
    for client, old in key_hashes(text).items():
        # The finance key and the probe key are on the vault route too: each
        # copy changes.
        assert text.count(old) == (1 if client == "no-tools" else 2)
        text = text.replace(old, "sha256:" + hashlib.sha256(keys[client].encode()).hexdigest())
    (work / "config.yaml").write_text(text)
    for path in work.iterdir():
        if path.is_file():
            path.chmod(0o644)
    tag = uuid.uuid4().hex[:8]
    front, back = f"mcpgw-front-{tag}", f"mcpgw-back-{tag}"
    names = {n: f"mcpgw-{n}-{tag}" for n in ("app", "mcp", "recorder", "gateway")}
    python = probe_python()
    try:
        for net in (front, back):
            run([tool, "network", "create", "--internal", net])
        # The fake app has the name of the proxy, as the MCP server calls it.
        run([tool, "run", "-d", "--name", names["app"], "--network", back,
             "--network-alias", "firefly-proxy.agents-finance.svc.cluster.local",
             "-v", f"{work}/app.py:/app.py:ro", "-v", f"{work}/rec:/rec",
             python, "python3", "-I", "/app.py"])
        server = kind(MCP, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        env = [x for e in server["env"] for x in ("-e", f"{e['name']}={e['value']}")]
        run([tool, "run", "-d", "--name", names["mcp"], "--network", back,
             "--network-alias", "firefly-mcp-upstream",
             "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--user", "1000",
             *env, mcp_image(), *server["command"]])
        # The gateway reaches the server through the recorder, at its name.
        run([tool, "run", "-d", "--name", names["recorder"], "--network", back,
             "--network-alias", "firefly-mcp.agents-finance.svc.cluster.local",
             "-v", f"{work}/recorder.py:/recorder.py:ro", "-v", f"{work}/rec:/rec",
             python, "python3", "-I", "/recorder.py", "http://firefly-mcp-upstream:3000"])
        # The gateway is on both networks; the probe and the agent's calls
        # are on the front one only, so the MCP server is out of their
        # reach, as the network policy makes it in the cluster.
        run([tool, "create", "--name", names["gateway"], "--network", back,
             "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--user", "65532",
             "-v", f"{work}/config.yaml:/config/config.yaml:ro",
             gateway_image(), "-f", "/config/config.yaml"])
        run([tool, "network", "connect", "--alias", "mcp-gateway.ai.svc.cluster.local",
             front, names["gateway"]])
        run([tool, "start", names["gateway"]])
        wait_ready(tool, front, python)
        yield {"tool": tool, "work": work, "keys": keys, "front": front, "back": back,
               "names": names, "python": python}
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        for net in (front, back):
            subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def probe_python() -> str:
    spec = kind(PROBE, "CronJob")["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return spec["containers"][0]["image"]


def on_front(tool: str, front: str, python: str, *args: str, env: dict | None = None,
             check: bool = True) -> subprocess.CompletedProcess:
    flags = [x for k, v in (env or {}).items() for x in ("-e", f"{k}={v}")]
    return run([tool, "run", "--rm", "--network", front, "--read-only", "--cap-drop", "ALL",
                "--user", "65534", "-v", f"{CHECKS.parent}:/scripts:ro",
                "-v", f"{CONFIG.parent}:/gateway:ro", *flags, python, *args], check=check)


def wait_ready(tool: str, front: str, python: str) -> None:
    code = ("import urllib.request,time\n"
            "for i in range(60):\n"
            "    try:\n"
            "        urllib.request.urlopen('http://mcp-gateway.ai.svc.cluster.local:15021/healthz/ready', timeout=2); break\n"
            "    except Exception:\n"
            "        time.sleep(1)\n"
            "else:\n"
            "    raise SystemExit('gateway not ready')\n"
            "for i in range(60):\n"
            "    try:\n"
            "        urllib.request.urlopen(urllib.request.Request('http://mcp-gateway.ai.svc.cluster.local:3000/finance/mcp', method='POST', data=b'{}'), timeout=5)\n"
            "    except urllib.error.HTTPError as e:\n"
            "        if e.code == 401: break\n"
            "    except Exception:\n"
            "        pass\n"
            "    time.sleep(1)\n")
    on_front(tool, front, python, "python3", "-I", "-c", code)
    time.sleep(3)  # the MCP server starts in a few seconds


def probe_env_for(lab: dict, probe_key: str, no_tools_key: str) -> dict:
    env = probe_env()
    env.update({"OWN_NAMESPACE": "agents-finance", "PROBE_KEY": probe_key,
                "NO_TOOLS_KEY": no_tools_key})
    # The lab has no vault server (its image has no release yet), so the
    # probe checks no vault here; test_vault_server.py reads its part.
    env.pop("VAULT_GATEWAY_URL")
    return env


def gateway_log(lab: dict) -> str:
    result = run([lab["tool"], "logs", lab["names"]["gateway"]])
    return result.stdout + result.stderr


def upstream_requests(lab: dict) -> list[dict]:
    path = lab["work"] / "rec/mcp.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def app_requests(lab: dict) -> list[dict]:
    path = lab["work"] / "rec/app.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_the_pinned_server_lists_the_tools_of_the_dump(lab):
    """The dump is the tool list of the pinned image, descriptions and
    schemas included."""
    code = ("import json,sys; sys.path.insert(0,'/tools'); import mcp_tools; "
            "print(json.dumps(mcp_tools.tools_list('http://firefly-mcp.agents-finance.svc.cluster.local:3000/mcp')))")
    result = run([lab["tool"], "run", "--rm", "--network", lab["back"], "--user", "65534",
                  "-v", f"{GATEWAY / 'tools'}:/tools:ro", lab["python"], "python3", "-c", code])
    assert json.loads(result.stdout) == dump()["tools"]


def test_the_probe_passes_and_the_app_sees_only_its_read(lab):
    before = len(app_requests(lab))
    result = on_front(lab["tool"], lab["front"], lab["python"],
                      "python3", "/scripts/platform_checks.py", "mcp-gateway",
                      env=probe_env_for(lab, lab["keys"]["finance-probe"], lab["keys"]["no-tools"]),
                      check=False)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(line["ok"] for line in lines)
    got = {line["target"]: line["got"] for line in lines}
    assert got["MCP server, direct from a pod that is not the gateway"].startswith("blocked")
    assert got["no key: tools/list"] == "refused: HTTP 401"
    assert got["a key that is not in the config: tools/list"] == "refused: HTTP 401"
    assert got["probe key: lists the 73 tools of the rule"] == "same"
    assert got["probe key: allowed read (get_about)"] == "reached: ok"
    assert got["key with no tool: tools/list"] == "tools: 0"
    assert got["probe key with headers that name the client finance: a write"] == "refused: gateway"
    assert not [k for k in got if "skipped" in got[k]]
    calls = app_requests(lab)[before:]
    assert [(c["method"], c["path"]) for c in calls] == [("GET", "/api/v1/about")]


def test_the_probe_skips_and_passes_with_pending_keys(lab):
    result = on_front(lab["tool"], lab["front"], lab["python"],
                      "python3", "/scripts/platform_checks.py", "mcp-gateway",
                      env=probe_env_for(lab, "pending", "pending"), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    got = {json.loads(line)["target"]: json.loads(line)["got"] for line in result.stdout.splitlines()}
    assert got["probe key"] == got["key with no tool"] == "skipped: the key is pending"
    assert got["no key: tools/list"] == "refused: HTTP 401"


def test_the_finance_key_gets_its_tools_and_nothing_else(lab):
    before = len(app_requests(lab))
    before_upstream = len(upstream_requests(lab))
    result = on_front(lab["tool"], lab["front"], lab["python"], "python3", "-c", FINANCE_CALLS,
                      lab["keys"]["finance"], json.dumps(CLIENT_HEADERS),
                      env={"OWN_NAMESPACE": "agents-finance", "TRUST_DOMAIN_NAMESPACES": "agents-finance",
                           "MCP_GATEWAY_URL": probe_env()["MCP_GATEWAY_URL"]})
    out = json.loads(result.stdout)
    assert out["list"] == rule()
    assert out["get_about"] == "reached: ok"
    assert out["create_tag"] == "reached: ok"
    assert out["delete_transaction"] == "refused: gateway"
    assert out["update_rule"] == "refused: gateway"
    assert out["with_headers"] == "reached: ok"
    assert out["made_up"] == "refused: gateway"
    calls = app_requests(lab)[before:]
    assert [c["method"] for c in calls] == ["GET", "POST", "GET"]
    assert not [c for c in calls if c["method"] == "DELETE" or "/rules" in c["path"]]
    # The app gets the fixed bearer, never a key of a client.
    assert {c["authorization"] for c in app_requests(lab)} == {"Bearer none"}
    # The MCP server gets the headers of MCP and its bearer only: no header
    # that the client set, and no key.
    upstream = upstream_requests(lab)[before_upstream:]
    assert upstream
    for request in upstream:
        assert set(request["headers"]) <= UPSTREAM_HEADERS, request["headers"]
        assert request["headers"]["authorization"] == "Bearer none"
        text = json.dumps(request["headers"])
        assert "gateway-test-header" not in text and "192.0.2.1" not in text
        assert lab["keys"]["finance"] not in text
    assert {r["headers"].get("mcp-name") for r in upstream} >= {"get_about", "create_tag"}


def test_the_metrics_hold_no_tool_name(lab):
    """A client sets the tool name of a request, also of a refused one: no
    metric carries it, so a client can not make series."""
    code = ("import sys, urllib.request\n"
            "sys.path.insert(0, '/scripts')\n"
            "import platform_checks as p\n"
            "assert p.called(sys.argv[1], 'zz_made_up_tool_name', {}) == 'refused: gateway'\n"
            "assert p.called(sys.argv[1], 'get_about', {}) == 'reached: ok'\n"
            "print(urllib.request.urlopen('http://mcp-gateway.ai.svc.cluster.local:15020/metrics', timeout=10).read().decode())")
    metrics = on_front(lab["tool"], lab["front"], lab["python"], "python3", "-c", code, lab["keys"]["finance"],
                       env={"OWN_NAMESPACE": "agents-finance", "TRUST_DOMAIN_NAMESPACES": "agents-finance",
                            "MCP_GATEWAY_URL": probe_env()["MCP_GATEWAY_URL"]}).stdout
    assert "agentgateway_requests_total" in metrics
    assert "agentgateway_mcp_requests_total" not in metrics
    for name in ("zz_made_up_tool_name", "get_about", "delete_transaction"):
        assert name not in metrics


def snake(rule: dict) -> dict:
    """A relabeling of a PodMonitor, as Prometheus reads it."""
    names = {"sourceLabels": "source_labels", "targetLabel": "target_label"}
    return {names.get(k, k): v for k, v in rule.items()}


def test_the_scrape_keeps_the_standard_methods_only(lab):
    """A client sends any word as the HTTP method, also with no key or to no
    route, and the gateway makes it a label value. Prometheus, with the
    relabelings of the PodMonitor, keeps the standard methods only, with no
    duplicate sample."""
    (endpoint,) = kind(GATEWAY / "app/podmonitor.yaml", "PodMonitor")["spec"]["podMetricsEndpoints"]
    work = scratch()
    (work / "prometheus.yml").write_text(yaml.safe_dump({
        "global": {"scrape_interval": "1s"},
        "scrape_configs": [{
            "job_name": "mcp-gateway",
            "static_configs": [{"targets": ["mcp-gateway.ai.svc.cluster.local:15020"]}],
            "metric_relabel_configs": [snake(r) for r in endpoint["metricRelabelings"]]}]}))
    (work / "prometheus.yml").chmod(0o644)
    name = f"mcpgw-prometheus-{uuid.uuid4().hex[:8]}"
    run([lab["tool"], "run", "-d", "--quiet", "--name", name, "--network", lab["front"],
         "--network-alias", "prometheus", "-v", f"{work}/prometheus.yml:/etc/prometheus/prometheus.yml:ro",
         PROMETHEUS, "--config.file=/etc/prometheus/prometheus.yml", "--storage.tsdb.path=/tmp"])
    code = r'''
import http.client, json, sys, time, urllib.parse, urllib.request
key = sys.argv[1]
for method in ("FOOBAR", "PROPFIND", "ZZMETHOD1", "GET", "DELETE"):
    for path, headers in (("/finance/mcp", {"Authorization": "Bearer " + key}), ("/finance/mcp", {}),
                          ("/nowhere", {})):
        c = http.client.HTTPConnection("mcp-gateway.ai.svc.cluster.local", 3000, timeout=10)
        c.request(method, path, body=b"{}", headers=headers)
        c.getresponse().read()
raw = urllib.request.urlopen("http://mcp-gateway.ai.svc.cluster.local:15020/metrics", timeout=10).read().decode()
def query(q):
    url = "http://prometheus:9090/api/v1/query?" + urllib.parse.urlencode({"query": q})
    return json.loads(urllib.request.urlopen(url, timeout=10).read())["data"]["result"]
for i in range(60):
    try:
        if query('agentgateway_requests_total{method="DELETE"}'):
            break
    except OSError:
        pass
    time.sleep(1)
time.sleep(3)
print(json.dumps({
    "raw": "FOOBAR" in raw,
    "methods": sorted({r["metric"].get("method") for r in query('{__name__=~"agentgateway_.+", method!=""}')}),
    "tmp": len(query('{__name__=~"agentgateway_.+", __tmp_known_method!=""}')),
    "duplicates": [line.split()[-1] for line in urllib.request.urlopen("http://prometheus:9090/metrics", timeout=10)
                   .read().decode().splitlines()
                   if line.startswith("prometheus_target_scrapes_sample_duplicate_timestamp_total")],
}))
'''
    try:
        result = on_front(lab["tool"], lab["front"], lab["python"], "python3", "-I", "-c", code,
                          lab["keys"]["finance"])
    finally:
        subprocess.run([lab["tool"], "container", "remove", "-f", name], capture_output=True, check=False)
    out = json.loads(result.stdout)
    assert out["raw"]  # the gateway takes the word as it is
    assert out["methods"] == ["DELETE", "GET", "POST"]
    assert out["tmp"] == 0
    assert out["duplicates"] == ["0"]


def test_the_log_has_one_line_a_request_with_the_client_and_no_argument(lab):
    log = gateway_log(lab)
    for key in lab["keys"].values():
        assert key not in log
    assert "gateway-test-argument" not in log
    assert "gateway-test-header" not in log
    lines = [json.loads(x) for x in log.splitlines() if x.startswith("{") and '"scope":"request"' in x]
    calls = [x for x in lines if x.get("mcp.method.name") == "tools/call"]
    assert calls
    assert {x.get("client") for x in calls} <= {"finance", "finance-probe", "no-tools"}
    refused = [x for x in calls if x.get("gen_ai.tool.name") == "delete_transaction"]
    assert refused and all(x["http.status"] == 400 and x["reason"] == "Authorization" for x in refused)


def test_the_admin_port_is_out_of_reach_of_other_containers(lab):
    code = ("import socket\n"
            "for port in (15000, 15020, 15021):\n"
            "    s = socket.socket(); s.settimeout(3)\n"
            "    try:\n"
            "        s.connect(('mcp-gateway.ai.svc.cluster.local', port)); print(port, 'open')\n"
            "    except OSError:\n"
            "        print(port, 'closed')\n")
    result = on_front(lab["tool"], lab["front"], lab["python"], "python3", "-I", "-c", code)
    assert result.stdout.split("\n")[:3] == ["15000 closed", "15020 open", "15021 open"]
