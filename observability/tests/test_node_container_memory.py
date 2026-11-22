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
    alerts = {rule["alert"]: rule for rule in rules.capacity_alerts()}
    for name, role, share, limit, wait, severity in [
            ("WorkerContainerMemoryNearLimit", "worker", 0.9, rules.SITE1_WORKER_LIMIT_GIB, "5m", "warning"),
            ("WorkerContainerMemoryAtLimit", "worker", 0.95, rules.SITE1_WORKER_LIMIT_GIB, "2m", "critical"),
            ("ControlPlaneContainerMemoryNearLimit", "controlplane", 0.9, rules.SITE1_CONTROLPLANE_LIMIT_GIB, "15m", "warning")]:
        rule = alerts[name]
        assert f'k8s_cluster="{rules.SITE1_CLUSTER}"' in rule["expr"], name
        assert f'node=~".+-{role}-[0-9]+"' in rule["expr"], name
        assert rule["expr"].endswith(f"> {share} * {limit} * 1024^3"), name
        assert (rule["for"], rule["labels"]["severity"]) == (wait, severity), name
        assert f"{limit} GiB" in rule["annotations"]["description"], name
    site2 = alerts["NodeContainerMemoryNearLimit"]["expr"]
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
