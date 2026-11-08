"""Wi-Fi clients (L3): Which device is having a bad time on Wi-Fi, and why?

The tables find the devices worth a look across the house; the graphs follow
the devices picked above (a click on a table row picks it). Client names come
from UniFi at runtime: nothing here names a household device.
"""

from __future__ import annotations

from ..design import NO_EVENTS
from ..model import Board, Variable, p
from ..queries import WIFI_CLIENT, band, wifi

PICKED = 'name=~"$client"'
# UniFi's API refreshes every 2 minutes (unpoller's scrape interval): a rate needs
# a window with several samples, wider than Grafana's $__rate_interval here.
RATE = "10m"
WEAK_DBM = -70
LOW_SATISFACTION = 0.8


def per_client(name: str) -> str:
    return f'max by (name) ({wifi(name, PICKED)})'


def retry_rate(window: str, extra: str = "") -> str:
    """Share of transmissions that were retries, per client: retries / (packets + retries),
    as UniFi shows it. Bounded below 100%; retries over attempts wasn't (a reconnect or
    a near-idle device read 350%)."""
    retries = f'sum by (name) (increase({wifi("unpoller_client_transmit_retries_total", extra)}[{window}]))'
    packets = f'sum by (name) (increase({wifi("unpoller_client_transmit_packets_total", extra)}[{window}]))'
    return f'{retries} / ({packets} + {retries})'



def board() -> Board:
    signal = wifi("unpoller_client_radio_signal_db")
    return Board("tnwks-wifi-clients", "Wi-Fi clients", "wifi", "l3",
              "Which device is having a bad time on Wi-Fi, and why?", {
            "Clients": [
                p("Wi-Fi clients", f'count(count by (name) ({wifi("unpoller_client_uptime_seconds")}))', "short",
                  "Wireless devices UniFi sees now (the poller's own copy, never the other site's federated one).", "stat"),
                p("Weak signal", f'count(max by (name) ({signal}) < {WEAK_DBM}) or (0 * count({signal}))', "short",
                  f"Devices below {WEAK_DBM} dBm at their access point. Below that, rates drop and retries climb; a device that never moves may just be far away.",
                  "stat"),
                p("Low satisfaction", f'count(max by (name) ({wifi("unpoller_client_satisfaction_ratio")}) < {LOW_SATISFACTION}) or (0 * count({wifi("unpoller_client_satisfaction_ratio")}))', "short",
                  f"Devices UniFi scores below {LOW_SATISFACTION:.0%} satisfaction: its own blend of signal, retries and latency for that client.", "stat"),
                p("Roams in range", f'round(sum(increase({wifi("unpoller_client_roam_count_total")}[$__range])))', "short",
                  "Moves between access points over the selected range, every device. A burst of roams for one device is it flapping between two APs.", "stat"),
                p("Weak signal clients", f'max by (name, ap_name, radio) ({band(signal)}) < {WEAK_DBM}', "dBm",
                  f"Each device below {WEAK_DBM} dBm, its access point and band, weakest first. Click a row to follow that device below.",
                  "table", WIFI_CLIENT, empty=f"None below {WEAK_DBM} dBm", column="Signal", role="status", sort="asc"),
                p("Low satisfaction clients", f'max by (name, ap_name) ({wifi("unpoller_client_satisfaction_ratio")}) < {LOW_SATISFACTION}', "percentunit",
                  f"Each device UniFi scores below {LOW_SATISFACTION:.0%}, worst first. Read it with its signal and retries to see which is to blame.",
                  "table", WIFI_CLIENT, empty=f"None below {LOW_SATISFACTION:.0%}", column="Satisfaction", role="status", sort="asc"),
                p("Most retries", f'topk(10, {retry_rate("$__range")} >= 0)', "percentunit",
                  "The ten devices whose transmissions most often needed a retry over the selected range. Interference or a weak link shows here before anything disconnects.",
                  "table", WIFI_CLIENT, column="Retry rate", role="status", sort="desc"),
                p("Reconnects", f'sum by (name) (resets({wifi("unpoller_client_uptime_seconds")}[$__range])) > 0', "short",
                  "Devices whose association restarted on the same access point in the selected range, most first. A move to another AP counts as a roam instead.",
                  "table", WIFI_CLIENT, empty=NO_EVENTS, column="Reconnects", role="status", sort="desc"),
                p("Signal", per_client("unpoller_client_radio_signal_db"), "dBm",
                  f"Signal strength at the access point for the picked devices. Better than -60 dBm is good; below {WEAK_DBM} dBm is weak."),
                p("Satisfaction", per_client("unpoller_client_satisfaction_ratio"), "percentunit",
                  "UniFi's experience score for the picked devices. A dip with steady signal is interference or a busy channel, not distance."),
                p("Retry rate", retry_rate(RATE, PICKED), "percentunit",
                  "Share of transmissions that needed a retry for the picked devices, over 10 minutes. Steadily above 20-30% the link is struggling."),
                p("Throughput", f'sum by (name) (rate({wifi("unpoller_client_receive_bytes_total", PICKED)}[{RATE}]) + rate({wifi("unpoller_client_transmit_bytes_total", PICKED)}[{RATE}]))', "Bps",
                  "Bytes to and from the picked devices. Poor scores while idle matter less than poor scores while busy."),
                p("Connected to", f'max by (name, ap_name, radio, channel) ({band(wifi("unpoller_client_uptime_seconds", PICKED))})', "s",
                  "Where each picked device is associated now, on which band and channel, and for how long: shortest first, so a fresh reconnect is on top.",
                  "table", WIFI_CLIENT, column="Connected for", sort="asc"),
            ],
        }, "Network", sections=True, parent="tnwks-wifi",
        variables=(Variable("client", "Client", 'label_values(unpoller_client_uptime_seconds{source_site="",wired="false"}, name)'),))
