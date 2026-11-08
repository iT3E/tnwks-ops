"""The catalogue: one module per board, in navigation order. Adding a board is one module here."""

from __future__ import annotations

from ..model import Board
from . import (
    alerts,
    apps,
    data,
    home,
    homelab,
    host,
    hosts,
    house,
    internet,
    kubernetes,
    network,
    observability,
    sites,
    wan_clients,
    wifi,
    wifi_clients,
)

ORDER = (homelab, sites, data, network, internet, wan_clients, wifi, wifi_clients, kubernetes, apps, home, hosts, host,
         observability, alerts, house)


def catalogue() -> list[Board]:
    return [module.board() for module in ORDER]
