"""The browser tool (kubernetes/apps/ai/playwright-mcp, docs/apps/playwright-mcp.md).

The static tests read the files: a network policy selects the pod of the
app, lets in the central MCP gateway and the kubelet on the MCP port, and
lets out the fetch proxy of the main agent and DNS for its name only. Each
peer names a cluster of the mesh, and each cluster that runs the app sets
both names. The app runs at one site at a time, so a peer that runs at the
other site is named there too.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/playwright-mcp"
POLICY = APP / "app/networkpolicy.yaml"
RELEASE = APP / "app/helmrelease.yaml"
HERE, PEER = "${CLUSTER_NAME}", "${PEER_CLUSTER_NAME}"
PORT = "8000"
CLUSTERS = ("wsl", "wsl-02")


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


def at_both_sites(namespace: str, **labels: str) -> list[dict]:
    return [{"matchLabels": {"io.kubernetes.pod.namespace": namespace, **labels,
                             "io.cilium.k8s.policy.cluster": cluster}}
            for cluster in (HERE, PEER)]


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
    assert gateway["fromEndpoints"] == at_both_sites("ai", **{"app.kubernetes.io/name": "mcp-gateway"})
    assert host["fromEntities"] == ["host"]
    for rule in ingress:
        assert set(rule) <= {"fromEndpoints", "fromEntities", "toPorts"}, rule
        assert rule["toPorts"] == [{"ports": [{"port": PORT, "protocol": "TCP"}]}]
        assert rule.get("fromEntities", ["host"]) == ["host"]
    sources = [s for rule in ingress for s in rule.get("fromEndpoints", [])]
    allowed = at_both_sites("ai", **{"app.kubernetes.io/name": "mcp-gateway"}) + at_both_sites(
        "networking", **{"app.kubernetes.io/name": "ingress-nginx",
                         "app.kubernetes.io/instance": "ingress-nginx-internal"})
    assert all(s in allowed for s in sources), sources


def test_the_pod_reaches_the_fetch_proxy_and_dns_for_its_name_only():
    proxy, dns = spec()["egress"]
    assert proxy["toEndpoints"] == at_both_sites("agents-main", **{"app.kubernetes.io/name": "fetch-proxy"})
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


def test_each_peer_names_a_cluster_of_the_mesh():
    s = spec()
    for rule in s["ingress"] + s["egress"]:
        for peer in rule.get("fromEndpoints", []) + rule.get("toEndpoints", []):
            assert peer["matchLabels"]["io.cilium.k8s.policy.cluster"] in (HERE, PEER), peer


def test_each_cluster_that_runs_the_app_sets_both_names():
    for cluster in CLUSTERS:
        data = yaml.safe_load((ROOT / f"kubernetes/clusters/{cluster}/cluster-settings.yaml").read_text())["data"]
        assert data["CLUSTER_NAME"].startswith("homelab-"), cluster
        assert data["PEER_CLUSTER_NAME"].startswith("homelab-"), cluster
        assert data["PEER_CLUSTER_NAME"] != data["CLUSTER_NAME"], cluster
    names = {c: yaml.safe_load((ROOT / f"kubernetes/clusters/{c}/cluster-settings.yaml").read_text())["data"]
             for c in CLUSTERS}
    assert names["wsl"]["PEER_CLUSTER_NAME"] == names["wsl-02"]["CLUSTER_NAME"]
    assert names["wsl-02"]["PEER_CLUSTER_NAME"] == names["wsl"]["CLUSTER_NAME"]


def test_only_the_wsl_clusters_run_the_app():
    site2 = yaml.safe_load((ROOT / "kubernetes/clusters/wsl-02/apps/kustomization.yaml").read_text())
    assert "../../../apps/ai/playwright-mcp/ks.yaml" in site2["resources"]
    site1 = yaml.safe_load((ROOT / "kubernetes/clusters/wsl/apps/kustomization.yaml").read_text())
    assert "../../../apps/ai" in site1["resources"]
    # MS-01 sets no peer name, so it must not run the app.
    other = yaml.safe_load((ROOT / "kubernetes/clusters/ms-01/apps/kustomization.yaml").read_text())
    assert not [r for r in other["resources"] if r.startswith("../../../apps/ai")]
    shared = yaml.safe_load((ROOT / "kubernetes/apps/kustomization.yaml").read_text())
    assert "../../../apps" in other["resources"] and "./ai" not in shared["resources"]


def test_the_server_keeps_its_session_isolation():
    app = values()["controllers"]["playwright-mcp"]["containers"]["app"]
    assert "--isolated" in app["args"]
