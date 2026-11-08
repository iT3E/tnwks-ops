"""Host (L3): Is this host short of CPU, memory, disk or network, and which?

USE per resource: how busy it is (utilization), how much work waits for it
(saturation) and what fails (errors). Windows exporter for the host, Linux
pressure stall information (PSI) for the WSL VM every node on it shares.
"""

from __future__ import annotations

from ..model import Board, Variable, p
from ..queries import host, selector

VOLUMES = 'volume!~"HarddiskVolume.+"'
# The site this host runs: its WSL VM's pressure, read from the nodes on it.
ON_HOST = f'and on (k8s_cluster) max by (k8s_cluster) ({host("windows_cpu_logical_processor")})'


def pressure(resource: str, kind: str = "waiting") -> str:
    """Share of time tasks in the WSL VM stalled on a resource (every node reads the same kernel)."""
    return (f'max by (k8s_cluster) (rate({selector(f"node_pressure_{resource}_{kind}_seconds_total")}'
            f'[$__rate_interval])) {ON_HOST}')


def latency(kind: str) -> str:
    """Average seconds per operation over the window, per volume."""
    operations = {"read": "reads", "write": "writes"}[kind]
    return (f'max by (volume) (rate({host(f"windows_logical_disk_{kind}_latency_seconds_total", VOLUMES)}[$__rate_interval])'
            f' / rate({host(f"windows_logical_disk_{operations}_total", VOLUMES)}[$__rate_interval]))')


def board() -> Board:
    cores = f'max by (instance) ({host("windows_cpu_logical_processor")})'
    return Board("tnwks-host", "Host", "hosts", "l3",
              "Is this host short of CPU, memory, disk or network, and which?", {
            "CPU": [
                p("CPU busy", f'1 - avg by (instance) (rate({host("windows_cpu_time_total", 'mode="idle"')}[$__rate_interval]))', "percentunit",
                  "Share of all logical processors doing work. Sustained above 80% leaves no room for a site failover.", "stat"),
                p("Run queue per core", f'max by (instance) (avg_over_time({host("windows_system_processor_queue_length")}[5m])) / {cores}', "short",
                  "Threads ready to run but waiting for a processor, per core, over 5 minutes. Sustained above 2 the CPU is the bottleneck, whatever the busy figure says.", "stat"),
                p("WSL VM CPU busy", f'max by (k8s_cluster) (1 - avg by (k8s_cluster, instance) (rate({selector("node_cpu_seconds_total", 'mode="idle"')}[$__rate_interval]))) {ON_HOST}', "percentunit",
                  "Share of the WSL VM's processors (its .wslconfig limit, fewer than the host's) doing work, from the cluster nodes on this host. Near 100% while the host still has room, the VM's limit is the ceiling.", "stat"),
                p("CPU by mode", f'sum by (mode) (rate({host("windows_cpu_time_total", 'mode!="idle"')}[$__rate_interval])) / scalar({cores})', "percentunit",
                  "Busy time split by mode. The WSL VM's work is counted as privileged time here (measured 2026-11-07: about 2.5 busy VM cores inside 3.5 privileged host cores), so privileged far above user is the cluster, not Windows apps."),
                p("Busiest cores", f'topk(5, max by (core) (1 - rate({host("windows_cpu_time_total", 'mode="idle"')}[$__rate_interval])))', "percentunit",
                  "The five busiest logical processors. One core pinned near 100% while the total looks calm is a single-threaded bottleneck."),
                p("Run queue", f'max by (instance) ({host("windows_system_processor_queue_length")})', "short",
                  "Threads waiting for a processor, sampled. Short spikes are normal; a raised floor is saturation."),
                p("WSL CPU pressure", pressure("cpu"), "percentunit",
                  "Share of time at least one task in the WSL VM waited for a CPU (Linux PSI, read from the cluster nodes on this host). Rising pressure with a calm Windows CPU means WSL's processor limit, not the host, is the ceiling."),
            ],
            "Memory": [
                p("Memory in use", f'1 - max by (instance) ({host("windows_memory_available_bytes")}) / max by (instance) ({host("windows_memory_physical_total_bytes")})', "percentunit",
                  "Physical memory not available to new work. Standby cache counts as available, so this is what the host can still give out.", "stat"),
                p("Commit charge", f'max by (instance) ({host("windows_memory_committed_bytes")}) / max by (instance) ({host("windows_memory_commit_limit")})', "percentunit",
                  "Memory promised to processes against RAM plus page file. At 100% Windows refuses new allocations and programs fail, WSL's VM included, however much RAM looks free.", "stat"),
                p("Paging to disk", f'sum by (instance) (rate({host("windows_memory_swap_page_operations_total")}[$__rate_interval]))', "ops",
                  "Pages read from or written to the page file per second. A steady stream is memory saturation: work waits on the disk instead of RAM.", "stat"),
                p("Memory and commit", f'max by (measure) (label_replace(1 - max by (instance) ({host("windows_memory_available_bytes")}) / max by (instance) ({host("windows_memory_physical_total_bytes")}), "measure", "In use", "", "")'
                  f' or label_replace(max by (instance) ({host("windows_memory_committed_bytes")}) / max by (instance) ({host("windows_memory_commit_limit")}), "measure", "Commit charge", "", ""))', "percentunit",
                  "Physical memory in use and commit charge over time. Commit climbing while use stays flat is memory reserved but untouched (WSL's VM reserves its whole limit)."),
                p("Page file traffic", f'sum by (instance) (rate({host("windows_memory_swap_page_operations_total")}[$__rate_interval]))', "ops",
                  "Paging over time: line it up with slow disk or a busy CPU to see which came first."),
                p("WSL memory pressure", pressure("memory"), "percentunit",
                  "Share of time at least one task in the WSL VM stalled reclaiming memory (Linux PSI). Above a few percent the VM's memory limit is too tight for its pods."),
            ],
            "Disk": [
                p("Slowest volume latency", f'max by (instance) (rate({host("windows_logical_disk_read_write_latency_seconds_total", VOLUMES)}[$__rate_interval])'
                  f' / (rate({host("windows_logical_disk_reads_total", VOLUMES)}[$__rate_interval]) + rate({host("windows_logical_disk_writes_total", VOLUMES)}[$__rate_interval])))', "s",
                  "Average time per disk operation on the worst volume. An SSD normally answers in under 5 ms; sustained above 20 ms the disk is what's slow.", "stat"),
                p("Busiest volume", f'max by (instance) (clamp_min(1 - rate({host("windows_logical_disk_idle_seconds_total", VOLUMES)}[$__rate_interval]), 0))', "percentunit",
                  "Share of time the busiest volume had work in flight. An SSD serves many requests at once, so read it with latency and queue: busy alone isn't saturation.", "stat"),
                p("Queued disk requests", f'sum by (instance) (avg_over_time({host("windows_logical_disk_requests_queued", VOLUMES)}[5m]))', "short",
                  "Requests waiting for the disk, averaged over 5 minutes. Sustained above 2 per disk, with latency rising, is disk saturation.", "stat"),
                p("Read latency by volume", latency("read"), "s",
                  "Average time per read. Spikes that line up with WSL I/O pressure are the slow disk the pods feel."),
                p("Write latency by volume", latency("write"), "s",
                  "Average time per write. Cached writes are fast until the cache fills; a step up under load is the disk catching up."),
                p("Queue depth by volume", f'max by (volume) ({host("windows_logical_disk_requests_queued", VOLUMES)})', "short",
                  "Requests waiting per volume, sampled."),
                p("Throughput by volume", f'sum by (volume) (rate({host("windows_logical_disk_read_bytes_total", VOLUMES)}[$__rate_interval]) + rate({host("windows_logical_disk_write_bytes_total", VOLUMES)}[$__rate_interval]))', "Bps",
                  "Bytes read and written per second. High throughput with low latency is a busy, healthy disk."),
                p("WSL I/O pressure", pressure("io", "stalled"), "percentunit",
                  "Share of time every runnable task in the WSL VM stalled on I/O at once (Linux PSI, full). This is how slow disk feels from inside the cluster: all nodes share one virtual disk on the host."),
                p("Free space by volume", f'max by (volume) ({host("windows_logical_disk_free_bytes", VOLUMES)} / {host("windows_logical_disk_size_bytes", VOLUMES)})', "percentunit",
                  "Free share of each volume. Below 10% Windows and the VHDX that holds WSL's disk start to slow down; below 5% writes fail.", "table", column="Free", sort="asc"),
            ],
            "Network": [
                p("Busiest adapter", f'max by (instance) (sum by (instance, nic) (rate({host("windows_net_bytes_total")}[$__rate_interval])) / max by (instance, nic) ({host("windows_net_current_bandwidth_bytes")} > 0))', "percentunit",
                  "Traffic on the busiest adapter against its link speed. Near 100% the link is full; on Wi-Fi the link speed itself moves with the signal.", "stat"),
                p("Received by adapter", f'sum by (nic) (rate({host("windows_net_bytes_received_total")}[$__rate_interval])) > 0', "Bps",
                  "Bytes received per second on each adapter in use."),
                p("Sent by adapter", f'sum by (nic) (rate({host("windows_net_bytes_sent_total")}[$__rate_interval])) > 0', "Bps",
                  "Bytes sent per second on each adapter in use."),
                p("Errors and discards by adapter", f'sum by (nic) (rate({host("windows_net_packets_received_errors_total")}[$__rate_interval]) + rate({host("windows_net_packets_outbound_errors_total")}[$__rate_interval])'
                  f' + rate({host("windows_net_packets_received_discarded_total")}[$__rate_interval]) + rate({host("windows_net_packets_outbound_discarded_total")}[$__rate_interval])) > 0', "pps",
                  "Packets dropped or damaged per second. Errors are the link (cable, radio, driver); discards are a full buffer. None is the normal state.",
                  empty="None in range"),
                p("Link speed by adapter", f'max by (nic) ({host("windows_net_current_bandwidth_bytes")} > 0) * 8', "bps",
                  "Negotiated speed of each adapter with a link. A wired link that drops from 1 Gbit/s to 100 Mbit/s is a cable or port fault.", "table", column="Link speed"),
            ],
        }, "Hosts", sections=True,
        variables=(Variable("host", "Host", 'label_values(windows_cpu_time_total{k8s_cluster=~"$cluster"}, instance)', multi=False),))
