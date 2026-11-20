"""The network rule of Prometheus (kube-prometheus-stack/app/networkpolicy.yaml).

In its first form the rule observes and denies nothing: each request to the
query port from any source goes through the proxy of Cilium and passes, and
no port of the pod goes into default deny.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app"
POLICY = APP / "networkpolicy.yaml"


def policy() -> dict:
    (doc,) = [d for d in yaml.safe_load_all(POLICY.read_text()) if d]
    assert doc["kind"] == "CiliumNetworkPolicy" and doc["apiVersion"] == "cilium.io/v2"
    return doc["spec"]


def test_the_rule_selects_the_pod_of_prometheus_as_its_services_do():
    selector = policy()["endpointSelector"]["matchLabels"]
    assert selector == {"app.kubernetes.io/name": "prometheus",
                        "operator.prometheus.io/name": "kube-prometheus-stack-prometheus"}
    peer = yaml.safe_load((APP / "peer-service.yaml").read_text())["spec"]["selector"]
    assert selector.items() <= peer.items()
    assert "./networkpolicy.yaml" in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]


def test_the_first_form_denies_nothing_and_sends_the_query_port_through_the_proxy():
    spec = policy()
    assert spec["enableDefaultDeny"] == {"ingress": False, "egress": False}
    assert set(spec) == {"endpointSelector", "enableDefaultDeny", "ingress"}
    (rule,) = spec["ingress"]
    assert rule["fromEntities"] == ["all"]
    (to,) = rule["toPorts"]
    assert to["ports"] == [{"port": "9090", "protocol": "TCP"}]
    assert to["rules"] == {"http": [{}]}
