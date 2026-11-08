"""Wi-Fi & LAN (L2): Is client experience failing at RF, the AP uplink, DHCP/DNS, or site 2's Wi-Fi path?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import ACCESS_POINT, HOST, SITE2_WIFI_CLIENT, metric, selector, wifi


def board() -> Board:
    return Board("tnwks-wifi", "Wi-Fi & LAN", "network", "l2",
              "Is client experience failing at RF, the AP uplink, DHCP/DNS, or site 2's Wi-Fi path?", {
            "Access points": [
                p("AP uptime", f'max by (k8s_cluster, name) ({metric("unpoller_device_uptime_seconds")})', "s",
                  "AP inventory/uptime. Device-only identifiers; household client names/MACs are excluded.", "table", ACCESS_POINT),
                p("AP uplink", f'max by (k8s_cluster, name) ({metric("unpoller_device_uplink_speed_mbps")})', "Mbits",
                  "Negotiated uplink speed. A slow or errored uplink can masquerade as an RF problem.", "table", ACCESS_POINT),
                p("Wi-Fi collector failures", f'sum by (k8s_cluster) (increase({metric("unpoller_prometheus_refresh_failures_total")}[$__range]))', "failures",
                  "Collector-specific failure evidence; HTTP scrape success does not prove the UniFi API refresh succeeded. Counted over the selected range, per site; anything but zero is red.", "stat"),
                p("Clients with weak signal", f'count(max by (name) ({wifi("unpoller_client_radio_signal_db")}) < -70) or (0 * count({wifi("unpoller_client_radio_signal_db")}))', "short",
                  "Wireless devices below -70 dBm at their access point, the whole house. Which ones, and their retries and satisfaction, are on Wi-Fi clients.",
                  "stat", "tnwks-wifi-clients"),
            ],
            "Site dependency": [
                p("Peer path success", metric("homelab_peer_probe_success"), "bool",
                  "Correlate site 2 connectivity loss with AP/uplink and WireGuard/BGP observations. This does not identify individual household clients."),
                p("Peer path duration", metric("homelab_peer_probe_duration_seconds"), "s",
                  "Bounded transport probe duration; one sample does not measure line-rate Wi-Fi performance."),
                # Measured 2026-11-07: site 1 on its wired NIC at 1 Gbit/s, site 2
                # only on Wi-Fi (300 Mbit/s link rate).
                p("Site host uplinks", f'label_replace(max by (k8s_cluster, instance, nic) ({selector("windows_net_current_bandwidth_bytes")} > 0) * 8, "host", "$1", "instance", "(.+)")', "bps",
                  "Each site's Windows host NICs with a link, at their negotiated rate (windows_exporter). Site 2's host reaches the router over Wi-Fi only, so its link rate changes with the radio.",
                  "table"),
                p("Site host uplink traffic", f'sum by (k8s_cluster, instance, nic) (rate({selector("windows_net_bytes_total")}[$__rate_interval])) > 0', "Bps",
                  "Traffic through each host NIC in use: everything the site sends or receives, the site tunnel included.", link=HOST),
                p("Site 2 Wi-Fi signal", f'max by (ap_name, channel) ({selector("unpoller_client_radio_signal_db", f'name="{SITE2_WIFI_CLIENT}"')})', "dBm",
                  "The site 2 host's signal at its access point, as UniFi reports it (either site's poller). A change of AP or channel starts a new line."),
                p("Site 2 Wi-Fi association", f'max by (ap_name) ({selector("unpoller_client_uptime_seconds", f'name="{SITE2_WIFI_CLIENT}"')})', "s",
                  "How long the site 2 host has been associated. A drop to zero is a reconnect or roam, which interrupts the site's tunnel and BGP sessions.",
                  "table"),
            ],
        }, "Network", sections=True)
