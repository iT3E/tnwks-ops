"""Internet (L2): Is the Internet path usable, and is loss/latency or saturation the issue?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import WAN_CLIENT, metric, wan

# The Internet targets and the router hop beside them. smokeping also pings the
# APs, SW03 and the radio gateway; their loss is theirs, not the Internet's.
WAN_PATH = 'role=~"lan|internet"'


def board() -> Board:
    return Board("tnwks-internet", "Internet", "network", "l2",
              "Is the Internet path usable, and is loss/latency or saturation the issue?", {
            "Experience": [
                p("Internet probe", metric("homelab:internet_state"), "state",
                  "Observed Internet probe state with freshness/coverage. A working exporter is insufficient.", "stat"),
                p("WAN p90 latency", f'max by (k8s_cluster, name) ({metric("smokeping:rtt_p90:rate1m", WAN_PATH)})', "s",
                  "p90 over the probe histogram, per Internet target and the router hop (sce-rtr01); compare with baseline/threshold and packet loss."),
                p("WAN latency threshold", f'max by (k8s_cluster, name) ({metric("smokeping:latency_threshold:1h", WAN_PATH)})', "s",
                  "The actual recording rule used by the WAN alert, not a separately maintained display threshold."),
                p("Packet loss", f'max by (k8s_cluster, name) ({metric("smokeping:loss_ratio:rate1m", WAN_PATH)})', "percentunit",
                  "Probe packet loss per Internet target and the router hop; distinguish a missing series from zero measured loss. The APs, SW03 and the radio gateway are on their own boards."),
            ],
            "Traffic": [
                p("Download", metric("wan:receive_bits:rate1m"), "bps",
                  "Measured WAN receive throughput. Current traffic is not the available speed of the connection."),
                p("Upload", metric("wan:transmit_bits:rate1m"), "bps",
                  "Measured WAN transmit throughput. Who is sending it is on WAN clients.", link="tnwks-wan-clients"),
                p("Top WAN devices now", f'topk(5, sum by (client) ({wan("receive")}) + sum by (client) ({wan("transmit")}))', "bps",
                  "The five devices moving the most Internet traffic now, both directions, from flow export. Click a row for its apps, destinations and history.",
                  "table", WAN_CLIENT, empty="No traffic seen by flow export", column="Traffic", sort="desc"),
                p("ISP radio collector", metric("unms_collector_success"), "bool",
                  "Whether the UISP exporter reached a UISP controller. None is hosted now (docs/network/architecture.md, Not running), so this reads No and says nothing about the radio link. Whether the radio network's gateway (EP-R6) answers and passes traffic is on Network & Routing, tab Radio gateway.",
                  link="tnwks-network"),
            ],
        }, "Network", sections=True)
