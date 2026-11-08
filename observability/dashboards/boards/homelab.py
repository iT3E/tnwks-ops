"""Homelab (L1): What is affected, which evidence is available, and where should I look next?"""

from __future__ import annotations

from ..design import NO_ALERTS
from ..model import Board, p
from ..queries import alerts, metric, selector
from .common import coverage


def board() -> Board:
    return Board("home", "Homelab", "overview", "l1",
              "What is affected, which evidence is available, and where should I look next?", {
            "Now": coverage() + [
                p("Workloads needing attention", f'sort_desc({metric("homelab:workload_state")} != 4 != 1)', "state",
                  "Apps not fully ready, from replica counts. Empty means every app is ready or a deliberate standby. This is workload evidence, not an end-to-end path check.",
                  "table", "tnwks-sites", empty="All ready or standby"),
                p("Current firing alerts", alerts("firing"), "short",
                  "Current Prometheus rule state, excluding heartbeats; this is not a historical Alertmanager event list and does not show silence/inhibition state.",
                  "table", "tnwks-alerts", empty=NO_ALERTS, column=None),
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
        }, "Overview")
