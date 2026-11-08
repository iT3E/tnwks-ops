"""Network & Routing (L2): Where does the path from the router through FRR/Cilium to a service fail?"""

from __future__ import annotations

from ..design import NO_EVENTS
from ..model import Board, p
from ..queries import changes, metric, selector


def board() -> Board:
    return Board("tnwks-network", "Network & Routing", "network", "l2",
              "Where does the path from the router through FRR/Cilium to a service fail?", {
            "Paths": [
                p("Site path probes", metric("homelab_peer_probe_success"), "bool",
                  "Cross-site probe success from each observer. Read with transport and endpoint evidence; a BGP Established session alone is insufficient.", "table", column="Path", words=("Up", "Down")),
                # The router, seen by homelab-wsl's mktxp: not scoped to $cluster.
                p("Site tunnel traffic", 'sum by (interface) (rate(mktxp_peer_rx_total{interface=~"wg-site[0-9]+"}[5m]))', "Bps",
                  "Received on the router's WireGuard interface for each site's distro. Missing series mean the router isn't observed, not an idle tunnel."),
            ],
            "BGP and mesh": [
                p("Path by hop", metric("homelab:network_path_state"), "state",
                  "Per site: the WireGuard tunnel to the router (handshake within 180 s), the router's BGP session with the distro's FRR, the Cilium agents' sessions with the distro, and the mesh (the site's agents and its KVStoreMesh both have the other cluster ready). Unknown when the router or the agents aren't observed.", "table"),
                p("Shared VIP routes", metric("homelab:shared_vip_route"), "bool",
                  "Which site the router sends each shared VIP to (1): the preferred site while its BGP session is up, else the other. Inferred from the router's live sessions and the declared local preference, not read from its route table. A client behind a site (its pods, the WSL host) reaches that site's own ingress either way.", "table", column="Carries the VIP", words=("Yes", "—")),
                p("Routes received per site", 'max by (k8s_cluster) (homelab:router_bgp_prefixes{k8s_cluster=~"$cluster"})', "short",
                  "Prefixes the router accepted from each site's FRR (its node, pod and shared-VIP routes). Zero with an established session means the site announces nothing.", "stat"),
                p("Cilium drops", f'sum by (k8s_cluster, reason) (rate({metric("cilium_drop_count_total")}[5m]))', "pps",
                  "Agent drop counters, with bounded labels. Cilium metrics activation belongs to the post-network-release integration."),
                p("Mesh connectivity", metric("cilium_clustermesh_remote_cluster_readiness_status"), "bool",
                  "Each Cilium agent's readiness for the other cluster, through its own site's KVStoreMesh cache.", "table", column="Mesh", words=("Connected", "Disconnected")),
                p("KVStoreMesh remote cluster", f'max by (k8s_cluster, target_cluster) ({selector("cilium_kvstoremesh_remote_cluster_readiness_status")})', "bool",
                  "Whether the site's clustermesh-apiserver (KVStoreMesh) has the other cluster's state synchronized. While it's down, agents keep serving the last cached state.", "table", column="KVStoreMesh", words=("Ready", "Not ready")),
                p("Mesh sync errors", f'sum by (k8s_cluster, scope) (rate({selector("cilium_kvstoremesh_kvstore_sync_errors_total")}[$__rate_interval]))', "ops",
                  "KVStoreMesh errors writing the other cluster's nodes, services and identities into the local cache. Any is worth a look; persistent ones leave stale cross-site endpoints."),
                p("Mesh remote failures", f'max by (k8s_cluster, target_cluster) ({selector("cilium_kvstoremesh_remote_cluster_failures")})', "short",
                  "KVStoreMesh connection failures to the other cluster since its last restart."),
            ],
            # The router itself, from mktxp (it replaces the MikroTik reference board).
            "Router": [
                p("Router uptime", 'max by (routerboard_name, version) (mktxp_system_uptime)', "s",
                  "Time since the router last booted, with its RouterOS version. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed.", "table"),
                p("Router CPU", 'max by (routerboard_name) (mktxp_system_cpu_load)', "percent",
                  "CPU load across the router's cores, as RouterOS reports it. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Router memory used", '1 - max by (routerboard_name) (mktxp_system_free_memory) / max by (routerboard_name) (mktxp_system_total_memory)', "percentunit",
                  "Share of the router's memory in use. The DNS adlists live in memory, so a jump after a list update is expected. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Router temperature", 'max by (routerboard_name) (mktxp_system_cpu_temperature)', "celsius",
                  "CPU temperature. The RB5009 is fanless, so this follows the room. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Router storage used", '1 - max by (routerboard_name) (mktxp_system_free_hdd_space) / max by (routerboard_name) (mktxp_system_total_hdd_space)', "percentunit",
                  "Share of the router's flash in use (RouterOS packages, files and backups). Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Interface receive", 'sum by (name) (rate(mktxp_interface_rx_byte_total{name!="lo"}[5m]))', "Bps",
                  "Bytes received per router interface: physical ports, bridge VLANs and WireGuard tunnels. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Interface transmit", 'sum by (name) (rate(mktxp_interface_tx_byte_total{name!="lo"}[5m]))', "Bps",
                  "Bytes sent per router interface. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Interface errors", 'sum by (name) (rate(mktxp_interface_rx_error_total[5m])) + sum by (name) (rate(mktxp_interface_tx_error_total[5m]))', "ops",
                  "Receive and transmit errors per interface. Errors on a copper port usually mean a cable or a duplex mismatch. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Interface drops", 'sum by (name) (rate(mktxp_interface_rx_drop_total[5m])) + sum by (name) (rate(mktxp_interface_tx_drop_total[5m]))', "ops",
                  "Packets dropped per interface, received or sent: queues full or the frame refused. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Interface link", 'max by (name) (mktxp_interface_running) and on (name) (max by (name) (mktxp_interface_disabled) == 0)', "bool",
                  "Whether each enabled interface is running. A port with nothing plugged in is false here, which is only a problem for a port that should be in use. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed.",
                  "table", column="Link", words=("Up", "Down")),
                p("Address pool use", 'max by (pool) (mktxp_ip_pool_used) / max by (pool) (mktxp_ip_pool_total)', "percentunit",
                  "Share of each DHCP address pool leased. A full pool refuses new clients on that VLAN. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed.",
                  "table"),
                p("Tracked connections", 'max by (routerboard_name) (mktxp_ip_connections_total)', "short",
                  "Entries in the router's connection-tracking table. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Routes by protocol", 'max by (protocol) (mktxp_routes_protocol_count)', "short",
                  "Routes in the router's table by origin. The BGP ones come from the sites' FRR; BGP and mesh splits them by site. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Firewall drop rules", 'sum by (name) (rate(mktxp_firewall_filter_total{name=~".*[|] drop [|].*"}[5m]))', "Bps",
                  "Traffic matched by each filter rule that drops, in bytes per second as mktxp counts it. The rule name is its chain, action and Terraform comment. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed."),
                p("Firmware upgrade", 'max by (routerboard_name, current_firmware, upgrade_firmware) (mktxp_routerboard_firmware_upgrade_available)', "bool",
                  "Whether the RouterBOOT firmware is behind the installed RouterOS. It's upgraded from the router, followed by a reboot. Only site 1's mktxp observes the router, so the site filter doesn't apply; no series means the router isn't observed.",
                  "table", column="Upgrade", words=("Available", "Up to date")),
            ],
            "History": [
                p("Router BGP session resets", f'max by (k8s_cluster, routerboard_name, name) (resets({selector("mktxp_bgp_uptime")}[$__range])) > 0', "short",
                  "Times each of the router's BGP sessions restarted in the range: its uptime went back. A session that dropped and came back between scrapes still counts. Only site 1 observes the router.",
                  "table", empty=NO_EVENTS),
                p("Cilium BGP session changes", changes("cilium_bgp_control_plane_session_state", "node"), "short",
                  "State changes of each node's Cilium BGP session with its distro's FRR in the range. Sampled at each scrape, so a shorter flap can be missed.",
                  "table", empty=NO_EVENTS),
                p("Path hop changes", changes("homelab:network_path_state", "hop"), "short",
                  "State changes of each path hop (tunnel, router BGP, agents, mesh) per site in the range, sampled at each rule evaluation.",
                  "table", empty=NO_EVENTS),
            ],
        }, "Network", sections=True)
