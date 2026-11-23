"""The catalogue: one module per board, in navigation order. Adding a board is one module here."""

from __future__ import annotations

from ..model import Board
from . import (
    ai,
    alerts,
    apps,
    cloud_cost,
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
    security,
    sites,
    wan_clients,
    wifi,
    wifi_clients,
)

ORDER = (homelab, sites, data, network, internet, wan_clients, wifi, wifi_clients, kubernetes, apps, ai, home, hosts,
         host, observability, cloud_cost, security, alerts, house)


def catalogue() -> list[Board]:
    return [module.board() for module in ORDER]
