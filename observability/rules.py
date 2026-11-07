"""Generate normalized evidence rules and executable PromQL behavior fixtures."""

from pathlib import Path

import yaml

from inventory import inventory

ROOT = Path(__file__).resolve().parents[1]
# The SLO window (kubernetes/apps/monitoring/pyrra/app/slos.yaml): inside the
# 15 d local retention, since Pyrra's rules run on each site's Prometheus.
SLO_WINDOW_SECONDS = 14 * 86400


def records():
    db = "k8s_cluster, namespace, db_cluster"
    expected_db = f"max by ({db}) (homelab_expected_database_info)"
    identity = "k8s_cluster, app, kind, workload"
    def fresh(name):
        return f"({name} and (time() - timestamp({name}) < 180))"

    desired = f"sum by (k8s_cluster, app) (max by ({identity}) ({fresh('homelab_workload_desired_replicas')}))"
    ready = f"sum by (k8s_cluster, app) (max by ({identity}) ({fresh('homelab_workload_ready_replicas')}))"
    return {
        "homelab:site_reporting": '(max by (k8s_cluster) (homelab_collection_success{collector="workloads"}) == bool 1) * on (k8s_cluster) (time() - max by (k8s_cluster) (homelab_collection_last_success_timestamp_seconds{collector="workloads"}) < bool 180) * on (k8s_cluster) (time() - max by (k8s_cluster) (homelab_collection_last_success_timestamp_seconds{collector="workloads"}) >= bool 0)',
        "homelab:site_state": '(homelab:site_reporting == 1) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_expected_site_info))',
        "homelab:database_in_recovery": f'max by ({db}) (label_replace(cnpg_pg_replication_in_recovery{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_replay_lag_seconds": f'max by ({db}) (label_replace(cnpg_pg_replication_lag{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_connections": f'sum by ({db}) (label_replace(cnpg_backends_total{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_backup_age_seconds": f'time() - max by ({db}) (label_replace(barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_backup_state": f'((homelab:database_backup_age_seconds >= bool 0) * (homelab:database_backup_age_seconds <= bool 108000) + 3 * (homelab:database_backup_age_seconds > bool 108000)) or on ({db}) (0 * {expected_db})',
        # Where the app runs, nobody needs to take it over: Not applicable (5).
        "homelab:recovery_state": f'(0 * ({desired} > 0) + 5) and on (k8s_cluster, app) (max by (k8s_cluster, app) (homelab_expected_workload_info{{tier="warm"}})) or on (k8s_cluster, app) ' + '(3 * max by (k8s_cluster, app) (' + fresh('homelab_recovery_prerequisite_state') + ' == 3) / 3) or on (k8s_cluster, app) ((count by (k8s_cluster, app) (max by (k8s_cluster, app, prerequisite) (' + fresh('homelab_recovery_prerequisite_state{prerequisite=~"database|backup|restore|fence"}') + ') == 1) == 4) / 4) or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info{tier="warm"}))',
        "homelab:local_history_seconds": 'time() - min by (k8s_cluster) (prometheus_tsdb_lowest_timestamp_seconds)',
        # Share of the SLO window this site's Prometheus holds: below 1, every
        # SLO's availability covers only that much of its window.
        "homelab:slo_window_coverage_ratio": f'clamp_max(homelab:local_history_seconds / {SLO_WINDOW_SECONDS}, 1)',
        # How far behind the archive is: the newest sample it holds for each
        # site. Prometheus cuts 2 h blocks once its head spans 3 h, so 1-3 h is
        # normal; more means uploads, the bucket or the Compactor's sync stalled.
        "homelab:archive_lag_seconds": 'time() - max by (k8s_cluster) (' + fresh('homelab_archive_newest_sample_timestamp_seconds') + ')',
        # History the archive can answer for, per site (Pyrra's 28-35 d windows).
        "homelab:archive_history_seconds": 'time() - min by (k8s_cluster) (' + fresh('homelab_archive_oldest_sample_timestamp_seconds') + ')',
        "homelab:archive_free_ratio": 'min by (k8s_cluster) (garage_local_disk_avail{volume="data"} / garage_local_disk_total{volume="data"})',
        "homelab:unuploaded_block_age_seconds": 'time() - max by (k8s_cluster) (thanos_objstore_bucket_last_successful_upload_time{job=~".*thanos-discovery"} > 0)',
        "homelab:archive_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="archive"} and (time() - timestamp(probe_success{observability_target="archive"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        # The broker accepted the collector's login (MQTT), from each site. A
        # global Service: either site's check reaches the broker wherever it runs.
        "homelab:mqtt_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="mqtt"} and (time() - timestamp(probe_success{observability_target="mqtt"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        "homelab:internet_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="internet"} and (time() - timestamp(probe_success{observability_target="internet"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        # Replica evidence, not an end-to-end path: 1 healthy, 2 degraded, 3 failed,
        # 4 standby (desired 0, the normal state of an app on the other site).
        "homelab:workload_state": (
            '0 * max by (k8s_cluster, app) (homelab_workload_present == 0) or '
            f'({desired} == 0) + 4'
            f' or ((({ready} >= {desired}) * 0) + 1)'
            f' or ((({ready} < {desired}) > 0) * 0 + 2)'
            f' or (({ready} < {desired}) * 0 + 3)'
            ' or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))'),
        **objective_records(fresh),
        **other_database_records(),
        **network_records(),
        # Each site's collector reaches the shared-VIP names the way a client does
        # (router DNS, verified TLS, the ingress routing the host). A pod reaches
        # its own site's ingress for either VIP, so this is per site, by name.
        "homelab:ingress_state": '(3 - 2 * min by (k8s_cluster, name) (probe_success{observability_target="ingress"} and (time() - timestamp(probe_success{observability_target="ingress"})) < 180)) or on (k8s_cluster, name) (0 * max by (k8s_cluster, name) (homelab_expected_ingress_info))',
        "homelab:ingress_cert_remaining_seconds": 'min by (k8s_cluster, name) (homelab_ingress_cert_expiry_timestamp_seconds) - time()',
        # Content probe from the site that runs the app; Standby (4) where it's
        # deliberately at zero; Unknown where neither is observed.
        "homelab:service_state": '(3 - 2 * min by (k8s_cluster, app) (probe_success{observability_target="service"} and (time() - timestamp(probe_success{observability_target="service"})) < 180)) or on (k8s_cluster, app) (homelab:workload_state == 4) or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))',
        **house_records(),
    }


def house_records():
    """The household view (L0): one state per domain, for this site's own share
    only, with no names or identifiers. 1 healthy, 2 degraded, 3 failed; a
    domain this site has no part in has no series. Home Assistant combines the
    sites (its house_systems package): docs/operations/observability-implementation.md,
    "Household view"."""
    own = "and on (k8s_cluster) (max by (k8s_cluster) (homelab_observer_info))"

    def domain(expr, name):
        return f'label_replace({expr}, "domain", "{name}", "", "")'

    def share(now, day, source):
        # All of what worked at some point in the last day is working: 1; some
        # of it: 2; none of it: 3. Something off for a whole day drops out.
        # Only while the source reports at all: a blind exporter (UniFi Poller
        # on a lost site, say) leaves no row, which reads Unknown, not Failed.
        return (f"((({now}) >= bool ({day})) + 2 * (({now}) < bool ({day})) "
                f"or (0 * ({day}) + 3)) and on () ({source})")

    cameras_now = "count(frigate_camera_fps > 0)"
    cameras_day = "count(max_over_time(frigate_camera_fps[1d]) > 0)"
    aps_now = "count(max by (name) (unpoller_device_uptime_seconds > 0))"
    aps_day = "count(max by (name) (max_over_time(unpoller_device_uptime_seconds[1d]) > 0))"
    active = f"(homelab:service_state != 4 {own})"
    return {
        # Worst over the apps this site serves: Failed > Degraded > Unknown >
        # Healthy. A reporting site serving none has nothing failing (1), so
        # every reporting site has an apps row and a missing one stands out.
        "homelab:house_apps_rank": (f"max((({active}) == bool 3) * 4 + (({active}) == bool 2) * 3"
                                    f" + (({active}) == bool 0) * 2 + (({active}) == bool 1))"
                                    " or (0 * max(homelab_observer_info) + 1)"),
        "homelab:house_state": " or ".join([
            domain(f"max(homelab:internet_state {own})", "internet"),
            domain(share(aps_now, aps_day, "count(unpoller_device_uptime_seconds)"), "wifi"),
            domain(f'max(homelab:service_state{{app="home-assistant"}} != 4 {own})', "automation"),
            domain(share(cameras_now, cameras_day, "count(frigate_camera_fps)"), "cameras"),
            domain("(homelab:house_apps_rank == bool 4) * 3 + (homelab:house_apps_rank == bool 3) * 2"
                   " + (homelab:house_apps_rank == bool 1)", "apps"),
            domain(f"3 - 2 * max(homelab:site_reporting {own})", "monitoring"),
        ]),
    }


def other_database_records():
    """Databases outside CNPG, from their workload's readiness: 1 every desired
    replica ready, 3 not, 4 a deliberate standby (desired 0), 0 unobserved."""
    key = "k8s_cluster, namespace, workload"
    expected = f"max by ({key}, engine, app) (homelab_expected_other_database_info)"

    def per_workload(sts, deploy):
        return (f'max by ({key}) (label_replace({sts}, "workload", "$1", "statefulset", "(.+)")'
                f' or label_replace({deploy}, "workload", "$1", "deployment", "(.+)"))')

    desired = per_workload("kube_statefulset_replicas", "kube_deployment_spec_replicas")
    ready = per_workload("kube_statefulset_status_replicas_ready", "kube_deployment_status_replicas_available")
    return {
        "homelab:other_database_state": (
            f"({expected} * 0 + 4) and on ({key}) ({desired} == 0)"
            f" or ({expected} * 0 + 1) and on ({key}) (({ready} >= {desired}) > 0)"
            f" or ({expected} * 0 + 3) and on ({key}) ({ready} < {desired})"
            f" or ({expected} * 0)"),
    }


def objective_records(fresh):
    """Per-app recovery objectives on each site (inventory.OBJECTIVES).

    Backup age is the app's oldest newest-backup over every scheduled PVC
    (Kopiur's last success for its policy) and every primary database
    (Barman's). A scheduled PVC or a primary without one, or with a timestamp
    in the future, leaves the app Unknown: a fresh backup of one piece can't
    stand in for another. Where nothing is scheduled (a standby), no row.
    """
    db = "k8s_cluster, namespace, db_cluster"
    scheduled = f"max by (k8s_cluster, namespace, policy, app, pvc) ({fresh('homelab_app_backup_scheduled')} == 1)"
    primaries = f"max by ({db}, app) (homelab_expected_database_info) and on ({db}) (homelab:database_in_recovery == 0)"

    def target(objective):
        return f'max by (k8s_cluster, app) (homelab_app_recovery_objective_seconds{{objective="{objective}"}})'

    gap = (f"max by (k8s_cluster, app) ({scheduled} unless on (k8s_cluster, app, pvc) homelab:app_pvc_backup_age_seconds)"
           f" or max by (k8s_cluster, app) ({primaries} unless on ({db}) (homelab:database_backup_age_seconds >= 0))")
    drill = f"max by (k8s_cluster, app) ({fresh('homelab_restore_last_duration_seconds')})"
    running = (f"sum by (k8s_cluster, app) (max by (k8s_cluster, app, kind, workload) "
               f"({fresh('homelab_workload_desired_replicas')}))")
    return {
        "homelab:app_pvc_backup_age_seconds": (
            "((time() - max by (k8s_cluster, namespace, policy) (kopiur_policy_last_backup_success_timestamp_seconds)) >= 0)"
            f" * on (k8s_cluster, namespace, policy) group_right {scheduled}"),
        "homelab:app_backup_age_seconds": (
            "max by (k8s_cluster, app) (homelab:app_pvc_backup_age_seconds or "
            f"((homelab:database_backup_age_seconds >= 0) * on ({db}) group_right max by ({db}, app) ({primaries})))"),
        # 1 within the objective, 3 past it, 0 when a piece has no evidence.
        "homelab:app_rpo_state": (
            f"(0 * ({gap})) or ((homelab:app_backup_age_seconds <= bool on (k8s_cluster, app) {target('rpo')})"
            f" + 3 * (homelab:app_backup_age_seconds > bool on (k8s_cluster, app) {target('rpo')}))"),
        # The last successful standby restore drill against the restore objective;
        # Unknown until a drill reports how long it took. The drill restores into
        # the standby, so where the app runs it's Not applicable (5).
        "homelab:app_rto_state": (
            f"(0 * ({running} > 0) + 5) and on (k8s_cluster, app) {target('rto')}"
            f" or on (k8s_cluster, app) (({drill} <= bool on (k8s_cluster, app) {target('rto')}) + 3 * ({drill} > bool on (k8s_cluster, app) {target('rto')}))"
            f" or on (k8s_cluster, app) (0 * {target('rto')})"),
    }


def network_records():
    """Per-site network path, by hop: 1 healthy, 2 degraded, 3 failed, 0 Unknown.

    tunnel: the router's WireGuard peer for the site's distro handshook within
    180 s (WireGuard rekeys every 2 min while traffic flows). router-bgp: the
    router's session with the distro's FRR is established; with the router
    observed but the session missing, it's down (RouterOS lists only live
    sessions). node-bgp: the Cilium agents' sessions with the distro, all
    (healthy), some (degraded) or none (failed). The router is observed by
    homelab-wsl's mktxp only, so homelab-wsl-02's rules leave those Unknown and
    the dashboards keep the real answer. mesh: the site's Cilium agents and its
    KVStoreMesh both have the other cluster ready.
    """
    sites = inventory()["sites"]

    def per_site(expr, site):
        return f'label_replace({expr}, "k8s_cluster", "{site["cluster"]}", "", "")'

    def hop(name, expr):
        return f'label_replace({expr}, "hop", "{name}", "", "")'

    router_up = "max(mktxp_system_uptime)"
    tunnel = " or ".join(per_site(
        f'3 - 2 * (min(mktxp_last_handshake{{interface="{s["tunnel"]}"}}) < bool 180)', s) for s in sites)
    router_bgp = " or ".join(per_site(
        f'(3 - 2 * max(mktxp_bgp_established{{name=~"{s["router_peer"]}(-[0-9]+)?"}}))'
        f' or (0 * {router_up} + 3)', s) for s in sites)
    nodes = 'cilium_bgp_control_plane_session_state{source_site=""}'
    node_bgp = (f'3 - (min by (k8s_cluster) ({nodes}) == bool 1)'
                f' - (max by (k8s_cluster) ({nodes}) == bool 1)')
    # Tagged apart first: `or` would drop KVStoreMesh's series, whose labels
    # match the agents'.
    ready = ('(min by (k8s_cluster) ('
             'label_replace(cilium_clustermesh_remote_cluster_readiness_status{source_site=""}, "reader", "agent", "", "")'
             ' or label_replace(cilium_kvstoremesh_remote_cluster_readiness_status{source_site=""}, "reader", "kvstoremesh", "", "")'
             ') == bool 1)')
    # Readiness is the control plane only. In the partition drill (2026-11-07)
    # site 2 reached site 1's clustermesh-apiserver around its downed tunnel
    # (eth0, the LAN, the router) and stayed ready while pod traffic between
    # the sites failed. So the site's own probe of its peer (pod to pod,
    # across the mesh) must pass too; without one, readiness alone decides.
    probe = ('min by (k8s_cluster) (label_replace(homelab_peer_probe_success{source_site=""},'
             ' "k8s_cluster", "$1", "observer_cluster", "(.+)"))')
    mesh = f"3 - 2 * (({ready} * on (k8s_cluster) {probe}) or {ready})"
    expected = "max by (k8s_cluster) (homelab_expected_site_info)"
    hops = (("tunnel", tunnel), ("router-bgp", router_bgp), ("node-bgp", node_bgp), ("mesh", mesh))
    known = " or ".join(hop(n, e) for n, e in hops)
    unknown = " or ".join(hop(n, f"0 * {expected}") for n, _ in hops)
    # Which site the router sends each shared VIP to: its preferred site while
    # that site carries routes, else the other one while it does. A site
    # carries routes while its session is up and it sends the router at least
    # one prefix: the distro's FRR keeps the session up with the site's nodes
    # down, and announces nothing (site-loss drill, 2026-11-07). Inferred from
    # those and the declared local preference (100/50), not read from the
    # router's route table. 1 routed, 0 not, absent Unknown.
    session = 'min(homelab:network_path_state{{hop="router-bgp",k8s_cluster="{}"}} > 0)'
    sent = 'sum(mktxp_bgp_prefix_count{{name=~"{}(-[0-9]+)?"}})'
    peer = {s["cluster"]: s["router_peer"] for s in sites}

    def carries(site):
        return f"(({session.format(site)} == bool 1) * ({sent.format(peer[site])} > bool 0))"

    routes = []
    for vip in inventory()["vips"]:
        first = vip["preferred"]
        other = next(s["cluster"] for s in sites if s["cluster"] != first)
        for site, expr in ((first, carries(first)),
                           (other, f"{carries(other)} * ({carries(first)} != bool 1)")):
            routes.append(f'label_replace(label_replace({expr}, "k8s_cluster", "{site}", "", ""), "vip", "{vip["address"]}", "", "")')
    prefixes = " or ".join(per_site(
        f'sum(mktxp_bgp_prefix_count{{name=~"{s["router_peer"]}(-[0-9]+)?"}})', s) for s in sites)
    return {
        "homelab:network_path_state": f"({known}) or on (k8s_cluster, hop) ({unknown})",
        "homelab:router_bgp_prefixes": prefixes,
        "homelab:shared_vip_route": " or ".join(routes),
    }


def rule_resource():
    rules = [{"record": name, "expr": expr} for name, expr in records().items()]
    rules += [
        {"alert": "ObservabilityEvidenceStale",
         "expr": '(homelab_collection_success == 0) or (time() - homelab_collection_last_success_timestamp_seconds > 180)',
         "for": "5m", "labels": {"severity": "warning"},
         "annotations": {"summary": "A monitoring collector is no longer observing its upstream",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Scrape success does not establish fresh upstream evidence. Check the collector source and network access."}},
        {"alert": "ObservabilityExpectedDatabaseBackupMissing",
         # Each site only answers for its own primaries: the peer's databases
         # are Unknown here by design, and a replica cluster takes no backups.
         "expr": 'homelab:database_backup_state{k8s_cluster="${CLUSTER_NAME}"} == 0 unless on (k8s_cluster, namespace, db_cluster) (homelab:database_in_recovery == 1)',
         "for": "30m",
         "labels": {"severity": "warning"},
         "annotations": {"summary": "Expected database has no backup evidence",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-data?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "No Barman plugin timestamp is available for this expected database. Another database's fresh backup cannot satisfy its recovery requirement."}},
        {"alert": "ObservabilityArchiveUnavailable", "expr": 'probe_success{observability_target="archive"} == 0',
         "for": "10m", "labels": {"severity": "warning"},
         "annotations": {"summary": "The WSL-02 metrics archive is unavailable",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Local metrics and alerts remain available. Restore archive uploads before retained local blocks expire."}},
        {"alert": "ObservabilityIngressNameFailing", "expr": 'homelab:ingress_state == 3',
         "for": "15m", "labels": {"severity": "warning"},
         "annotations": {"summary": "A shared-VIP name fails from {{ $labels.k8s_cluster }}: {{ $labels.name }}",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-apps?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "That site's collector couldn't use the name as a client would: the router's DNS answer, the certificate, or the site's ingress routing the host. homelab_ingress_dns_match and homelab_ingress_http_status say which."}},
        {"alert": "ObservabilityIngressCertificateExpiring", "expr": 'homelab:ingress_cert_remaining_seconds < 14 * 86400',
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": "The certificate for {{ $labels.name }} on {{ $labels.k8s_cluster }} expires within 14 days",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-apps?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "cert-manager renews at 30 days left. Check that site's Certificate and its ACME challenge."}},
        {"alert": "ObservabilityArchiveBehind", "expr": 'homelab:archive_lag_seconds > 14400',
         "for": "30m", "labels": {"severity": "warning"},
         "annotations": {"summary": "The metrics archive's newest data for a site is over 4 hours old",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Normal is 1-3 hours. Check that site's Thanos sidecar uploads (thanos_shipper_upload_failures_total) and the Compactor's bucket sync."}},
        {"alert": "ObservabilityArchiveSpaceLow", "expr": 'homelab:archive_free_ratio < 0.1',
         "for": "1h", "labels": {"severity": "warning"},
         "annotations": {"summary": "The metrics archive has under 10% free space",
                         "dashboard_url": 'https://grafana.${INTERNAL_DOMAIN}/d/tnwks-observability?var-cluster={{ $labels.k8s_cluster }}',
                         "description": "Garage shares its disk with the WSL-02 node. Shorten the Compactor's retention or free space before uploads fail."}},
    ]
    return {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
            "metadata": {"name": "homelab-observability", "namespace": "monitoring"},
            "spec": {"groups": [{"name": "homelab.evidence", "interval": "1m", "rules": rules}]}}


def fixtures():
    from dashboards import coverage
    header = {panel.title: panel.expr for panel in coverage()}
    # Real cases: two same-name DBs in distinct sites, missing expected DB,
    # stale upstream despite successful scrape, and absent fence/restore evidence.
    return {"rule_files": ["rules-for-test.yaml"], "evaluation_interval": "1m", "tests": [
        {"name": "header counts distinct selected sites, not observers", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_site_info{{k8s_cluster="{site}",observer="{observer}"}}', "values": "1+0x10"}
               for site in ("one", "two") for observer in ("a", "b")],
             *[{"series": f'homelab:site_reporting{{k8s_cluster="{site}",observer="{observer}"}}', "values": "1+0x10"}
               for site in ("one", "two") for observer in ("a", "b")],
         ], "promql_expr_test": [
             {"expr": expr.replace("$cluster", scope), "eval_time": "5m",
              "exp_samples": [{"labels": "{}", "value": value}]}
             for expr in header.values() if expr in (header["Expected sites"], header["Reporting sites"])
             for scope, value in (("one", 1), (".*", 2))
         ]},
        {"name": "future observer timestamps are not current evidence", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="one"}', "values": "1+0x10"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "10000+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:site_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:site_state{k8s_cluster="one"}', "value": 0}]},
         ]},
        {"name": "stale complete prerequisites cannot certify recovery", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             *[{"series": f'homelab_recovery_prerequisite_state{{k8s_cluster="one",app="test",prerequisite="{name}"}}', "values": "1 _ _ _ _ _"}
               for name in ("database", "backup", "restore", "fence")],
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "4m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "one missing component does not leave a multi-workload app healthy", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test"}', "values": "1+0x10"},
             {"series": 'homelab_workload_present{k8s_cluster="one",app="test",workload="missing"}', "values": "0+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="test",workload="running"}', "values": "1+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="test",workload="running"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:workload_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "duplicate collectors do not invent desired replicas", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test"}', "values": "1+0x10"},
             *[{"series": f'homelab_workload_desired_replicas{{k8s_cluster="one",app="test",workload="running",pod="{pod}"}}', "values": "1+0x10"}
               for pod in ("old", "new")],
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="test",workload="running",pod="new"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:workload_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="test"}', "value": 1}]},
         ]},
        {"name": "same database name and missing backup remain distinct", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "values": "1+0x10"},
             {"series": 'homelab_expected_database_info{k8s_cluster="two",namespace="databases",db_cluster="app-pg"}', "values": "1+0x10"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "30+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:database_backup_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_backup_state{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "value": 1},
                 {"labels": 'homelab:database_backup_state{k8s_cluster="two",namespace="databases",db_cluster="app-pg"}', "value": 0},
             ]},
         ]},
        {"name": "successful scrape cannot refresh a stale upstream", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="one"}', "values": "1+0x10"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:site_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:site_state{k8s_cluster="one"}', "value": 0}]},
         ]},
        {"name": "recovery: Not applicable where the warm app runs; a parked site still needs evidence", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_workload_info{{k8s_cluster="{site}",app="test",tier="warm"}}', "values": "1+0x10"}
               for site in ("one", "two")],
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="test",kind="deployment",workload="test"}', "values": "1+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="two",app="test",kind="deployment",workload="test"}', "values": "0+0x10"},
             {"series": 'homelab_recovery_prerequisite_state{k8s_cluster="two",app="test",prerequisite="backup"}', "values": "3+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 5},
                 {"labels": 'homelab:recovery_state{k8s_cluster="two",app="test"}', "value": 3}]},
         ]},
        {"name": "absent fencing or restore evidence cannot certify readiness", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             {"series": 'homelab_recovery_prerequisite_state{k8s_cluster="one",app="test",prerequisite="database"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "stale probes cannot certify current paths", "interval": "1m",
         "input_series": [
             {"series": 'homelab_observer_info{k8s_cluster="one"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",observability_target="archive"}', "values": "1 _ _ _ _ _"},
         ], "promql_expr_test": [
             {"expr": "homelab:archive_state", "eval_time": "4m", "exp_samples": [
                 {"labels": 'homelab:archive_state{k8s_cluster="one"}', "value": 0}]},
         ]},
        {"name": "archive lag is per source site, and Unknown once the index goes stale", "interval": "1m",
         "input_series": [
             {"series": 'homelab_archive_newest_sample_timestamp_seconds{k8s_cluster="one",observer_cluster="a"}', "values": "0+0x2"},
             {"series": 'homelab_archive_newest_sample_timestamp_seconds{k8s_cluster="one",observer_cluster="b"}', "values": "-60+0x2"},
         ], "promql_expr_test": [
             # Two observers report the same archive; lag is per source site and
             # goes absent (Unknown) once the index stops being read.
             {"expr": "homelab:archive_lag_seconds", "eval_time": "2m", "exp_samples": [
                 {"labels": 'homelab:archive_lag_seconds{k8s_cluster="one"}', "value": 120}]},
             {"expr": "homelab:archive_lag_seconds", "eval_time": "6m", "exp_samples": []},
         ]},
        {"name": "network path by hop: stale tunnel, missing router session, partial node sessions", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl"}', "values": "1+0x10"},
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl-02"}', "values": "1+0x10"},
             {"series": 'mktxp_system_uptime{k8s_cluster="homelab-wsl"}', "values": "100+60x10"},
             {"series": 'mktxp_last_handshake{interface="wg-site1"}', "values": "30+0x10"},
             {"series": 'mktxp_last_handshake{interface="wg-site2"}', "values": "400+0x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site1-1"}', "values": "1+0x10"},
             {"series": 'cilium_bgp_control_plane_session_state{k8s_cluster="homelab-wsl",node="a"}', "values": "1+0x10"},
             {"series": 'cilium_bgp_control_plane_session_state{k8s_cluster="homelab-wsl",node="b"}', "values": "0+0x10"},
             {"series": 'cilium_clustermesh_remote_cluster_readiness_status{k8s_cluster="homelab-wsl",target_cluster="homelab-wsl-02"}', "values": "1+0x10"},
             {"series": 'cilium_kvstoremesh_remote_cluster_readiness_status{k8s_cluster="homelab-wsl",target_cluster="homelab-wsl-02"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:network_path_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="tunnel"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="tunnel"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="router-bgp"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="router-bgp"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="node-bgp"}', "value": 2},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="node-bgp"}', "value": 0},
                 # The agents see the peer, KVStoreMesh doesn't: failed, not averaged.
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="mesh"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="mesh"}', "value": 0},
             ]},
         ]},
        {"name": "mesh hop: ready but the peer probe fails is Failed; no probe falls back to readiness", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl"}', "values": "1+0x10"},
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl-02"}', "values": "1+0x10"},
             # Both sites' control planes ready.
             *[{"series": f'cilium_{kind}_remote_cluster_readiness_status{{k8s_cluster="{site}",target_cluster="x"}}', "values": "1+0x10"}
               for site in ("homelab-wsl", "homelab-wsl-02") for kind in ("clustermesh", "kvstoremesh")],
             # Site 2's probe of site 1 fails (partitioned, pod traffic down);
             # site 1 has no probe series at all.
             {"series": 'homelab_peer_probe_success{k8s_cluster="homelab-wsl",observer_cluster="homelab-wsl-02"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": 'homelab:network_path_state{hop="mesh"}', "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="mesh"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="mesh"}', "value": 3},
             ]},
         ]},
        {"name": "shared VIPs follow the preferred site's session, else the other's", "interval": "1m",
         "input_series": [
             {"series": 'mktxp_system_uptime{k8s_cluster="homelab-wsl"}', "values": "100+60x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site2-1"}', "values": "1+0x10"},
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site1-1"}', "values": "7+0x10"},
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site2-1"}', "values": "7+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:shared_vip_route", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.10"}', "value": 0},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.10"}', "value": 1},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.200"}', "value": 0},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.200"}', "value": 1},
             ]},
         ]},
        {"name": "shared VIPs: a site whose session is up but sends no prefixes carries nothing", "interval": "1m",
         "input_series": [
             {"series": 'mktxp_system_uptime{k8s_cluster="homelab-wsl"}', "values": "100+60x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site1-1"}', "values": "1+0x10"},
             {"series": 'mktxp_bgp_established{name="wsl-site2-1"}', "values": "1+0x10"},
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site1-1"}', "values": "7+0x10"},
             # Site 2's nodes are down; its distro FRR still holds the session.
             {"series": 'mktxp_bgp_prefix_count{name="wsl-site2-1"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:shared_vip_route", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.10"}', "value": 1},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.10"}', "value": 0},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl",vip="10.10.95.200"}', "value": 1},
                 {"labels": 'homelab:shared_vip_route{k8s_cluster="homelab-wsl-02",vip="10.10.95.200"}', "value": 0},
             ]},
         ]},
        {"name": "ingress names: passing, failing, unobserved", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_ingress_info{{k8s_cluster="{site}",name="{name}"}}', "values": "1+0x10"}
               for site in ("one", "two") for name in ("a", "b")],
             {"series": 'probe_success{k8s_cluster="one",name="a",observability_target="ingress"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",name="b",observability_target="ingress"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:ingress_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:ingress_state{k8s_cluster="one",name="a"}', "value": 1},
                 {"labels": 'homelab:ingress_state{k8s_cluster="one",name="b"}', "value": 3},
                 {"labels": 'homelab:ingress_state{k8s_cluster="two",name="a"}', "value": 0},
                 {"labels": 'homelab:ingress_state{k8s_cluster="two",name="b"}', "value": 0},
             ]},
         ]},
        {"name": "an unobserved router leaves the router hops Unknown", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_site_info{k8s_cluster="homelab-wsl"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": 'homelab:network_path_state{hop=~"tunnel|router-bgp"}', "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="tunnel"}', "value": 0},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="router-bgp"}', "value": 0},
             ]},
         ]},
        {"name": "service state: probed, failing, standby, unobserved", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_workload_info{{k8s_cluster="one",app="{a}"}}', "values": "1+0x10"}
               for a in ("up", "broken", "parked", "unseen")],
             {"series": 'probe_success{k8s_cluster="one",app="up",observability_target="service"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",app="broken",observability_target="service"}', "values": "0+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:service_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="up"}', "value": 1},
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="broken"}', "value": 3},
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="parked"}', "value": 4},
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="unseen"}', "value": 0},
             ]},
         ]},
        {"name": "duplicate observations of one prerequisite are not four prerequisites", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             *[{"series": f'homelab_recovery_prerequisite_state{{k8s_cluster="one",app="test",prerequisite="database",observer="{i}"}}', "values": "1+0x10"} for i in range(4)],
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 0}]},
         ]},
        {"name": "workload state tells standby from failed and keeps missing apps Unknown", "interval": "1m",
         "input_series": [
             *[{"series": f'homelab_expected_workload_info{{k8s_cluster="one",app="{a}"}}', "values": "1+0x10"}
               for a in ("up", "partial", "down", "parked", "missing")],
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="up"}', "values": "2+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="up"}', "values": "2+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="partial"}', "values": "2+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="partial"}', "values": "1+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="down"}', "values": "1+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="down"}', "values": "0+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
             {"series": 'homelab_workload_ready_replicas{k8s_cluster="one",app="parked"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:workload_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="up"}', "value": 1},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="partial"}', "value": 2},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="down"}', "value": 3},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="parked"}', "value": 4},
                 {"labels": 'homelab:workload_state{k8s_cluster="one",app="missing"}', "value": 0},
             ]},
         ]},
        {"name": "only a local primary without backup evidence alerts", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="missing-pg"}', "values": "1+0x40"},
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="replica-pg"}', "values": "1+0x40"},
             {"series": 'homelab_expected_database_info{k8s_cluster="two",namespace="databases",db_cluster="peer-pg"}', "values": "1+0x40"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="replica-pg-1"}', "values": "1+0x40"},
         ], "alert_rule_test": [
             {"alertname": "ObservabilityExpectedDatabaseBackupMissing", "eval_time": "35m", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": "one", "namespace": "databases", "db_cluster": "missing-pg"},
                  "exp_annotations": {"summary": "Expected database has no backup evidence",
                                      "dashboard_url": "https://grafana.${INTERNAL_DOMAIN}/d/tnwks-data?var-cluster=one",
                                      "description": "No Barman plugin timestamp is available for this expected database. Another database's fresh backup cannot satisfy its recovery requirement."}},
             ]},
         ]},
        {"name": "future backup evidence is unknown and peer federation is not counted twice", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "values": "1+0x10"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "1000+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "0+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="app-pg-1",source_site="one"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:database_backup_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_backup_state{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "value": 0}]},
             {"expr": "homelab:database_in_recovery", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_in_recovery{k8s_cluster="one",namespace="databases",db_cluster="app-pg"}', "value": 0}]},
         ]},
        {"name": "an idle database is not a failed one", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="idle-pg"}', "values": "1+0x40"},
             {"series": 'cnpg_backends_total{k8s_cluster="one",namespace="databases",pod="idle-pg-1"}', "values": "0+0x40"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="idle-pg-1"}', "values": "0+0x40"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="idle-pg-1"}', "values": "100+0x40"},
         ], "promql_expr_test": [
             {"expr": "homelab:database_connections", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_connections{k8s_cluster="one",namespace="databases",db_cluster="idle-pg"}', "value": 0}]},
             {"expr": "homelab:database_backup_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:database_backup_state{k8s_cluster="one",namespace="databases",db_cluster="idle-pg"}', "value": 1}]},
         ], "alert_rule_test": [
             {"alertname": "ObservabilityExpectedDatabaseBackupMissing", "eval_time": "35m", "exp_alerts": []},
         ]},
        {"name": "no traffic is not an outage: the probe decides", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="quiet"}', "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",app="quiet",observability_target="service"}', "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:service_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:service_state{k8s_cluster="one",app="quiet"}', "value": 1}]},
         ]},
        {"name": "an expiring credential alerts before cert-manager's renewal is missed", "interval": "5m",
         "input_series": [
             {"series": 'homelab_ingress_cert_expiry_timestamp_seconds{k8s_cluster="one",name="soon"}', "values": "864000+0x20"},
             {"series": 'homelab_ingress_cert_expiry_timestamp_seconds{k8s_cluster="one",name="later"}', "values": "3456000+0x20"},
         ], "alert_rule_test": [
             {"alertname": "ObservabilityIngressCertificateExpiring", "eval_time": "70m", "exp_alerts": [
                 {"exp_labels": {"severity": "warning", "k8s_cluster": "one", "name": "soon"},
                  "exp_annotations": {"summary": "The certificate for soon on one expires within 14 days",
                                      "dashboard_url": "https://grafana.${INTERNAL_DOMAIN}/d/tnwks-apps?var-cluster=one",
                                      "description": "cert-manager renews at 30 days left. Check that site's Certificate and its ACME challenge."}},
             ]},
         ]},
        {"name": "a failed restore drill fails recovery even with everything else ready", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_workload_info{k8s_cluster="one",app="test",tier="warm"}', "values": "1+0x10"},
             *[{"series": f'homelab_recovery_prerequisite_state{{k8s_cluster="one",app="test",prerequisite="{name}"}}', "values": f"{value}+0x10"}
               for name, value in (("database", 1), ("backup", 1), ("fence", 1), ("restore", 3))],
         ], "promql_expr_test": [
             {"expr": "homelab:recovery_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:recovery_state{k8s_cluster="one",app="test"}', "value": 3}]},
         ]},
        *objective_fixtures(),
        *other_database_fixtures(),
        *house_fixtures(),
    ]}


def house_fixtures():
    own = {"series": 'homelab_observer_info{k8s_cluster="one",observer_cluster="one"}', "values": "1+0x10"}

    def state(domain, value):
        return {"labels": f'homelab:house_state{{domain="{domain}"}}', "value": value}

    service = 'homelab_expected_workload_info{k8s_cluster="%s",app="%s"}'
    return [
        {"name": "household view: this site's share, worst app, partial cameras", "interval": "1m",
         "input_series": [
             own,
             {"series": 'probe_success{k8s_cluster="one",observability_target="internet"}', "values": "1+0x10"},
             {"series": 'homelab_observer_info{k8s_cluster="one",observer_cluster="one",job="x"}', "values": "1+0x10"},
             # Home Assistant runs here; a failed app here; a standby app; the
             # other site's apps (Unknown from here) don't count.
             {"series": 'probe_success{k8s_cluster="one",observability_target="service",app="home-assistant"}', "values": "1+0x10"},
             {"series": service % ("one", "home-assistant"), "values": "1+0x10"},
             {"series": 'probe_success{k8s_cluster="one",observability_target="service",app="broken"}', "values": "0+0x10"},
             {"series": service % ("one", "broken"), "values": "1+0x10"},
             {"series": service % ("two", "elsewhere"), "values": "1+0x10"},
             # Three cameras worked today, two do now, one has been off all along.
             {"series": 'frigate_camera_fps{camera="a"}', "values": "5+0x10"},
             {"series": 'frigate_camera_fps{camera="b"}', "values": "5+0x10"},
             {"series": 'frigate_camera_fps{camera="c"}', "values": "5 5 0 0 0 0 0 0 0 0 0"},
             {"series": 'frigate_camera_fps{camera="off"}', "values": "0+0x10"},
             # Every AP that was up is up.
             {"series": 'unpoller_device_uptime_seconds{name="ap1"}', "values": "100+60x10"},
             {"series": 'unpoller_device_uptime_seconds{name="ap2",source_site="two"}', "values": "100+60x10"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "1+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "0+60x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:house_state", "eval_time": "5m", "exp_samples": [
                 state("apps", 3), state("automation", 1), state("cameras", 2),
                 state("internet", 1), state("monitoring", 1), state("wifi", 1)]},
         ]},
        {"name": "household view: nothing working, and no part in a domain", "interval": "1m",
         "input_series": [
             own,
             {"series": 'probe_success{k8s_cluster="one",observability_target="internet"}', "values": "0+0x10"},
             {"series": 'frigate_camera_fps{camera="a"}', "values": "5 5 0 0 0 0 0 0 0 0 0"},
             # The AP is reported, and down.
             {"series": 'unpoller_device_uptime_seconds{name="ap1"}', "values": "100 160 0 0 0 0 0 0 0 0 0"},
             {"series": 'homelab_collection_success{k8s_cluster="one",collector="workloads"}', "values": "0+0x10"},
             {"series": 'homelab_collection_last_success_timestamp_seconds{k8s_cluster="one",collector="workloads"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             # No apps here: nothing failing. No Home Assistant here: no row.
             {"expr": "homelab:house_state", "eval_time": "10m", "exp_samples": [
                 state("apps", 1), state("cameras", 3), state("internet", 3), state("monitoring", 3),
                 state("wifi", 3)]},
         ]},
        {"name": "household view: a blind source is Unknown, not Failed", "interval": "1m",
         "input_series": [
             own,
             # Both exporters stop reporting: the site that runs UniFi Poller is
             # lost, Frigate's scrape fails. What they watch may be fine.
             {"series": 'frigate_camera_fps{camera="a"}', "values": "5 5 _ _ _ _ _ _ _ _ _"},
             {"series": 'unpoller_device_uptime_seconds{name="ap1",source_site="two"}', "values": "100 160 _ _ _ _ _ _ _ _ _"},
         ], "promql_expr_test": [
             {"expr": 'homelab:house_state{domain=~"wifi|cameras"}', "eval_time": "10m", "exp_samples": []},
         ]},
    ]


def other_database_fixtures():
    info = 'homelab_expected_other_database_info{k8s_cluster="one",observer_cluster="one",namespace="db",workload="%s",kind="%s",engine="e",app="a"}'
    return [
        {"name": "other databases: ready, failed, standby, unobserved", "interval": "1m",
         "input_series": [
             {"series": info % ("ok", "statefulset"), "values": "1+0x10"},
             {"series": 'kube_statefulset_replicas{k8s_cluster="one",namespace="db",statefulset="ok"}', "values": "1+0x10"},
             {"series": 'kube_statefulset_status_replicas_ready{k8s_cluster="one",namespace="db",statefulset="ok"}', "values": "1+0x10"},
             {"series": info % ("down", "deployment"), "values": "1+0x10"},
             {"series": 'kube_deployment_spec_replicas{k8s_cluster="one",namespace="db",deployment="down"}', "values": "1+0x10"},
             {"series": 'kube_deployment_status_replicas_available{k8s_cluster="one",namespace="db",deployment="down"}', "values": "0+0x10"},
             {"series": info % ("standby", "statefulset"), "values": "1+0x10"},
             {"series": 'kube_statefulset_replicas{k8s_cluster="one",namespace="db",statefulset="standby"}', "values": "0+0x10"},
             {"series": 'kube_statefulset_status_replicas_ready{k8s_cluster="one",namespace="db",statefulset="standby"}', "values": "0+0x10"},
             {"series": info % ("missing", "statefulset"), "values": "1+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:other_database_state", "eval_time": "5m", "exp_samples": [
                 {"labels": f'homelab:other_database_state{{k8s_cluster="one",namespace="db",workload="{w}",engine="e",app="a"}}', "value": v}
                 for w, v in (("down", 3), ("missing", 0), ("ok", 1), ("standby", 4))]},
         ]},
    ]


def objective_fixtures():
    sched = 'homelab_app_backup_scheduled{k8s_cluster="one",observer_cluster="one",namespace="apps",app="%s",pvc="%s",policy="%s"}'
    kopiur = 'kopiur_policy_last_backup_success_timestamp_seconds{k8s_cluster="one",namespace="apps",policy="%s"}'
    objective = 'homelab_app_recovery_objective_seconds{k8s_cluster="one",observer_cluster="one",app="%s",objective="%s"}'
    return [
        {"name": "an app's backup age is its oldest piece, and a piece without evidence is Unknown", "interval": "1m",
         "input_series": [
             # fresh: one PVC 100 s old and a primary database 200 s old.
             {"series": sched % ("fresh", "data", "fresh"), "values": "1+0x10"},
             {"series": kopiur % "fresh", "values": "200+0x10"},
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="fresh-pg",app="fresh"}', "values": "1+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="fresh-pg-1"}', "values": "0+0x10"},
             {"series": 'barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{k8s_cluster="one",namespace="databases",pod="fresh-pg-1"}', "values": "100+0x10"},
             {"series": objective % ("fresh", "rpo"), "values": "1000+0x10"},
             # stale: past its objective.
             {"series": sched % ("stale", "data", "stale"), "values": "1+0x10"},
             {"series": kopiur % "stale", "values": "0+0x10"},
             {"series": objective % ("stale", "rpo"), "values": "60+0x10"},
             # partial: one of two scheduled PVCs has never backed up.
             {"series": sched % ("partial", "a", "partial-a"), "values": "1+0x10"},
             {"series": sched % ("partial", "b", "partial-b"), "values": "1+0x10"},
             {"series": kopiur % "partial-a", "values": "250+0x10"},
             {"series": objective % ("partial", "rpo"), "values": "1000+0x10"},
             # standby: suspended here, so no row at all.
             {"series": sched % ("standby", "data", "standby"), "values": "0+0x10"},
             {"series": objective % ("standby", "rpo"), "values": "1000+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:app_backup_age_seconds", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_backup_age_seconds{k8s_cluster="one",app="fresh"}', "value": 200},
                 {"labels": 'homelab:app_backup_age_seconds{k8s_cluster="one",app="partial"}', "value": 50},
                 {"labels": 'homelab:app_backup_age_seconds{k8s_cluster="one",app="stale"}', "value": 300}]},
             {"expr": "homelab:app_rpo_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="fresh"}', "value": 1},
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="partial"}', "value": 0},
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="stale"}', "value": 3}]},
         ]},
        {"name": "a primary without backup evidence is Unknown; a replica's missing backup isn't counted", "interval": "1m",
         "input_series": [
             {"series": 'homelab_expected_database_info{k8s_cluster="one",namespace="databases",db_cluster="app-pg",app="app"}', "values": "1+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="one",namespace="databases",pod="app-pg-1"}', "values": "0+0x10"},
             {"series": 'homelab_expected_database_info{k8s_cluster="two",namespace="databases",db_cluster="app-pg",app="app"}', "values": "1+0x10"},
             {"series": 'cnpg_pg_replication_in_recovery{k8s_cluster="two",namespace="databases",pod="app-pg-1"}', "values": "1+0x10"},
             {"series": 'homelab_app_recovery_objective_seconds{k8s_cluster="one",app="app",objective="rpo"}', "values": "1000+0x10"},
             {"series": 'homelab_app_recovery_objective_seconds{k8s_cluster="two",app="app",objective="rpo"}', "values": "1000+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:app_rpo_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_rpo_state{k8s_cluster="one",app="app"}', "value": 0}]},
         ]},
        {"name": "restore time: within, past, Unknown without a drill, Not applicable where the app runs", "interval": "1m",
         "input_series": [
             {"series": 'homelab_restore_last_duration_seconds{k8s_cluster="one",observer_cluster="one",app="quick"}', "values": "30+0x10"},
             {"series": objective % ("quick", "rto"), "values": "3600+0x10"},
             {"series": 'homelab_restore_last_duration_seconds{k8s_cluster="one",observer_cluster="one",app="slow"}', "values": "7200+0x10"},
             {"series": objective % ("slow", "rto"), "values": "3600+0x10"},
             {"series": objective % ("untested", "rto"), "values": "3600+0x10"},
             {"series": objective % ("active", "rto"), "values": "3600+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="active",kind="deployment",workload="active"}', "values": "1+0x10"},
             {"series": 'homelab_workload_desired_replicas{k8s_cluster="one",app="untested",kind="deployment",workload="untested"}', "values": "0+0x10"},
         ], "promql_expr_test": [
             {"expr": "homelab:app_rto_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="quick"}', "value": 1},
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="slow"}', "value": 3},
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="untested"}', "value": 0},
                 {"labels": 'homelab:app_rto_state{k8s_cluster="one",app="active"}', "value": 5}]},
         ]},
    ]


def generate():
    resource = rule_resource()
    result = {
        ROOT / "kubernetes/apps/monitoring/observability-evidence/rules/rules.yaml": resource,
        ROOT / "observability/generated/rules-for-test.yaml": {"groups": resource["spec"]["groups"]},
        ROOT / "observability/generated/rule-tests.yaml": fixtures(),
    }
    for path, data in result.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\n" + yaml.safe_dump(data, sort_keys=False, width=110))


if __name__ == "__main__":
    generate()
