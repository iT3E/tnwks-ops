"""Typed, deterministic Grafana v2 resources. No Grafana API writes."""

from __future__ import annotations

import argparse
import difflib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from grafana_foundation_sdk.builders import dashboardv2 as dashboard
from grafana_foundation_sdk.builders import common as common_builder
from grafana_foundation_sdk.builders import prometheus, stat, table, text, timeseries
from grafana_foundation_sdk.cog.encoder import JSONEncoder
from grafana_foundation_sdk.models import common
from grafana_foundation_sdk.models import prometheus as prom_model
from grafana_foundation_sdk.models import dashboardv2 as model
from conventions import OBSERVATION_BUDGET_SECONDS, dashboard_link, runbook_path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "kubernetes/apps/monitoring/grafana/dashboards"
CLUSTERS = ("homelab-wsl", "homelab-wsl-02")
SCOPE = 'k8s_cluster=~"$cluster"'
ALERT_SCOPE = 'cluster=~"$cluster"'
# Each site's collector and rules report on both sites, so the global view returns
# these once per observer. max keeps the worst state, and Unknown (0) yields to a
# real answer. Keys are the series identity without the observing Prometheus.
IDENTITY = {
    "homelab:service_state": ("app",),
    "homelab:workload_state": ("app",),
    "homelab:recovery_state": ("app",),
    "homelab:network_path_state": ("hop",),
    "homelab:ingress_state": ("name",),
    "homelab:ingress_cert_remaining_seconds": ("name",),
    "homelab:shared_vip_route": ("vip",),
    "homelab:database_backup_state": ("namespace", "db_cluster"),
    "homelab:app_rpo_state": ("app",),
    "homelab:other_database_state": ("workload", "engine"),
    "homelab:app_rto_state": ("app",),
    "homelab:app_backup_age_seconds": ("app",),
    "homelab_expected_database_info": ("namespace", "db_cluster", "app"),
    "homelab_expected_workload_info": ("app", "tier", "kind", "workload"),
}
# Table columns that describe the scrape, not the thing being reported.
SCRAPE_COLUMNS = ("__name__", "Time", "__replica__", "alertstate", "cluster", "container", "endpoint",
                  "instance", "job", "kind", "namespace", "observer_cluster", "pod", "prometheus",
                  "service")
NO_ALERTS = "None"
# An event list over the selected range: empty is the normal state. Whether the
# source is reporting shows on the Observability board, not here.
NO_EVENTS = "None in range"
# Entity boards (pinned upstream, chart mixins and the kept classic boards),
# opened on the clicked series: domain -> entity.
NGINX = ("k8s-nginx-ingress-prometheus-ng", {"ingress": "ingress"})
ROUTER = ("gxf3edrffkw", {"node": "routerboard_name"})
NODE = ("200ac8fdbfbb74b39aff88118e4d1c2c", {"cluster": "k8s_cluster", "node": "kubernetes_node"})
WINDOWS = ("IV0hu1m7z", {"server": "instance"})
ACCESS_POINT = ("home-wifi", {"ap": "name"})
# The site 2 host, a UniFi Wi-Fi client (its Windows computer name, as in the
# Ansible inventory). Household clients are never named on the boards.
SITE2_WIFI_CLIENT = "WSL-02"


def alerts(state: str) -> str:
    """Real alerts by the site that raised them; heartbeats carry severity none."""
    selector = f'ALERTS{{{ALERT_SCOPE},alertstate="{state}",severity!="none"}}'
    return ('label_replace(max by (cluster, alertname, severity) '
            f'({selector}), "k8s_cluster", "$1", "cluster", "(.+)")')
STATE = {
    "0": model.ValueMappingResult(text="Unknown", color="#6b6b6b"),
    "1": model.ValueMappingResult(text="Healthy", color="#1a7f37"),
    "2": model.ValueMappingResult(text="Degraded", color="#e5ac0e"),
    "3": model.ValueMappingResult(text="Failed", color="#c4302b"),
    "4": model.ValueMappingResult(text="Standby", color="#2f6bd0"),
    "5": model.ValueMappingResult(text="Not applicable", color="#6b6b6b"),
    "6": model.ValueMappingResult(text="Maintenance", color="#8a4fb5"),
}


@dataclass(frozen=True)
class Panel:
    title: str
    expr: str
    unit: str
    description: str
    kind: str = "timeseries"
    # A board UID (keeps the selected sites), or (UID, {variable: label}) to open
    # an entity board on the clicked series or row.
    link: str | tuple[str, dict[str, str]] | None = None
    legend: str = "{{k8s_cluster}} {{app}} {{instance}}"
    empty: str = "Unknown — missing evidence"


@dataclass(frozen=True)
class Board:
    uid: str
    title: str
    domain: str
    level: str
    question: str
    tabs: dict[str, list[Panel]]
    folder: str
    kiosk: bool = False


def p(title: str, expr: str, unit: str, explanation: str, kind="timeseries", link=None,
      empty="Unknown — missing evidence"):
    return Panel(title, expr, unit, explanation, kind, link, empty=empty)


def selector(name: str, extra: str = "") -> str:
    if name.startswith(("unpoller_", "cnpg_")):
        extra = 'source_site=""' + ("," + extra if extra else "")
    return f"{name}{{{SCOPE}{',' + extra if extra else ''}}}"


def metric(name: str, extra: str = "") -> str:
    keys = IDENTITY.get(name)
    if keys is None:
        return selector(name, extra)
    return f"max by (k8s_cluster, {', '.join(keys)}) ({selector(name, extra)})"


def since(name: str, by: str) -> str:
    """Events kept as series whose value is the event time (from the API, not the
    scrape): the latest per label set in the selected range, in ms for Grafana."""
    return (f'max by (k8s_cluster, {by}) (max_over_time({selector(name)}[$__range])) * 1000 > $__from')


def changes(name: str, by: str) -> str:
    """Sampled state changes in the selected range, rows with none dropped."""
    return f'max by (k8s_cluster, {by}) (changes({selector(name)}[$__range])) > 0'


LONG_WINDOW_SECONDS = 28 * 86400


def slo_indicators() -> list[tuple[str, str]]:
    """(name, bool_gauge selector) for each Pyrra SLO, from the deployed definitions."""
    docs = yaml.safe_load_all((ROOT / "kubernetes/apps/monitoring/pyrra/app/slos.yaml").read_text())
    return [(doc["metadata"]["name"], doc["spec"]["indicator"]["bool_gauge"]["metric"])
            for doc in docs if doc and doc.get("kind") == "ServiceLevelObjective"]


def per_slo(template: str) -> str:
    """One series per site and SLO: the template on its selector scoped to $cluster, labelled slo."""
    parts = []
    for name, metric_selector in slo_indicators():
        scoped = metric_selector.replace("{", "{" + SCOPE + ",", 1)
        parts.append(f'label_replace({template.format(sel=scoped)}, "slo", "{name}", "", "")')
    return " or ".join(parts)


def coverage() -> list[Panel]:
    expected = f'count(max by (k8s_cluster) ({selector("homelab_expected_site_info")}))'
    reporting = f'count(max by (k8s_cluster) ({selector("homelab:site_reporting")}) == 1) or vector(0)'
    return [
        p("Expected sites", expected,
          "short", "Declared inventory survives the disappearance of a source. This is expected scope, not health.", "stat"),
        p("Reporting sites", reporting, "short",
          "Distinct selected sites with fresh, successful evidence. Duplicate observations count once. Compare with Expected sites; partial results never certify fleet health.", "stat"),
        p("Site evidence", f'max by (k8s_cluster) ({metric("homelab:site_state")})', "coverage",
          "Measured site evidence, including explicit Unknown when an expected site stops reporting. Independent site probes distinguish unreachable from unobserved.", "table", "tnwks-sites"),
        p("Evidence age", f'time() - {metric("homelab_observer_last_success_timestamp_seconds")}', "s",
          "Age of the last successful Kubernetes inventory observation. A recent Prometheus scrape alone does not prove the collector is seeing its upstream.", "table", "tnwks-observability"),
    ]


def catalogue() -> list[Board]:
    boards = [
        Board("home", "Homelab", "overview", "l1",
              "What is affected, which evidence is available, and where should I look next?", {
            "Now": coverage() + [
                p("Workloads needing attention", f'sort_desc({metric("homelab:workload_state")} != 4 != 1)', "state",
                  "Apps not fully ready, from replica counts. Empty means every app is ready or a deliberate standby. This is workload evidence, not an end-to-end path check.",
                  "table", "tnwks-sites", empty="All ready or standby"),
                p("Current firing alerts", alerts("firing"), "short",
                  "Current Prometheus rule state, excluding heartbeats; this is not a historical Alertmanager event list and does not show silence/inhibition state.",
                  "table", "tnwks-alerts", empty=NO_ALERTS),
                p("Recovery evidence", metric("homelab:recovery_state"), "state",
                  "Per-app prerequisites. Unknown fencing or untested recovery blocks a positive readiness conclusion. A dashboard never authorizes promotion.", "table", "tnwks-data"),
                p("Archive reachable", metric("homelab:archive_state"), "state",
                  "The archive is hosted only on WSL-02. Its failure does not remove local 15-day metrics; older history becomes unavailable.", "stat", "tnwks-observability"),
            ],
            "Change and history": [
                p("Observed state changes", f'max by (k8s_cluster, app) (changes({selector("homelab:service_state")}[1h]))', "short",
                  "Sampled transitions in observed service state. This is supplementary evidence and can miss transitions during a monitoring gap."),
                p("Latest workload rollout", f'time() - {metric("homelab_workload_transition_timestamp_seconds")}', "s",
                  "Time since a workload Ready transition reported by Kubernetes. This is not necessarily its deployment or git revision time.", "table"),
                p("Available local history", metric("homelab:local_history_seconds"), "s",
                  "Observed earliest retained sample, not configured retention. New persistent storage accumulates history gradually.", "table"),
            ],
        }, "Overview"),
        Board("tnwks-sites", "Sites & Failover", "sites", "l2",
              "Which site serves each workload, and what recovery evidence is missing?", {
            "Sites": coverage() + [
                p("Ready nodes", f'sum by (k8s_cluster) ({metric("kube_node_status_condition", "condition=\"Ready\",status=\"true\"")})', "short",
                  "Ready nodes per Kubernetes cluster. These nested Talos nodes share the Windows/WSL host's physical CPU, RAM and disk."),
                p("Cross-site observation", metric("homelab_peer_probe_success"), "bool",
                  "Fresh bounded probes from one site to the other. Observer identity is retained; this is connectivity evidence, not the remote site's internal condition.", "table"),
            ],
            "Placement": [
                p("Workload state", metric("homelab:workload_state"), "state",
                  "Per app and site, from replica counts: ready, degraded, failed, or a deliberate standby at zero replicas. A site that stops reporting shows Unknown.", "table"),
                p("Declared placement", metric("homelab_expected_workload_info"), "short",
                  "Expected app/site/tier inventory independent of discovery. Standbys and hardware-pinned applications remain visible.", "table"),
                p("Desired replicas", metric("homelab_workload_desired_replicas"), "short",
                  "Effective workload desired replicas from the local Kubernetes API, separate from git's default placement.", "table"),
                p("Ready replicas", metric("homelab_workload_ready_replicas"), "short",
                  "Observed readiness. Ready replicas do not prove the global ingress is serving this site.", "table"),
                p("Runtime overrides present", metric("homelab_site_override_present"), "bool",
                  "Presence of flux-system/site-overrides. Raw contents and secret-like values are never exposed.", "table"),
                p("Override creation age", f'time() - {metric("homelab_site_override_created_timestamp_seconds")}', "s",
                  "Age of creation, not last modification. Consult structured operation events for when a particular override changed.", "table",
                  empty="No overrides"),
            ],
            "Recovery": [
                p("Readiness evidence", metric("homelab:recovery_state"), "state",
                  "Unknown is retained whenever fencing, backup or restore evidence is absent. This is not a safe-to-promote guarantee.", "table"),
                p("Fencing evidence age", f'time() - {metric("homelab_fence_last_verified_timestamp_seconds")}', "s",
                  "Present only for verified fence evidence. DNS ownership alone cannot establish that the former primary is unable to write.", "table"),
                p("Fence job age", f'time() - {metric("homelab_fence_job_last_success_timestamp_seconds")}', "s",
                  "Last successful fleet fence Job. It may succeed with unreadable leases, so this never establishes per-app fencing.", "table"),
                p("Lease authority reachable", metric("homelab_lease_authority_reachable"), "bool",
                  "The current TXT lookup returned a recognized site or none. Reachability does not prove a former primary was fenced.", "table"),
                p("Lease blocks this site", metric("homelab_site_lease_blocks_local"), "bool",
                  "One means the lease prohibits this site from serving the app. Zero alone cannot authorize promotion.", "table"),
                p("Last restore evidence", f'time() - {metric("homelab_restore_last_success_timestamp_seconds")}', "s",
                  "Per-app restore evidence, not one successful fleet CronJob. Missing per-app results remain Unknown.", "table"),
            ],
            "History": [
                p("Serving-state changes", changes("homelab:service_state", "app"), "short",
                  "How often each app's observed state at a site changed in the range: a failover shows as a change on both sites. Sampled at each rule evaluation, so a blip shorter than that can be missed.",
                  "table", empty=NO_EVENTS),
                p("Shared VIP route changes", changes("homelab:shared_vip_route", "vip"), "short",
                  "How often the router's inferred route for each shared VIP to each site changed in the range, from its BGP sessions and preferences. Sampled at each rule evaluation.",
                  "table", empty=NO_EVENTS),
                p("Workload availability transitions", since("homelab_workload_transition_timestamp_seconds", "app, workload"), "dateTimeAsIso",
                  "When each tracked workload's Available condition last changed in the range, from Kubernetes: a scale to or from zero, or a rollout that lost availability.",
                  "table", empty=NO_EVENTS),
            ],
        }, "Sites & Failover"),
        Board("tnwks-data", "Databases & Backups", "data", "l2",
              "Are databases healthy, and can each application's data actually be recovered?", {
            "Databases": [
                p("Expected databases", metric("homelab_expected_database_info"), "short",
                  "Expected database inventory by site/namespace/db_cluster. The Kubernetes site label is never overloaded with the CNPG name.", "table"),
                p("CNPG role: in recovery", metric("homelab:database_in_recovery"), "bool",
                  "PostgreSQL's observed recovery state. Zero means primary role, not fencing proof. Same-name databases at two sites stay distinct.", "table"),
                p("Connections", f'sum by (k8s_cluster, namespace, db_cluster) ({metric("homelab:database_connections")})', "short",
                  "Observed database connections; compare against the configured limit and wait/transaction evidence."),
                p("Replay timestamp lag", metric("homelab:database_replay_lag_seconds"), "s",
                  "Replay timestamp lag can grow on idle databases. It is not by itself an RPO or a measure of unarchived writes."),
            ],
            "Other databases": [
                p("Database state", metric("homelab:other_database_state"), "state",
                  "Databases outside CloudNativePG (MongoDB for UniFi, Immich's Postgres, Plausible's ClickHouse, Valkey), which have no exporter: Healthy when every desired replica of the workload is ready, Standby at zero replicas, Unknown when the site doesn't report it. Ready means the pod passes its readiness checks; ClickHouse has none, so the Plausible service probe, which needs ClickHouse to answer, is its query evidence.",
                  "table"),
                p("Database container restarts", f'sum by (k8s_cluster, pod, container) (increase({selector("kube_pod_container_status_restarts_total", 'container=~"mongo|clickhouse"')}[1h]) or increase({selector("kube_pod_container_status_restarts_total", 'pod=~"immich-postgres-[0-9]+|valkey-.+|immich-valkey-.+"')}[1h]))', "short",
                  "Restarts in the last hour of the database containers, from kube-state-metrics. A crash loop can pass readiness between restarts."),
            ],
            "Backups": [
                p("Base backup age", metric("homelab:database_backup_age_seconds"), "s",
                  "Barman plugin's last successful base backup per database. The warning threshold is 30h; a missing metric is separately reported as missing coverage.", "table"),
                p("Backup coverage", metric("homelab:database_backup_state"), "state",
                  "Expected database left join observed Barman evidence. Missing series cannot be hidden by another database's fresh backup.", "table"),
                p("WAL failures", f'increase({metric("cnpg_pg_stat_archiver_failed_count")}[1h])', "short",
                  "Failed WAL archiving attempts in the selected window. Idle WAL age alone is not a failure."),
                p("Weekly PVC drill age", f'time() - {metric("kube_cronjob_status_last_successful_time", "namespace=\"kopiur-system\",cronjob=\"standby-restore\"")}', "s",
                  "The existing weekly job covers its configured PVC entries only. This does not certify every app or a database restore.", "table"),
            ],
            "Objectives": [
                p("Recovery point by app", metric("homelab:app_rpo_state"), "state",
                  "Per app and site: is its oldest newest-backup, over every scheduled PVC and every primary database, within its recovery point objective (inventory.py OBJECTIVES, provisionally 30 h)? Unknown when a piece has no backup evidence. A site where nothing is scheduled, like a standby, has no row.",
                  "table"),
                p("Backup age by app", metric("homelab:app_backup_age_seconds"), "s",
                  "The oldest piece's backup age per app and site: the data an unplanned restore would lose, at most.",
                  "table"),
                p("Restore time by app", metric("homelab:app_rto_state"), "state",
                  "Per warm app with PVCs: did its last standby restore drill write the data back within the restore time objective (provisionally 1 h)? Unknown until a drill reports how long it took. This is the data restore, not the whole failover.",
                  "table"),
                p("Last drill duration", f'max by (k8s_cluster, app) ({selector("homelab_restore_last_duration_seconds")})', "s",
                  "From each Restore's creation to its Ready condition, the slowest PVC of the app's last successful drill.",
                  "table"),
                p("PVCs without a backup schedule", f'max by (app, pvc) ({selector("homelab_app_backup_scheduled")}) == 0', "short",
                  "PVCs the failover catalogue moves that no site has a running backup schedule for: a suspended schedule, or no SnapshotPolicy covering the PVC. Their recovery point is unbounded.",
                  "table", empty="Every PVC is scheduled on a site"),
            ],
            "Storage": [
                p("Windows disk free", metric("windows_logical_disk_free_bytes", 'volume=~"C:|E:"'), "bytes",
                  "Physical Windows free capacity. WSL's virtual disk maximum is not an independent physical capacity budget.", link=WINDOWS),
            ],
        }, "Data"),
        Board("tnwks-network", "Network & Routing", "network", "l2",
              "Where does the path from the router through FRR/Cilium to a service fail?", {
            "Paths": [
                p("Site path probes", metric("homelab_peer_probe_success"), "bool",
                  "Cross-site probe success from each observer. Read with transport and endpoint evidence; a BGP Established session alone is insufficient.", "table"),
                # The router, seen by homelab-wsl's mktxp: not scoped to $cluster.
                p("Site tunnel traffic", 'sum by (interface) (rate(mktxp_peer_rx_total{interface=~"wg-site[0-9]+"}[5m]))', "Bps",
                  "Received on the router's WireGuard interface for each site's distro. Missing series mean the router isn't observed, not an idle tunnel."),
                p("Router interface errors", f'sum by (k8s_cluster, routerboard_name, name) (rate({metric("mktxp_interface_rx_error_total")}[5m]))', "ops",
                  "Router receive errors by interface. Telemetry enables are integrated after the network migration release.", link=ROUTER),
            ],
            "BGP and mesh": [
                p("Path by hop", metric("homelab:network_path_state"), "state",
                  "Per site: the WireGuard tunnel to the router (handshake within 180 s), the router's BGP session with the distro's FRR, the Cilium agents' sessions with the distro, and the mesh (the site's agents and its KVStoreMesh both have the other cluster ready). Unknown when the router or the agents aren't observed.", "table"),
                p("Shared VIP routes", metric("homelab:shared_vip_route"), "bool",
                  "Which site the router sends each shared VIP to (1): the preferred site while its BGP session is up, else the other. Inferred from the router's live sessions and the declared local preference, not read from its route table. A client behind a site (its pods, the WSL host) reaches that site's own ingress either way.", "table"),
                p("Routes received per site", 'max by (k8s_cluster) (homelab:router_bgp_prefixes{k8s_cluster=~"$cluster"})', "short",
                  "Prefixes the router accepted from each site's FRR (its node, pod and shared-VIP routes). Zero with an established session means the site announces nothing.", "stat"),
                p("Cilium drops", f'sum by (k8s_cluster, reason) (rate({metric("cilium_drop_count_total")}[5m]))', "pps",
                  "Agent drop counters, with bounded labels. Cilium metrics activation belongs to the post-network-release integration."),
                p("Mesh connectivity", metric("cilium_clustermesh_remote_cluster_readiness_status"), "bool",
                  "Each Cilium agent's readiness for the other cluster, through its own site's KVStoreMesh cache.", "table"),
                p("KVStoreMesh remote cluster", f'max by (k8s_cluster, target_cluster) ({selector("cilium_kvstoremesh_remote_cluster_readiness_status")})', "bool",
                  "Whether the site's clustermesh-apiserver (KVStoreMesh) has the other cluster's state synchronized. While it's down, agents keep serving the last cached state.", "table"),
                p("Mesh sync errors", f'sum by (k8s_cluster, scope) (rate({selector("cilium_kvstoremesh_kvstore_sync_errors_total")}[$__rate_interval]))', "ops",
                  "KVStoreMesh errors writing the other cluster's nodes, services and identities into the local cache. Any is worth a look; persistent ones leave stale cross-site endpoints."),
                p("Mesh remote failures", f'max by (k8s_cluster, target_cluster) ({selector("cilium_kvstoremesh_remote_cluster_failures")})', "short",
                  "KVStoreMesh connection failures to the other cluster since its last restart."),
            ],
            "History": [
                p("Router BGP session resets", f'max by (k8s_cluster, routerboard_name, name) (resets({selector("mktxp_bgp_uptime")}[$__range])) > 0', "short",
                  "Times each of the router's BGP sessions restarted in the range: its uptime went back. A session that dropped and came back between scrapes still counts. Only site 1 observes the router.",
                  "table", ROUTER, empty=NO_EVENTS),
                p("Cilium BGP session changes", changes("cilium_bgp_control_plane_session_state", "node"), "short",
                  "State changes of each node's Cilium BGP session with its distro's FRR in the range. Sampled at each scrape, so a shorter flap can be missed.",
                  "table", empty=NO_EVENTS),
                p("Path hop changes", changes("homelab:network_path_state", "hop"), "short",
                  "State changes of each path hop (tunnel, router BGP, agents, mesh) per site in the range, sampled at each rule evaluation.",
                  "table", empty=NO_EVENTS),
            ],
        }, "Network"),
        Board("tnwks-internet", "Internet", "network", "l2",
              "Is the Internet path usable, and is loss/latency or saturation the issue?", {
            "Experience": [
                p("Internet probe", metric("homelab:internet_state"), "state",
                  "Observed Internet probe state with freshness/coverage. A working exporter is insufficient.", "stat"),
                p("WAN p90 latency", metric("smokeping:rtt_p90:rate1m"), "s",
                  "p90 over the probe histogram; compare with baseline/threshold and packet loss."),
                p("WAN latency threshold", metric("smokeping:latency_threshold:1h"), "s",
                  "The actual recording rule used by the WAN alert, not a separately maintained display threshold."),
                p("Packet loss", metric("smokeping:loss_ratio:rate1m"), "percentunit",
                  "Probe packet loss; distinguish a missing series from zero measured loss."),
            ],
            "Traffic": [
                p("Download", metric("wan:receive_bits:rate1m"), "bps",
                  "Measured WAN receive throughput. Current traffic is not the available speed of the connection."),
                p("Upload", metric("wan:transmit_bits:rate1m"), "bps",
                  "Measured WAN transmit throughput. Detailed traffic remains available at its stable dashboard UID.", link="wan-traffic"),
                p("ISP radio collector", metric("unms_collector_success"), "bool",
                  "Whether the UISP exporter reached its controller. This is collector evidence, not the radio link's own health."),
            ],
        }, "Network"),
        Board("tnwks-wifi", "Wi-Fi & LAN", "network", "l2",
              "Is client experience failing at RF, the AP uplink, DHCP/DNS, or site 2's Wi-Fi path?", {
            "Access points": [
                p("AP uptime", f'max by (k8s_cluster, name) ({metric("unpoller_device_uptime_seconds")})', "s",
                  "AP inventory/uptime. Device-only identifiers; household client names/MACs are excluded.", "table", ACCESS_POINT),
                p("AP uplink", f'max by (k8s_cluster, name) ({metric("unpoller_device_uplink_speed_mbps")})', "Mbits",
                  "Negotiated uplink speed. A slow or errored uplink can masquerade as an RF problem.", "table", ACCESS_POINT),
                p("Wi-Fi collector failures", f'sum by (k8s_cluster) (increase({metric("unpoller_prometheus_refresh_failures_total")}[15m]))', "short",
                  "Collector-specific failure evidence; HTTP scrape success does not prove the UniFi API refresh succeeded."),
            ],
            "Site dependency": [
                p("Peer path success", metric("homelab_peer_probe_success"), "bool",
                  "Correlate site 2 connectivity loss with AP/uplink and WireGuard/BGP observations. This does not identify individual household clients."),
                p("Peer path duration", metric("homelab_peer_probe_duration_seconds"), "s",
                  "Bounded transport probe duration; one sample does not measure line-rate Wi-Fi performance."),
                # Measured 2026-11-07: site 1 on its wired NIC at 1 Gbit/s, site 2
                # only on Wi-Fi (300 Mbit/s link rate).
                p("Site host uplinks", f'label_replace(max by (k8s_cluster, instance, nic) ({selector("windows_net_current_bandwidth_bytes")} > 0) * 8, "host", "$1", "instance", "(.+)")', "bps",
                  "Each site's Windows host NICs with a link, at their negotiated rate (windows_exporter). Site 2's host reaches the router over Wi-Fi only, so its link rate changes with the radio.",
                  "table"),
                p("Site host uplink traffic", f'sum by (k8s_cluster, instance, nic) (rate({selector("windows_net_bytes_total")}[$__rate_interval])) > 0', "Bps",
                  "Traffic through each host NIC in use: everything the site sends or receives, the site tunnel included.", link=WINDOWS),
                p("Site 2 Wi-Fi signal", f'max by (ap_name, channel) ({selector("unpoller_client_radio_signal_db", f'name="{SITE2_WIFI_CLIENT}"')})', "dBm",
                  "The site 2 host's signal at its access point, as UniFi reports it (either site's poller). A change of AP or channel starts a new line."),
                p("Site 2 Wi-Fi association", f'max by (ap_name) ({selector("unpoller_client_uptime_seconds", f'name="{SITE2_WIFI_CLIENT}"')})', "s",
                  "How long the site 2 host has been associated. A drop to zero is a reconnect or roam, which interrupts the site's tunnel and BGP sessions.",
                  "table"),
            ],
        }, "Network"),
        Board("tnwks-kubernetes", "Kubernetes & GitOps", "kubernetes", "l2",
              "Is desired state reconciling, and which resource or controller is blocking it?", {
            "Reconciliation": [
                p("Flux resource readiness", metric("homelab_flux_resource_ready"), "bool",
                  "Current Ready conditions read from the API with revision/suspension shown separately. A live controller process can still fail reconciliation.", "table"),
                p("Suspended resources", metric("homelab_flux_resource_suspended"), "bool",
                  "Declared suspension; do not report deliberate suspension as a controller crash.", "table"),
                p("Reconcile failures", f'sum by (k8s_cluster, controller) (rate({metric("controller_runtime_reconcile_total", "result=\"error\"")}[5m]))', "ops",
                  "Controller process metrics supplement the CR Ready state; controller PodMonitor access needs explicit network-policy integration."),
            ],
            "Workloads": [
                p("Unready workloads", " - ".join(
                    f'sum by (k8s_cluster, app) (max by (k8s_cluster, app, kind, workload) ({selector(name)}))'
                    for name in ("homelab_workload_desired_replicas", "homelab_workload_ready_replicas")), "short",
                  "Effective desired minus Ready per tracked app/site. A zero-replica standby is not a failed application."),
                p("Node CPU utilization", f'1 - avg by (k8s_cluster, instance, kubernetes_node) (rate({metric("node_cpu_seconds_total", "mode=\"idle\"")}[$__rate_interval]))', "percentunit",
                  "CPU busy fraction per nested node view. Do not sum shared WSL kernel capacity as independent physical nodes.", link=NODE),
                p("Node memory available", metric("node_memory_MemAvailable_bytes"), "bytes",
                  "Shared WSL kernel memory view on nested Talos nodes. Cross-check Windows physical available memory.", link=NODE),
            ],
            # Each site's Prometheus keeps these for 15 days and the archive for 35,
            # so the history reads locally on either site.
            "History": [
                p("Source revisions fetched", since("homelab_flux_source_revision_timestamp_seconds", "resource, revision"), "dateTimeAsIso",
                  "Each Git or OCI revision Flux fetched in the range and when, from the source's artifact. Fetched isn't applied: the Ready transitions of the Kustomizations that use it show the apply.",
                  "table", empty=NO_EVENTS),
                p("Helm releases deployed", since("homelab_flux_release_deployed_timestamp_seconds", "resource, chart_version, app_version, release_status"), "dateTimeAsIso",
                  "The latest install or upgrade of each HelmRelease in the range, from its release history, with the chart and app version and whether it deployed or failed. An upgrade that changed only values keeps its version.",
                  "table", empty=NO_EVENTS),
                p("Flux Ready transitions",
                  'label_replace(' + since("homelab_flux_ready_transition_timestamp_seconds", "kind, resource, ready")
                  + ', "flux_kind", "$1", "kind", "(.+)")', "dateTimeAsIso",
                  "When each Flux resource's Ready condition last changed in the range, per resulting state, from the API. Only the latest change per state is kept: a resource that flapped shows its last False and its last True.",
                  "table", empty=NO_EVENTS),
                p("Node versions", f'min by (k8s_cluster, node, os_image, kubelet_version, kernel_version) (min_over_time(timestamp({selector("kube_node_info")})[$__range:5m])) * 1000', "dateTimeAsIso",
                  "When each node's Talos, kubelet and kernel versions were first seen in the range, at 5-minute resolution. Two rows for one node mean it was upgraded, and the later row is when.",
                  "table"),
            ],
        }, "Kubernetes"),
        Board("tnwks-apps", "Apps & Ingress", "apps", "l2",
              "Can users reach a usable application, and where is the request path failing?", {
            "User paths": [
                p("Application probe state", metric("homelab:service_state"), "state",
                  "Content probe to the in-cluster Service on its active site. This bypasses client DNS, ingress and sign-in; those paths need separate evidence.", "table"),
                p("HTTP requests", f'sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests")}[$__rate_interval]))', "reqps",
                  "Rate counters before aggregation. Use the same scope when comparing the error numerator and total denominator.", link=NGINX),
                p("HTTP 5xx fraction", f'sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests", "status=~\"5..\"")}[$__rate_interval])) / sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests")}[$__rate_interval]))', "percentunit",
                  "Weighted errors/requests. No traffic is not a perfect availability measurement; independent synthetic evidence remains necessary.", link=NGINX),
                p("HTTP p95 duration", f'histogram_quantile(0.95, sum by (k8s_cluster, ingress, le) (rate({metric("nginx_ingress_controller_request_duration_seconds_bucket")}[$__rate_interval])))', "s",
                  "Buckets combined before calculating the percentile. Never average per-pod percentiles.", link=NGINX),
            ],
            "Shared-VIP names": [
                p("Name state by site", metric("homelab:ingress_state"), "state",
                  "Each site's collector uses every shared-VIP name as a client would: the router's DNS answer must be the name's VIP, TLS must verify, and that site's ingress must route the host (any answer but a 404 or 5xx). A pod reaches its own site's ingress for either VIP, so this is each site's readiness to serve the name.", "table"),
                p("Served by", f'max by (k8s_cluster, name, served_cluster) ({selector("homelab_ingress_served_by_info")})', "short",
                  "The site each ingress said served the request (its X-Homelab-Site header). It matches the observer's site, because each site's pods reach their own ingress.", "table"),
                p("Name certificate remaining", metric("homelab:ingress_cert_remaining_seconds"), "s",
                  "Certificate the client saw for each name, per site. Under 14 days alerts; cert-manager renews at 30.", "table"),
            ],
            "Dependencies": [
                p("Certificate validity remaining", f'max by (k8s_cluster, name) ({metric("certmanager_certificate_expiration_timestamp_seconds")}) - time()', "s",
                  "Expiry of certificates managed by cert-manager. S3 transport trust and external identity-provider health are separate checks.", "table"),
                p("Tunnel connections", metric("cloudflared_tunnel_ha_connections"), "short",
                  "Cloudflared connection evidence. This does not by itself certify the origin, identity provider or application content."),
            ],
        }, "Apps"),
        Board("tnwks-home", "Home Automation & Cameras", "home", "l2",
              "Are automations and recording useful, including their hardware dependencies?", {
            "Cameras": [
                p("Recording input", f'sum by (k8s_cluster) ({metric("frigate_camera_fps")})', "fps",
                  "Aggregate camera input FPS without camera/zone/person identifiers. Input FPS alone does not prove recordings were persisted."),
                p("Skipped frames", f'sum by (k8s_cluster) ({metric("frigate_skipped_fps")})', "fps",
                  "Aggregate skipped frames; inspect host/detector load and source health before drawing conclusions."),
                p("Detector inference", f'max by (k8s_cluster, detector) ({metric("frigate_detector_inference_speed_seconds")})', "s",
                  "Detector-specific inference latency; hardware absent by design is not a failed detector."),
            ],
            "Automation": [
                p("Home Assistant path", metric("homelab:service_state", 'app="home-assistant"'), "state",
                  "Path probe with freshness. USB/Z-Wave dependencies remain pinned and cannot fully fail over.", "stat"),
                p("Unavailable entities", f'sum by (k8s_cluster) ({metric("homeassistant_entity_available")} == 0)', "short",
                  "Only an aggregate entity count is returned. No person, entity or camera identifiers are displayed."),
                p("MQTT broker", metric("homelab:mqtt_state"), "state",
                  "Whether the broker accepted the evidence collector's login, from each site, through its global Service. Message delivery between clients isn't measured.", "stat"),
                p("MQTT connected clients", metric("homelab_mqtt_clients_connected"), "short",
                  "From the broker's retained $SYS/broker/clients/connected, read by each site's collector. A sudden drop is clients disconnecting, not the broker restarting."),
            ],
        }, "Home"),
        Board("tnwks-hosts", "Hosts & Capacity", "hosts", "l2",
              "Can the physical hosts sustain the workload and a site failure?", {
            "Physical resources": [
                p("Windows available memory", metric("windows_memory_available_bytes"), "bytes",
                  "Physical host availability. Do not add this to WSL MemAvailable, which overlaps the same memory.", link=WINDOWS),
                p("Windows disk free", metric("windows_logical_disk_free_bytes", 'volume=~"C:|E:"'), "bytes",
                  "Physical free space remains authoritative even if an ext4 VHDX reports much more virtual space.", "table"),
                p("Disk temperature", metric("smartctl_device_temperature"), "celsius",
                  "SMART device temperature where published. Missing SMART data is a collection gap, not a passing disk test."),
            ],
            "Survivor fit": [
                p("Required app replicas", f'sum by (k8s_cluster, app) ({metric("homelab_workload_desired_replicas")})', "short",
                  "Current placement demand. Full survivor-fit evidence also needs measured requests/peaks, storage placement and pinned hardware.", "table"),
                p("Recovery readiness", metric("homelab:recovery_state"), "state",
                  "Readiness remains Unknown until storage/fencing/restore/capacity evidence exists. This page does not promise all services fit or fail over.", "table"),
            ],
        }, "Hosts"),
        Board("tnwks-observability", "Observability", "observability", "l2",
              "Can the other dashboards be trusted, and is monitoring itself sustainable?", {
            "Trust": coverage() + [
                p("Collector source success", metric("homelab_collection_success"), "bool",
                  "Collector-specific upstream success. HTTP scrape success can coexist with a blind collector.", "table"),
                p("Scrape failures", f'count by (k8s_cluster, job) ({metric("up")} == 0) or on (k8s_cluster) (0 * count by (k8s_cluster) ({metric("up")}))', "short",
                  "Failed targets per job; a site showing 0 has none. Compare with declared inventory to detect targets that disappeared from discovery.", "table"),
            ],
            "Cost": [
                p("Active series", metric("prometheus_tsdb_head_series"), "short",
                  "Head series/churn drive memory and disk growth. Add telemetry in bounded batches before raising resource limits."),
                p("Ingested samples", f'rate({metric("prometheus_tsdb_head_samples_appended_total")}[$__rate_interval])', "ops",
                  "Ingested samples per second; compare before/after each collector family."),
                p("Retained blocks", metric("prometheus_tsdb_storage_blocks_bytes"), "bytes",
                  "Compressed blocks only; WAL/head, metadata and compaction scratch require additional disk."),
                p("Rule evaluation failures", f'rate({metric("prometheus_rule_evaluation_failures_total")}[$__rate_interval])', "ops",
                  "Local alerts must remain independent of Grafana and the remote archive."),
            ],
            "Objectives": [
                p("SLO window coverage", metric("homelab:slo_window_coverage_ratio"), "percentunit",
                  "How much of the 14 d SLO window each site's Prometheus holds. Below 100%, every availability on this tab covers only that share of its window: incomplete, not wrong.", "stat"),
                p("SLO availability", f'max by (k8s_cluster, slo) ({selector("pyrra_availability")})', "percentunit",
                  "Good probe intervals over all of them in the window, per SLO and site (Pyrra, from slos.yaml). Targets are provisional until a full window exists; the Pyrra UI has burn rates.", "table"),
                # The same indicators over 28 days, at query time: through the Global
                # datasource the archive (35 d) supplies what local retention lacks.
                p("28-day availability", per_slo("avg by (k8s_cluster) (avg_over_time({sel}[28d]))"), "percentunit",
                  "Each SLO's indicator averaged over 28 days at query time, against the same targets as the 14-day Pyrra objectives. With the Global datasource the archive supplies the older weeks; read it with 28-day window coverage.",
                  "table"),
                p("28-day window coverage", per_slo(f"clamp_max((time() - min by (k8s_cluster) (min_over_time(timestamp({{sel}})[28d:1h]))) / {LONG_WINDOW_SECONDS}, 1)"), "percentunit",
                  "How much of the 28 days the chosen datasource holds for each indicator, from its earliest sample (hourly resolution). Below 100% the 28-day availability covers only that share; Local can't exceed 15 days.",
                  "table"),
                p("Error budget left", f'max by (k8s_cluster, slo) (1 - (1 - {selector("pyrra_availability")}) / (1 - {selector("pyrra_objective")}))', "percentunit",
                  "Share of the allowed failures not yet used in the window. Negative means the objective is missed. Read with SLO window coverage.", "table"),
            ],
            "Archive and delivery": [
                p("Archive state", metric("homelab:archive_state"), "state",
                  "Local Garage on WSL-02 only. A loss of this endpoint makes archive history unavailable without invalidating retained local data.", "stat"),
                p("Archive lag", metric("homelab:archive_lag_seconds"), "s",
                  "Age of the newest sample the archive holds for each site, from the Compactor's block index. 1-3 h is normal (Prometheus cuts 2 h blocks once its head spans 3 h); over 4 h alerts."),
                p("Archive history", metric("homelab:archive_history_seconds"), "s",
                  "How far back the archive can answer for each site. Retention is 35 d; Pyrra's 28 d windows are incomplete until this passes 28 d."),
                p("Archive free space", metric("homelab:archive_free_ratio"), "percentunit",
                  "Garage's data volume on WSL-02, shared with that node. Under 10% alerts."),
                p("Time since last upload", metric("homelab:unuploaded_block_age_seconds"), "s",
                  "Since each site's sidecar last uploaded a block. Climbs to about 2 h between blocks and resets; Archive lag is the one to read."),
                p("Notification failures", f'sum by (k8s_cluster, integration) (rate({metric("alertmanager_notifications_failed_total")}[$__rate_interval]))', "ops",
                  "Delivery failures. No active alerts is reassuring only when local evaluation, routing and external heartbeat delivery are functioning."),
            ],
        }, "Observability"),
        Board("tnwks-alerts", "Alerts", "observability", "l2",
              "What is firing now, and can I reach its evidence and runbook?", {
            "Current": [
                p("Firing now", alerts("firing"), "short",
                  "Current Prometheus firing state, independent of the dashboard historical time selection, excluding heartbeats. Follow the rule's dashboard/runbook annotation for diagnosis.",
                  "table", empty=NO_ALERTS),
                p("Pending now", alerts("pending"), "short",
                  "Pending is not yet firing. Alertmanager silencing/inhibition and notification state are viewed through the per-site Alertmanager links.",
                  "table", empty=NO_ALERTS),
            ],
            "Alertmanager": [
                p("Alerts in Alertmanager", f'sum by (k8s_cluster, state) ({selector("alertmanager_alerts")})', "short",
                  "Alerts each site's Alertmanager holds: active, or suppressed by a silence or inhibition. Compare with Firing now: an alert firing in Prometheus but missing here never reached Alertmanager."),
                p("Active silences", f'max by (k8s_cluster) ({selector("alertmanager_silences", "state=\"active\"")})', "short",
                  "Silences in force. A forgotten silence hides real alerts; check each one has an end time and a reason.", "stat"),
                p("Configuration loaded", f'min by (k8s_cluster) ({selector("alertmanager_config_last_reload_successful")})', "bool",
                  "Whether each Alertmanager's last configuration reload succeeded. A failed reload keeps the previous routes and receivers.", "stat"),
                p("Notifications sent", f'sum by (k8s_cluster, integration) (increase({selector("alertmanager_notifications_total")}[1h]))', "short",
                  "Notification attempts per integration in the last hour."),
                p("Failed notifications", f'sum by (k8s_cluster, integration) (increase({metric("alertmanager_notifications_failed_total")}[1h]))', "short",
                  "Failures during the window, not a definitive end-to-end notification receipt check."),
            ],
        }, "Observability"),
    ]
    house_panels = [
        p("Internet", 'homelab_house_state{domain="internet"}', "state",
          "Upstream-approved aggregate Internet state. No observation or stale observation is Unknown.", "stat"),
        p("Wi-Fi", 'homelab_house_state{domain="wifi"}', "state",
          "Aggregate Wi-Fi evidence. Working APs do not prove every client's experience; missing evidence remains Unknown.", "stat"),
        p("Home automation", 'homelab_house_state{domain="automation"}', "state",
          "Aggregate usable Home Assistant path; hardware-pinned integration limitations remain visible in operator diagnostics.", "stat"),
        p("Cameras", 'homelab_house_state{domain="cameras"}', "state",
          "Aggregate recording evidence. Missing persistence evidence cannot certify that cameras are recording.", "stat"),
        p("Apps", 'homelab_house_state{domain="apps"}', "state",
          "Aggregate app path evidence, with missing expected paths represented as Unknown.", "stat"),
        p("Monitoring", 'homelab_house_state{domain="monitoring"}', "state",
          "Whether fresh approved aggregate evidence is available. This identity can query only the aggregate backend.", "stat"),
    ]
    boards.append(Board("home", "House", "home", "l0", "Is the house working?", {"House": house_panels}, "House", True))
    return boards


def visualization(panel: Panel, runbook: str | None = None):
    cls = {"stat": stat.Visualization, "table": table.Visualization,
           "timeseries": timeseries.Visualization}[panel.kind]
    viz = cls().unit("short" if panel.unit in ("state", "coverage") else panel.unit).no_value(panel.empty)
    if panel.unit == "state":
        viz.mappings([model.ValueMap(options=STATE)])
    if panel.unit == "coverage":
        viz.mappings([model.ValueMap(options={
            "0": model.ValueMappingResult(text="Unknown", color="#6b6b6b"),
            "1": model.ValueMappingResult(text="Reporting", color="#2f6bd0"),
        })])
    if panel.kind == "table":
        viz.min_width(70)
    # Colored text failed WCAG contrast for most states on the light theme
    # (Degraded 2.0:1). State cells and stats are colored backgrounds instead:
    # Grafana writes white text on the dark ones (5.1-5.5:1, the STATE palette)
    # and black on amber (10.2:1). The word carries the state, never the color.
    if panel.kind == "table" and panel.unit in ("state", "coverage"):
        viz.override_by_name("State", [model.DynamicConfigValue(
            id_val="custom.cellOptions", value={"type": "color-background", "mode": "basic"})])
    if panel.kind == "stat" and panel.unit in ("state", "coverage"):
        viz.color_mode(common.BigValueColorMode.BACKGROUND)
    if panel.kind == "stat":
        viz.reduce_options(common_builder.ReduceDataOptions().calcs(["lastNotNull"]).values(False))
        if panel.unit not in ("state", "coverage"):
            viz.color_scheme(dashboard.FieldColor().mode(model.FieldColorModeId.FIXED).fixed_color("#5794f2"))
    if panel.kind == "timeseries":
        viz.legend(common_builder.VizLegendOptions().display_mode(common.LegendDisplayMode.TABLE)
                   .placement(common.LegendPlacement.BOTTOM).calcs(["lastNotNull", "max"]))
    links = []
    if panel.link:
        uid, entity = (panel.link, None) if isinstance(panel.link, str) else panel.link
        if entity and panel.kind == "table":
            assert not set(entity.values()) & set(SCRAPE_COLUMNS), (panel.title, "table hides the entity's field")
        links.append(model.DataLink(title="Diagnose", url=dashboard_link(uid, entity, panel.kind == "table")))
    if runbook:
        links.append(model.DataLink(title="Runbook", url=f'https://github.com/iT3E/tnwks-ops/blob/main/{runbook}', target_blank=True))
    if links:
        viz.data_links(links)
    return viz


def resource(board: Board) -> dict:
    builder = (dashboard.Dashboard(board.title).description(board.question)
               .editable(False).tags(["tnwks", board.level, board.domain])
               .cursor_sync(model.DashboardCursorSync.CROSSHAIR)
               .time_settings(dashboard.TimeSettings().timezone("browser").from_val("now-6h")
                              .to("now").auto_refresh("1m")
                              .auto_refresh_intervals(["1m", "5m", "15m", "30m", "1h"])))
    datasource = "prometheus-house" if board.kiosk else "${datasource}"
    if not board.kiosk:
        builder.variable(dashboard.DatasourceVariable("datasource").label("Metrics")
                         .plugin_id("prometheus").regex("/^(Global|Local|Site 1|Site 2)$/")
                         .current(model.VariableOption(text="Global", value="prometheus-global")))
        builder.variable(dashboard.CustomVariable("cluster").label("Cluster")
                         .query(",".join(CLUSTERS)).multi(True).include_all(True).all_value(".*")
                         .allow_custom_value(False)
                         .current(model.VariableOption(text="All", value="$__all")))
        builder.links([dashboard.DashboardLink().title("Dashboards").type(model.DashboardLinkType.DASHBOARDS)
                       .tags(["tnwks"]).as_dropdown(True).keep_time(True).include_vars(True)])
    tab_layout = dashboard.Tabs()
    count = 0
    about = (dashboard.Panel().id(1).title("About this view")
             .description("Purpose, scope and the interpretation of missing evidence.")
             .visualization(text.Visualization().content(board.question + "\n\n"
                 + ("Missing evidence is Unknown."
                    if board.kiosk else "Missing or partial evidence is Unknown. "
                       + f"[Runbook](https://github.com/iT3E/tnwks-ops/blob/main/{runbook_path(board.domain)})"))))
    builder.element("about", about)
    for tab_name, panels in board.tabs.items():
        grid = (dashboard.AutoGrid().max_column_count(3).column_width_mode("narrow")
                .row_height_mode("standard" if board.level == "l2" else "short"))
        if count == 0:
            grid.with_item("about")
        for panel in panels:
            count += 1
            key = f"panel-{count}"
            query = (prometheus.Query().expr(panel.expr).legend_format(panel.legend)
                     .datasource(dashboard.Dashboardv2DataQueryKindDatasource().name(datasource)))
            if panel.kind in ("stat", "table"):
                query.instant()
            else:
                query.range()
            data = dashboard.QueryGroup().target(dashboard.Target().ref_id("A").query(query))
            if panel.kind == "table":
                query.format(prom_model.PromQueryFormat.TABLE)
                data.transformation(dashboard.Transformation().group("organize").options({
                    "excludeByName": dict.fromkeys(SCRAPE_COLUMNS, True),
                    "indexByName": {"Value": 0, "k8s_cluster": 1, "app": 2, "db_cluster": 2},
                    "renameByName": {"k8s_cluster": "Site", "db_cluster": "Database",
                                     "app": "App", "observer_cluster": "Observer",
                                     "Value": {"state": "State", "coverage": "State",
                                               "dateTimeAsIso": "When"}.get(panel.unit, "Value")},
                }))
            description = panel.description
            if not board.kiosk:
                description += (f"\n\nObservation budget: {OBSERVATION_BUDGET_SECONDS}s for current evidence; "
                                "backup/restore objectives and historical windows are separate. "
                                "Source contract: observability/generated/panel-contracts.json.")
            element = (dashboard.Panel().id(count + 1).title(panel.title).description(description)
                       .data(data)
                       .visualization(visualization(panel, None if board.kiosk else runbook_path(board.domain, panel.title))))
            builder.element(key, element)
            grid.with_item(key)
        tab_layout.tab(dashboard.Tab().title(tab_name).layout(grid))
    builder.layout(tab_layout)
    spec = json.loads(json.dumps(builder.build(), cls=JSONEncoder))
    return {"apiVersion": "dashboard.grafana.app/v2", "kind": "Dashboard",
            "metadata": {"name": board.uid}, "spec": spec}


def generated() -> dict[Path, str]:
    files = {}
    generators = []
    expressions = []
    for board in catalogue():
        suffix = "house" if board.kiosk else board.uid
        name = f"{suffix}.json"
        files[OUTPUT / name] = json.dumps(resource(board), indent=2, ensure_ascii=False) + "\n"
        generators.append({"name": f"grafana-{suffix}", "files": [name], "options": {
            "annotations": {"grafana_folder": board.folder,
                            "kustomize.toolkit.fluxcd.io/substitute": "disabled"},
            "labels": {"grafana_house_dashboard" if board.kiosk else "grafana_dashboard": "true"}}})
        for tab_name, panels in board.tabs.items():
            expressions.extend({"dashboard": suffix, "tab": tab_name, "title": p.title,
                                "expr": p.expr, "unit": p.unit, "kiosk": board.kiosk} for p in panels)
    cm = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
          "namespace": "monitoring", "configMapGenerator": generators,
          "generatorOptions": {"disableNameSuffixHash": True}}
    files[OUTPUT / "kustomization.yaml"] = "---\n" + yaml.safe_dump(cm, sort_keys=False)
    files[ROOT / "observability/generated/panel-queries.json"] = json.dumps(expressions, indent=2) + "\n"
    files[ROOT / "observability/generated/kiosk-query-allowlist.json"] = json.dumps(
        sorted(p["expr"] for p in expressions if p["kiosk"]), indent=2) + "\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = False
    for path, expected in generated().items():
        actual = path.read_text() if path.exists() else ""
        if actual == expected:
            continue
        changed = True
        if args.check:
            print("".join(difflib.unified_diff(actual.splitlines(True), expected.splitlines(True),
                                               fromfile=str(path), tofile="regenerated")))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected)
    return int(changed and args.check)


if __name__ == "__main__":
    raise SystemExit(main())
