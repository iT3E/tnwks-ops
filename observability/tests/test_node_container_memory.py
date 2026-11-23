"""The memory limits of the node containers that the capacity alerts state
(observability/rules.py, the group homelab.capacity).

A node container of Talos in Docker has a Docker memory limit, which no series
carries and which the kubelet does not see: it reports the memory of the whole
WSL VM. So each alert states its limit as a constant, and this test holds the
constant equal to the Ansible inventory that sets the limit
(infrastructure/ansible/inventory/group_vars/all/talos.yml), and checks that
each site scrapes the series the alert reads.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "observability"))
import rules
from versions import PROMETHEUS

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


def test_the_resident_memory_alert_reads_the_root_cgroup_of_each_role_of_site_1():
    (rule,) = [r for r in rules.capacity_alerts() if r["alert"] == "NodeContainerRssNearLimit"]
    expr = rule["expr"]
    for role, limit in (("worker", rules.SITE1_WORKER_LIMIT_GIB), ("controlplane", rules.SITE1_CONTROLPLANE_LIMIT_GIB)):
        assert (f'container_memory_rss{{k8s_cluster="{rules.SITE1_CLUSTER}",job="kubelet",'
                f'metrics_path="/metrics/cadvisor",id="/",node=~".+-{role}-[0-9]+"}})'
                f" > {rules.SITE1_RSS_SHARE} * {limit} * 1024^3)") in expr, role
        assert f"{limit} GiB" in rule["annotations"]["description"], role
    assert rule["for"] == rules.SITE1_RSS_FOR
    assert rule["labels"] == {"severity": "warning"}


# The cAdvisor relabel list of kube-prometheus-stack (values.yaml,
# kubelet.serviceMonitor.cAdvisorMetricRelabelings) at the version of the
# copy, which is also the list of the live ServiceMonitor of site 1 on
# 2026-11-23. Site 1 replaces it with a copy that spares one series.
CHART_COPY_VERSION = "93.2.2"
CHART_CADVISOR = [
    {"sourceLabels": ["__name__"], "action": "drop",
     "regex": "container_cpu_(cfs_throttled_seconds_total|load_average_10s|system_seconds_total|user_seconds_total)"},
    {"sourceLabels": ["__name__"], "action": "drop",
     "regex": ("container_fs_(io_current|io_time_seconds_total|io_time_weighted_seconds_total|reads_merged_total"
               "|sector_reads_total|sector_writes_total|writes_merged_total)")},
    {"sourceLabels": ["__name__"], "action": "drop", "regex": "container_memory_mapped_file"},
    {"sourceLabels": ["__name__"], "action": "drop", "regex": "container_(file_descriptors|tasks_state|threads_max)"},
    {"sourceLabels": ["__name__", "scope"], "action": "drop", "regex": "container_memory_failures_total;hierarchy"},
    {"sourceLabels": ["__name__", "interface"], "action": "drop",
     "regex": "container_network_.*;(cali|cilium|cni|lxc|nodelocaldns|tunl).*"},
    {"sourceLabels": ["__name__"], "action": "drop", "regex": "container_spec.*"},
    {"sourceLabels": ["id", "pod"], "action": "drop", "regex": ".+;"},
]
SPARE = [
    {"sourceLabels": ["__name__", "id"], "regex": "container_memory_rss;/", "targetLabel": "__tmp_keep",
     "replacement": "true", "action": "replace"},
    {"sourceLabels": ["id", "pod", "__tmp_keep"], "action": "drop", "regex": ".+;;"},
    {"regex": "__tmp_keep", "action": "labeldrop"},
]
HELMRELEASE = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml"


def kubelet_operations(site):
    """The ops of the patch of kube-prometheus-stack of a site on the
    kubelet's ServiceMonitor, by the last part of their path."""
    text = (ROOT / f"kubernetes/clusters/{site}/apps/kustomization.yaml").read_text()
    found = {}
    for doc in yaml.safe_load_all(text):
        for patch in (doc or {}).get("patches", []):
            outer = yaml.safe_load(patch.get("patch", "")) or {}
            if outer.get("metadata", {}).get("name") != "kube-prometheus-stack":
                continue
            for inner in outer.get("spec", {}).get("patches", []):
                for op in yaml.safe_load(inner["patch"]) or []:
                    if op["path"].startswith("/spec/values/kubelet/serviceMonitor/"):
                        assert op["op"] == "add", op
                        found[op["path"].rsplit("/", 1)[1]] = op["value"]
    return found


def test_site_1_copies_the_cadvisor_list_of_the_chart_and_spares_one_series():
    assert kubelet_operations("wsl")["cAdvisorMetricRelabelings"] == CHART_CADVISOR[:-1] + SPARE
    # Site 2 keeps the chart's list.
    assert "cAdvisorMetricRelabelings" not in kubelet_operations("wsl-02")
    # The copy is of the major version of the chart that the HelmRelease
    # installs. At a new major version, compare the chart's list with the
    # copy, and change both here and in the patch of site 1.
    version = yaml.safe_load(HELMRELEASE.read_text())["spec"]["chart"]["spec"]["version"]
    assert version.split(".")[0] == CHART_COPY_VERSION.split(".")[0], version


# A sample of the cAdvisor endpoint of a kubelet: the series of the root
# cgroup with the labels that the live endpoint of site 1 gives it, a slice
# of the system, a container of a pod, and series that the chart drops by name.
CADVISOR = """\
# TYPE container_memory_rss gauge
container_memory_rss{container="",id="/",image="",name="",namespace="",pod=""} 3.0e+09
container_memory_rss{container="",id="/system.slice",image="",name="",namespace="",pod=""} 1.0e+08
container_memory_rss{container="app",id="/kubepods/burstable/pod1/c1",image="img",name="c1",namespace="ns",pod="p1"} 5.0e+07
# TYPE container_memory_working_set_bytes gauge
container_memory_working_set_bytes{container="",id="/",image="",name="",namespace="",pod=""} 3.5e+09
container_memory_working_set_bytes{container="app",id="/kubepods/burstable/pod1/c1",image="img",name="c1",namespace="ns",pod="p1"} 6.0e+07
# TYPE container_memory_cache gauge
container_memory_cache{container="",id="/",image="",name="",namespace="",pod=""} 5.0e+08
# TYPE container_memory_mapped_file gauge
container_memory_mapped_file{container="app",id="/kubepods/burstable/pod1/c1",image="img",name="c1",namespace="ns",pod="p1"} 1.0e+06
# TYPE container_spec_memory_limit_bytes gauge
container_spec_memory_limit_bytes{container="app",id="/kubepods/burstable/pod1/c1",image="img",name="c1",namespace="ns",pod="p1"} 0
"""

SCRIPT = """\
set -eu
httpd -p 127.0.0.1:8080 -h /t/srv
prometheus --config.file=/t/prometheus.yml --storage.tsdb.path=/prometheus/data \
  --web.listen-address=127.0.0.1:9090 >/prometheus/log 2>&1 &
for job in chart site1; do
  query="http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22$job%22%7D%3D%3D1"
  for i in $(seq 1 60); do
    case "$(wget -qO- "$query" 2>/dev/null || true)" in *'"value"'*) break ;; esac
    sleep 1
  done
done
sleep 3
wget -qO- 'http://127.0.0.1:9090/api/v1/query?query=%7B__name__%3D~%22container_.%2A%22%7D'
echo
"""


def snake(rule):
    keys = {"sourceLabels": "source_labels", "targetLabel": "target_label"}
    return {keys.get(key, key): value for key, value in rule.items()}


def test_prometheus_keeps_the_series_of_the_chart_and_the_resident_memory_of_the_root_cgroup():
    """The chart's list and the list of site 1, each on one scrape of the
    same sample, in Prometheus in a container with no network."""
    binary = docker()
    work = scratch()
    (work / "srv").mkdir()
    (work / "srv/metrics").write_text(CADVISOR)
    config = {"scrape_configs": [
        {"job_name": job, "scrape_interval": "1s", "fallback_scrape_protocol": "PrometheusText0.0.4",
         "static_configs": [{"targets": ["127.0.0.1:8080"]}],
         "metric_relabel_configs": [snake(rule) for rule in relabelings]}
        for job, relabelings in (("chart", CHART_CADVISOR), ("site1", kubelet_operations("wsl")["cAdvisorMetricRelabelings"]))
    ]}
    (work / "prometheus.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    subprocess.run(["chmod", "-R", "a+rX", str(work)], check=True)
    container = subprocess.run(
        [binary, "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
         "--mount", f"type=bind,src={work},dst=/t,readonly", PROMETHEUS, "-c", SCRIPT],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert container.returncode == 0, (container.stdout + container.stderr)[-8000:]
    result = json.loads(container.stdout.strip().splitlines()[-1])["data"]["result"]
    kept = {"chart": set(), "site1": set()}
    for series in result:
        labels = dict(series["metric"])
        job = labels.pop("job")
        labels.pop("instance")
        kept[job].add(tuple(sorted(labels.items())))
    root = (("__name__", "container_memory_rss"), ("container", ""), ("id", "/"), ("image", ""), ("name", ""),
            ("namespace", ""), ("pod", ""))
    # Prometheus stores no label with an empty value.
    root = tuple((k, v) for k, v in root if v)
    assert kept["chart"], result
    assert root not in kept["chart"]
    assert kept["site1"] == kept["chart"] | {root}, kept
    assert not any(k == "__tmp_keep" for series in kept["site1"] for k, _ in series)
