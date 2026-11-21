"""The read servers of main (kubernetes/apps/ai/agents/main, Design Doc of
effort 0003, section 4.2.6): upstream MCP servers, each in a pod of its own
in agents-main, each behind a route of its own on the central MCP gateway
(kubernetes/apps/ai/mcp-gateway) with the key of the main agent.

The static tests read the files: each route names exactly the kept tools of
the dump of the pinned image, takes the key of the main agent only, and sets
no credential on its target; each pod is restricted and pinned; only the
gateway reaches a server; and the read account has the role view in each
namespace of the repository but agents-finance and ai.

The container test runs the pinned gateway with the config of the repo (the
hash of the main key swapped for the hash of a test key) and each pinned
server behind a recorder at the name of its Service. It needs Docker:
without it the test is skipped, except in CI, where it fails.
"""

import hashlib
import json
import subprocess
import time
import tomllib
import uuid
from pathlib import Path

import pytest
import yaml
from test_ai_inventory import SITE_APPS, resource
from test_hand_written_rules import docker, scratch
from test_mcp_gateway import RECORDER, gateway_image, run

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "kubernetes/apps/ai/mcp-gateway"
CONFIG = GATEWAY / "app/config/config.yaml"
MAIN = ROOT / "kubernetes/apps/ai/agents/main"
READ_ACCESS = MAIN / "read-access/app/rbac.yaml"
PYTHON = "docker.io/library/python:3.13-alpine"

# Each read server: its directory under agents/main, its route, its port,
# the dump of its pinned image and the groups of the dump, and the tools
# that its route keeps (the design, section 4.2.6).
SERVERS = {
    "cluster": {
        "dir": "cluster-mcp", "route": "main-cluster", "port": 8080, "dump": "cluster.json",
        "groups": "core",
        "keep": ["api_resources_list", "events_list", "namespaces_list", "pods_get",
                 "pods_list_in_namespace", "pods_log", "resources_get", "resources_list"],
        # Never kept, whatever the list says.
        "never": ["pods_list", "pods_exec", "pods_run", "pods_delete", "resources_create_or_update",
                  "resources_delete", "resources_scale", "nodes_log", "nodes_stats_summary"],
    },
}
# The namespaces with no binding of the read account (R6): the finance trust
# domain, and ai with the log of the MCP gateway.
NO_READ = {"agents-finance", "ai"}


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def kind(path: Path, k: str) -> dict:
    (found,) = [d for d in docs(path) if d["kind"] == k]
    return found


def route(name: str) -> dict:
    (found,) = [r for r in yaml.safe_load(CONFIG.read_text())["routes"] if r["name"] == name]
    return found


def rule_names(name: str) -> list[str]:
    (rule,) = route(name)["policies"]["mcpAuthorization"]["rules"]
    text = rule["allow"]
    body = text[text.index("[") + 1:text.index("]")]
    return [n.strip().strip('"') for n in body.split(",") if n.strip()]


def pod(server: str) -> dict:
    path = MAIN / SERVERS[server]["dir"] / "app/deployment.yaml"
    return kind(path, "Deployment")["spec"]["template"]


def container(server: str, name: str = "server") -> dict:
    (found,) = [c for c in pod(server)["spec"]["containers"] if c["name"] == name]
    return found


def dump(server: str) -> dict:
    return json.loads((GATEWAY / "tools" / SERVERS[server]["dump"]).read_text())


def main_hash() -> str:
    (key,) = [k for k in route("main-vault")["policies"]["apiKey"]["keys"] if k["metadata"]["client"] == "main"]
    return key["keyHash"]


def peers(rule: dict) -> list[dict]:
    return [p["matchLabels"] for p in rule.get("fromEndpoints", rule.get("toEndpoints", []))]


def ports(rule: dict) -> list[str]:
    return [p["port"] for tp in rule["toPorts"] for p in tp["ports"]]


# The tool rule


@pytest.mark.parametrize("server", SERVERS)
def test_the_dump_is_of_the_pinned_image(server):
    """A new image (a Renovate PR) fails here until a new dump of it is in
    git, and the diff of the dump shows each new or changed tool."""
    data = dump(server)
    assert data["image"] == container(server)["image"]
    assert data["groups"] == SERVERS[server]["groups"]
    names = [t["name"] for t in data["tools"]]
    assert names == sorted(set(names))


@pytest.mark.parametrize("server", SERVERS)
def test_the_route_names_exactly_the_kept_tools(server):
    spec = SERVERS[server]
    names = {t["name"] for t in dump(server)["tools"]}
    assert rule_names(spec["route"]) == sorted(spec["keep"])
    assert set(spec["keep"]) <= names
    assert not set(spec["keep"]) & set(spec["never"])
    (rule,) = route(spec["route"])["policies"]["mcpAuthorization"]["rules"]
    assert set(rule) == {"allow"}
    assert rule["allow"].rstrip().endswith('] && apiKey.client == "main"')


@pytest.mark.parametrize("server", SERVERS)
def test_the_route_takes_the_main_key_only_and_sets_no_credential(server):
    spec = SERVERS[server]
    r = route(spec["route"])
    assert r["matches"] == [{"path": {"pathPrefix": f"/{spec['route']}/"}}]
    assert r["policies"]["apiKey"]["keys"] == [
        {"keyHash": main_hash(), "metadata": {"client": "main", "route": spec["route"]}}]
    assert r["policies"]["timeout"] == {"requestTimeout": "30s"}
    (backend,) = r["backends"]
    assert backend["mcp"]["statefulMode"] == "stateless"
    assert backend["mcp"]["failureMode"] == "failClosed"
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == f"http://{spec['dir']}.agents-main.svc.cluster.local:{spec['port']}/mcp"
    # No backendAuth: the credential of a read server stays in its pod.
    assert target["policies"] == {"http": {"requestTimeout": "30s"}}


def test_no_other_route_names_a_read_server():
    """The route of finance and each other key reach no read server (R6)."""
    hosts = {f"{s['dir']}.agents-main.svc.cluster.local" for s in SERVERS.values()}
    for r in yaml.safe_load(CONFIG.read_text())["routes"]:
        if r["name"] in {s["route"] for s in SERVERS.values()}:
            continue
        for b in r["backends"]:
            for t in b["mcp"]["targets"]:
                assert t["mcp"]["host"].split("//")[1].split(":")[0] not in hosts


# The pods


@pytest.mark.parametrize("server", SERVERS)
def test_the_pod_is_restricted_and_pinned(server):
    spec = pod(server)["spec"]
    assert spec["enableServiceLinks"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    for c in spec["containers"]:
        assert c["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}}
        assert "@sha256:" in c["image"] and len(c["image"].split("@sha256:")[1]) == 64
        assert "limits" in c["resources"] and "memory" in c["resources"]["limits"]


@pytest.mark.parametrize("server", SERVERS)
def test_only_the_gateway_and_the_kubelet_reach_the_server(server):
    spec = SERVERS[server]
    (policy,) = [d for d in docs(MAIN / spec["dir"] / "app/networkpolicy.yaml") if d["kind"] == "CiliumNetworkPolicy"]
    assert policy["spec"]["endpointSelector"] == {"matchLabels": {"app.kubernetes.io/name": spec["dir"]}}
    gateway, host = policy["spec"]["ingress"]
    assert [(p["io.kubernetes.pod.namespace"], p["app.kubernetes.io/name"]) for p in peers(gateway)] == [
        ("ai", "mcp-gateway")]
    assert ports(gateway) == [str(spec["port"])]
    assert host["fromEntities"] == ["host"] and ports(host) == [str(spec["port"])]


@pytest.mark.parametrize("server", SERVERS)
def test_the_gateway_reaches_the_server_by_its_name(server):
    spec = SERVERS[server]
    policy = kind(GATEWAY / "app/networkpolicy.yaml", "CiliumNetworkPolicy")["spec"]
    out = [(p["io.kubernetes.pod.namespace"], p.get("app.kubernetes.io/name"), ports(r))
           for r in policy["egress"] for p in peers(r)]
    assert ("agents-main", spec["dir"], [str(spec["port"])]) in out
    names = [d["matchName"] for r in policy["egress"] for tp in r.get("toPorts", [])
             for d in (tp.get("rules") or {}).get("dns", [])]
    assert f"{spec['dir']}.agents-main.svc.cluster.local" in names


# The cluster server and the read rights


def cluster_config() -> dict:
    return tomllib.loads((MAIN / "cluster-mcp/app/config.toml").read_text())


def test_the_cluster_server_reads_only_as_its_own_account():
    config = cluster_config()
    assert config["read_only"] is True
    assert config["toolsets"] == ["core"]
    assert config["enabled_tools"] == sorted(SERVERS["cluster"]["keep"])
    # Never the bearer of a request: always the service account of the pod.
    assert config["cluster_auth_mode"] == "kubeconfig"
    assert config["cluster_provider_strategy"] == "in-cluster"
    assert {"group": "", "version": "v1", "kind": "Secret"} in config["denied_resources"]
    assert "kubeconfig" not in config
    spec = pod("cluster")["spec"]
    assert spec["serviceAccountName"] == "cluster-mcp"
    assert spec["automountServiceAccountToken"] is True
    assert "env" not in container("cluster")


def test_the_cluster_server_reaches_the_api_server_and_its_name_only():
    (policy,) = [d for d in docs(MAIN / "cluster-mcp/app/networkpolicy.yaml") if d["kind"] == "CiliumNetworkPolicy"]
    api, dns = policy["spec"]["egress"]
    assert api == {"toEntities": ["kube-apiserver"]}
    names = [d["matchName"] for tp in dns["toPorts"] for d in tp["rules"]["dns"]]
    assert names == ["kubernetes.default.svc.agents-main.svc.cluster.local",
                     "kubernetes.default.svc.svc.cluster.local", "kubernetes.default.svc.cluster.local"]
    assert pod("cluster")["metadata"]["labels"]["agents.home.arpa/dns"] == "own-rule"


def namespaces_of_the_cluster() -> set[str]:
    """Each namespace that Flux applies on the cluster of site 1, where the
    agents run: the walk of the app list of that cluster, as
    test_ai_inventory.py walks it. The app tree also holds namespaces of
    another cluster, which this one does not have."""
    entries = yaml.safe_load((SITE_APPS / "kustomization.yaml").read_text())["resources"]
    return {doc["metadata"]["name"] for entry in entries for _, doc in resource((SITE_APPS / entry).resolve())
            if doc.get("kind") == "Namespace" and doc.get("apiVersion") == "v1"}


def manifests(*kinds: str):
    """Each Kubernetes document of the given kinds under kubernetes/, with its
    path. A file that is not a manifest (the config of an app) is skipped."""
    for path in (ROOT / "kubernetes").rglob("*.yaml"):
        if path.name.endswith(".sops.yaml"):
            continue
        text = path.read_text()
        if not any(f"kind: {k}" in text for k in kinds):
            continue
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind") in kinds:
                doc["__path"] = path
                yield doc


def readers() -> list[dict]:
    return [{"kind": "ServiceAccount", "name": s["dir"], "namespace": "agents-main"}
            for s in SERVERS.values() if s.get("reader", s["dir"] == "cluster-mcp")]


def test_the_read_account_has_view_in_each_namespace_but_finance_and_ai():
    """The check of the design: each namespace of the cluster with no
    binding of the read account. Only agents-finance and ai are on the list.
    A namespace that comes later fails here until a PR adds its binding or
    names it in NO_READ."""
    bindings = [d for d in docs(READ_ACCESS) if d["kind"] == "RoleBinding"]
    bound = [b["metadata"]["namespace"] for b in bindings]
    assert len(bound) == len(set(bound))
    # Each namespace of the cluster but those two has a binding, and no
    # binding names a namespace that the cluster does not have.
    assert namespaces_of_the_cluster() - set(bound) == NO_READ
    assert set(bound) <= namespaces_of_the_cluster()
    assert not set(bound) & NO_READ
    for b in bindings:
        assert b["metadata"]["name"] == "agents-main-read"
        assert b["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "view"}
        assert b["subjects"] == readers()


def test_no_cluster_wide_read_but_the_names_of_the_namespaces():
    (role,) = [d for d in docs(READ_ACCESS) if d["kind"] == "ClusterRole"]
    assert role["rules"] == [{"apiGroups": [""], "resources": ["namespaces"], "verbs": ["get", "list", "watch"]}]
    (binding,) = [d for d in docs(READ_ACCESS) if d["kind"] == "ClusterRoleBinding"]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert binding["subjects"] == [r for r in readers() if r["name"] == "cluster-mcp"]
    # No other binding in the repository names a read account.
    names = {r["name"] for r in readers()}
    for doc in manifests("RoleBinding", "ClusterRoleBinding"):
        if doc["__path"] == READ_ACCESS:
            continue
        for s in doc.get("subjects") or []:
            assert not (s.get("namespace") == "agents-main" and s.get("name") in names), doc["__path"]


# The container test

# Calls of a client of the main key through the gateway: the tool list, and
# the outcome of each call. A refusal of the gateway is a 400 with the
# JSON-RPC error "unknown resource" (agentgateway 1.7.0); a call that it
# sends on gets an answer of the server.
CLIENT = r'''
import json, sys, urllib.request, urllib.error
url, key, calls, extra = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4])
def post(method, params, headers):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
         "Authorization": "Bearer " + key, **headers}
    r = urllib.request.Request(url, method="POST", headers=h,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode())
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            status, raw = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode(errors="replace")
    ev = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
    try:
        return status, json.loads(ev[-1] if ev else raw)
    except ValueError:
        return status, raw[:200]
out = {}
status, body = post("tools/list", {}, extra)
out["list"] = sorted(t["name"] for t in body["result"]["tools"]) if status == 200 else status
for name, args in calls:
    status, body = post("tools/call", {"name": name, "arguments": args}, extra)
    error = body.get("error") if isinstance(body, dict) else None
    if status == 400 and error and error.get("message") == "unknown resource":
        out[name] = "refused: gateway"
    elif status == 200 and isinstance(body, dict) and "result" in body:
        out[name] = "reached"
    else:
        out[name] = f"other: {status}"
print(json.dumps(out))
'''

# Headers and paths that a client sets, which must not reach a server: the
# mode headers of the code host server among them.
CLIENT_HEADERS = {"Cookie": "session=read-test-header", "X-Api-Key": "read-test-header",
                  "X-MCP-Readonly": "false", "X-MCP-Tools": "read-test-header",
                  "X-MCP-Toolsets": "all", "X-MCP-Insiders": "true",
                  "X-Forwarded-For": "192.0.2.1", "Tfe-Token": "read-test-header"}
UPSTREAM_HEADERS = {"accept", "content-type", "content-length", "host",
                    "mcp-method", "mcp-name", "mcp-protocol-version", "mcp-session-id"}


def start_cluster_server(tool: str, work: Path, net: str, name: str) -> None:
    """The cluster server with the config of the repo, as in its pod: an
    in-cluster account whose API server does not answer (no tool call needs
    one to reach the server)."""
    sa = work / "sa"
    sa.mkdir()
    (sa / "token").write_text("read-test-not-a-token")
    (sa / "namespace").write_text("agents-main")
    (sa / "ca.crt").write_text("")
    for p in sa.iterdir():
        p.chmod(0o644)
    sa.chmod(0o755)
    config = work / "cluster.toml"
    config.write_text((MAIN / "cluster-mcp/app/config.toml").read_text())
    config.chmod(0o644)
    run([tool, "run", "-d", "--name", name, "--network", net, "--network-alias", "cluster-upstream",
         "--read-only", "--cap-drop", "ALL", "--user", "65532",
         "-e", "KUBERNETES_SERVICE_HOST=192.0.2.10", "-e", "KUBERNETES_SERVICE_PORT=443",
         "-v", f"{sa}:/var/run/secrets/kubernetes.io/serviceaccount:ro",
         "-v", f"{config}:/etc/cluster-mcp/config.toml:ro",
         container("cluster")["image"], *container("cluster")["args"]])


STARTERS = {"cluster": start_cluster_server}


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    (work / "rec").mkdir(mode=0o777)
    (work / "rec").chmod(0o777)
    recorder = RECORDER.replace('open("/rec/mcp.jsonl", "a")', 'open("/rec/" + sys.argv[2] + ".jsonl", "a")')
    recorder = recorder.replace('"headers": {k.lower()', '"path": self.path, "headers": {k.lower()')
    # On the port of the server whose name it takes.
    recorder = recorder.replace('("0.0.0.0", 3000)', '("0.0.0.0", int(sys.argv[3]))')
    (work / "recorder.py").write_text(recorder)
    key = f"test-main-{uuid.uuid4().hex}"
    text = CONFIG.read_text()
    text = text.replace(main_hash(), "sha256:" + hashlib.sha256(key.encode()).hexdigest())
    (work / "config.yaml").write_text(text)
    for path in work.iterdir():
        if path.is_file():
            path.chmod(0o644)
    tag = uuid.uuid4().hex[:8]
    front, back = f"readsrv-front-{tag}", f"readsrv-back-{tag}"
    names = {"gateway": f"readsrv-gateway-{tag}"}
    try:
        for net in (front, back):
            run([tool, "network", "create", "--internal", net])
        for server, spec in SERVERS.items():
            names[server] = f"readsrv-{server}-{tag}"
            STARTERS[server](tool, work, back, names[server])
            names[server + "-recorder"] = f"readsrv-{server}-rec-{tag}"
            upstream = spec.get("upstream", f"http://{server}-upstream:{spec['port']}")
            run([tool, "run", "-d", "--name", names[server + "-recorder"], "--network", back,
                 "--network-alias", f"{spec['dir']}.agents-main.svc.cluster.local",
                 "-v", f"{work}/recorder.py:/recorder.py:ro", "-v", f"{work}/rec:/rec",
                 PYTHON, "python3", "-I", "/recorder.py", upstream, server, str(spec["port"])])
        run([tool, "create", "--name", names["gateway"], "--network", back,
             "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--user", "65532",
             "-v", f"{work}/config.yaml:/config/config.yaml:ro",
             gateway_image(), "-f", "/config/config.yaml"])
        run([tool, "network", "connect", "--alias", "mcp-gateway.ai.svc.cluster.local", front, names["gateway"]])
        run([tool, "start", names["gateway"]])
        lab = {"tool": tool, "work": work, "key": key, "front": front, "names": names}
        wait_ready(lab)
        yield lab
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        for net in (front, back):
            subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def on_front(lab: dict, *args: str) -> subprocess.CompletedProcess:
    return run([lab["tool"], "run", "--rm", "--network", lab["front"], "--read-only", "--cap-drop", "ALL",
                "--user", "65534", PYTHON, *args])


def wait_ready(lab: dict) -> None:
    code = ("import sys, json, urllib.request, urllib.error, time\n"
            "for url in sys.argv[2:]:\n"
            "    for i in range(90):\n"
            "        r = urllib.request.Request(url, method='POST', data=json.dumps({'jsonrpc': '2.0', 'id': 1,\n"
            "            'method': 'tools/list', 'params': {}}).encode(), headers={'Authorization': 'Bearer ' + sys.argv[1],\n"
            "            'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})\n"
            "        try:\n"
            "            urllib.request.urlopen(r, timeout=5).read(); break\n"
            "        except Exception:\n"
            "            time.sleep(1)\n"
            "    else:\n"
            "        raise SystemExit('not ready: ' + url)\n")
    on_front(lab, "python3", "-I", "-c", code, lab["key"],
             *[url(s) for s in SERVERS])


def url(server: str) -> str:
    return f"http://mcp-gateway.ai.svc.cluster.local:3000/{SERVERS[server]['route']}/mcp"


def calls(lab: dict, target: str, pairs: list, headers: dict | None = None) -> dict:
    result = on_front(lab, "python3", "-I", "-c", CLIENT, target, lab["key"], json.dumps(pairs),
                      json.dumps(headers or {}))
    return json.loads(result.stdout)


def recorded(lab: dict, server: str) -> list[dict]:
    path = lab["work"] / "rec" / f"{server}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("server", SERVERS)
def test_the_main_key_sees_and_calls_the_kept_tools_only(lab, server):
    spec = SERVERS[server]
    pairs = [[spec["keep"][0], {}], *[[n, {}] for n in spec["never"]], ["zz_made_up_tool_name", {}]]
    out = calls(lab, url(server), pairs)
    assert out["list"] == sorted(spec["keep"])
    assert out[spec["keep"][0]] == "reached"
    for name in [*spec["never"], "zz_made_up_tool_name"]:
        assert out[name] == "refused: gateway", name


@pytest.mark.parametrize("server", SERVERS)
def test_no_header_or_path_of_a_client_reaches_the_server(lab, server):
    spec = SERVERS[server]
    before = len(recorded(lab, server))
    base = url(server).removesuffix("/mcp")
    for target in (url(server), f"{base}/readonly", f"{base}/x/repos", f"{base}/insiders?features=all"):
        calls(lab, target, [[spec["keep"][0], {}]], CLIENT_HEADERS)
    seen = recorded(lab, server)[before:]
    assert seen
    for request in seen:
        assert set(request["headers"]) <= UPSTREAM_HEADERS, request["headers"]
        assert "authorization" not in request["headers"]
        assert request["path"] == "/mcp"
        text = json.dumps(request)
        assert "read-test-header" not in text and "192.0.2.1" not in text and lab["key"] not in text


def test_the_gateway_log_has_no_key_and_no_argument(lab):
    time.sleep(1)
    result = run([lab["tool"], "logs", lab["names"]["gateway"]])
    log = result.stdout + result.stderr
    assert lab["key"] not in log
    assert "read-test-header" not in log
