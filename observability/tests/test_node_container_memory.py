"""The memory limits of the node containers that the capacity alerts state
(observability/rules.py, the group homelab.capacity).

A node container of Talos in Docker has a Docker memory limit, which no series
carries and which the kubelet does not see: it reports the memory of the whole
WSL VM. So each alert states its limit as a constant, and this test holds the
constant equal to the Ansible inventory that sets the limit
(infrastructure/ansible/inventory/group_vars/all/talos.yml), and checks that
each site scrapes the series the alert reads.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest
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
    # The rules give each role one limit, so a node with a size of its own
    # (memory_nodes, which moves a running node) must have that same limit.
    nodes = cluster.get("memory_nodes") or {}
    for node, size in nodes.items():
        role = re.fullmatch(r"(worker|controlplane)-\d+", node)
        assert role, node
        limit = rules.SITE1_WORKER_LIMIT_GIB if role[1] == "worker" else rules.SITE1_CONTROLPLANE_LIMIT_GIB
        assert gib(size) == limit, node


def test_the_limit_of_worker_1_of_site_2_is_the_one_of_the_inventory():
    cluster = talos_clusters()[CLUSTERS["homelab-wsl-02"]]
    node = rules.CAPACITY_NODE.removeprefix("homelab-wsl-02-")
    assert gib(cluster["memory_nodes"][node]) == rules.CAPACITY_NODE_LIMIT_GIB


def test_each_alert_reads_its_site_and_states_its_limit():
    alerts = {(rule["alert"], rule["labels"]["severity"]): rule for rule in rules.capacity_alerts()}
    assert len(alerts) == len(rules.capacity_alerts())
    for name, severity, role, share, limit, wait in [
            ("WorkerContainerMemoryNearLimit", "warning", "worker", 0.98, rules.SITE1_WORKER_LIMIT_GIB, "15m"),
            ("WorkerContainerMemoryNearLimit", "critical", "worker", 0.99, rules.SITE1_WORKER_LIMIT_GIB, "2m"),
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


def test_the_guard_reads_each_node_with_the_selector_of_the_resident_memory_alert():
    """The guard takes the nodes of site 1 in kube-state-metrics, one series
    each, unless the alert reads a root series of the same node: the selector
    of the alert, the roles of both of its forms in one match. A change of
    one selector and not of the other fails here."""
    alerts = rules.capacity_alerts()
    (alert,) = [r for r in alerts if r["alert"] == "NodeContainerRssNearLimit"]
    (guard,) = [r for r in alerts if r["alert"] == "NodeContainerRssMissing"]
    selector = re.compile(r'container_memory_rss\{[^}]*node=~"\.\+-(\(worker\|controlplane\)|worker|controlplane)-\[0-9\]\+"\}')
    alert_selectors = {match[0].replace(f"-{match[1]}-", "-ROLE-") for match in selector.finditer(alert["expr"])}
    guard_selectors = {match[0].replace(f"-{match[1]}-", "-ROLE-") for match in selector.finditer(guard["expr"])}
    assert len(alert_selectors) == 1 and alert_selectors == guard_selectors, (alert_selectors, guard_selectors)
    assert sorted(match[1] for match in selector.finditer(alert["expr"])) == ["controlplane", "worker"]
    assert [match[1] for match in selector.finditer(guard["expr"])] == ["(worker|controlplane)"]
    nodes = f'max by (k8s_cluster, node) (kube_node_info{{k8s_cluster="{rules.SITE1_CLUSTER}",job="kube-state-metrics"}})'
    assert guard["expr"].startswith(f"{nodes} unless on (k8s_cluster, node) max by (k8s_cluster, node) (container_memory_rss{{")
    assert guard["expr"].count("kube_node_info") == 1 and "count" not in guard["expr"]
    assert guard["for"] == rules.SITE1_RSS_MISSING_FOR
    assert guard["labels"] == {"severity": "warning"}


# The cAdvisor relabel list of kube-prometheus-stack (values.yaml,
# kubelet.serviceMonitor.cAdvisorMetricRelabelings), copied from 93.2.2,
# which is also the list of the live ServiceMonitor of site 1 on
# 2026-11-23. Site 1 replaces it with a copy that spares one series.
# test_the_copy_is_the_list_of_the_chart_that_the_helmrelease_installs
# compares it with the chart that the HelmRelease names, at each version.
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


HELM_CHART_LAYER = "application/vnd.cncf.helm.chart.content.v1.tar+gzip"


def fetch_oci_chart(release):
    """The chart archive of the version that the HelmRelease names, from its
    OCI HelmRepository, checked against the digest of its layer in the
    manifest of that version (as validate_grafana.py checks a chart against
    the digest of its index), so a bump of the chart by Renovate alone moves
    what this compares. The registry gives an anonymous token for a pull of
    a public chart."""
    spec = release["spec"]["chart"]["spec"]
    source = yaml.safe_load((ROOT / "kubernetes/flux/meta/helm" / f"{spec['sourceRef']['name']}.yaml").read_text())
    assert source["spec"].get("type") == "oci", source["spec"]
    registry, _, path = source["spec"]["url"].removeprefix("oci://").rstrip("/").partition("/")
    repository = f"{path}/{spec['chart']}"

    def get(url, **headers):
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            return response.read()

    token = json.loads(get(f"https://{registry}/token?scope=repository:{repository}:pull&service={registry}"))["token"]
    auth = {"Authorization": f"Bearer {token}"}
    manifest = json.loads(get(f"https://{registry}/v2/{repository}/manifests/{spec['version']}",
                              Accept="application/vnd.oci.image.manifest.v1+json", **auth))
    (layer,) = [entry for entry in manifest["layers"] if entry["mediaType"] == HELM_CHART_LAYER]
    data = get(f"https://{registry}/v2/{repository}/blobs/{layer['digest']}", **auth)
    assert "sha256:" + hashlib.sha256(data).hexdigest() == layer["digest"], f"{spec['chart']} {spec['version']} digest"
    return data


# A network error: no route, no name, a refused or reset connection, or a
# time out (a stall in a read raises TimeoutError, which is no URLError).
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)


def fetch_or_skip(release):
    """The chart of the HelmRelease. An answer of the registry that is an
    error (a version that does not exist, say) fails everywhere. A network
    error fails in CI and skips on a workstation with no network."""
    try:
        return fetch_oci_chart(release)
    except urllib.error.HTTPError as error:
        pytest.fail(f"the registry refused the chart: {error}")
    except NETWORK_ERRORS as error:
        if os.environ.get("CI"):
            pytest.fail(f"the chart could not be fetched: {error}")
        pytest.skip(f"no network for the chart: {error}")


@pytest.mark.parametrize("ci", ["1", None])
@pytest.mark.parametrize(("error", "outcome"), [
    (urllib.error.HTTPError("https://registry.invalid/v2", 404, "Not Found", {}, None), "fail"),
    (urllib.error.URLError("no route"), "skip"),
    (TimeoutError("The read operation timed out"), "skip"),
    (ConnectionResetError("reset"), "skip"),
])
def test_the_chart_fetch_fails_on_an_error_of_the_registry_and_skips_on_no_network_off_ci(monkeypatch, ci, error, outcome):
    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    if ci:
        monkeypatch.setenv("CI", ci)
    else:
        monkeypatch.delenv("CI", raising=False)
    expected = pytest.fail.Exception if ci or outcome == "fail" else pytest.skip.Exception
    # Catch both outcomes, so a skip where a failure is due fails here and
    # does not end the test as skipped.
    with pytest.raises((pytest.fail.Exception, pytest.skip.Exception)) as caught:
        fetch_or_skip(yaml.safe_load(HELMRELEASE.read_text()))
    assert caught.type is expected, (caught.type, expected)


def test_the_copy_is_the_list_of_the_chart_that_the_helmrelease_installs():
    """A bump of the chart with the same default list stays green; a bump
    that changes it fails until the copy (here and in the patch of site 1)
    follows. It needs the network: it fails in CI without it, and skips on
    a workstation with none."""
    release = yaml.safe_load(HELMRELEASE.read_text())
    data = fetch_or_skip(release)
    name = release["spec"]["chart"]["spec"]["chart"]
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        meta = yaml.safe_load(archive.extractfile(f"{name}/Chart.yaml"))
        values = yaml.safe_load(archive.extractfile(f"{name}/values.yaml"))
    assert meta["version"] == release["spec"]["chart"]["spec"]["version"], meta["version"]
    assert values["kubelet"]["serviceMonitor"]["cAdvisorMetricRelabelings"] == CHART_CADVISOR, (
        f"the cAdvisor relabel list of {name} {meta['version']} is not the copy: copy it to CHART_CADVISOR, and to the"
        " patch of site 1 (kubernetes/clusters/wsl/apps/kustomization.yaml) with the change that spares one series")


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
