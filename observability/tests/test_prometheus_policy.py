"""The network rule of Prometheus (kube-prometheus-stack/app/networkpolicy.yaml).

The static tests read the files. The rule selects the pod of Prometheus. It
names each reader that git shows, with its port and its cluster. A reader
gets the requests that it sends, and nobody gets a write, the admin API or
the lifecycle paths. A new reader of Prometheus in git, without a line in
the rule, fails the test that scans the repository.

The proxy of Cilium matches the method and the path of a rule as a whole
regular expression against the method and the whole path with its query
string (Envoy, safe_regex on ":method" and ":path"). The tests use
re.fullmatch the same way.
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app"
POLICY = APP / "networkpolicy.yaml"
HERE, PEER = "${CLUSTER_NAME}", "${PEER_CLUSTER_NAME}"


def policy() -> dict:
    (doc,) = [d for d in yaml.safe_load_all(POLICY.read_text()) if d]
    assert doc["kind"] == "CiliumNetworkPolicy" and doc["apiVersion"] == "cilium.io/v2"
    return doc["spec"]


def source(labels: dict) -> tuple:
    return (labels["io.cilium.k8s.policy.cluster"], labels["io.kubernetes.pod.namespace"],
            labels.get("app.kubernetes.io/component") or labels.get("app.kubernetes.io/instance")
            or labels.get("operator.prometheus.io/name") or labels["app.kubernetes.io/name"])


def grants() -> dict:
    """Each source: {port: [(method, path), ...] or None for the whole port}."""
    out: dict = {}
    for rule in policy()["ingress"]:
        sources = ([source(e["matchLabels"]) for e in rule.get("fromEndpoints", [])]
                   + [("entity", e) for e in rule.get("fromEntities", [])])
        assert sources and not ({"fromEndpoints", "fromEntities"} <= rule.keys())
        for to in rule["toPorts"]:
            http = (to.get("rules") or {}).get("http")
            for port in to["ports"]:
                assert port["protocol"] == "TCP"
                for s in sources:
                    assert port["port"] not in out.setdefault(s, {}), (s, port["port"])
                    out[s][port["port"]] = None if http is None else [
                        (h["method"], h["path"]) for h in http]
    return out


def allowed(source_key: tuple, method: str, path: str, port: str = "9090") -> bool:
    rules = grants().get(source_key, {})
    if port not in rules:
        return False
    if rules[port] is None:
        return True
    return any(re.fullmatch(m, method) and re.fullmatch(p, path) for m, p in rules[port])


READ_API = [("GET", "/.*"), ("POST", (r"/api/v1/(query|query_range|query_exemplars|series|labels"
                                      r"|format_query|parse_query)(\?.*)?"))]


def test_the_rule_selects_the_pod_of_prometheus_as_its_services_do():
    selector = policy()["endpointSelector"]["matchLabels"]
    assert selector == {"app.kubernetes.io/name": "prometheus",
                        "operator.prometheus.io/name": "kube-prometheus-stack-prometheus"}
    peer = yaml.safe_load((APP / "peer-service.yaml").read_text())["spec"]["selector"]
    assert selector.items() <= peer.items()
    assert set(policy()) == {"endpointSelector", "ingress"}, "an ingress rule only: egress stays open"
    assert "./networkpolicy.yaml" in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]


def test_each_reader_has_its_port_and_its_requests_and_no_other():
    assert grants() == {
        (HERE, "monitoring", "grafana"): {"9090": READ_API},
        (PEER, "monitoring", "grafana"): {"9090": READ_API},
        (HERE, "monitoring", "ui"): {"9090": READ_API},
        (HERE, "networking", "ingress-nginx-internal"): {"9090": READ_API},
        (HERE, "monitoring", "kube-prometheus-stack-prometheus"): {
            "9090": READ_API, "8080": None, "10902": None},
        (PEER, "monitoring", "observability-evidence"): {
            "9090": [("GET", "/-/ready"), ("GET", r"/api/v1/query(\?.*)?")]},
        ("entity", "world"): {"9090": [("GET", r"/federate(\?.*)?"), ("GET", r"/api/v1/query(\?.*)?"),
                                      ("GET", "/-/(healthy|ready)")]},
        ("entity", "remote-node"): {"9090": [("GET", r"/federate(\?.*)?"), ("GET", r"/api/v1/query(\?.*)?"),
                                      ("GET", "/-/(healthy|ready)")]},
        ("entity", "kube-apiserver"): {"9090": [("GET", r"/federate(\?.*)?"), ("GET", r"/api/v1/query(\?.*)?"),
                                      ("GET", "/-/(healthy|ready)")]},
        (HERE, "monitoring", "thanos-query"): {"10901": None},
        (PEER, "monitoring", "thanos-query"): {"10901": None},
        ("entity", "host"): {"9090": None},
    }


def test_each_selector_names_its_cluster_and_the_pod_labels_of_its_reader():
    for rule in policy()["ingress"]:
        for e in rule.get("fromEndpoints", []):
            labels = e["matchLabels"]
            assert labels["io.cilium.k8s.policy.cluster"] in (HERE, PEER)
            assert "app.kubernetes.io/name" in labels
    for rule in policy()["ingress"]:
        assert set(rule.get("fromEntities", [])) <= {"world", "remote-node", "kube-apiserver", "host"}


WRITES = [
    ("POST", "/api/v1/write"), ("PUT", "/api/v1/write"), ("POST", "/api/v1/write?x=1"),
    ("POST", "/api/v1/otlp/v1/metrics"), ("POST", "/api/v1/read"),
    ("POST", "/api/v1/admin/tsdb/snapshot"), ("PUT", "/api/v1/admin/tsdb/delete_series?match[]=up"),
    ("POST", "/api/v1/admin/tsdb/clean_tombstones"), ("POST", "/-/quit"), ("PUT", "/-/reload"),
    ("POST", "/-/reload"), ("DELETE", "/api/v1/series?match[]=up"),
    ("POST", "/api/v1/query/../write"), ("POST", "/api/v1/queryx"), ("POST", "/api/v1/query_range/x"),
]


def test_nobody_may_write_or_call_the_admin_and_lifecycle_paths():
    # The host is the exception: Cilium allows the local host
    # (allow-localhost), and its line names the kubelet with no HTTP rule.
    for s in set(grants()) - {("entity", "host")}:
        for method, path in WRITES:
            assert not allowed(s, method, path), (s, method, path)


def test_each_reader_may_send_the_requests_it_sends_today():
    grafana = [("POST", "/api/v1/query_range"), ("POST", "/api/v1/query"), ("POST", "/api/v1/labels"),
               ("POST", "/api/v1/series"), ("POST", "/api/v1/query_exemplars"),
               ("GET", "/api/v1/label/__name__/values?start=1&end=2"), ("GET", "/api/v1/metadata"),
               ("GET", "/api/v1/status/buildinfo"), ("GET", "/api/v1/query?query=up&time=1")]
    for site in (HERE, PEER):
        for method, path in grafana:
            assert allowed((site, "monitoring", "grafana"), method, path), (site, method, path)
    for method, path in [*grafana, ("GET", "/query"), ("GET", "/assets/index.js"), ("GET", "/-/ready")]:
        assert allowed((HERE, "monitoring", "ui"), method, path)
        assert allowed((HERE, "networking", "ingress-nginx-internal"), method, path)
    assert allowed((HERE, "monitoring", "kube-prometheus-stack-prometheus"), "GET", "/metrics")
    evidence = (PEER, "monitoring", "observability-evidence")
    assert allowed(evidence, "GET", "/-/ready")
    assert allowed(evidence, "GET", "/api/v1/query?query=max+by+%28app%29+%28x%29")
    assert not allowed(evidence, "POST", "/api/v1/query")
    assert not allowed(evidence, "GET", "/federate?match[]=up")
    for entity in ("world", "remote-node", "kube-apiserver"):
        assert allowed(("entity", entity), "GET", "/federate?match%5B%5D=up")
        assert allowed(("entity", entity), "GET", "/api/v1/query?query=count+by+%28version%29+%28x%29")
        assert not allowed(("entity", entity), "GET", "/api/v1/series?match[]=up")
        assert allowed(("entity", entity), "GET", "/-/ready")
        assert not allowed(("entity", entity), "GET", "/-/ready?x=1")
        assert not allowed(("entity", entity), "GET", "/-/reload")
        assert not allowed(("entity", entity), "GET", "/", port="10901")
    assert not allowed((HERE, "monitoring", "grafana"), "GET", "/", port="10901")
    for site in (HERE, PEER):
        assert allowed((site, "monitoring", "thanos-query"), "POST", "/thanos.Store/Series", port="10901")
        assert not allowed((site, "monitoring", "thanos-query"), "GET", "/api/v1/query")


def test_the_named_paths_are_what_the_readers_call_in_git():
    collector = (ROOT / "kubernetes/apps/monitoring/observability-evidence/app/config/collector.py").read_text()
    assert '"/api/v1/query?"' in collector
    inventory = json.loads((ROOT / "kubernetes/apps/monitoring/observability-evidence/app/config/inventory.json")
                           .read_text())
    probes = [s["probe_url"] for s in inventory["sites"] if s.get("probe_url")]
    assert probes and all(p.endswith(":9090/-/ready") for p in probes)
    checks = (ROOT / "kubernetes/apps/security/api-checks/app/scripts/api_checks.py").read_text()
    assert "/api/v1/query?" in checks
    for release in (APP / "helmrelease.yaml", ROOT / "kubernetes/clusters/wsl-02/apps/kustomization.yaml"):
        text = release.read_text()
        assert re.search(r"job_name: peer-federate\s+honor_labels: true\s+metrics_path: /federate", text)
    datasources = yaml.safe_load((ROOT / "kubernetes/apps/monitoring/grafana/app/helmrelease.yaml").read_text())[
        "spec"]["values"]["datasources"]["datasources.yaml"]["datasources"]
    assert {d["url"] for d in datasources if d["type"] == "prometheus" and d["url"].endswith(":9090")} == {
        "http://kube-prometheus-stack-prometheus.monitoring:9090",
        "http://prometheus-homelab-wsl.monitoring.svc.cluster.local:9090",
        "http://prometheus-homelab-wsl-02.monitoring.svc.cluster.local:9090"}


# Each file of git that names a Service or an address of Prometheus, and the
# reader of the rule that it is. A new file here is a new reader: add it to
# the rule first, then here.
READERS = {
    "kubernetes/apps/monitoring/grafana/app/helmrelease.yaml": "grafana",
    "kubernetes/apps/monitoring/pyrra/app/helmrelease.yaml": "the Pyrra UI",
    "kubernetes/apps/monitoring/observability-evidence/app/config/inventory.json": "the evidence of the other site",
    "kubernetes/apps/security/api-checks/app/cronjob.yaml": "the bridge (world or a node)",
    "kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml": "the bridge, and the ingress",
    "kubernetes/clusters/wsl-02/apps/kustomization.yaml": "the bridge (world or a node)",
    "kubernetes/apps/monitoring/thanos-metrics/app/services.yaml": "the global Services, and Thanos Query",
    "kubernetes/apps/monitoring/thanos-metrics/app/helmrelease.yaml": "Thanos Query",
}
NAMES = re.compile(r"kube-prometheus-stack-prometheus[.:]|prometheus-operated|prometheus-homelab-wsl|"
                   r"prometheus-\$\{(PEER_)?CLUSTER_NAME\}|PEER_WINDOWS_HOST_ADDR\}:9090|"
                   r"thanos-sidecar-\$\{|prometheus\.\$\{INTERNAL_DOMAIN\}")


def test_each_reader_of_prometheus_in_git_is_in_the_rule():
    found = set()
    for path in (ROOT / "kubernetes").rglob("*"):
        if path.suffix not in (".yaml", ".yml", ".json", ".py") or "/dashboards/" in str(path):
            continue
        if path.name.endswith(".sops.yaml") or "/flux-system/" in str(path):
            continue
        if NAMES.search(path.read_text(errors="replace")):
            found.add(str(path.relative_to(ROOT)))
    assert found == set(READERS), sorted(found ^ set(READERS))


# The probes that the operator sets on the container prometheus (live pod,
# 2026-11-20): startup and readiness GET /-/ready, liveness GET /-/healthy, on
# 9090. The entities that can be the local host of the node of Prometheus: the
# host itself, all, and kube-apiserver (the host of the control plane node
# carries it). Cilium gives the host no HTTP rule on a port that a rule with
# HTTP rules opens to it (pkg/policy/l4.go, L4Filter.attach, v1.19.4). The
# probe paths pass even without that.
PROBES = [("GET", "/-/ready"), ("GET", "/-/healthy")]


def test_the_kubelet_probes_pass_in_each_case():
    assert grants()[("entity", "host")] == {"9090": None}
    for entity in ("all", "host", "kube-apiserver"):
        if ("entity", entity) in grants():
            for method, path in PROBES:
                assert allowed(("entity", entity), method, path), (entity, path)
