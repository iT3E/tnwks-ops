"""The network policy of Grafana (monitoring/grafana/app/networkpolicy.yaml).

The static tests read the files. The rule selects the Grafana pod and not
the image renderer, which has the policy of its chart. It opens port 3000
only, to the clients that git and the flow log show, each with its cluster.
The clients that the flow log of both sites showed on 2026-11-27 pass, and
pods that are not clients do not.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/monitoring/grafana/app"
POLICY = APP / "networkpolicy.yaml"
RELEASE = APP / "helmrelease.yaml"
HERE, PEER = "homelab-wsl", "homelab-wsl-02"


def policy() -> dict:
    (doc,) = [d for d in yaml.safe_load_all(POLICY.read_text()) if d]
    assert doc["kind"] == "CiliumNetworkPolicy" and doc["apiVersion"] == "cilium.io/v2"
    return doc


def values() -> dict:
    return yaml.safe_load(RELEASE.read_text())["spec"]["values"]


def admitted(source: dict, port: int = 3000, protocol: str = "TCP", here: str = HERE, peer: str = PEER) -> bool:
    """Whether one rule admits a source (its labels, with
    io.cilium.k8s.policy.cluster, or {"entity": name}) on a port, with the
    variables of the site of the Grafana pod."""
    for rule in policy()["spec"]["ingress"]:
        ports = {(p["port"], p["protocol"]) for tp in rule["toPorts"] for p in tp["ports"]}
        if (str(port), protocol) not in ports:
            continue
        if source.get("entity") in rule.get("fromEntities", []):
            return True
        for selector in rule.get("fromEndpoints", []):
            want = {k: v.replace("${CLUSTER_NAME}", here).replace("${PEER_CLUSTER_NAME}", peer)
                    for k, v in selector["matchLabels"].items()}
            if all(source.get(k) == v for k, v in want.items()):
                return True
    return False


def pod(cluster: str, namespace: str, **labels: str) -> dict:
    return {"io.cilium.k8s.policy.cluster": cluster, "io.kubernetes.pod.namespace": namespace,
            **{f"app.kubernetes.io/{k}": v for k, v in labels.items()}}


INTERNAL = {"name": "ingress-nginx", "instance": "ingress-nginx-internal", "component": "controller"}
# The sources of the flows to the Grafana pod in the flow log of Cilium
# (Hubble, through the relay of site 1, both sites, 2026-11-27), with their
# pod labels as Hubble gave them.
OBSERVED = [
    {"entity": "host"},
    pod(HERE, "networking", **INTERNAL, **{"part-of": "ingress-nginx"}),
    pod(PEER, "networking", **INTERNAL, **{"part-of": "ingress-nginx"}),
    pod(HERE, "monitoring", name="prometheus", instance="kube-prometheus-stack-prometheus",
        **{"managed-by": "prometheus-operator"}) | {"operator.prometheus.io/name": "kube-prometheus-stack-prometheus"},
    pod(HERE, "monitoring", name="observability-evidence", instance="observability-evidence", component="evidence"),
]
# From git, not in the window of the flow log: the callback of the image
# renderer runs only during a render.
RENDERER = pod(HERE, "monitoring", name="grafana-image-renderer", instance="grafana")


def test_the_rule_selects_the_grafana_pod_and_not_the_renderer():
    selector = policy()["spec"]["endpointSelector"]["matchLabels"]
    # The chart labels its pods with the release name as the instance.
    release = yaml.safe_load(RELEASE.read_text())
    assert release["metadata"]["name"] == "grafana" and release["spec"]["chart"]["spec"]["chart"] == "grafana"
    assert selector == {"app.kubernetes.io/name": "grafana", "app.kubernetes.io/instance": "grafana"}
    assert "./networkpolicy.yaml" in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]
    assert policy()["metadata"]["name"] == "grafana"


def test_the_renderer_keeps_the_policy_of_its_chart():
    """The chart makes a NetworkPolicy for the renderer that admits Grafana
    only (imageRenderer.networkPolicy.limitIngress, true by default)."""
    renderer = values()["imageRenderer"]
    assert renderer.get("networkPolicy", {}).get("limitIngress", True) is True


def test_ingress_only_and_port_3000_only():
    spec = policy()["spec"]
    # No egress: the egress of the pod stays as it is.
    assert set(spec) == {"endpointSelector", "ingress"}
    for rule in spec["ingress"]:
        assert [p for tp in rule["toPorts"] for p in tp["ports"]] == [{"port": "3000", "protocol": "TCP"}]
        assert not any("rules" in tp for tp in rule["toPorts"])
        assert set(rule) <= {"fromEndpoints", "fromEntities", "toPorts"}
        assert set(rule.get("fromEntities", [])) <= {"host"}


def test_each_selector_names_its_cluster_and_only_the_ingress_comes_from_the_other_site():
    for rule in policy()["spec"]["ingress"]:
        for selector in rule.get("fromEndpoints", []):
            labels = selector["matchLabels"]
            assert labels["io.cilium.k8s.policy.cluster"] in ("${CLUSTER_NAME}", "${PEER_CLUSTER_NAME}")
            assert "io.kubernetes.pod.namespace" in labels and "app.kubernetes.io/name" in labels
            if labels["io.cilium.k8s.policy.cluster"] == "${PEER_CLUSTER_NAME}":
                assert labels["app.kubernetes.io/instance"] == "ingress-nginx-internal"


def test_the_ingress_of_the_other_site_reaches_grafana_through_its_global_service():
    """Why the other site's ingress is a client: the Service is global, and
    the Ingresses go to it, not to its EndpointSlices."""
    assert values()["service"]["annotations"]["service.cilium.io/global"] == "true"
    assert values()["ingress"]["annotations"]["nginx.ingress.kubernetes.io/service-upstream"] == "true"
    embed = yaml.safe_load((APP / "ingress-embed.yaml").read_text())
    assert embed["spec"]["ingressClassName"] == "nginx-internal"
    assert embed["metadata"]["annotations"]["nginx.ingress.kubernetes.io/service-upstream"] == "true"


def test_each_observed_client_and_the_renderer_pass_at_each_site():
    for here, peer in ((HERE, PEER), (PEER, HERE)):
        for source in [*OBSERVED, RENDERER]:
            moved = dict(source)
            if "io.cilium.k8s.policy.cluster" in source:
                moved["io.cilium.k8s.policy.cluster"] = here if source["io.cilium.k8s.policy.cluster"] == HERE else peer
            assert admitted(moved, here=here, peer=peer), (here, source)


def test_other_sources_and_ports_are_dropped():
    others = [
        {"entity": "world"}, {"entity": "remote-node"}, {"entity": "kube-apiserver"}, {"entity": "cluster"},
        # The public ingress, and the internal ingress with no cluster label.
        pod(HERE, "networking", name="ingress-nginx", instance="ingress-nginx", component="controller"),
        {k: v for k, v in pod(HERE, "networking", **INTERNAL).items() if k != "io.cilium.k8s.policy.cluster"},
        # Prometheus, the evidence and the renderer of the other site.
        pod(PEER, "monitoring", name="prometheus", instance="kube-prometheus-stack-prometheus")
        | {"operator.prometheus.io/name": "kube-prometheus-stack-prometheus"},
        pod(PEER, "monitoring", name="observability-evidence"),
        pod(PEER, "monitoring", name="grafana-image-renderer", instance="grafana"),
        # Pods that are no client, with a name of a client in another namespace.
        pod(HERE, "agents-main", name="observability-evidence"),
        pod(HERE, "default", name="prometheus", instance="kube-prometheus-stack-prometheus")
        | {"operator.prometheus.io/name": "kube-prometheus-stack-prometheus"},
        pod(HERE, "monitoring", name="alloy", instance="alloy"),
        pod(HERE, "monitoring", name="grafana", instance="grafana"),
    ]
    for source in others:
        assert not admitted(source), source
    for port, protocol in ((9094, "TCP"), (9094, "UDP"), (6060, "TCP"), (80, "TCP")):
        for source in OBSERVED:
            assert not admitted(source, port, protocol), (source, port)


def test_each_in_cluster_client_of_grafana_in_git_is_in_the_rule():
    """The in-cluster address of Grafana in git: the health probe of the
    evidence (observability/inventory.py and its generated copies). A new
    client by that address needs a line in the rule."""
    found = set()
    for base in ("kubernetes", "observability"):
        for path in (ROOT / base).rglob("*"):
            if not path.is_file() or path.suffix not in (".yaml", ".yml", ".json", ".py"):
                continue
            if path.name.endswith(".sops.yaml") or "/tests/" in str(path) or "/dashboards/" in str(path):
                continue
            if re.search(r"grafana\.monitoring(\.svc)?|//grafana[:/]", path.read_text(errors="replace")):
                found.add(str(path.relative_to(ROOT)))
    assert found == {
        "kubernetes/apps/monitoring/observability-evidence/app/config/inventory.json",
        "observability/generated/inventory.json",
        "observability/inventory.py",
    }, sorted(found)
