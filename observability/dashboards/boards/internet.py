"""Internet (L2): Is the Internet path usable, and is loss/latency or saturation the issue?"""

from __future__ import annotations

from ..model import Board, p
from ..queries import WAN_CLIENT, metric, wan


def board() -> Board:
    return Board("tnwks-internet", "Internet", "network", "l2",
              "Is the Internet path usable, and is loss/latency or saturation the issue?", {
            "Experience": [
                p("Internet probe", metric("homelab:internet_state"), "state",
                  "Observed Internet probe state with freshness/coverage. A working exporter is insufficient.", "stat"),
                p("WAN p90 latency", metric("smokeping:rtt_p90:rate1m"), "s",
                  "p90 over the probe histogram; compare with baseline/threshold and packet loss."),
                p("WAN latency threshold", metric("smokeping:latency_threshold:1h"), "s",
                  "The actual recording rule used by the WAN alert, not a separately maintained display threshold."),
                p("Packet loss", metric("smokeping:loss_ratio:rate1m"), "percentunit",
                  "Probe packet loss; distinguish a missing series from zero measured loss."),
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
                  "Whether the UISP exporter reached its controller. This is collector evidence, not the radio link's own health."),
            ],
        }, "Network", sections=True)
