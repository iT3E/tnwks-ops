"""The results of the host check reach the rules of the registry on homelab-wsl-02
from both Windows hosts, with no wait on the cluster of homelab-wsl (Design Doc
0004, section 5.5).

homelab-wsl-02 scrapes the security series of the textfile collector of both
hosts itself (job windows-exporter-security), and each distro writes to the
directory of its host. homelab-wsl scrapes the same series in its job
windows-exporter with the label standby, and alerts on them only while
homelab-wsl-02 does not scrape their host: it federates the `up` of that job.
`peer-federate` of homelab-wsl-02 still brings the two series of Trivy Operator
of homelab-wsl, and `peer-federate` of homelab-wsl the accepted findings of
`trivy` from the reader of the encrypted list on homelab-wsl-02.

When homelab-wsl-02 can not scrape the site 1 host, SecurityHostUnreachable fires,
and two inhibit rules of Alertmanager hold the late results behind that site and
PeerSiteUnreachable of that site.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "kubernetes/clusters/wsl-02/apps/kustomization.yaml"
SHARED = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml"
ALERTMANAGER = ROOT / "kubernetes/apps/monitoring/alertmanager/app/config/alertmanager.yml"
PEER_RULES = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/peer-rules.yaml"
SETTINGS = {site: ROOT / f"kubernetes/clusters/{site}/cluster-settings.yaml" for site in ("wsl", "wsl-02")}



def load(name: str, path: Path):
    """A module by its path: observability/ and security/ both have a generate.py."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# rules.py imports its neighbours (inventory.py): last on the path, so no module of
# security/ resolves there.
sys.path.append(str(ROOT / "observability"))
generate = load("security_generate_for_scrape", ROOT / "security/generate.py")
rules = load("observability_rules_for_scrape", ROOT / "observability/rules.py")

JOB = "windows-exporter-security"
RESULTS = ("homelab_security_check_result", "homelab_security_last_result_timestamp_seconds")


def scrape_jobs_of_site_2() -> dict:
    """The scrape jobs that the overlay of homelab-wsl-02 gives its Prometheus."""
    overlay = yaml.safe_load(OVERLAY.read_text())
    for entry in overlay["patches"]:
        patch = yaml.safe_load(entry.get("patch", "")) or {}
        if not isinstance(patch, dict) or patch.get("metadata", {}).get("name") != "kube-prometheus-stack":
            continue
        for inner in patch["spec"]["patches"]:
            for op in yaml.safe_load(inner["patch"]):
                if op["path"] == "/spec/values/prometheus/prometheusSpec/additionalScrapeConfigs":
                    return {job["job_name"]: job for job in op["value"]}
    raise AssertionError("the overlay of homelab-wsl-02 has no scrape jobs for its Prometheus")


def scrape_jobs_of_site_1() -> dict:
    """The scrape jobs of the shared values, which homelab-wsl runs as they are."""
    release = yaml.safe_load(SHARED.read_text())
    return {job["job_name"]: job for job in release["spec"]["values"]["prometheus"]["prometheusSpec"]["additionalScrapeConfigs"]}


def settings(site: str) -> dict:
    return yaml.safe_load(SETTINGS[site].read_text())["data"]


def test_site_2_scrapes_the_security_series_of_both_hosts_and_no_other():
    job = scrape_jobs_of_site_2()[JOB]
    groups = job["static_configs"]
    assert [group["targets"] for group in groups] == [["${WINDOWS_HOST_IP}:9182"], ["${PEER_WINDOWS_HOST_ADDR}:9182"]]
    assert groups[0]["labels"] == {"instance": "${WINDOWS_HOST_NAME}", "k8s_cluster": "${CLUSTER_NAME}"}
    assert groups[1]["labels"] == {"instance": "${PEER_WINDOWS_HOST_NAME}", "k8s_cluster": "${PEER_CLUSTER_NAME}",
                                   "behind_site": "${PEER_CLUSTER_NAME}"}
    assert job["metric_relabel_configs"] == [
        {"source_labels": ["__name__"], "regex": "homelab_security_.+", "action": "keep"}]
    assert "honor_labels" not in job, "the target sets system of no series: the file does"
    assert "windows-exporter" not in scrape_jobs_of_site_2(), "site 2 does not scrape the other series of the hosts"


@pytest.mark.parametrize("name", ["observability/rules.py", "security/generate.py"])
def test_the_rules_name_the_scrape_job_of_site_2(name):
    value = rules.SECURITY_STANDBY_JOB if name.startswith("observability") else generate.HOST_SCRAPE_JOB
    assert value == JOB


def test_peer_federate_of_site_2_brings_trivy_and_not_the_host_check():
    """The host check comes from the scrape of site 2 itself: a federated copy too
    would give the rules each host series two times."""
    match = scrape_jobs_of_site_2()["peer-federate"]["params"]["match[]"]
    for series in RESULTS:
        # source_site="": the series of homelab-wsl itself, never a copy that
        # homelab-wsl federated from this site.
        assert f'{series}{{source="trivy",source_site=""}}' in match
    assert not [line for line in match if "host-check" in line]


def test_site_1_scrapes_both_hosts_and_marks_the_security_series_as_the_standby():
    job = scrape_jobs_of_site_1()["windows-exporter"]
    targets = [target for group in job["static_configs"] for target in group["targets"]]
    assert targets == ["${WINDOWS_HOST_IP}:9182", "${PEER_WINDOWS_HOST_ADDR}:9182"]
    assert job["metric_relabel_configs"] == [
        {"source_labels": ["__name__"], "regex": "homelab_security_.+", "target_label": "standby",
         "replacement": "true"}]


def test_peer_federate_of_site_1_brings_the_scrape_state_of_site_2_and_the_accepted_findings_of_trivy():
    match = scrape_jobs_of_site_1()["peer-federate"]["params"]["match[]"]
    assert f'up{{job="{JOB}",source_site=""}}' in match
    accepted = [line for line in match if "homelab_security_accepted_until_timestamp_seconds" in line]
    assert accepted == ['homelab_security_accepted_until_timestamp_seconds{source="trivy",source_site=""}']


def test_the_finding_alert_holds_a_standby_series_only_by_the_scrape_of_its_host_on_site_2():
    """Reviewer's note: the hold is the scrape of that host on site 2, not the answer
    of the Prometheus of site 2 (`up` of peer-federate)."""
    assert "standby" in [label.strip() for label in rules.SECURITY_FINDING_LABELS.split(",")]
    (alert,) = [rule for rule in rules.security_alerts() if rule["alert"] == "SecurityFindingNew"]
    assert f'unless on (k8s_cluster, standby) label_replace(up{{job="{JOB}",source_site!=""}} == 1,' in alert["expr"]
    assert 'job="peer-federate"' not in alert["expr"]


def test_the_systems_behind_site_1_are_its_host_its_distro_and_the_router():
    registry = generate.load()
    site_1 = settings("wsl")["CLUSTER_NAME"]
    assert settings("wsl-02")["PEER_CLUSTER_NAME"] == site_1
    assert generate.behind(registry) == {
        settings("wsl")["WINDOWS_HOST_NAME"]: site_1,
        settings("wsl")["WINDOWS_HOST_NAME"] + "-ubuntu": site_1,
        next(system["name"] for system in registry["systems"] if system["type"] == "router"): site_1}


def test_the_late_alert_of_each_system_behind_a_site_carries_the_site():
    registry = generate.load()
    expr = generate.late_expr(registry)
    sites = generate.behind(registry)
    for source, system, _, _ in generate.expected(registry):
        labelled = re.search(rf'label_replace\([^\n]*?system="{re.escape(system)}"[^\n]*?"behind_site", "([^"]+)"', expr)
        found = labelled.group(1) if labelled and system in sites else None
        assert found == sites.get(system), (source, system)


def test_the_host_alert_fires_on_the_targets_behind_another_site_after_5_minutes():
    rule = generate.host_unreachable_rule()
    assert rule["expr"] == f'up{{job="{JOB}",behind_site!=""}} == 0'
    assert rule["for"] == "5m"
    peer = yaml.safe_load(PEER_RULES.read_text())["spec"]["groups"][0]["rules"]
    (unreachable,) = [item for item in peer if item["alert"] == "PeerSiteUnreachable"]
    assert unreachable["for"] == "10m", "the host alert comes first, so it holds PeerSiteUnreachable from its start"


def test_the_host_alert_text_names_a_stopped_exporter_as_a_cause():
    """Reviewer's note: the exporter of the host can stop while the host and the link are up."""
    text = generate.host_unreachable_rule()["annotations"]["description"]
    assert "windows_exporter on that host stopped" in text
    assert "link between the sites" in text


def inhibit_rules() -> list:
    return yaml.safe_load(ALERTMANAGER.read_text())["inhibit_rules"]


def held(alert: dict, sources: list[dict]) -> bool:
    """Whether Alertmanager holds `alert` while each of `sources` fires: the matchers
    of an inhibit rule, and the same value (or no value on both) of each `equal` label."""
    def matches(labels, matchers):
        for matcher in matchers:
            name, value = re.fullmatch(r'(\w+) = "([^"]*)"', matcher).groups()
            if labels.get(name, "") != value:
                return False
        return True

    for rule in inhibit_rules():
        if not matches(alert, rule["target_matchers"]):
            continue
        for source in sources:
            if matches(source, rule["source_matchers"]) and all(
                    alert.get(name, "") == source.get(name, "") for name in rule["equal"]):
                return True
    return False


SITE_1, SITE_2 = "homelab-wsl", "homelab-wsl-02"
# The labels that the rules of site 2 give each alert.
HOST = {"alertname": "SecurityHostUnreachable", "severity": "warning", "job": JOB, "instance": "DESKTOP-HKE05KI",
        "k8s_cluster": SITE_1, "behind_site": SITE_1}
PEER = {"alertname": "PeerSiteUnreachable", "severity": "warning", "job": "peer-federate",
        "instance": "10.10.91.142:9090", "k8s_cluster": SITE_1, "source_site": SITE_1}


def late(system, site=None):
    return {"alertname": "SecurityResultLate", "severity": "warning", "source": "host-check", "system": system,
            **({"behind_site": site} if site else {})}


def test_the_host_alert_holds_the_late_results_behind_its_site_and_peer_site_unreachable():
    for alert in (late("DESKTOP-HKE05KI", SITE_1), late("DESKTOP-HKE05KI-ubuntu", SITE_1),
                  {**late("sce-rtr01", SITE_1), "source": "api-check"}, PEER):
        assert held(alert, [HOST]), alert


def test_the_host_alert_holds_no_late_result_of_another_system():
    for alert in (late("WSL-02"), late("WSL-02-ubuntu"), {**late("aws-prod"), "source": "prowler"}):
        assert not held(alert, [HOST]), alert


def test_peer_site_unreachable_alone_holds_no_late_result():
    """An outage of the cluster of site 1 alone: no host alert, so each late result shows."""
    assert not held(late("DESKTOP-HKE05KI", SITE_1), [PEER])
    assert not held(PEER, [late("DESKTOP-HKE05KI", SITE_1)])


def test_the_host_alert_is_not_held_by_the_rules_that_it_raises():
    assert not held(HOST, [PEER, late("DESKTOP-HKE05KI", SITE_1)])


def test_a_peer_site_unreachable_of_another_site_is_not_held():
    other = {**PEER, "k8s_cluster": SITE_2, "source_site": SITE_2}
    assert not held(other, [HOST])
