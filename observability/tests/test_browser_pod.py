"""The browser tool (kubernetes/apps/ai/playwright-mcp, docs/apps/playwright-mcp.md).

The static tests read the files: a network policy selects the pod of the
app, lets in the central MCP gateway and the kubelet on the MCP port, and
lets out the fetch proxy of the main agent and DNS for its name only. The
app, the gateway and the fetch proxy run at homelab-wsl only, so each peer
names this cluster of the mesh. The app has one replica there, homelab-wsl-02
does not deploy it, its Service is not global, and the declared AI list has
it at homelab-wsl only. The app has no ingress and no name on the router,
and the ingress probe of the evidence collector does not look for one.
"""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/playwright-mcp"
POLICY = APP / "app/networkpolicy.yaml"
RELEASE = APP / "app/helmrelease.yaml"
HERE = "${CLUSTER_NAME}"
PORT = "8000"


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def spec() -> dict:
    (policy,) = docs(POLICY)
    assert policy["kind"] == "CiliumNetworkPolicy"
    assert policy["metadata"]["name"] == "playwright-mcp"
    return policy["spec"]


def values() -> dict:
    (release,) = docs(RELEASE)
    return release["spec"]["values"]


def here(namespace: str, **labels: str) -> list[dict]:
    return [{"matchLabels": {"io.kubernetes.pod.namespace": namespace, **labels,
                             "io.cilium.k8s.policy.cluster": HERE}}]


def settings(cluster: str) -> dict:
    return yaml.safe_load((ROOT / f"kubernetes/clusters/{cluster}/cluster-settings.yaml").read_text())["data"]


def test_the_policy_is_a_resource_of_the_app():
    k = yaml.safe_load((APP / "app/kustomization.yaml").read_text())
    assert k["namespace"] == "ai"
    assert "./networkpolicy.yaml" in k["resources"]


def test_the_policy_selects_the_pod_of_the_app():
    # app-template labels each pod with the name of the release.
    (release,) = docs(RELEASE)
    name = release["metadata"]["name"]
    assert spec()["endpointSelector"] == {"matchLabels": {"app.kubernetes.io/name": name}}


def test_the_policy_port_is_the_port_of_the_server():
    app = values()["controllers"]["playwright-mcp"]["containers"]["app"]
    assert f"--port={PORT}" in app["args"]
    assert values()["service"]["app"]["ports"]["http"]["port"] == int(PORT)


def test_the_gateway_and_the_kubelet_come_in_on_the_mcp_port_only():
    ingress = spec()["ingress"]
    gateway, host = ingress[0], ingress[-1]
    assert gateway["fromEndpoints"] == here("ai", **{"app.kubernetes.io/name": "mcp-gateway"})
    assert host["fromEntities"] == ["host"]
    for rule in ingress:
        assert set(rule) <= {"fromEndpoints", "fromEntities", "toPorts"}, rule
        assert rule["toPorts"] == [{"ports": [{"port": PORT, "protocol": "TCP"}]}]
        assert rule.get("fromEntities", ["host"]) == ["host"]
    assert len(ingress) == 2


def test_the_pod_reaches_the_fetch_proxy_and_dns_for_its_name_only():
    proxy, dns = spec()["egress"]
    assert proxy["toEndpoints"] == here("agents-main", **{"app.kubernetes.io/name": "fetch-proxy"})
    assert proxy["toPorts"] == [{"ports": [{"port": "8080", "protocol": "TCP"}]}]
    assert dns["toEndpoints"] == [{"matchLabels": {"io.kubernetes.pod.namespace": "kube-system",
                                                   "k8s-app": "kube-dns",
                                                   "io.cilium.k8s.policy.cluster": HERE}}]
    (ports,) = dns["toPorts"]
    assert ports["ports"] == [{"port": "53", "protocol": "ANY"}]
    assert ports["rules"] == {"dns": [{"matchName": "fetch-proxy.agents-main.svc.cluster.local"}]}
    for rule in spec()["egress"]:
        assert set(rule) == {"toEndpoints", "toPorts"}, rule


def test_no_deny_rule_and_no_cidr_or_entity_out():
    assert set(spec()) == {"endpointSelector", "ingress", "egress"}


def test_each_peer_names_this_cluster_of_the_mesh():
    s = spec()
    for rule in s["ingress"] + s["egress"]:
        for peer in rule.get("fromEndpoints", []) + rule.get("toEndpoints", []):
            assert peer["matchLabels"]["io.cilium.k8s.policy.cluster"] == HERE, peer
    assert "PEER_CLUSTER_NAME" not in POLICY.read_text()
    assert settings("wsl")["CLUSTER_NAME"] == "homelab-wsl"


def test_only_homelab_wsl_runs_the_app():
    ks = "../../../apps/ai/playwright-mcp/ks.yaml"
    site2 = yaml.safe_load((ROOT / "kubernetes/clusters/wsl-02/apps/kustomization.yaml").read_text())
    assert ks not in site2["resources"]
    assert not [r for r in site2["resources"] if "playwright" in r]
    site1 = yaml.safe_load((ROOT / "kubernetes/clusters/wsl/apps/kustomization.yaml").read_text())
    assert "../../../apps/ai" in site1["resources"]
    assert "./playwright-mcp/ks.yaml" in yaml.safe_load((ROOT / "kubernetes/apps/ai/kustomization.yaml").read_text())["resources"]
    # MS-01 sets no peer name, so it must not run the app.
    other = yaml.safe_load((ROOT / "kubernetes/clusters/ms-01/apps/kustomization.yaml").read_text())
    assert not [r for r in other["resources"] if r.startswith("../../../apps/ai")]
    shared = yaml.safe_load((ROOT / "kubernetes/apps/kustomization.yaml").read_text())
    assert "../../../apps" in other["resources"] and "./ai" not in shared["resources"]


def test_one_replica_and_a_service_of_this_cluster_only():
    # The app does not move between the sites, so no cluster sets its replicas,
    # and no pod of the other cluster reaches it through a global Service.
    assert values()["controllers"]["playwright-mcp"]["replicas"] == 1
    for cluster in ("wsl", "wsl-02"):
        assert not [k for k in settings(cluster) if k.startswith("PLAYWRIGHT")], cluster
    assert "service.cilium.io" not in RELEASE.read_text()
    assert not values()["service"]["app"].get("annotations")


def test_the_declared_ai_list_has_the_app_at_homelab_wsl_only():
    # Healthy there, Not applicable at homelab-wsl-02 (homelab:ai_part_state).
    sys.path.insert(0, str(ROOT / "observability"))
    from inventory import ai_inventory

    real = ai_inventory([{"cluster": "homelab-wsl"}, {"cluster": "homelab-wsl-02"}])
    (part,) = [p for p in real["parts"] if p["workload"] == "playwright-mcp"]
    assert (part["namespace"], part["clusters"]) == ("ai", ["homelab-wsl"])
    for path in (ROOT / "observability/generated/inventory.json",
                 ROOT / "kubernetes/apps/monitoring/observability-evidence/app/config/inventory.json"):
        (row,) = [p for p in json.loads(path.read_text())["ai"]["parts"] if p["workload"] == "playwright-mcp"]
        assert row["clusters"] == ["homelab-wsl"], path


def test_the_server_keeps_its_session_isolation():
    app = values()["controllers"]["playwright-mcp"]["containers"]["app"]
    assert "--isolated" in app["args"]


def test_the_app_has_no_ingress():
    assert "ingress" not in values()
    assert "INTERNAL_DOMAIN" not in RELEASE.read_text()


def test_no_router_name_and_no_ingress_probe_for_the_app():
    records = (ROOT / "infrastructure/terraform/environments/prod/mikrotik/dns-sites.tf").read_text()
    assert "playwright" not in records
    for path in (ROOT / "observability/generated/inventory.json",
                 ROOT / "kubernetes/apps/monitoring/observability-evidence/app/config/inventory.json"):
        names = [n["name"] for n in json.loads(path.read_text())["ingress"]]
        assert names and "playwright-mcp" not in names, path
