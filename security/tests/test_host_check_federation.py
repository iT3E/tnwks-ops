"""The host check of both sites reaches the rules of the registry on homelab-wsl-02.

homelab-wsl scrapes the textfile collector of both Windows hosts, and each distro
writes to the directory of its host. The rules of the registry run on homelab-wsl-02
only, so `peer-federate` of homelab-wsl-02 brings the two series of the source
(security/README.md, "What a source must do").
"""

from pathlib import Path

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


def test_peer_federate_of_site_2_brings_the_two_series_of_the_host_check():
    match = scrape_jobs_of_site_2()["peer-federate"]["params"]["match[]"]
    for series in ("homelab_security_check_result", "homelab_security_last_result_timestamp_seconds"):
        # source_site="": the series of homelab-wsl itself, never a copy that
        # homelab-wsl federated from this site.
        assert f'{series}{{source="host-check",source_site=""}}' in match


def test_site_1_scrapes_the_windows_exporter_of_both_hosts():
    """The textfile collector of each host is in the job windows-exporter of homelab-wsl."""
    release = yaml.safe_load(SHARED.read_text())
    jobs = {job["job_name"]: job for job in release["spec"]["values"]["prometheus"]["prometheusSpec"]["additionalScrapeConfigs"]}
    targets = [target for group in jobs["windows-exporter"]["static_configs"] for target in group["targets"]]
    assert targets == ["${WINDOWS_HOST_IP}:9182", "${PEER_WINDOWS_HOST_ADDR}:9182"]
    assert "windows-exporter" not in scrape_jobs_of_site_2(), "site 2 does not scrape the hosts a second time"
