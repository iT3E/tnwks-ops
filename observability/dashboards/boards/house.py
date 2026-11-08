"""House (L0): the household kiosk, aggregate-only (prometheus-house).

Six big tiles, one word each, in plain household language. Grey means the
house can't tell, never that it's fine.
"""

from __future__ import annotations

from ..model import Board, p

UNKNOWN = " Grey (Unknown) means it can't tell right now, not that it's fine."

# (tile, domain, what the tile answers)
TILES = [
    ("Internet", "internet", "Can the house reach the Internet right now?"),
    ("Wi-Fi", "wifi", "Are the Wi-Fi access points up? A working access point doesn't promise every device is happy."),
    ("Home automation", "automation", "Is Home Assistant working, so lights, sensors and automations respond?"),
    ("Cameras", "cameras", "Are the cameras recording?"),
    ("Apps", "apps", "Do the household apps open?"),
    ("Monitoring", "monitoring", "Is the house checking itself? If not, the other tiles can't be trusted."),
]


def board() -> Board:
    tiles = [p(title, f'homelab_house_state{{domain="{domain}"}}', "state", question + UNKNOWN, "stat",
               empty="Unknown", role="wall")
             for title, domain, question in TILES]
    return Board("home", "House", "home", "l0", "Is the house working?", {"House": tiles}, "House", True,
                 sections=True)
