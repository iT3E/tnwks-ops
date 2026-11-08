"""Observability (L2): Can the other dashboards be trusted, and is monitoring itself sustainable?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import LONG_WINDOW_SECONDS, metric, per_slo, selector
from .common import coverage


def board() -> Board:
    return Board("tnwks-observability", "Observability", "observability", "l2",
              "Can the other dashboards be trusted, and is monitoring itself sustainable?", {
            "Trust": coverage() + [
                p("Collector source success", metric("homelab_collection_success"), "bool",
                  "Collector-specific upstream success. HTTP scrape success can coexist with a blind collector.", "table", column="Source", words=("OK", "Failing")),
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
        }, "Observability", sections=True)
