"""Typed, deterministic Grafana v2 resources. No Grafana API writes.

design (vocabulary) -> queries (PromQL) -> model (what a board declares) -> boards/ (the catalogue)
-> recipes (panel visualizations) -> layout (dashboard resources) -> render (files and contracts).
"""

from .boards import catalogue
from .boards.common import coverage
from .design import LABELS, NO_ALERTS, NO_EVENTS, SCRAPE_COLUMNS, STATE, UNKNOWN_GREY
from .layout import resource
from .model import Board, Panel, legend, p, value_header
from .queries import CLUSTERS, IDENTITY, ROOT, SCOPE
from .recipes import visualization
from .render import OUTPUT, generated, main

__all__ = ["CLUSTERS", "IDENTITY", "LABELS", "NO_ALERTS", "NO_EVENTS", "OUTPUT", "ROOT", "SCOPE", "SCRAPE_COLUMNS", "STATE", "UNKNOWN_GREY", "Board", "Panel", "catalogue", "coverage", "generated", "legend", "main", "p", "resource", "value_header", "visualization"]
