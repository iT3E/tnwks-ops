"""The catalogue: one module per board, in navigation order. Adding a board is one module here."""

from __future__ import annotations

from ..model import Board
from . import (
    alerts,
    apps,
    data,
    home,
    homelab,
    hosts,
    house,
    internet,
    kubernetes,
    network,
    observability,
    sites,
    wifi,
)

ORDER = (homelab, sites, data, network, internet, wifi, kubernetes, apps, home, hosts, observability, alerts, house)


def catalogue() -> list[Board]:
    return [module.board() for module in ORDER]
