"""House (L0): the household kiosk, aggregate-only (prometheus-house)."""

from __future__ import annotations

from ..model import Board, p


def board() -> Board:
    house_panels = [
        p("Internet", 'homelab_house_state{domain="internet"}', "state",
          "Upstream-approved aggregate Internet state. No observation or stale observation is Unknown.", "stat"),
        p("Wi-Fi", 'homelab_house_state{domain="wifi"}', "state",
          "Aggregate Wi-Fi evidence. Working APs do not prove every client's experience; missing evidence remains Unknown.", "stat"),
        p("Home automation", 'homelab_house_state{domain="automation"}', "state",
          "Aggregate usable Home Assistant path; hardware-pinned integration limitations remain visible in operator diagnostics.", "stat"),
        p("Cameras", 'homelab_house_state{domain="cameras"}', "state",
          "Aggregate recording evidence. Missing persistence evidence cannot certify that cameras are recording.", "stat"),
        p("Apps", 'homelab_house_state{domain="apps"}', "state",
          "Aggregate app path evidence, with missing expected paths represented as Unknown.", "stat"),
        p("Monitoring", 'homelab_house_state{domain="monitoring"}', "state",
          "Whether fresh approved aggregate evidence is available. This identity can query only the aggregate backend.", "stat"),
    ]
    return Board("home", "House", "home", "l0", "Is the house working?", {"House": house_panels}, "House", True)
