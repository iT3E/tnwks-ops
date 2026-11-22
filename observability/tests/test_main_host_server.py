"""The host server of main at site 1 (Design Doc of effort 0003, section
4.2.6, "The host server"; task 6.22; docs/apps/host-mcp.md).

The static tests read the files:

- The Flux Kustomization is not listed until the values of its Secret
  exist.
- The pod runs the image of ours by digest with the flags of the design,
  with no shared process namespace. The token, the config and the SSH key
  come from the Secret by reference only. The proxy is the one port of the
  Service and sets the token.
- The network policy lets in the gateway and the kubelet, and lets out port
  22 of two addresses only.
- The route main-host takes a key of its own (a placeholder until the lead
  makes it), the one that the host approval plugin reads from MCP_HOST_KEY,
  and names the six read tools of the read step.

The lab runs the gateway at its pin with the config of git (a key of the
test in place of the placeholder), the proxy and the server from their
pinned images in one network namespace, as in the pod. No host: a read
reaches the server, which can not connect. It needs Docker: without it a
test is skipped, except in CI.
"""

import hashlib
import json
import re
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch
from test_main_read_servers import CONFIG, GATEWAY, PYTHON, docs, peers, ports, route
from test_mcp_gateway import PLACEHOLDER, gateway_image, run

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "kubernetes/apps/ai/agents/main"
APP = MAIN / "host-mcp/app"
DEPLOYMENT = APP / "deployment.yaml"
POLICY = APP / "networkpolicy.yaml"
TEMPLATE = APP / "proxy/default.conf.template"
AGENT_CONFIG = MAIN / "agent/config/openclaw.json"
ROUTE = "main-host"
SERVICE = "host-mcp.agents-main.svc.cluster.local"
IMAGE = "ghcr.io/it3e/tnwks-host-mcp:0.1.0@sha256:8c17e38459ed4c7bc11f967c40e73a7994a5f2bc5cac7c451a4a3f327d8909d6"
READS = ["list-connections", "list-sessions", "read-command", "read-session-output", "sftp-download", "sftp-list"]
WRITES = ["run-command", "privileged-command", "open-session", "close-session", "signal-process", "sftp-upload"]
# Out of every rule: a reload of the config, and the two tools that move a
# file of the server pod.
NEVER = ["reload-config", "sftp-download-file", "sftp-upload-file"]


def pod() -> dict:
    (d,) = [x for x in docs(DEPLOYMENT) if x["kind"] == "Deployment"]
    return d["spec"]["template"]["spec"]


def container(name: str) -> dict:
    (c,) = [x for x in pod()["containers"] if x["name"] == name]
    return c


def rule() -> list[str]:
    (r,) = route(ROUTE)["policies"]["mcpAuthorization"]["rules"]
    text = r["allow"]
    return [n.strip().strip('"') for n in text[text.index("[") + 1:text.index("]")].split(",") if n.strip()]


def plugin() -> dict:
    return json.loads(AGENT_CONFIG.read_text())["plugins"]["entries"]["host-approval"]["config"]


def test_the_server_is_not_applied_until_its_secret_exists():
    k = yaml.safe_load((MAIN / "kustomization.yaml").read_text())
    assert "./host-mcp/ks.yaml" not in k["resources"]
    assert not (APP / "secret-host-mcp.sops.yaml").exists()


def test_the_dump_is_of_the_pinned_image_and_the_rule_names_the_six_reads():
    dump = json.loads((GATEWAY / "tools/host.json").read_text())
    assert dump["image"] == container("server")["image"] == IMAGE
    names = {t["name"] for t in dump["tools"]}
    assert names == set(READS) | set(WRITES) | set(NEVER)
    assert rule() == READS


def test_the_route_takes_a_key_of_its_own_that_the_plugin_reads():
    r = route(ROUTE)
    (key,) = r["policies"]["apiKey"]["keys"]
    assert key["metadata"] == {"client": "main-host", "route": ROUTE}
    assert PLACEHOLDER.match(key["keyHash"]) or re.fullmatch(r"sha256:[0-9a-f]{64}", key["keyHash"])
    others = {k["keyHash"] for x in yaml.safe_load(CONFIG.read_text())["routes"] if x["name"] != ROUTE
              for k in x["policies"]["apiKey"]["keys"]}
    assert key["keyHash"] not in others
    assert r["policies"]["mcpAuthorization"]["rules"][0]["allow"].rstrip().endswith('apiKey.client == "main-host"')
    (backend,) = r["backends"]
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == f"http://{SERVICE}:8080/mcp"
    assert "backendAuth" not in target.get("policies", {})
    assert plugin()["mcpUrl"] == f"http://mcp-gateway.ai.svc.cluster.local:3000/{ROUTE}/mcp"
    assert plugin()["mcpKeyEnv"] == "MCP_HOST_KEY"


def test_the_server_runs_with_the_flags_of_the_design():
    args = container("server")["args"]
    for flag in ("--transport=http", "--httpHost=0.0.0.0", "--hostKeyMode=strict",
                 f"--allowedHosts={SERVICE}", "--bearerToken=$(HOST_MCP_TOKEN)"):
        assert flag in args, flag
    assert "${" not in DEPLOYMENT.read_text()


def test_the_pod_is_out_of_the_dns_of_the_namespace():
    (d,) = [x for x in docs(DEPLOYMENT) if x["kind"] == "Deployment"]
    assert d["spec"]["template"]["metadata"]["labels"]["agents.home.arpa/dns"] == "none"


def test_the_secrets_come_by_reference_only_and_no_process_is_shared():
    spec = pod()
    assert spec["shareProcessNamespace"] is False
    assert spec["automountServiceAccountToken"] is False
    for c in spec["containers"]:
        for e in c.get("env", []):
            if e["name"] == "HOST_MCP_TOKEN":
                assert set(e) == {"name", "valueFrom"}
                assert e["valueFrom"]["secretKeyRef"] == {"name": "host-mcp-secret", "key": "HOST_MCP_TOKEN"}
        assert c["securityContext"]["readOnlyRootFilesystem"] is True
        assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]
    secrets_ = {v["name"]: v["secret"] for v in spec["volumes"] if "secret" in v}
    assert {n: [i["key"] for i in s["items"]] for n, s in secrets_.items()} == {
        "secret": ["config.toml", "agent_ed25519"]}
    mounts = {c["name"]: {m["name"] for m in c.get("volumeMounts", [])}
              for c in spec["containers"] + spec["initContainers"]}
    # The step files copies them with mode 0600 for the server, in memory.
    assert mounts["files"] == {"secret", "config", "keys"}
    assert {"config", "keys"} <= mounts["server"] and "secret" not in mounts["server"]
    assert not {"secret", "config", "keys"} & mounts["proxy"]
    for v in spec["volumes"]:
        if v["name"] in ("config", "keys"):
            assert v["emptyDir"]["medium"] == "Memory"


def test_the_service_is_the_proxy_port_only():
    (svc,) = [x for x in docs(DEPLOYMENT) if x["kind"] == "Service"]
    assert [(p["port"], p["targetPort"]) for p in svc["spec"]["ports"]] == [(8080, "mcp")]
    text = TEMPLATE.read_text()
    assert 'proxy_set_header Authorization "Bearer ${HOST_MCP_TOKEN}";' in text
    assert f"proxy_set_header Host {SERVICE};" in text
    assert container("proxy")["env"][0] == {"name": "NGINX_ENVSUBST_FILTER", "value": "^HOST_MCP_"}


def test_the_policy_lets_in_the_gateway_and_out_port_22_of_two_addresses():
    (policy,) = docs(POLICY)
    spec = policy["spec"]
    who = [(p["io.kubernetes.pod.namespace"], p["app.kubernetes.io/name"], ports(r))
           for r in spec["ingress"] for p in peers(r)]
    assert who == [("ai", "mcp-gateway", ["8080"])]
    (host,) = [r for r in spec["ingress"] if r.get("fromEntities")]
    assert host["fromEntities"] == ["host"]
    (out,) = spec["egress"]
    assert out == {"toCIDR": ["${WINDOWS_HOST_IP}/32", "${HOST_MCP_DISTRO_ADDR}/32"],
                   "toPorts": [{"ports": [{"port": "22", "protocol": "TCP"}]}]}
    assert "egressDeny" not in spec and "ingressDeny" not in spec


def test_the_route_refuses_the_key_of_main():
    """The key of the agent of main is not a key of the host route, and the
    rule lets in the client main-host only. Today the hash is a placeholder,
    which no key matches, so the route refuses each key (the lab: 401)."""
    routes = yaml.safe_load(CONFIG.read_text())["routes"]
    main = {k["keyHash"] for x in routes for k in x["policies"]["apiKey"]["keys"]
            if k["metadata"]["client"] == "main"}
    assert len(main) == 1
    keys = route(ROUTE)["policies"]["apiKey"]["keys"]
    assert not main & {k["keyHash"] for k in keys}
    assert {k["metadata"]["client"] for k in keys} == {"main-host"}
    assert route(ROUTE)["policies"]["apiKey"]["mode"] == "strict"
    assert "main\"" not in route(ROUTE)["policies"]["mcpAuthorization"]["rules"][0]["allow"].replace(
        'main-host"', "")


def test_no_route_of_finance_or_of_another_key_names_a_host_tool():
    for r in yaml.safe_load(CONFIG.read_text())["routes"]:
        if r["name"] != ROUTE:
            assert SERVICE not in json.dumps(r), r["name"]
            assert not [t for t in READS + WRITES if f'"{t}"' in json.dumps(r)], r["name"]


# The lab

SERVER_CONFIG = """[defaults]
defaultProfile = "agent"
approvalMode = "auto"
commandTimeoutMs = 5000

[[profiles]]
name = "agent"
host = "lab-host.invalid"
port = 22
user = "agent"
auth = "key"
keyRef = "/keys/agent_ed25519"
trustedHostKey = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
role = "operator"
group = "staging"
approvalPolicy = "auto"
announceAgent = false
"""

CLIENT = r'''
import json, sys, urllib.request, urllib.error
def post(url, method, params, key=None, host=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key:
        h["Authorization"] = "Bearer " + key
    if host:
        h["Host"] = host
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, body, h), timeout=30) as r:
            code, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode(errors="replace")
    ev = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
    try:
        return code, json.loads(ev[-1] if ev else raw)
    except ValueError:
        return code, None
GW, KEY = sys.argv[1], sys.argv[2]
out = {}
code, body = post(GW, "tools/list", {}, KEY)
out["list"] = sorted(t["name"] for t in body["result"]["tools"]) if code == 200 else code
for name in json.loads(sys.argv[3]):
    code, body = post(GW, "tools/call", {"name": name, "arguments": {"command": "uptime"}}, KEY)
    err = (body or {}).get("error") or {}
    out[name] = ("refused: gateway" if code == 400 and err.get("message") == "unknown resource"
                 else "reached" if code == 200 and body and "result" in body else f"other: {code}")
out["no key"] = post(GW, "tools/list", {})[0]
out["main key"] = post(GW, "tools/list", {}, sys.argv[7])[0]
out["main key, cluster route"] = post(GW.replace("/main-host/", "/main-cluster/"), "tools/list", {}, sys.argv[7])[0]
out["server, no token"] = post(sys.argv[4], "tools/list", {}, host=sys.argv[5])[0]
code, body = post(sys.argv[6], "tools/list", {}, host=sys.argv[5])
out["proxy, no token"] = code
print(json.dumps(out))
'''


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    key, token = f"test-host-{uuid.uuid4().hex}", secrets.token_hex(24)
    (old,) = [k["keyHash"] for k in route(ROUTE)["policies"]["apiKey"]["keys"]]
    # A key of main for the lab, in place of the hash of the real one.
    main_key = f"test-main-{uuid.uuid4().hex}"
    (main_old,) = {k["keyHash"] for x in yaml.safe_load(CONFIG.read_text())["routes"]
                   for k in x["policies"]["apiKey"]["keys"] if k["metadata"]["client"] == "main"}
    (work / "config.yaml").write_text(CONFIG.read_text().replace(
        old, "sha256:" + hashlib.sha256(key.encode()).hexdigest()).replace(
        main_old, "sha256:" + hashlib.sha256(main_key.encode()).hexdigest()))
    (work / "secret").mkdir()
    (work / "secret/config.toml").write_text(SERVER_CONFIG)
    # Not a key: the lab connects to no host.
    (work / "secret/agent_ed25519").write_text("not a key\n")
    (work / "templates").mkdir()
    (work / "templates/default.conf.template").write_text(TEMPLATE.read_text())
    for p in work.rglob("*"):
        p.chmod(0o755 if p.is_dir() else 0o644)
    work.chmod(0o755)
    tag = uuid.uuid4().hex[:8]
    net = f"hostmcp-{tag}"
    names = {n: f"hostmcp-{n}-{tag}" for n in ("server", "proxy", "gateway")}
    hardened = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    try:
        run([tool, "network", "create", "--internal", net])
        server = container("server")
        args = [a.replace("$(HOST_MCP_TOKEN)", token) for a in server["args"]]
        (files,) = pod()["initContainers"]
        # The step files of the pod, then the server, in one container with
        # the same volumes: the Secret read only, the copies in memory.
        start = " ".join(files["command"][2:]) + ' && exec node build/index.js "$@"'
        run([tool, "run", "-d", "--name", names["server"], "--network", net, "--network-alias", SERVICE,
             *hardened, "--tmpfs", "/tmp", "--tmpfs", "/config:uid=65532,mode=0700",
             "--tmpfs", "/keys:uid=65532,mode=0700", "--user", "65532",
             "-v", f"{work}/secret:/secret:ro", "--entrypoint", "sh", IMAGE, "-c", start, "server", *args])
        proxy = container("proxy")
        run([tool, "run", "-d", "--name", names["proxy"], "--network", f"container:{names['server']}",
             *hardened, "--user", "101",
             "--tmpfs", "/tmp", "--tmpfs", "/var/cache/nginx", "--tmpfs", "/etc/nginx/conf.d:uid=101",
             "-e", "NGINX_ENVSUBST_FILTER=^HOST_MCP_", "-e", f"HOST_MCP_TOKEN={token}",
             "-v", f"{work}/templates:/etc/nginx/templates:ro", proxy["image"]])
        run([tool, "run", "-d", "--name", names["gateway"], "--network", net,
             "--network-alias", "mcp-gateway.ai.svc.cluster.local", *hardened, "--tmpfs", "/tmp",
             "--user", "65532", "-v", f"{work}/config.yaml:/config/config.yaml:ro",
             gateway_image(), "-f", "/config/config.yaml"])
        wait = ("import time, urllib.request\n"
                "for u in ('http://mcp-gateway.ai.svc.cluster.local:15021/healthz/ready',"
                f" 'http://{SERVICE}:8080/healthz'):\n"
                "    for i in range(30):\n"
                "        try:\n"
                "            urllib.request.urlopen(u, timeout=2); break\n"
                "        except Exception:\n"
                "            time.sleep(1)\n")
        run([tool, "run", "--rm", "--network", net, PYTHON, "python3", "-I", "-c", wait])
        time.sleep(2)
        yield {"tool": tool, "net": net, "key": key, "main_key": main_key, "token": token, "names": names}
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def test_the_lab_route_lists_the_reads_and_refuses_the_rest(lab):
    result = run([lab["tool"], "run", "--rm", "--network", lab["net"], "--read-only", "--cap-drop", "ALL",
                  "--user", "65534", PYTHON, "python3", "-I", "-c", CLIENT,
                  f"http://mcp-gateway.ai.svc.cluster.local:3000/{ROUTE}/mcp", lab["key"],
                  json.dumps(["read-command", *WRITES, *NEVER]),
                  f"http://{SERVICE}:3000/", SERVICE, f"http://{SERVICE}:8080/mcp", lab["main_key"]])
    out = json.loads(result.stdout)
    assert out["list"] == READS
    assert out["read-command"] == "reached"
    for name in WRITES + NEVER:
        assert out[name] == "refused: gateway", name
    assert out["no key"] == 401
    # The key of main: refused by the host route, taken by a route of main
    # (that target does not run in the lab, so not a 200).
    assert out["main key"] == 401
    assert out["main key, cluster route"] != 401
    # The server takes no call with no token. Through the proxy, the same
    # request passes the token check: the server answers it as a request
    # with no MCP session (400), not with a 401.
    assert out["server, no token"] == 401
    assert out["proxy, no token"] == 400


def test_the_lab_logs_hold_no_token_and_no_key(lab):
    for name in lab["names"].values():
        result = run([lab["tool"], "logs", name])
        log = result.stdout + result.stderr
        assert lab["token"] not in log, name
        assert lab["key"] not in log, name
        assert lab["main_key"] not in log, name
