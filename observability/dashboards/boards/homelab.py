"""Homelab (L1): What is affected, which evidence is available, and where should I look next?

The domain wall is the navigation: one state per area, rolled up from the same
recorded states its board shows, each opening that board. Below it, the things
that need attention, by area, and the alerts firing now.
"""

from __future__ import annotations

from dataclasses import replace

from ..design import NO_ALERTS
from ..model import Board, p
from ..queries import ROW_BOARD, alerts, house, metric, selector, site_state, worst
from .common import coverage

ROLLUP = ("The worst of {what}: Failed, then Degraded, then Unknown, then Healthy. Standby and "
          "Not applicable don't count. Open it for the board that explains it.")

# (area, rolled-up query, what it covers, board)
DOMAINS = [
    ("Sites", worst(site_state(), metric("homelab:workload_state")),
     "each site's evidence and every app's replicas", "tnwks-sites"),
    ("Network", worst(metric("homelab:network_path_state")),
     "every hop between the router, BGP, the mesh and each site", "tnwks-network"),
    ("Internet", worst(metric("homelab:internet_state")), "each site's Internet probe", "tnwks-internet"),
    ("Wi-Fi", worst(house("wifi")), "the access points each site watches", "tnwks-wifi"),
    ("Kubernetes", worst(f'3 - 2 * {metric("homelab_flux_resource_ready")}'),
     "every Flux resource's Ready condition", "tnwks-kubernetes"),
    ("Hosts", worst(metric("homelab:host_state")),
     "each Windows host's CPU, memory in use, disk latency and free space", "tnwks-hosts"),
    ("Data", worst(metric("homelab:database_backup_state"), metric("homelab:app_rpo_state"),
                   metric("homelab:other_database_state")),
     "database backups, each app's recovery point and the databases outside CNPG", "tnwks-data"),
    ("Recovery", worst(metric("homelab:recovery_state"), metric("homelab:app_rto_state")),
     "each warm app's readiness on its standby and its restore time", "tnwks-sites"),
    ("Apps", worst(metric("homelab:service_state"), metric("homelab:ingress_state")),
     "every app's probe and every shared name", "tnwks-apps"),
    ("Home", worst(metric("homelab:service_state", 'app="home-assistant"'), metric("homelab:mqtt_state"),
                   house("cameras")),
     "Home Assistant, MQTT and the cameras", "tnwks-home"),
    ("Observability", worst(metric("homelab:archive_state"), f'3 - 2 * {metric("homelab_collection_success")}'),
     "the archive and every evidence collector", "tnwks-observability"),
]

# (area, state query, what isn't a problem, the board a row opens) for the attention list.
AREAS = [
    ("Site", site_state(), "!= 1", "tnwks-sites"),
    ("Workload", metric("homelab:workload_state"), "!= 1 != 4", "tnwks-sites"),
    ("Service", metric("homelab:service_state"), "!= 1 != 4", "tnwks-apps"),
    ("Name", metric("homelab:ingress_state"), "!= 1", "tnwks-apps"),
    ("Network path", metric("homelab:network_path_state"), "!= 1", "tnwks-network"),
    ("Flux resource", f'3 - 2 * {metric("homelab_flux_resource_ready")}', "!= 1", "tnwks-kubernetes"),
    ("Host", metric("homelab:host_state"), "!= 1", "tnwks-hosts"),
    ("Recovery", metric("homelab:recovery_state"), "!= 1 != 5", "tnwks-sites"),
    ("Restore time", metric("homelab:app_rto_state"), "!= 1 != 5", "tnwks-data"),
    ("Backups", metric("homelab:app_rpo_state"), "!= 1", "tnwks-data"),
    ("Database backup", metric("homelab:database_backup_state"), "!= 1", "tnwks-data"),
    ("Database", metric("homelab:other_database_state"), "!= 1 != 4", "tnwks-data"),
]
ATTENTION = "sort_desc(" + " or ".join(
    f'label_replace(label_replace(({query}) {healthy}, "area", "{area}", "", ""), "board", "{uid}", "", "")'
    for area, query, healthy, uid in AREAS) + ")"


def board() -> Board:
    wall = [p(area, query, "state", ROLLUP.format(what=what), "stat", link)
            for area, query, what, link in DOMAINS]
    evidence = [replace(panel, role="evidence") for panel in coverage()]
    return Board("home", "Homelab", "overview", "l1",
              "What is affected, which evidence is available, and where should I look next?", {
            "Now": wall + [
                p("Needs attention", ATTENTION, "state",
                  "Everything not healthy right now, by area: sites, workloads, services, names, network paths, Flux resources, hosts, recovery, restore time, backups and databases. Standby and Not applicable are left out. Empty means nothing needs you. A row opens its area's board.",
                  "table", ROW_BOARD, empty="Nothing needs attention"),
                p("Current firing alerts", alerts("firing"), "short",
                  "Current Prometheus rule state, excluding heartbeats; this is not a historical Alertmanager event list and does not show silence/inhibition state.",
                  "table", "tnwks-alerts", empty=NO_ALERTS, column=None, role="status"),
                *evidence,
            ],
            "Change and history": [
                p("Observed state changes", f'max by (k8s_cluster, app) (changes({selector("homelab:service_state")}[1h]))', "short",
                  "Sampled transitions in observed service state. This is supplementary evidence and can miss transitions during a monitoring gap."),
                p("Latest workload rollout", f'time() - {metric("homelab_workload_transition_timestamp_seconds")}', "s",
                  "Time since a workload Ready transition reported by Kubernetes. This is not necessarily its deployment or git revision time.", "table"),
                p("Available local history", metric("homelab:local_history_seconds"), "s",
                  "Observed earliest retained sample, not configured retention. New persistent storage accumulates history gradually.", "table"),
            ],
        }, "Overview", sections=True)
