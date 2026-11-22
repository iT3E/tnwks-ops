"""The read servers of main (kubernetes/apps/ai/agents/main, Design Doc of
effort 0003, section 4.2.6): upstream MCP servers, each in a pod of its own
in agents-main, each behind a route of its own on the central MCP gateway
(kubernetes/apps/ai/mcp-gateway) with the key of the main agent.

The static tests read the files: each route names exactly the kept tools of
the dump of the pinned image, takes the key of the main agent only, and sets
no credential on its target; each pod is restricted and pinned; only the
gateway reaches a server, no other policy lets a pod into it, and both
gateway probes try each server straight each day and pass on a time out
only; and the read account has the role view in each namespace of the
repository but agents-finance and ai.

The container test runs the pinned gateway with the config of the repo (the
hash of the main key swapped for the hash of a test key) and each pinned
server behind a recorder at the name of its Service. It needs Docker:
without it the test is skipped, except in CI, where it fails.
"""

import hashlib
import importlib.util
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
    "flux": {
        "dir": "flux-mcp", "route": "main-flux", "port": 8080, "dump": "flux.json",
        "groups": "read-only",
        "keep": ["get_kubernetes_api_versions", "get_kubernetes_events", "get_kubernetes_logs",
                 "get_kubernetes_metrics", "get_kubernetes_resources", "read_flux_doc", "search_flux_docs",
                 "trace_kubernetes_resource"],
        # The diff and the tool of the Flux Operator (the dump of the
        # read-only mode), and the writes of the full mode, which the
        # read-only mode does not list.
        "never": ["diff_kubernetes_manifest", "get_flux_instance", "reconcile_flux_resource",
                  "apply_kubernetes_manifest", "delete_kubernetes_resource", "suspend_flux_reconciliation",
                  "resume_flux_reconciliation", "install_flux_instance"],
        "reader": True,
    },
    "terraform": {
        "dir": "terraform-mcp", "route": "main-terraform", "port": 8080, "dump": "terraform.json",
        "groups": "registry",
        "keep": ["get_latest_module_version", "get_latest_provider_version", "get_module_details",
                 "get_policy_details", "get_provider_capabilities", "get_provider_details", "search_modules",
                 "search_policies", "search_provider_docs"],
        # Tools of HCP Terraform, which the server lists only with a token
        # and other toolsets.
        "never": ["list_terraform_orgs", "list_workspaces", "get_workspace_details", "create_run",
                  "search_private_modules", "list_runs", "create_workspace"],
        "reader": False,
    },
    "github": {
        "dir": "github-mcp", "route": "main-github", "port": 8080, "dump": "github.json",
        # The kubelet probes the proxy on a port of its own, with no token.
        "health_port": 8081,
        "groups": "repos,pull_requests,actions,issues,context read-only",
        "keep": ["actions_get", "actions_list", "get_commit", "get_file_contents", "get_job_logs", "get_label",
                 "get_latest_release", "get_release_by_tag", "get_tag", "issue_read", "list_branches",
                 "list_commits", "list_issues", "list_pull_requests", "list_releases", "list_tags",
                 "pull_request_read"],
        # The reads that the design leaves out, and writes of the full mode.
        "never": ["search_code", "search_repositories", "search_issues", "search_pull_requests",
                  "search_commits", "get_me", "get_teams", "get_team_members", "list_repository_collaborators",
                  "list_issue_types", "list_issue_fields", "ui_get", "create_branch", "push_files",
                  "create_or_update_file", "create_pull_request", "merge_pull_request", "actions_run_trigger"],
        # Each call names the ops repository, so the guardrail of the route
        # lets it pass to the tool rule.
        "args": {"owner": "iT3E", "repo": "tnwks-ops"},
        "reader": False,
        # The proxy container takes the requests of the gateway.
        "container": "proxy",
    },
}
# A token of the form of an installation token of the code host, which is
# no token.
FAKE_CODE_HOST_TOKEN = "ghs_readtestnotatoken000000000000000000"
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


# The config of the main agent (the read step: each server in SERVERS is live)

AGENT_CONFIG = MAIN / "agent/config/openclaw.json"
AGENT_SECRET = MAIN / "agent/app/secret.sops.yaml"


def agent_config() -> dict:
    return json.loads(AGENT_CONFIG.read_text())


@pytest.mark.parametrize("server", SERVERS)
def test_the_agent_lists_the_kept_tools_of_the_server_and_no_other(server):
    """The design: "The config of the agent lists the same tools in its
    filter." The client is in the gateway process of the product, with the
    key of the agent from its env, never from the config."""
    spec = SERVERS[server]
    entry = agent_config()["mcp"]["servers"][server]
    assert entry == {
        "url": f"http://mcp-gateway.ai.svc.cluster.local:3000/{spec['route']}/mcp",
        "transport": "streamable-http",
        "headers": {"Authorization": "Bearer ${MCP_GATEWAY_KEY}"},
        "toolFilter": {"include": sorted(spec["keep"])},
    }


def test_the_sandbox_allow_list_names_each_kept_tool_by_its_exact_name():
    """In a sandboxed session an MCP tool also passes the sandbox allow list,
    so it names each tool of each route by its exact name, never with a
    wildcard (the design, section 4.2.6; OBS-944)."""
    config = agent_config()
    allow = config["tools"]["sandbox"]["tools"]["alsoAllow"]
    mcp = [a for a in allow if a.split("__")[0] in SERVERS and "__" in a]
    assert len(mcp) == len(set(mcp))
    assert not [a for a in mcp if "*" in a or "?" in a]
    assert sorted(mcp) == sorted(f"{s}__{t}" for s in SERVERS
                                 for t in config["mcp"]["servers"][s]["toolFilter"]["include"])
    # The vault of main (test_main_vault.py), the read servers and the
    # browser (test_main_browser.py), no other.
    assert set(config["mcp"]["servers"]) == {"vault", "browser"} | set(SERVERS)


def test_the_live_step_has_the_key_of_main_and_its_hash():
    """The read step is live only with the key: the values check of the agent
    pod stops the start with the placeholder, so CI stops it first. A read
    route with another hash than main-vault fails
    test_the_route_takes_the_main_key_only_and_sets_no_credential: the lead
    runs bin/main-mcp-key on the branch before the push."""
    assert agent_config()["mcp"]["servers"]
    assert not main_hash().startswith("sha256:" + "0" * 63), "run bin/main-mcp-key"
    (secret,) = docs(AGENT_SECRET)
    assert "MCP_GATEWAY_KEY" in secret["stringData"], "run bin/main-mcp-key"


# The pods


@pytest.mark.parametrize("server", SERVERS)
def test_the_pod_is_restricted_and_pinned(server):
    spec = pod(server)["spec"]
    assert spec["enableServiceLinks"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    for c in spec["containers"]:
        context = dict(c["securityContext"])
        # A container may set its own user (the image of the code host
        # server runs as root unless told), never root.
        assert context.pop("runAsUser", 65532) != 0 and context.pop("runAsGroup", 65532) != 0
        assert context == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
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
    # The kubelet, for the probes: on the health port where a server has
    # one, so the host gets no port that adds a token.
    assert host["fromEntities"] == ["host"] and ports(host) == [str(spec.get("health_port", spec["port"]))]


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


# The reach, proven each day: both gateway probes (main and finance) try each
# read server straight and need a time out: a policy drop in this cluster is
# silent, while a Service with no ready pod refuses at once, which proves
# nothing. The probe of finance is a pod of
# another namespace; the probe of main is a pod of the agent's namespace that
# is not the gateway, as the agent pod is. The policy of the server names the
# gateway only (above) and no other policy lets anything into the server
# (below), so the agent pod meets the rule that the probe of main meets.

PROBES = {"main": MAIN / "checks/app/mcp-gateway-probe.yaml",
          "finance": ROOT / "kubernetes/apps/ai/agents/finance/checks/app/mcp-gateway-probe.yaml"}
CHECKS = ROOT / "kubernetes/apps/ai/agents/checks/app/scripts/platform_checks.py"
POLICY_SELECTORS = {"CiliumNetworkPolicy": "endpointSelector",
                    "CiliumClusterwideNetworkPolicy": "endpointSelector", "NetworkPolicy": "podSelector"}


def service(server: str) -> str:
    spec = SERVERS[server]
    return f"{spec['dir']}.agents-main.svc.cluster.local:{spec['port']}"


def probe_container(path: Path) -> dict:
    cron = kind(path, "CronJob")
    (found,) = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    return found


@pytest.mark.parametrize("probe", PROBES)
def test_each_gateway_probe_tries_each_read_server_straight(probe):
    env = {e["name"]: e.get("value") for e in probe_container(PROBES[probe])["env"]}
    # The browser of main follows the read servers (test_main_browser.py),
    # then the host server of main (test_main_host_server.py).
    assert env["GATEWAY_ONLY_SERVERS"].split(",") == [service(s) for s in SERVERS] + [
        "playwright-mcp.ai.svc.cluster.local:8000", "host-mcp.agents-main.svc.cluster.local:8080"]
    # The probe's own policy lets the try out, so a drop comes from the
    # policy of the server.
    (policy,) = [d for d in docs(PROBES[probe]) if d["kind"] == "CiliumNetworkPolicy"]
    out = [(p["io.kubernetes.pod.namespace"], p["app.kubernetes.io/name"], ports(r))
           for r in policy["spec"]["egress"] for p in peers(r)]
    for spec in SERVERS.values():
        assert ("agents-main", spec["dir"], [str(spec["port"])]) in out


def test_the_probes_use_no_key_on_a_read_route():
    """The read routes take the key of main only (above); the probes name no
    read route and get no key but their own Secret."""
    routes = {s["route"] for s in SERVERS.values()}
    for path in PROBES.values():
        c = probe_container(path)
        values = " ".join(str(e.get("value", "")) for e in c["env"])
        assert not [r for r in routes if f"/{r}/" in values]
        assert [e["secretRef"]["name"] for e in c["envFrom"]] == ["mcp-gateway-probe-secret"]


def selects(selector: dict | None, labels: dict) -> bool:
    """Whether a pod selector (Kubernetes or Cilium) selects a pod with these
    labels. The cluster label of Cilium is taken as a match."""
    if selector is None:
        return False

    def key(k: str) -> str:
        source, _, rest = k.partition(":")
        return rest if rest and source in {"k8s", "any"} else k
    for k, v in (selector.get("matchLabels") or {}).items():
        if key(k) != "io.cilium.k8s.policy.cluster" and labels.get(key(k)) != v:
            return False
    for e in selector.get("matchExpressions") or []:
        k, op, values = key(e["key"]), e["operator"], e.get("values", [])
        if ((op == "In" and labels.get(k) not in values) or (op == "NotIn" and labels.get(k) in values)
                or (op == "Exists" and k not in labels) or (op == "DoesNotExist" and k in labels)):
            return False
    return True


def test_the_selector_reading_is_right():
    labels = {"app.kubernetes.io/name": "cluster-mcp", "agents.home.arpa/dns": "own"}
    assert selects({}, labels)
    assert selects({"matchLabels": {"k8s:app.kubernetes.io/name": "cluster-mcp"}}, labels)
    assert not selects({"matchLabels": {"app.kubernetes.io/name": "agent"}}, labels)
    assert not selects({"matchExpressions": [{"key": "agents.home.arpa/dns", "operator": "DoesNotExist"}]}, labels)
    assert selects({"matchExpressions": [{"key": "x", "operator": "NotIn", "values": ["y"]}]}, labels)
    assert not selects(None, labels)


@pytest.mark.parametrize("server", SERVERS)
def test_no_other_policy_lets_a_pod_into_the_server(server):
    """Each network policy of the repository that may select the server pod
    (one of agents-main, one with no namespace, which a Flux Kustomization
    may put there, or a cluster-wide one) has no ingress rule, but the
    server's own. Policies that a Helm chart makes are not in this reading."""
    labels = {**pod(server)["metadata"]["labels"], "io.kubernetes.pod.namespace": "agents-main"}
    own = MAIN / SERVERS[server]["dir"] / "app/networkpolicy.yaml"
    found, other = [], []
    for path in sorted((ROOT / "kubernetes").rglob("*.yaml")):
        text = path.read_text()
        if path.name.endswith(".sops.yaml") or "NetworkPolicy" not in text:
            continue
        for d in yaml.safe_load_all(text):
            if not isinstance(d, dict) or d.get("kind") not in POLICY_SELECTORS:
                continue
            found.append(d)
            namespace = (d.get("metadata") or {}).get("namespace")
            if d["kind"] != "CiliumClusterwideNetworkPolicy" and namespace not in (None, "agents-main"):
                continue
            spec = d.get("spec") or {}
            if selects(spec.get(POLICY_SELECTORS[d["kind"]]), labels) and spec.get("ingress") and path != own:
                other.append((str(path.relative_to(ROOT)), d["metadata"]["name"]))
    assert len(found) > 20
    assert other == []


def load_checks(monkeypatch, servers: str):
    monkeypatch.setenv("OWN_NAMESPACE", "agents-main")
    monkeypatch.setenv("TRUST_DOMAIN_NAMESPACES", "agents-main")
    monkeypatch.setenv("GATEWAY_ONLY_SERVERS", servers)
    for name in ("MCP_GATEWAY_URL", "VAULT_GATEWAY_URL", "OTHER_VAULT_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("platform_checks_reach", CHECKS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("connect", "ok", "got"), [
    ("timeout", True, "blocked: TimeoutError"),
    ("refused", False, "refused: ConnectionRefusedError (no ready backend or a closed port: proves nothing)"),
    ("answer", False, "connected"),
    ("no name", False, "unresolved: gaierror"),
])
def test_the_try_passes_on_a_time_out_only(monkeypatch, capsys, connect, ok, got):
    """A time out passes: a policy drop is silent. A refusal fails, since a
    server that is down or rolling refuses at once; an answer fails; a name
    that does not resolve fails, so a server that is gone does not pass."""
    checks = load_checks(monkeypatch, ",".join(service(s) for s in SERVERS))

    def getaddrinfo(host, port, *args, **kwargs):
        if connect == "no name":
            raise checks.socket.gaierror(-2, "Name or service not known")
        return [(2, 1, 6, "", ("192.0.2.1", port))]

    class Answer:
        def close(self):
            pass

    def create_connection(address, timeout=None):
        if connect == "refused":
            raise ConnectionRefusedError(111, "Connection refused")
        if connect == "timeout":
            raise TimeoutError("timed out")
        return Answer()
    monkeypatch.setattr(checks.socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(checks.socket, "create_connection", create_connection)
    checks.mcp_gateway()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["target"] for line in lines] == [
        f"{s['dir']} in agents-main, direct from a pod that is not the gateway" for s in SERVERS.values()]
    assert {(line["ok"], line["got"]) for line in lines} == {(ok, got)}
    assert checks.failures == (0 if ok else len(SERVERS))


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


def test_the_flux_server_reads_only_as_its_own_account_with_no_dns():
    spec = pod("flux")["spec"]
    assert spec["serviceAccountName"] == "flux-mcp"
    assert spec["automountServiceAccountToken"] is True
    args = container("flux")["args"]
    assert args[:4] == ["serve", "--transport", "http", "--port"]
    assert "--read-only" in args and "--mask-secrets=true" in args
    # No kubeconfig and no other identity: the in-cluster account only.
    assert not [a for a in args if a.startswith(("--kubeconfig", "--kube-as", "--as"))]
    assert [e["name"] for e in container("flux")["env"]] == ["HOME"]
    assert pod("flux")["metadata"]["labels"]["agents.home.arpa/dns"] == "none"
    (policy,) = [d for d in docs(MAIN / "flux-mcp/app/networkpolicy.yaml") if d["kind"] == "CiliumNetworkPolicy"]
    assert policy["spec"]["egress"] == [{"toEntities": ["kube-apiserver"]}]


def test_the_terraform_server_has_the_registry_only_and_no_token():
    """The design row: tools/list shows registry tools only, and the pod has
    no TFE_TOKEN. No account at the API server, no Secret, and out to the
    public registry only."""
    assert container("terraform")["args"] == ["streamable-http", "--toolsets", "registry"]
    env = {e["name"]: e for e in container("terraform")["env"]}
    assert set(env) == {"TRANSPORT_HOST", "TRANSPORT_PORT", "MCP_SESSION_MODE"}
    assert not [e for e in env.values() if "valueFrom" in e]
    assert "envFrom" not in container("terraform")
    spec = pod("terraform")["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in spec
    assert not [v for v in spec["volumes"] if "secret" in v or "projected" in v]
    assert {t["name"] for t in dump("terraform")["tools"]} == set(SERVERS["terraform"]["keep"])
    (policy,) = [d for d in docs(MAIN / "terraform-mcp/app/networkpolicy.yaml") if d["kind"] == "CiliumNetworkPolicy"]
    out, dns = policy["spec"]["egress"]
    assert out == {"toFQDNs": [{"matchName": "registry.terraform.io"}],
                   "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]}
    names = [d["matchName"] for tp in dns["toPorts"] for d in tp["rules"]["dns"]]
    host = "registry.terraform.io"
    # The name, and the name with each search suffix of the pod: no other name.
    assert set(names) == {host} | {f"{host}.{s}" for s in ("agents-main.svc.cluster.local", "svc.cluster.local", "cluster.local")}
    assert pod("terraform")["metadata"]["labels"]["agents.home.arpa/dns"] == "own-rule"


def test_the_code_host_server_reads_only_with_the_token_of_its_proxy():
    """The token is in the proxy container only, from the Secret
    github-mcp-token; the server listens on the loopback, so each request
    passes the proxy, which sets the token and drops each mode header."""
    args = container("github")["args"]
    assert args[0] == "http"
    assert {"--read-only", "--lockdown-mode", "--toolsets=", "--listen-host=127.0.0.1", "--port=8082"} <= set(args)
    (tools,) = [a for a in args if a.startswith("--tools=")]
    assert tools.removeprefix("--tools=").split(",") == sorted(SERVERS["github"]["keep"])
    assert not [a for a in args if a.startswith(("--insiders", "--features", "--gh-host", "--enable-command"))]
    assert "env" not in container("github") and "envFrom" not in container("github")
    proxy = container("github", "proxy")
    env = {e["name"]: e for e in proxy["env"]}
    assert env["GITHUB_MCP_UPSTREAM"]["value"] == "127.0.0.1:8082"
    assert env["GITHUB_MCP_TOKEN"]["valueFrom"] == {"secretKeyRef": {"name": "github-mcp-token", "key": "GITHUB_MCP_TOKEN"}}
    assert [(p["name"], p["containerPort"]) for p in proxy["ports"]] == [("mcp", 8080), ("health", 8081)]
    for probe in ("readinessProbe", "livenessProbe"):
        assert proxy[probe]["httpGet"] == {"path": "/healthz", "port": "health"}
    template = (MAIN / "github-mcp/app/proxy/default.conf.template").read_text()
    health, mcp = template.split("server {")[1:]
    # The health port serves the health path only and never the token.
    assert "listen 8081;" in health and "location = /healthz {" in health
    assert "proxy_pass" not in health and "GITHUB_MCP_TOKEN" not in health
    assert "listen 8080;" in mcp and "/healthz" not in mcp
    # The query string of a client stops at the proxy.
    assert "rewrite ^ /mcp? break;" in mcp and "proxy_pass http://${GITHUB_MCP_UPSTREAM};" in mcp
    assert 'proxy_set_header Authorization "Bearer ${GITHUB_MCP_TOKEN}";' in template
    for header in ("Readonly", "Toolsets", "Tools", "Exclude-Tools", "Features", "Insiders", "Lockdown"):
        assert f'proxy_set_header X-MCP-{header} "";' in template
    assert "location = /mcp {" in template and "location / {\n        return 404;" in template
    spec = pod("github")["spec"]
    assert spec["automountServiceAccountToken"] is False
    # No other pod of the repository names the Secret.
    for doc in manifests("Deployment", "StatefulSet", "CronJob", "Job", "DaemonSet", "Pod"):
        if doc["__path"] != MAIN / "github-mcp/app/deployment.yaml":
            assert "github-mcp-token" not in json.dumps({k: v for k, v in doc.items() if k != "__path"}), doc["__path"]


def test_the_code_host_token_is_an_encrypted_secret_of_the_app():
    """The stopgap token of the owner (D143) is the Secret of the app, in its
    kustomization, encrypted: its key name only, never a value in clear."""
    app = yaml.safe_load((MAIN / "github-mcp/app/kustomization.yaml").read_text())
    assert "./secret.sops.yaml" in app["resources"]
    (secret,) = docs(MAIN / "github-mcp/app/secret.sops.yaml")
    assert secret["kind"] == "Secret"
    assert secret["metadata"] == {"name": "github-mcp-token", "namespace": "agents-main"}
    values = secret.get("stringData") or secret.get("data")
    assert set(values) == {"GITHUB_MCP_TOKEN"}
    assert all(str(v).startswith("ENC[") for v in values.values())
    assert "sops" in secret


def test_the_code_host_route_takes_the_ops_repository_only():
    """The repository of the guardrail is the repository that Flux reads."""
    sync = (ROOT / "kubernetes/clusters/wsl/flux-system/gotk-sync.yaml").read_text()
    owner, repo = sync.split("github.com/")[1].split()[0].split("/")
    (processor,) = route("main-github")["policies"]["mcpGuardrails"]["processors"]
    assert processor["kind"] == "expression" and processor["methods"] == {"tools/call": "request"}
    assert f"mcp.params.arguments.owner == '{owner}'" in processor["condition"]
    assert f"mcp.params.arguments.repo == '{repo}'" in processor["condition"]
    assert processor["condition"].startswith("!(") and processor["reject"]
    assert SERVERS["github"]["args"] == {"owner": owner, "repo": repo}


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
    assert binding["subjects"] == readers()
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
    elif status == 200 and isinstance(body, dict) and body.get("result", {}).get("isError") and \
            " ".join(c.get("text", "") for c in body["result"].get("content", [])).endswith("the ops repository only"):
        out[name] = "refused: guardrail"
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


def fake_account(work: Path) -> Path:
    """The files of a service account in a pod, with a token that is no
    token, for each server."""
    sa = work / "sa"
    if not sa.exists():
        sa.mkdir()
        (sa / "token").write_text("read-test-not-a-token")
        (sa / "namespace").write_text("agents-main")
        (sa / "ca.crt").write_text("")
        for p in sa.iterdir():
            p.chmod(0o644)
        sa.chmod(0o755)
    return sa


def start_cluster_server(tool: str, work: Path, net: str, name: str) -> None:
    """The cluster server with the config of the repo, as in its pod: an
    in-cluster account whose API server does not answer (no tool call needs
    one to reach the server)."""
    sa = fake_account(work)
    config = work / "cluster.toml"
    config.write_text((MAIN / "cluster-mcp/app/config.toml").read_text())
    config.chmod(0o644)
    run([tool, "run", "-d", "--name", name, "--network", net, "--network-alias", "cluster-upstream",
         "--read-only", "--cap-drop", "ALL", "--user", "65532",
         "-e", "KUBERNETES_SERVICE_HOST=192.0.2.10", "-e", "KUBERNETES_SERVICE_PORT=443",
         "-v", f"{sa}:/var/run/secrets/kubernetes.io/serviceaccount:ro",
         "-v", f"{config}:/etc/cluster-mcp/config.toml:ro",
         container("cluster")["image"], *container("cluster")["args"]])


def start_flux_server(tool: str, work: Path, net: str, name: str) -> None:
    """The Flux server with the args of its pod, in-cluster: its API server
    does not answer (no tool call needs one to reach the server)."""
    sa = fake_account(work)
    run([tool, "run", "-d", "--name", name, "--network", net, "--network-alias", "flux-upstream",
         "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--user", "65532",
         "-e", "HOME=/tmp", "-e", "KUBERNETES_SERVICE_HOST=192.0.2.10", "-e", "KUBERNETES_SERVICE_PORT=443",
         "-v", f"{sa}:/var/run/secrets/kubernetes.io/serviceaccount:ro",
         container("flux")["image"], *container("flux")["args"]])


def start_terraform_server(tool: str, work: Path, net: str, name: str) -> None:
    """The Terraform server with the args and the env of its pod. No
    registry behind it: the tool list and the tool rule need none."""
    env = [x for e in container("terraform")["env"] for x in ("-e", f"{e['name']}={e['value']}")]
    run([tool, "run", "-d", "--name", name, "--network", net, "--network-alias", "terraform-upstream",
         "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--user", "65532", *env,
         container("terraform")["image"], *container("terraform")["args"]])


def start_github_proxy(tool: str, net: str, name: str, alias: str, upstream: str) -> None:
    """The proxy of the code host server, with the template of the repo and
    a token that is no token."""
    proxy = container("github", "proxy")
    env = {e["name"]: e.get("value") for e in proxy["env"]}
    run([tool, "run", "-d", "--name", name, "--network", net, "--network-alias", alias,
         "--read-only", "--tmpfs", "/tmp:uid=101,gid=101", "--tmpfs", "/etc/nginx/conf.d:uid=101,gid=101",
         "--tmpfs", "/var/cache/nginx:uid=101,gid=101", "--cap-drop", "ALL", "--user", "101",
         "-e", f"GITHUB_MCP_UPSTREAM={upstream}",
         "-e", f"NGINX_ENVSUBST_FILTER={env['NGINX_ENVSUBST_FILTER']}",
         "-e", f"GITHUB_MCP_TOKEN={FAKE_CODE_HOST_TOKEN}",
         "-v", f"{MAIN / 'github-mcp/app/proxy'}:/etc/nginx/templates:ro", proxy["image"]])


# What the proxy sends on: the path and whether a bearer came with it.
PROXY_SEEN = r'''
import http.server, json
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open("/rec/proxy-seen.jsonl", "a") as f:
            f.write(json.dumps({"path": self.path,
                                "bearer": (self.headers.get("Authorization") or "").startswith("Bearer ")}) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")
    do_GET = do_POST
    def log_message(self, *args):
        pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8082), Handler).serve_forever()
'''


def start_github_server(tool: str, work: Path, net: str, name: str) -> None:
    """The code host server with the args of its pod, and its proxy with the
    template of the repo and a token that is no token. In the pod both share
    the loopback; here the proxy reaches the server by a name. No code host
    behind it: the tool list, the tool rule and the guardrail need none."""
    args = [a if not a.startswith("--listen-host") else "--listen-host=" for a in container("github")["args"]]
    run([tool, "run", "-d", "--name", name, "--network", net, "--network-alias", "github-server",
         "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--user", "65532",
         container("github")["image"], *args])
    start_github_proxy(tool, net, name + "-proxy", "github-upstream", "github-server:8082")


STARTERS = {"cluster": start_cluster_server, "flux": start_flux_server, "terraform": start_terraform_server,
            "github": start_github_server}


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    (work / "rec").mkdir(mode=0o777)
    (work / "rec").chmod(0o777)
    recorder = RECORDER.replace('open("/rec/mcp.jsonl", "a")', 'open("/rec/" + sys.argv[2] + ".jsonl", "a")')
    recorder = recorder.replace('"headers": {k.lower()',
                                '"path": self.path, "body": (body or b"").decode(errors="replace"), '
                                '"headers": {k.lower()')
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
            if server == "github":
                names["github-proxy"] = names[server] + "-proxy"
                # A second proxy with a recorder in place of the server.
                (work / "proxy_seen.py").write_text(PROXY_SEEN)
                (work / "proxy_seen.py").chmod(0o644)
                names["proxy-seen"] = f"readsrv-proxy-seen-{tag}"
                run([tool, "run", "-d", "--name", names["proxy-seen"], "--network", back,
                     "--network-alias", "proxy-seen", "-v", f"{work}/proxy_seen.py:/proxy_seen.py:ro",
                     "-v", f"{work}/rec:/rec", PYTHON, "python3", "-I", "/proxy_seen.py"])
                names["proxy-check"] = f"readsrv-proxy-check-{tag}"
                start_github_proxy(tool, back, names["proxy-check"], "proxy-check", "proxy-seen:8082")
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
        lab = {"tool": tool, "work": work, "key": key, "front": front, "back": back, "names": names}
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
    args = spec.get("args", {})
    pairs = [[spec["keep"][0], args], *[[n, args] for n in spec["never"]], ["zz_made_up_tool_name", args]]
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
        calls(lab, target, [[spec["keep"][0], spec.get("args", {})]], CLIENT_HEADERS)
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


# Each request of the client below: the method, the URL, and the status and
# the paths that the recorder behind the proxy saw, as JSON.
PROXY_CLIENT = r'''
import json, sys, time, urllib.error, urllib.request
out = []
for method, url in json.loads(sys.argv[1]):
    for i in range(30):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method=method, data=b"{}" if method == "POST" else None,
                                                               headers={"Content-Type": "application/json"}), timeout=5) as r:
                status = r.status
            break
        except urllib.error.HTTPError as e:
            status = e.code
            break
        except OSError:
            time.sleep(1)
    else:
        status = "no answer"
    out.append([method, url, status])
print(json.dumps(out))
'''


def proxy_requests(lab: dict, pairs: list) -> tuple[list, list]:
    path = lab["work"] / "rec/proxy-seen.jsonl"
    before = len(path.read_text().splitlines()) if path.exists() else 0
    result = run([lab["tool"], "run", "--rm", "--network", lab["back"], "--read-only", "--cap-drop", "ALL",
                  "--user", "65534", PYTHON, "python3", "-I", "-c", PROXY_CLIENT, json.dumps(pairs)])
    seen = [json.loads(line) for line in path.read_text().splitlines()][before:] if path.exists() else []
    return json.loads(result.stdout), seen


def test_the_health_port_of_the_proxy_serves_the_health_path_only(lab):
    """The kubelet reaches the health port only (the policy), and that port
    never sends a request on with the token: /mcp there is a 404."""
    out, seen = proxy_requests(lab, [["GET", "http://proxy-check:8081/healthz"],
                                     ["POST", "http://proxy-check:8081/mcp"],
                                     ["GET", "http://proxy-check:8081/mcp"],
                                     ["GET", "http://proxy-check:8080/healthz"]])
    assert [status for _, _, status in out] == [200, 404, 404, 404]
    assert seen == []


def test_the_proxy_sends_no_query_string_to_the_server(lab):
    out, seen = proxy_requests(lab, [["POST", "http://proxy-check:8080/mcp?features=all&toolsets=all"],
                                     ["POST", "http://proxy-check:8080/mcp"]])
    assert [status for _, _, status in out] == [200, 200]
    assert seen == [{"path": "/mcp", "bearer": True}, {"path": "/mcp", "bearer": True}]


def test_a_call_for_another_repository_is_refused_and_not_sent_on(lab):
    before = len(recorded(lab, "github"))
    pairs = [["list_branches", {"owner": "iT3E", "repo": "other"}],
             ["get_file_contents", {"owner": "someone", "repo": "tnwks-ops", "path": "README.md"}],
             ["list_commits", {}]]
    out = calls(lab, url("github"), pairs)
    for name, _ in pairs:
        assert out[name] == "refused: guardrail", name
    # Only the tools/list of the client reached the server.
    seen = recorded(lab, "github")[before:]
    assert all(json.loads(r.get("body") or "{}").get("method") != "tools/call" for r in seen), seen
