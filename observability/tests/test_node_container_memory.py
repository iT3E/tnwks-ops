"""The memory limits of the node containers that the capacity alerts state
(observability/rules.py, the group homelab.capacity).

A node container of Talos in Docker has a Docker memory limit, which no series
carries and which the kubelet does not see: it reports the memory of the whole
WSL VM. So each alert states its limit as a constant, and this test holds the
constant equal to the Ansible inventory that sets the limit
(infrastructure/ansible/inventory/group_vars/all/talos.yml), and checks that
each site scrapes the series the alert reads.
"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "observability"))
import rules

INVENTORY = ROOT / "infrastructure/ansible/inventory/group_vars/all/talos.yml"
CLUSTERS = {"homelab-wsl": "wsl", "homelab-wsl-02": "wsl-02"}


def talos_clusters():
    return yaml.safe_load(INVENTORY.read_text())["talos_clusters"]


def gib(value):
    match = re.fullmatch(r"(\d+)GiB", value)
    assert match, value
    return int(match[1])


def test_the_limits_of_site_1_are_the_ones_of_the_inventory():
    cluster = talos_clusters()[CLUSTERS[rules.SITE1_CLUSTER]]
    assert gib(cluster["memory_workers"]) == rules.SITE1_WORKER_LIMIT_GIB
    assert gib(cluster["memory_controlplanes"]) == rules.SITE1_CONTROLPLANE_LIMIT_GIB
    # No node with a limit of its own: the rules give each role one limit.
    assert not cluster.get("memory_nodes")


def test_the_limit_of_worker_1_of_site_2_is_the_one_of_the_inventory():
    cluster = talos_clusters()[CLUSTERS["homelab-wsl-02"]]
    node = rules.CAPACITY_NODE.removeprefix("homelab-wsl-02-")
    assert gib(cluster["memory_nodes"][node]) == rules.CAPACITY_NODE_LIMIT_GIB


def test_each_alert_reads_its_site_and_states_its_limit():
    alerts = {(rule["alert"], rule["labels"]["severity"]): rule for rule in rules.capacity_alerts()}
    assert len(alerts) == len(rules.capacity_alerts())
    for name, severity, role, share, limit, wait in [
            ("WorkerContainerMemoryNearLimit", "warning", "worker", 0.9, rules.SITE1_WORKER_LIMIT_GIB, "5m"),
            ("WorkerContainerMemoryNearLimit", "critical", "worker", 0.95, rules.SITE1_WORKER_LIMIT_GIB, "2m"),
            ("ControlPlaneContainerMemoryNearLimit", "warning", "controlplane", 0.98,
             rules.SITE1_CONTROLPLANE_LIMIT_GIB, "15m")]:
        rule = alerts[name, severity]
        assert f'k8s_cluster="{rules.SITE1_CLUSTER}"' in rule["expr"], name
        assert f'node=~".+-{role}-[0-9]+"' in rule["expr"], name
        assert rule["expr"].endswith(f"> {share} * {limit} * 1024^3"), name
        assert rule["for"] == wait, name
        assert f"{limit} GiB" in rule["annotations"]["description"], name
    site2 = alerts["NodeContainerMemoryNearLimit", "warning"]["expr"]
    assert site2.endswith(f"> 0.9 * {rules.CAPACITY_NODE_LIMIT_GIB} * 1024^3")


def resource_scrape(site):
    """The ops of the patch of kube-prometheus-stack of a site on the kubelet's
    resource endpoint."""
    text = (ROOT / f"kubernetes/clusters/{site}/apps/kustomization.yaml").read_text()
    found = {}
    for doc in yaml.safe_load_all(text):
        for patch in (doc or {}).get("patches", []):
            outer = yaml.safe_load(patch.get("patch", "")) or {}
            if outer.get("metadata", {}).get("name") != "kube-prometheus-stack":
                continue
            for inner in outer.get("spec", {}).get("patches", []):
                for op in yaml.safe_load(inner["patch"]) or []:
                    if op["path"].startswith("/spec/values/kubelet/serviceMonitor/resource"):
                        found[op["path"].rsplit("/", 1)[1]] = op["value"]
    return found


def test_each_site_scrapes_the_working_set_of_its_nodes():
    for site in CLUSTERS.values():
        found = resource_scrape(site)
        assert found["resource"] is True, site
        assert found["resourcePath"] == "/metrics/resource", site
        assert found["resourceMetricRelabelings"] == [
            {"sourceLabels": ["__name__"], "regex": "node_memory_working_set_bytes", "action": "keep"}], site


ALERTMANAGER = ROOT / "kubernetes/apps/monitoring/alertmanager/app/config/alertmanager.yml"


def held(alert, sources):
    """Whether Alertmanager holds `alert` while each of `sources` fires: the
    matchers of an inhibit rule, and the same value (or no value on both) of
    each `equal` label (as security/tests/test_host_check_scrape.py)."""
    def matches(labels, matchers):
        for matcher in matchers:
            name, value = re.fullmatch(r'(\w+) = "([^"]*)"', matcher).groups()
            if labels.get(name, "") != value:
                return False
        return True

    for rule in yaml.safe_load(ALERTMANAGER.read_text())["inhibit_rules"]:
        if not matches(alert, rule["target_matchers"]):
            continue
        for source in sources:
            if matches(source, rule["source_matchers"]) and all(
                    alert.get(name, "") == source.get(name, "") for name in rule["equal"]):
                return True
    return False


def test_the_critical_worker_alert_holds_back_the_warning_of_the_same_node():
    def worker(node, severity):
        # The labels of the rule, and the external label of the Prometheus of site 1.
        return {"alertname": "WorkerContainerMemoryNearLimit", "severity": severity,
                "k8s_cluster": rules.SITE1_CLUSTER, "node": node, "cluster": rules.SITE1_CLUSTER}

    assert held(worker("lab-worker-1", "warning"), [worker("lab-worker-1", "critical")])
    assert not held(worker("lab-worker-1", "critical"), [worker("lab-worker-1", "warning")])
    # The inhibit rule compares the alert name and labels that these alerts
    # do not carry, not the node: while one worker is critical, the warning
    # of the other worker is held back too (the runbook says so).
