"""Generate normalized evidence rules and executable PromQL behavior fixtures."""

from pathlib import Path
import yaml

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
        "homelab:internet_state": '(3 - 2 * min by (k8s_cluster) (probe_success{observability_target="internet"} and (time() - timestamp(probe_success{observability_target="internet"})) < 180)) or on (k8s_cluster) (0 * max by (k8s_cluster) (homelab_observer_info))',
        # Replica evidence, not an end-to-end path: 1 healthy, 2 degraded, 3 failed,
        # 4 standby (desired 0, the normal state of an app on the other site).
        "homelab:workload_state": (
            f'({desired} == 0) + 4'
            f' or ((({ready} >= {desired}) * 0) + 1)'
            f' or ((({ready} < {desired}) > 0) * 0 + 2)'
            f' or (({ready} < {desired}) * 0 + 3)'
            ' or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))'),
        "homelab:service_state": '(3 - 2 * min by (k8s_cluster, app) (probe_success{observability_target="service"} and (time() - timestamp(probe_success{observability_target="service"})) < 180)) or on (k8s_cluster, app) (0 * max by (k8s_cluster, app) (homelab_expected_workload_info))',
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
