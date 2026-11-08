"""WAN clients (L3): Which device is filling the Internet line, with what, and to where?

From the router's flow export (monitoring/flow-collector): per device, app and
remote network. Device names come from DHCP leases at runtime. Latency sits
beside the traffic, crosshair-synced, so a spike can be lined up with whoever
was sending at the time.
"""

from __future__ import annotations

from ..model import Board, Variable, p
from ..queries import WAN_CLIENT, metric, wan


def both(by: str, inner: str = "{sel}", picked: bool = False) -> str:
    """Download plus upload per `by`, keeping a device that only sends one way."""
    return (f'sum by ({by}) (label_replace({inner.format(sel=wan("receive", picked))}, "direction", "down", "", "")'
            f' or label_replace({inner.format(sel=wan("transmit", picked))}, "direction", "up", "", ""))')


def named(expr: str) -> str:
    """A remote network by its owner's name: the ASN table leaves a "- " in front."""
    return f'label_replace({expr}, "as_name", "$1", "as_name", "(?:- )?(.+)")'


def in_range(by: str) -> str:
    """Bytes over the selected range: the average rate times the range."""
    return f'{both(by, "avg_over_time({sel}[$__range])")} * $__range_s / 8'


def board() -> Board:
    flows = "Flow export covers what the router forwards to the Internet; a device's name comes from its DHCP lease, else its address."
    return Board("tnwks-wan-clients", "WAN clients", "network", "l3",
              "Which device is filling the Internet line, with what, and to where?", {
            "Devices": [
                p("Download now", metric("wan:receive_bits:rate1m"), "bps",
                  "Everything the Internet line is receiving, from the router's own counters.", "stat"),
                p("Upload now", metric("wan:transmit_bits:rate1m"), "bps",
                  "Everything the line is sending. Upload is the narrow direction: a full upload delays every device's traffic, downloads included.", "stat"),
                p("Devices on the line", f'count({both("client")} > 0)', "short",
                  "Devices with any Internet traffic in the last two minutes. " + flows, "stat"),
                p("Download attributed", f'sum({wan("receive")}) / sum({metric("wan:receive_bits:rate1m")})', "percentunit",
                  "Share of the line's download that flow export puts a device to. Well under 100% means flows are missing (exporter, relay or collector), so the device lists undercount.", "stat"),
                p("Top downloaders now", f'topk(10, sum by (client) ({wan("receive")})) > 0', "bps",
                  "The ten devices downloading most right now. Click a row to follow that device below.",
                  "table", WAN_CLIENT, empty="No traffic seen by flow export", column="Download", role="status", sort="desc"),
                p("Top uploaders now", f'topk(10, sum by (client) ({wan("transmit")})) > 0', "bps",
                  "The ten devices uploading most right now: the usual cause when latency climbs for everyone (backups, cameras, cloud sync).",
                  "table", WAN_CLIENT, empty="No traffic seen by flow export", column="Upload", role="status", sort="desc"),
                p("Data in range by device", f'topk(10, {in_range("client")})', "bytes",
                  "Bytes each device moved over the selected range, both directions, most first.",
                  "table", WAN_CLIENT, column="Data", role="status", sort="desc"),
                p("Download by device", f'sum by (client) ({wan("receive", True)})', "bps",
                  "Download per picked device over time."),
                p("Upload by device", f'sum by (client) ({wan("transmit", True)})', "bps",
                  "Upload per picked device over time. Line it up with the latency beside it."),
                p("Internet latency", f'max by (k8s_cluster) ({metric("smokeping:rtt_p90:rate1m", 'role="internet"')})', "s",
                  "p90 round trip to Internet targets. A rise that tracks one device's upload is that device filling the line (bufferbloat)."),
            ],
            "Apps and destinations": [
                p("Now: device, app and destination", named(f'topk(15, {both("client, app, as_name")})'), "bps",
                  "The fifteen busiest device, app and remote-network combinations right now, both directions. Apps are guessed from ports and networks, so read them as a hint.",
                  "table", WAN_CLIENT, empty="No traffic seen by flow export", column="Traffic", role="status", sort="desc"),
                p("Data in range by destination", named(f'topk(10, {in_range("as_name")})'), "bytes",
                  "Bytes to and from each remote network (by its owner, from the ASN) over the selected range.",
                  "table", column="Data", role="status", sort="desc"),
                p("Traffic by app", both("app", picked=True), "bps",
                  "Both directions per app for the picked devices."),
                p("Traffic by destination", named(f'topk(10, {both("as_name", picked=True)})'), "bps",
                  "Both directions to the ten busiest remote networks at each moment, for the picked devices."),
            ],
        }, "Network", sections=True, parent="tnwks-internet",
        variables=(Variable("client", "Device", "label_values(wan:client_app_receive_bits:rate2m, client)"),))
