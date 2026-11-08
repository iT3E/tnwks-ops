"""Hosts & Capacity (L2): Can the physical hosts sustain the workload and a site failure?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import HOST, host_cpu_busy, metric


def board() -> Board:
    return Board("tnwks-hosts", "Hosts & Capacity", "hosts", "l2",
              "Can the physical hosts sustain the workload and a site failure?", {
            "Physical resources": [
                p("Host state", f'label_replace({metric("homelab:host_state")}, "host", "$1", "instance", "(.+)")', "state",
                  "Each host's resources, graded: CPU busy over 15 minutes (Degraded above 85%, Failed above 95%), RAM in use over 15 minutes (90%, 97%), "
                  "latency of the slowest busy volume (50 ms, 200 ms) and the fullest volume's free space (below 10%, 5%). "
                  "Unknown when the host's exporter is down. Click a row for that host's detail.",
                  "table", ("tnwks-host", {"host": "host"})),
                p("Windows available memory", metric("windows_memory_available_bytes"), "bytes",
                  "Physical host availability. Do not add this to WSL MemAvailable, which overlaps the same memory.", link=HOST),
                p("Host CPU busy", host_cpu_busy(), "percentunit",
                  "Share of each Windows host's logical processors doing work. Click a line for that host's CPU, memory, disk and network detail.", link=HOST),
                p("Host disk latency", f'max by (k8s_cluster, instance) (rate({metric("windows_logical_disk_read_write_latency_seconds_total", 'volume!~"HarddiskVolume.+"')}[$__rate_interval])'
                  f' / (rate({metric("windows_logical_disk_reads_total", 'volume!~"HarddiskVolume.+"')}[$__rate_interval]) + rate({metric("windows_logical_disk_writes_total", 'volume!~"HarddiskVolume.+"')}[$__rate_interval])))', "s",
                  "Average time per disk operation on each host's slowest volume. An SSD normally answers in under 5 ms; sustained above 20 ms the disk is what's slow. Click a line for the host's detail.", link=HOST),
                p("Host memory in use", f'1 - max by (k8s_cluster, instance) ({metric("windows_memory_available_bytes")}) / max by (k8s_cluster, instance) ({metric("windows_memory_physical_total_bytes")})', "percentunit",
                  "Share of each host's RAM not available to new work (standby cache counts as available). Near 100% with steady paging the host is short of memory. Click a line for its memory detail, commit charge included.", link=HOST),
                p("Windows disk free", metric("windows_logical_disk_free_bytes", 'volume=~"C:|E:"'), "bytes",
                  "Physical free space remains authoritative even if an ext4 VHDX reports much more virtual space.", "table"),
                p("Disk temperature", metric("smartctl_device_temperature"), "celsius",
                  "SMART device temperature where published. Missing SMART data is a collection gap, not a passing disk test."),
            ],
            "Survivor fit": [
                p("Required app replicas", f'sum by (k8s_cluster, app) ({metric("homelab_workload_desired_replicas")})', "short",
                  "Current placement demand. Full survivor-fit evidence also needs measured requests/peaks, storage placement and pinned hardware.", "table", per_site="app"),
                p("Recovery readiness", metric("homelab:recovery_state"), "state",
                  "Per warm app: could this site take it over (database, backup, restore drill, fence)? Not applicable where the app runs. Unknown until every prerequisite has evidence. This page does not promise all services fit or fail over.", "table", per_site="app"),
            ],
        }, "Hosts", sections=True)
