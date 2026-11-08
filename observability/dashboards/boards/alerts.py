"""Alerts (L2): What is firing now, and can I reach its evidence and runbook?"""

from __future__ import annotations

from ..design import NO_ALERTS
from ..model import Board, p
from ..queries import alerts, metric, selector


def board() -> Board:
    return Board("tnwks-alerts", "Alerts", "observability", "l2",
              "What is firing now, and can I reach its evidence and runbook?", {
            "Current": [
                p("Firing now", alerts("firing"), "short",
                  "Current Prometheus firing state, independent of the dashboard historical time selection, excluding heartbeats. Follow the rule's dashboard/runbook annotation for diagnosis.",
                  "table", empty=NO_ALERTS, column=None),
                p("Pending now", alerts("pending"), "short",
                  "Pending is not yet firing. Alertmanager silencing/inhibition and notification state are viewed through the per-site Alertmanager links.",
                  "table", empty=NO_ALERTS, column=None),
            ],
            "Alertmanager": [
                p("Alerts in Alertmanager", f'sum by (k8s_cluster, state) ({selector("alertmanager_alerts")})', "short",
                  "Alerts each site's Alertmanager holds: active, or suppressed by a silence or inhibition. Compare with Firing now: an alert firing in Prometheus but missing here never reached Alertmanager."),
                p("Active silences", f'max by (k8s_cluster) ({selector("alertmanager_silences", "state=\"active\"")})', "short",
                  "Silences in force. A forgotten silence hides real alerts; check each one has an end time and a reason.", "stat"),
                p("Configuration loaded", f'min by (k8s_cluster) ({selector("alertmanager_config_last_reload_successful")})', "bool",
                  "Whether each Alertmanager's last configuration reload succeeded. A failed reload keeps the previous routes and receivers.", "stat", words=("Loaded", "Failed")),
                p("Notifications sent", f'sum by (k8s_cluster, integration) (increase({selector("alertmanager_notifications_total")}[1h]))', "short",
                  "Notification attempts per integration in the last hour."),
                p("Failed notifications", f'sum by (k8s_cluster) (increase({metric("alertmanager_notifications_failed_total")}[$__range]))', "failures",
                  "Delivery failures, not a definitive end-to-end notification receipt check. Counted over the selected range, per site; anything but zero is red.", "stat"),
            ],
        }, "Observability", sections=True)
