"""The sources of homelab-wsl reach the rules of the registry on homelab-wsl-02.

The rules of the registry run on homelab-wsl-02 only, so `peer-federate` of
homelab-wsl-02 brings the two series of each source that runs on homelab-wsl
(security/README.md, "What a source must do"): the host check (homelab-wsl
scrapes the textfile collector of both Windows hosts, and each distro writes to
the directory of its host) and Trivy Operator. The other way, `peer-federate` of
homelab-wsl brings the accepted findings of `trivy` from the reader of the
encrypted list on homelab-wsl-02, the exception list of the Trivy rules there.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "kubernetes/clusters/wsl-02/apps/kustomization.yaml"
SHARED = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml"


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


@pytest.mark.parametrize("source", ["host-check", "trivy"])
def test_peer_federate_of_site_2_brings_the_two_series_of_each_source_of_site_1(source):
    match = scrape_jobs_of_site_2()["peer-federate"]["params"]["match[]"]
    for series in ("homelab_security_check_result", "homelab_security_last_result_timestamp_seconds"):
        # source_site="": the series of homelab-wsl itself, never a copy that
        # homelab-wsl federated from this site.
        assert f'{series}{{source="{source}",source_site=""}}' in match


def test_peer_federate_of_site_1_brings_the_accepted_findings_of_trivy():
    """Only the entries of `trivy`, and only the ones of homelab-wsl-02 itself."""
    match = scrape_jobs_of_site_1()["peer-federate"]["params"]["match[]"]
    accepted = [line for line in match if "homelab_security_accepted_until_timestamp_seconds" in line]
    assert accepted == ['homelab_security_accepted_until_timestamp_seconds{source="trivy",source_site=""}']


def test_site_1_scrapes_the_windows_exporter_of_both_hosts():
    """The textfile collector of each host is in the job windows-exporter of homelab-wsl."""
    jobs = scrape_jobs_of_site_1()
    targets = [target for group in jobs["windows-exporter"]["static_configs"] for target in group["targets"]]
    assert targets == ["${WINDOWS_HOST_IP}:9182", "${PEER_WINDOWS_HOST_ADDR}:9182"]
    assert "windows-exporter" not in scrape_jobs_of_site_2(), "site 2 does not scrape the hosts a second time"
