"""Databases & Backups (L2): Are databases healthy, and can each application's data actually be recovered?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import WINDOWS, metric, selector


def board() -> Board:
    return Board("tnwks-data", "Databases & Backups", "data", "l2",
              "Are databases healthy, and can each application's data actually be recovered?", {
            "Databases": [
                p("Expected databases", metric("homelab_expected_database_info"), "short",
                  "Expected database inventory by site/namespace/db_cluster. The Kubernetes site label is never overloaded with the CNPG name.", "table", column=None),
                p("CNPG role: in recovery", metric("homelab:database_in_recovery"), "bool",
                  "PostgreSQL's observed recovery state. Zero means primary role, not fencing proof. Same-name databases at two sites stay distinct.", "table", words=("Replica", "Primary"), per_site="db_cluster"),
                p("Connections", f'sum by (k8s_cluster, db_cluster) ({metric("homelab:database_connections")})', "short",
                  "Observed database connections; compare against the configured limit and wait/transaction evidence."),
                p("Replay timestamp lag", f'max by (k8s_cluster, db_cluster) ({selector("homelab:database_replay_lag_seconds")})', "s",
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
                  "Barman plugin's last successful base backup per database. The warning threshold is 30h; a missing metric is separately reported as missing coverage.", "table", per_site="db_cluster"),
                p("Backup coverage", metric("homelab:database_backup_state"), "state",
                  "Expected database left join observed Barman evidence. Missing series cannot be hidden by another database's fresh backup.", "table", per_site="db_cluster"),
                p("WAL failures", f'sum by (k8s_cluster) (increase({metric("cnpg_pg_stat_archiver_failed_count")}[$__range]))', "failures",
                  "Failed WAL archiving attempts. Idle WAL age alone is not a failure. Counted over the selected range, per site; anything but zero is red.", "stat"),
                p("Weekly PVC drill age", f'time() - {metric("kube_cronjob_status_last_successful_time", "namespace=\"kopiur-system\",cronjob=\"standby-restore\"")}', "s",
                  "The existing weekly job covers its configured PVC entries only. This does not certify every app or a database restore.", "table"),
            ],
            "Objectives": [
                p("Recovery point by app", metric("homelab:app_rpo_state"), "state",
                  "Per app and site: is its oldest newest-backup, over every scheduled PVC and every primary database, within its recovery point objective (inventory.py OBJECTIVES: 30 h, a daily backup plus slack)? Unknown when a piece has no backup evidence. A site where nothing is scheduled, like a standby, shows a dash.",
                  "table", per_site="app"),
                p("Backup age by app", metric("homelab:app_backup_age_seconds"), "s",
                  "The oldest piece's backup age per app and site: the data an unplanned restore would lose, at most.",
                  "table", per_site="app"),
                p("Restore time by app", metric("homelab:app_rto_state"), "state",
                  "Per warm app with PVCs: did its last standby restore drill write the data back within the restore time objective (1 h)? Measured on the standby; Not applicable where the app runs. Unknown until a drill reports how long it took. This is the data restore, not the whole failover.",
                  "table", per_site="app"),
                p("Last drill duration", f'max by (k8s_cluster, app) ({selector("homelab_restore_last_duration_seconds")})', "s",
                  "From each Restore's creation to its Ready condition, the slowest PVC of the app's last successful drill.",
                  "table", per_site="app"),
                p("PVCs without a backup schedule", f'max by (app, pvc) ({selector("homelab_app_backup_scheduled")}) == 0', "short",
                  "PVCs the failover catalogue moves that no site has a running backup schedule for: a suspended schedule, or no SnapshotPolicy covering the PVC. Their recovery point is unbounded.",
                  "table", empty="Every PVC is scheduled on a site"),
            ],
            "Storage": [
                p("Windows disk free", metric("windows_logical_disk_free_bytes", 'volume=~"C:|E:"'), "bytes",
                  "Physical Windows free capacity. WSL's virtual disk maximum is not an independent physical capacity budget.", link=WINDOWS),
            ],
        }, "Data", sections=True)
