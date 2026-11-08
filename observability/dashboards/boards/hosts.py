"""Hosts & Capacity (L2): Can the physical hosts sustain the workload and a site failure?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import WINDOWS, metric


def board() -> Board:
    return Board("tnwks-hosts", "Hosts & Capacity", "hosts", "l2",
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
                  "Per warm app: could this site take it over (database, backup, restore drill, fence)? Not applicable where the app runs. Unknown until every prerequisite has evidence. This page does not promise all services fit or fail over.", "table"),
            ],
        }, "Hosts", sections=True)
