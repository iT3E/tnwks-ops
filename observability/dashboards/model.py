"""What a board declares: panels, boards, and how a panel's values are named and worded."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .design import STATE, UNKNOWN_GREY


@dataclass(frozen=True)
class Panel:
    title: str
    expr: str
    unit: str
    description: str
    kind: str = "timeseries"
    # A board UID (keeps the selected sites), or (UID, {variable: label}) to open
    # an entity board on the clicked series or row.
    link: str | tuple[str, dict[str, str]] | None = None
    # Empty: the labels of the query's first aggregation (see legend()).
    legend: str = ""
    empty: str = "Unknown — missing evidence"
    # A table's value column header: empty means the panel title; None hides a
    # value that only says "this row exists" (an _info series, a firing alert).
    column: str | None = ""
    # A bool's words for 1 and 0. A bare True/False never reaches the board.
    words: tuple[str, str] = ("Yes", "No")
    # Its section in the tab (design.SECTIONS); empty means by kind: see role().
    role: str = ""


@dataclass(frozen=True)
class Board:
    uid: str
    title: str
    domain: str
    level: str
    question: str
    tabs: dict[str, list[Panel]]
    folder: str
    kiosk: bool = False
    # Lay tabs out in sections (headline, status, trends, detail) instead of one grid.
    sections: bool = False


def p(title: str, expr: str, unit: str, explanation: str, kind="timeseries", link=None,
      empty="Unknown — missing evidence", column: str | None = "", words=("Yes", "No"), role=""):
    return Panel(title, expr, unit, explanation, kind, link, empty=empty, column=column, words=words, role=role)


def role(panel: Panel) -> str:
    """A stat is a headline; a table of states or yes/no answers is status; a graph
    is a trend; any other table is detail."""
    if panel.role:
        return panel.role
    if panel.kind == "stat":
        return "headline"
    if panel.kind == "table":
        return "status" if panel.unit in ("state", "coverage", "bool") else "detail"
    return "trends"


def value_header(panel: Panel) -> str | None:
    """The value column's header, or None when the column is hidden."""
    if panel.unit in ("state", "coverage"):
        return "State"
    if panel.unit == "dateTimeAsIso":
        return "When"
    if panel.column is None:
        return None
    return panel.column or panel.title


def mapping(options: dict[str, tuple[str, str | None]]) -> list[dict]:
    """A raw value-mapping list for overrides: value -> (text, colour or None)."""
    return [{"type": "value", "options": {
        value: ({"text": text, "color": colour, "index": i} if colour else {"text": text, "index": i})
        for i, (value, (text, colour)) in enumerate(options.items())}}]


def value_mappings(panel: Panel) -> list[dict]:
    if panel.unit == "state":
        return mapping({k: (v.text, v.color) for k, v in STATE.items()})
    if panel.unit == "coverage":
        return mapping({"0": ("Unknown", UNKNOWN_GREY), "1": ("Reporting", "#2f6bd0")})
    if panel.unit == "bool":
        return mapping({"1": (panel.words[0], None), "0": (panel.words[1], None)})
    return []


def legend(panel: Panel) -> str:
    """A series is named by the labels its query keeps: the first `by (...)`,
    else the site, app and instance. A bucket bound (le) never names a series."""
    if panel.legend:
        return panel.legend
    match = re.search(r"\bby \(([^)]*)\)", panel.expr)
    labels = [label.strip() for label in match[1].split(",")] if match else ["k8s_cluster", "app", "instance"]
    return " ".join(f"{{{{{label}}}}}" for label in labels if label not in ("", "le"))
