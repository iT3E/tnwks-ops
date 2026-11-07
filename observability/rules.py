"""Generate normalized evidence rules and executable PromQL behavior fixtures."""

from pathlib import Path
import yaml

from inventory import inventory

ROOT = Path(__file__).resolve().parents[1]


def records():
    db = "k8s_cluster, namespace, db_cluster"
    expected_db = f"max by ({db}) (homelab_expected_database_info)"
    desired = "sum by (k8s_cluster, app) (homelab_workload_desired_replicas)"
    ready = "sum by (k8s_cluster, app) (homelab_workload_ready_replicas)"
    return {
        "homelab:site_reporting": '(max by (k8s_cluster) (homelab_collection_success{collector="workloads"}) == bool 1) * on (k8s_cluster) (time() - max by (k8s_cluster) (homelab_collection_last_success_timestamp_seconds{collector="workloads"}) < bool 180)',
        "homelab:site_state": '(homelab:site_reporting == 1) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_expected_site_info))',
        "homelab:database_in_recovery": f'max by ({db}) (label_replace(cnpg_pg_replication_in_recovery{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_replay_lag_seconds": f'max by ({db}) (label_replace(cnpg_pg_replication_lag{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_connections": f'sum by ({db}) (label_replace(cnpg_backends_total{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_backup_age_seconds": f'time() - max by ({db}) (label_replace(barman_cloud_cloudnative_pg_io_last_available_backup_timestamp{{source_site=""}}, "db_cluster", "$1", "pod", "(.+)-[0-9]+"))',
        "homelab:database_backup_state": f'((homelab:database_backup_age_seconds >= bool 0) * (homelab:database_backup_age_seconds <= bool 108000) + 3 * (homelab:database_backup_age_seconds > bool 108000)) or on ({db}) (0 * {expected_db})',
        "homelab:recovery_state": '(3 * max by (k8s_cluster, app) (homelab_recovery_prerequisite_state == 3) / 3) or on (k8s_cluster, app) ((count by (k8s_cluster, app) (max by (k8s_cluster, app, prerequisite) (homelab_recovery_prerequisite_state{prerequisite=~"database|backup|restore|fence"}) == 1) == 4) / 4) or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info{tier="warm"}))',
        "homelab:local_history_seconds": 'time() - min by (k8s_cluster) (prometheus_tsdb_lowest_timestamp_seconds)',
        "homelab:unuploaded_block_age_seconds": 'time() - max by (k8s_cluster) (thanos_objstore_bucket_last_successful_upload_time{job=~".*thanos-discovery"} > 0)',
        "homelab:archive_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="archive"} and (time() - timestamp(probe_success{observability_target="archive"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        # The broker accepted the collector's login (MQTT), from each site. A
        # global Service: either site's check reaches the broker wherever it runs.
        "homelab:mqtt_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="mqtt"} and (time() - timestamp(probe_success{observability_target="mqtt"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        "homelab:internet_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="internet"} and (time() - timestamp(probe_success{observability_target="internet"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        # Replica evidence, not an end-to-end path: 1 healthy, 2 degraded, 3 failed,
        # 4 standby (desired 0, the normal state of an app on the other site).
        "homelab:workload_state": (
            f'({desired} == 0) + 4'
            f' or ((({ready} >= {desired}) * 0) + 1)'
            f' or ((({ready} < {desired}) > 0) * 0 + 2)'
            f' or (({ready} < {desired}) * 0 + 3)'
            ' or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))'),
        **network_records(),
        # Content probe from the site that runs the app; Standby (4) where it's
        # deliberately at zero; Unknown where neither is observed.
        "homelab:service_state": '(3 - 2 * min by (k8s_cluster, app) (probe_success{observability_target="service"} and (time() - timestamp(probe_success{observability_target="service"})) < 180)) or on (k8s_cluster, app) (homelab:workload_state == 4) or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))',
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
    the dashboards keep the real answer.
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
    expected = "max by (k8s_cluster) (homelab_expected_site_info)"
    known = " or ".join(hop(n, e) for n, e in (("tunnel", tunnel), ("router-bgp", router_bgp), ("node-bgp", node_bgp)))
    unknown = " or ".join(hop(n, f"0 * {expected}") for n in ("tunnel", "router-bgp", "node-bgp"))
    prefixes = " or ".join(per_site(
        f'sum(mktxp_bgp_prefix_count{{name=~"{s["router_peer"]}(-[0-9]+)?"}})', s) for s in sites)
    return {
        "homelab:network_path_state": f"({known}) or on (k8s_cluster, hop) ({unknown})",
        "homelab:router_bgp_prefixes": prefixes,
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
    ]
    return {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
            "metadata": {"name": "homelab-observability", "namespace": "monitoring"},
            "spec": {"groups": [{"name": "homelab.evidence", "interval": "1m", "rules": rules}]}}


def fixtures():
    # Real cases: two same-name DBs in distinct sites, missing expected DB,
    # stale upstream despite successful scrape, and absent fence/restore evidence.
    return {"rule_files": ["rules-for-test.yaml"], "evaluation_interval": "1m", "tests": [
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
         ], "promql_expr_test": [
             {"expr": "homelab:network_path_state", "eval_time": "5m", "exp_samples": [
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="tunnel"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="tunnel"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="router-bgp"}', "value": 1},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="router-bgp"}', "value": 3},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl",hop="node-bgp"}', "value": 2},
                 {"labels": 'homelab:network_path_state{k8s_cluster="homelab-wsl-02",hop="node-bgp"}', "value": 0},
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
    ]}


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
