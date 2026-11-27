"""The copies of the cluster and Flux servers of main at site 2
(kubernetes/apps/ai/agents/main/site2; docs/apps/mcp-gateway.md, "Site 2").

Each copy is the app of site 1 with -site2 on each name and on its pod
label, in the cluster of site 2 with an account of its own there, behind
the central MCP gateway of site 1 on a route of its own, through a global
Service that exists on both sites. The tests read the files and render each
copy and its app of site 1:

- each copy has the image, the config or args and the policy of its server
  at site 1, its own label, no Service of its own, and lets in the gateway
  of the other cluster only;
- the global Services select the label of a copy, and no pod of site 1
  carries such a label, so a Service has no local endpoint at site 1;
- the read account of site 2 has the role view in each namespace of site 2
  but ai and security;
- each route has the rule, the key and the timeouts of its route at site 1,
  and its target is the global Service;
- the gateway, the agent and both gateway probes name the copies.

The render needs kustomize (or kubectl): without it the render tests are
skipped, except in CI, where they fail. The tool calls through the gateway
are in the container test of test_main_read_servers.py.
"""

import copy
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_agent_rollout import kustomize
from test_ai_inventory import SITES, resource
from test_main_read_servers import (
    CONFIG,
    GATEWAY,
    MAIN,
    PROBES,
    SERVERS,
    agent_config,
    docs,
    kind,
    main_hash,
    peers,
    ports,
    probe_container,
    route,
    rule_names,
)

ROOT = Path(__file__).resolve().parents[2]
SITE2 = MAIN / "site2"
READ_ACCESS = SITE2 / "read-access/app/rbac.yaml"
SERVICES = SITE2 / "services/app/service.yaml"
# Each copy: the server of site 1 that it copies, and the name the agent
# gives it.
COPIES = {"cluster": "cluster2", "flux": "flux2"}
# The namespaces of site 2 with no binding of the read account: ai (the
# gateway and its log, as at site 1) and security (at site 2 the home of the
# security platform). Site 2 has no agents-finance.
NO_READ = {"ai", "security"}
PEER = "${PEER_CLUSTER_NAME}"


def name(server: str) -> str:
    return f"{SERVERS[server]['dir']}-site2"


def host(server: str) -> str:
    return f"{name(server)}.agents-main.svc.cluster.local"


def render(path: Path) -> list[dict]:
    result = subprocess.run([*kustomize(), "--load-restrictor=LoadRestrictionsNone", str(path)],
                            capture_output=True, text=True, check=True)
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def by_kind(rendered: list[dict]) -> dict[str, dict]:
    found = {}
    for d in rendered:
        assert d["kind"] not in found, d["kind"]
        found[d["kind"]] = d
    return found


# The copies


def renamed(value, old: str, new: str, config: dict[str, str]):
    """The value with each string equal to the name of the server of site 1
    (or of its generated config) set to the name of the copy."""
    if isinstance(value, dict):
        return {k: renamed(v, old, new, config) for k, v in value.items()}
    if isinstance(value, list):
        return [renamed(v, old, new, config) for v in value]
    if value == old:
        return new
    return config.get(value, value)


@pytest.mark.parametrize("server", COPIES)
def test_the_copy_is_the_app_of_site_1_but_for_its_names(server):
    """Each object of the copy is its object of site 1 with -site2 on the
    names and the pod label, and the gateway of the other cluster in its
    policy. The image, the args, the config, the account and the limits are
    the ones of site 1 (whose config test_main_read_servers.py checks: no
    kubeconfig, no other identity)."""
    base = by_kind(render(MAIN / SERVERS[server]["dir"] / "app"))
    copy_ = by_kind(render(SITE2 / SERVERS[server]["dir"] / "app"))
    # No Service of its own: the global one (../services) takes its place.
    assert "Service" in base and "Service" not in copy_
    assert set(copy_) == set(base) - {"Service"}
    config = {}
    if "ConfigMap" in base:
        config[base["ConfigMap"]["metadata"]["name"]] = copy_["ConfigMap"]["metadata"]["name"]
        assert copy_["ConfigMap"]["metadata"]["name"].startswith(f"{name(server)}-config-")
    want = renamed(base, SERVERS[server]["dir"], name(server), config)
    want.pop("Service")
    (gateway, *_) = want["CiliumNetworkPolicy"]["spec"]["ingress"]
    (peer,) = gateway["fromEndpoints"]
    assert peer["matchLabels"]["io.cilium.k8s.policy.cluster"] == "${CLUSTER_NAME}"
    peer["matchLabels"]["io.cilium.k8s.policy.cluster"] = PEER
    assert copy_ == want
    deployment = copy_["Deployment"]
    assert deployment["spec"]["selector"] == {"matchLabels": {"app.kubernetes.io/name": name(server)}}
    assert deployment["spec"]["template"]["spec"]["serviceAccountName"] == name(server)
    assert copy_["ServiceAccount"]["automountServiceAccountToken"] is False
    assert not [v for v in deployment["spec"]["template"]["spec"].get("volumes", [])
                if "secret" in v or "projected" in v]


@pytest.mark.parametrize("server", COPIES)
def test_the_copy_lets_in_the_gateway_of_the_other_cluster_only(server):
    base = by_kind(render(MAIN / SERVERS[server]["dir"] / "app"))["CiliumNetworkPolicy"]["spec"]
    spec = by_kind(render(SITE2 / SERVERS[server]["dir"] / "app"))["CiliumNetworkPolicy"]["spec"]
    assert spec["endpointSelector"] == {"matchLabels": {"app.kubernetes.io/name": name(server)}}
    gateway, kubelet = spec["ingress"]
    assert peers(gateway) == [{"io.kubernetes.pod.namespace": "ai", "app.kubernetes.io/name": "mcp-gateway",
                               "io.cilium.k8s.policy.cluster": PEER}]
    assert ports(gateway) == ["8080"]
    assert kubelet == base["ingress"][1]
    # Out: the same as at site 1, so the API server of site 2 only.
    assert spec["egress"] == base["egress"]


def site_docs(site: str):
    entries = yaml.safe_load((SITES[site] / "kustomization.yaml").read_text())["resources"]
    for entry in entries:
        yield from resource((SITES[site] / entry).resolve())


POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod"}


def template_labels(doc: dict) -> dict:
    spec = doc.get("spec") or {}
    if doc["kind"] == "Pod":
        return doc["metadata"].get("labels") or {}
    if doc["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return ((spec.get("template") or {}).get("metadata") or {}).get("labels") or {}


def test_no_pod_of_site_1_carries_the_label_of_a_copy():
    """So each global Service of a copy has no local endpoint at site 1, and a
    call through a site 2 route never gets an answer of site 1."""
    labels = {name(s) for s in COPIES}
    found = []
    for _, doc in site_docs("homelab-wsl"):
        if doc.get("kind") in POD_KINDS and template_labels(doc).get("app.kubernetes.io/name") in labels:
            found.append(doc["metadata"]["name"])
        assert doc["metadata"].get("name") not in labels or doc["kind"] == "Service", doc["metadata"]["name"]
    assert found == []
    # Site 2 runs each copy.
    deployments = {(ns, d["metadata"]["name"]) for ns, d in site_docs("homelab-wsl-02") if d.get("kind") == "Deployment"}
    assert {("agents-main", name(s)) for s in COPIES} <= deployments


def test_the_global_services_are_on_both_sites_and_select_a_copy():
    services = {d["metadata"]["name"]: d for d in docs(SERVICES)}
    assert set(services) == {name(s) for s in COPIES}
    for server in COPIES:
        s = services[name(server)]
        assert s["metadata"]["annotations"] == {"service.cilium.io/global": "true"}
        assert "service.cilium.io/affinity" not in s["metadata"]["annotations"]
        assert s["spec"]["selector"] == {"app.kubernetes.io/name": name(server)}
        assert s["spec"]["ports"] == [{"name": "mcp", "port": 8080, "targetPort": 8080, "protocol": "TCP"}]
    for site in SITES:
        listed = {(ns, d["metadata"]["name"]) for ns, d in site_docs(site) if d.get("kind") == "Service"}
        assert {("agents-main", name(s)) for s in COPIES} <= listed, site


# The read rights at site 2


def namespaces_of_site_2() -> set[str]:
    return {d["metadata"]["name"] for _, d in site_docs("homelab-wsl-02")
            if d.get("kind") == "Namespace" and d.get("apiVersion") == "v1"}


def readers() -> list[dict]:
    return [{"kind": "ServiceAccount", "name": name(s), "namespace": "agents-main"} for s in COPIES]


def test_the_read_account_of_site_2_has_view_in_each_namespace_but_ai_and_security():
    """A namespace that comes later at site 2 fails here until a PR adds its
    binding or names it in NO_READ."""
    bindings = [d for d in docs(READ_ACCESS) if d["kind"] == "RoleBinding"]
    bound = [b["metadata"]["namespace"] for b in bindings]
    assert len(bound) == len(set(bound))
    assert namespaces_of_site_2() - set(bound) == NO_READ
    assert set(bound) <= namespaces_of_site_2()
    for b in bindings:
        assert b["metadata"]["name"] == "agents-main-read-site2"
        assert b["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "view"}
        assert b["subjects"] == readers()
    (role,) = [d for d in docs(READ_ACCESS) if d["kind"] == "ClusterRole"]
    assert role["rules"] == [{"apiGroups": [""], "resources": ["namespaces"], "verbs": ["get", "list", "watch"]}]
    (binding,) = [d for d in docs(READ_ACCESS) if d["kind"] == "ClusterRoleBinding"]
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert binding["subjects"] == readers()


def test_only_site_2_applies_its_read_rights_and_copies():
    site1 = {d["metadata"]["name"] for _, d in site_docs("homelab-wsl")
             if d.get("kind") in {"RoleBinding", "ClusterRoleBinding", "ClusterRole"}}
    assert not {"agents-main-read-site2", "agents-main-read-namespaces-site2"} & site1
    site2 = [(ns, d) for ns, d in site_docs("homelab-wsl-02")]
    assert {("agents-main", "ServiceAccount", name(s)) for s in COPIES} <= {
        (ns, d["kind"], d["metadata"]["name"]) for ns, d in site2}
    # No other binding of the repository names an account of a copy.
    names = {r["name"] for r in readers()}
    for path in (ROOT / "kubernetes").rglob("*.yaml"):
        if path == READ_ACCESS or path.name.endswith(".sops.yaml") or "Binding" not in path.read_text():
            continue
        for d in yaml.safe_load_all(path.read_text()):
            if isinstance(d, dict) and d.get("kind") in {"RoleBinding", "ClusterRoleBinding"}:
                for s in d.get("subjects") or []:
                    assert not (s.get("namespace") == "agents-main" and s.get("name") in names), path


# The routes, the gateway, the agent and the probes


def without_site(r: dict) -> dict:
    """A route with the parts that name its site taken out."""
    r = copy.deepcopy(r)
    r.pop("name")
    r.pop("matches")
    for key in r["policies"]["apiKey"]["keys"]:
        key["metadata"].pop("route")
    (backend,) = r["backends"]
    (target,) = backend["mcp"]["targets"]
    target.pop("name")
    target["mcp"].pop("host")
    return r


@pytest.mark.parametrize("server", COPIES)
def test_the_route_of_a_copy_is_the_route_of_site_1_at_the_global_service(server):
    site1 = SERVERS[server]["route"]
    r = route(f"{site1}-site2")
    assert r["matches"] == [{"path": {"pathPrefix": f"/{site1}-site2/"}}]
    assert r["policies"]["apiKey"]["keys"] == [
        {"keyHash": main_hash(), "metadata": {"client": "main", "route": f"{site1}-site2"}}]
    assert rule_names(f"{site1}-site2") == rule_names(site1) == sorted(SERVERS[server]["keep"])
    (backend,) = r["backends"]
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == f"http://{host(server)}:8080/mcp"
    assert target["policies"] == {"http": {"requestTimeout": "30s"}}
    assert without_site(r) == without_site(route(site1))


def test_no_other_route_names_a_copy():
    hosts = {host(s) for s in COPIES}
    own = {f"{SERVERS[s]['route']}-site2" for s in COPIES}
    for r in yaml.safe_load(CONFIG.read_text())["routes"]:
        for b in r["backends"]:
            for t in b["mcp"]["targets"]:
                if t["mcp"]["host"].split("//")[1].split(":")[0] in hosts:
                    assert r["name"] in own


@pytest.mark.parametrize("server", COPIES)
def test_the_gateway_reaches_the_copy_at_site_2_by_its_name(server):
    policy = kind(GATEWAY / "app/networkpolicy.yaml", "CiliumNetworkPolicy")["spec"]
    out = [(p, ports(r)) for r in policy["egress"] for p in peers(r)]
    assert ({"io.kubernetes.pod.namespace": "agents-main", "app.kubernetes.io/name": name(server),
             "io.cilium.k8s.policy.cluster": PEER}, ["8080"]) in out
    names = [d["matchName"] for r in policy["egress"] for tp in r.get("toPorts", [])
             for d in (tp.get("rules") or {}).get("dns", [])]
    assert host(server) in names


@pytest.mark.parametrize("server", COPIES)
def test_the_agent_names_the_copy_with_the_tools_of_its_server(server):
    config = agent_config()
    servers = config["mcp"]["servers"]
    entry = servers[COPIES[server]]
    site1 = SERVERS[server]["route"]
    assert entry == {**servers[server],
                     "url": f"http://mcp-gateway.ai.svc.cluster.local:3000/{site1}-site2/mcp"}
    allow = config["tools"]["sandbox"]["tools"]["alsoAllow"]
    assert sorted(a for a in allow if a.startswith(f"{COPIES[server]}__")) == sorted(
        f"{COPIES[server]}__{t}" for t in SERVERS[server]["keep"])


@pytest.mark.parametrize("probe", PROBES)
def test_each_gateway_probe_tries_each_copy_straight(probe):
    """Over Cluster Mesh: the policy of the copy at site 2 must drop the try
    (a time out), as the policy of each server at site 1 does."""
    c = probe_container(PROBES[probe])
    env = {e["name"]: e.get("value") for e in c["env"]}
    assert env["GATEWAY_ONLY_SERVERS"].split(",")[-len(COPIES):] == [f"{host(s)}:8080" for s in COPIES]
    (policy,) = [d for d in docs(PROBES[probe]) if d["kind"] == "CiliumNetworkPolicy"]
    out = [(p, ports(r)) for r in policy["spec"]["egress"] for p in peers(r)]
    for server in COPIES:
        assert ({"io.kubernetes.pod.namespace": "agents-main", "app.kubernetes.io/name": name(server),
                 "io.cilium.k8s.policy.cluster": PEER}, ["8080"]) in out


def test_the_dumps_hold_for_the_copies():
    """The copies run the images of site 1 (rendered above), so the dumps of
    site 1 (tools/cluster.json, tools/flux.json) are their dumps too."""
    for server in COPIES:
        data = json.loads((GATEWAY / "tools" / SERVERS[server]["dump"]).read_text())
        (deployment,) = [d for d in render(SITE2 / SERVERS[server]["dir"] / "app") if d["kind"] == "Deployment"]
        (c,) = deployment["spec"]["template"]["spec"]["containers"]
        assert data["image"] == c["image"]
