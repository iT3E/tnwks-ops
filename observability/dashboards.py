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
    "homelab:recovery_state": ("app",),
    "homelab:database_backup_state": ("namespace", "db_cluster"),
    "homelab_expected_database_info": ("namespace", "db_cluster", "app"),
    "homelab_expected_workload_info": ("app", "tier", "kind", "workload"),
}
# Table columns that describe the scrape, not the thing being reported.
SCRAPE_COLUMNS = ("__name__", "Time", "__replica__", "cluster", "container", "endpoint",
                  "instance", "job", "observer_cluster", "pod", "prometheus", "service")
STATE = {
    "0": model.ValueMappingResult(text="Unknown", color="#8e8e8e"),
    "1": model.ValueMappingResult(text="Healthy", color="#299c46"),
    "2": model.ValueMappingResult(text="Degraded", color="#e5ac0e"),
    "3": model.ValueMappingResult(text="Failed", color="#d44a3a"),
    "4": model.ValueMappingResult(text="Standby", color="#5794f2"),
    "5": model.ValueMappingResult(text="Not applicable", color="#8e8e8e"),
    "6": model.ValueMappingResult(text="Maintenance", color="#b877d9"),
}


@dataclass(frozen=True)
class Panel:
    title: str
    expr: str
    unit: str
    description: str
    kind: str = "timeseries"
    link: str | None = None
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


def coverage() -> list[Panel]:
    return [
        p("Expected sites", 'count(max by (k8s_cluster) (homelab_expected_site_info))',
          "short", "Declared inventory survives the disappearance of a source. This is expected scope, not health.", "stat"),
        p("Reporting sites", 'count(homelab:site_reporting == 1) or vector(0)', "short",
          "A reporting site has fresh, successful evidence collection. Compare with expected sites; partial results never certify fleet health.", "stat"),
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
                p("Service paths", metric("homelab:service_state"), "state",
                  "Synthetic content/transport evidence per service. No probe or stale probe is Unknown, never an implicit success.", "table", "tnwks-apps"),
                p("Current firing alerts", f'ALERTS{{{ALERT_SCOPE},alertstate="firing"}}', "short",
                  "Current Prometheus rule state; this is not a historical Alertmanager event list and does not show silence/inhibition state.", "table", "tnwks-alerts"),
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
                p("Last restore evidence", f'time() - {metric("homelab_restore_last_success_timestamp_seconds")}', "s",
                  "Per-app restore evidence, not one successful fleet CronJob. Missing per-app results remain Unknown.", "table"),
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
            "Storage": [
                p("Windows disk free", metric("windows_logical_disk_free_bytes", 'volume=~"C:|E:"'), "bytes",
                  "Physical Windows free capacity. WSL's virtual disk maximum is not an independent physical capacity budget."),
            ],
        }, "Data"),
        Board("tnwks-network", "Network & Routing", "network", "l2",
              "Where does the path from the router through FRR/Cilium to a service fail?", {
            "Paths": [
                p("Site path probes", metric("homelab_peer_probe_success"), "bool",
                  "Cross-site probe success from each observer. Read with transport and endpoint evidence; a BGP Established session alone is insufficient.", "table"),
                p("WireGuard received", f'rate({metric("mktxp_wireguard_peer_rx_bytes")}[5m])', "Bps",
                  "WireGuard peer traffic where supported by the pinned exporter. Missing metrics indicate telemetry coverage, not a quiet healthy tunnel."),
                p("Router interface errors", f'sum by (k8s_cluster, name) (rate({metric("mktxp_interface_rx_error_total")}[5m]))', "ops",
                  "Router receive errors by interface. Telemetry enables are integrated after the network migration release."),
            ],
            "BGP and mesh": [
                p("BGP sessions", metric("homelab_bgp_session_established"), "bool",
                  "Normalized router/FRR/Cilium session evidence. Unimplemented or stale adapters show Unknown rather than invented healthy sessions.", "table"),
                p("Expected route observed", metric("homelab_route_observed"), "bool",
                  "Expected VIP/pod routes checked against observed selected routes. Session state cannot substitute for this evidence.", "table"),
                p("Cilium drops", f'sum by (k8s_cluster, reason) (rate({metric("cilium_drop_count_total")}[5m]))', "pps",
                  "Agent drop counters, with bounded labels. Cilium metrics activation belongs to the post-network-release integration."),
                p("Mesh connectivity", metric("cilium_clustermesh_remote_cluster_readiness_status"), "bool",
                  "Remote cluster readiness from Cilium. Mesh API/KVStoreMesh metrics have separate enabling requirements.", "table"),
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
                  "AP inventory/uptime. Device-only identifiers; household client names/MACs are excluded.", "table", "home-wifi"),
                p("AP uplink", f'max by (k8s_cluster, name) ({metric("unpoller_device_uplink_speed_mbps")})', "Mbits",
                  "Negotiated uplink speed. A slow or errored uplink can masquerade as an RF problem.", "table"),
                p("Wi-Fi collector failures", f'sum by (k8s_cluster) (increase({metric("unpoller_prometheus_refresh_failures_total")}[15m]))', "short",
                  "Collector-specific failure evidence; HTTP scrape success does not prove the UniFi API refresh succeeded."),
            ],
            "Site dependency": [
                p("Peer path success", metric("homelab_peer_probe_success"), "bool",
                  "Correlate site 2 connectivity loss with AP/uplink and WireGuard/BGP observations. This does not identify individual household clients."),
                p("Peer path duration", metric("homelab_peer_probe_duration_seconds"), "s",
                  "Bounded transport probe duration; one sample does not measure line-rate Wi-Fi performance."),
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
                p("Unready workloads", f'{metric("homelab_workload_desired_replicas")} - {metric("homelab_workload_ready_replicas")}', "short",
                  "Effective desired minus Ready per tracked app/site. A zero-replica standby is not a failed application."),
                p("Node CPU utilization", f'1 - avg by (k8s_cluster, instance) (rate({metric("node_cpu_seconds_total", "mode=\"idle\"")}[$__rate_interval]))', "percentunit",
                  "CPU busy fraction per nested node view. Do not sum shared WSL kernel capacity as independent physical nodes."),
                p("Node memory available", metric("node_memory_MemAvailable_bytes"), "bytes",
                  "Shared WSL kernel memory view on nested Talos nodes. Cross-check Windows physical available memory."),
            ],
        }, "Kubernetes"),
        Board("tnwks-apps", "Apps & Ingress", "apps", "l2",
              "Can users reach a usable application, and where is the request path failing?", {
            "User paths": [
                p("Application probe state", metric("homelab:service_state"), "state",
                  "End-to-end content-aware probe evidence. An auth redirect alone cannot certify the application behind it.", "table"),
                p("HTTP requests", f'sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests")}[$__rate_interval]))', "reqps",
                  "Rate counters before aggregation. Use the same scope when comparing the error numerator and total denominator."),
                p("HTTP 5xx fraction", f'sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests", "status=~\"5..\"")}[$__rate_interval])) / sum by (k8s_cluster, ingress) (rate({metric("nginx_ingress_controller_requests")}[$__rate_interval]))', "percentunit",
                  "Weighted errors/requests. No traffic is not a perfect availability measurement; independent synthetic evidence remains necessary."),
                p("HTTP p95 duration", f'histogram_quantile(0.95, sum by (k8s_cluster, ingress, le) (rate({metric("nginx_ingress_controller_request_duration_seconds_bucket")}[$__rate_interval])))', "s",
                  "Buckets combined before calculating the percentile. Never average per-pod percentiles."),
            ],
            "Dependencies": [
                p("Certificate validity remaining", f'{metric("certmanager_certificate_expiration_timestamp_seconds")} - time()', "s",
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
                p("MQTT connected clients", metric("mosquitto_clients_connected"), "short",
                  "Requires an actual MQTT exporter. Missing telemetry remains Unknown; broker reachability and message delivery need separate evidence."),
            ],
        }, "Home"),
        Board("tnwks-hosts", "Hosts & Capacity", "hosts", "l2",
              "Can the physical hosts sustain the workload and a site failure?", {
            "Physical resources": [
                p("Windows available memory", metric("windows_memory_available_bytes"), "bytes",
                  "Physical host availability. Do not add this to WSL MemAvailable, which overlaps the same memory."),
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
            "Archive and delivery": [
                p("Archive state", metric("homelab:archive_state"), "state",
                  "Local Garage on WSL-02 only. A loss of this endpoint makes archive history unavailable without invalidating retained local data.", "stat"),
                p("Unuploaded block age", metric("homelab:unuploaded_block_age_seconds"), "s",
                  "Sidecar last-upload evidence; absent while uploads are not configured. An unavailable archive needs attention before local retention deletes the backlog."),
                p("Notification failures", f'sum by (k8s_cluster, integration) (rate({metric("alertmanager_notifications_failed_total")}[$__rate_interval]))', "ops",
                  "Delivery failures. No active alerts is reassuring only when local evaluation, routing and external heartbeat delivery are functioning."),
            ],
        }, "Observability"),
        Board("tnwks-alerts", "Alerts", "observability", "l2",
              "What is firing now, and can I reach its evidence and runbook?", {
            "Current": [
                p("Firing now", f'ALERTS{{{ALERT_SCOPE},alertstate="firing"}}', "short",
                  "Current Prometheus firing state, independent of the dashboard historical time selection. Follow the rule's dashboard/runbook annotation for diagnosis.", "table"),
                p("Pending now", f'ALERTS{{{ALERT_SCOPE},alertstate="pending"}}', "short",
                  "Pending is not yet firing. Alertmanager silencing/inhibition and notification state are viewed through the per-site Alertmanager links.", "table"),
            ],
            "Delivery": [
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


def visualization(panel: Panel):
    cls = {"stat": stat.Visualization, "table": table.Visualization,
           "timeseries": timeseries.Visualization}[panel.kind]
    viz = cls().unit("short" if panel.unit in ("state", "coverage") else panel.unit).no_value(panel.empty)
    if panel.unit == "state":
        viz.mappings([model.ValueMap(options=STATE)])
    if panel.unit == "coverage":
        viz.mappings([model.ValueMap(options={
            "0": model.ValueMappingResult(text="Unknown", color="#8e8e8e"),
            "1": model.ValueMappingResult(text="Reporting", color="#5794f2"),
        })])
    if panel.kind == "table":
        viz.min_width(70)
    if panel.kind == "stat":
        viz.reduce_options(common_builder.ReduceDataOptions().calcs(["lastNotNull"]).values(False))
        if panel.unit not in ("state", "coverage"):
            viz.color_scheme(dashboard.FieldColor().mode(model.FieldColorModeId.FIXED).fixed_color("#5794f2"))
        if panel.title == "Reporting sites":
            viz.color_scheme(dashboard.FieldColor().mode(model.FieldColorModeId.THRESHOLDS))
            viz.thresholds(dashboard.ThresholdsConfig().mode(model.ThresholdsMode.ABSOLUTE).steps([
                model.Threshold(value=None, color="#d44a3a"),
                model.Threshold(value=1, color="#e5ac0e"),
                model.Threshold(value=len(CLUSTERS), color="#299c46"),
            ]))
    if panel.kind == "timeseries":
        viz.legend(common_builder.VizLegendOptions().display_mode(common.LegendDisplayMode.TABLE)
                   .placement(common.LegendPlacement.BOTTOM).calcs(["lastNotNull", "max"]))
    if panel.link:
        viz.data_links([model.DataLink(title="Diagnose", url=f'/d/{panel.link}?from=${{__from}}&to=${{__to}}&var-cluster=${{cluster:percentencode}}&var-datasource=${{datasource:percentencode}}')])
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
                       + "[Runbook](https://github.com/iT3E/tnwks-ops/blob/main/docs/operations/grafana-dashboard-design.md)"))))
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
                                     "Value": "State" if panel.unit in ("state", "coverage") else "Value"},
                }))
            element = (dashboard.Panel().id(count + 1).title(panel.title).description(panel.description)
                       .data(data)
                       .visualization(visualization(panel)))
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
