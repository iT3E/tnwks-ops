"""A declared panel to a Grafana visualization: units, words, colours, legends and links."""

from __future__ import annotations

import functools
import json

from grafana_foundation_sdk.builders import common as common_builder
from grafana_foundation_sdk.builders import dashboardv2 as dashboard
from grafana_foundation_sdk.builders import stat, table, timeseries
from grafana_foundation_sdk.cog.encoder import JSONEncoder
from grafana_foundation_sdk.models import common
from grafana_foundation_sdk.models import dashboardv2 as model

from conventions import dashboard_link

from .design import NEUTRAL, SCRAPE_COLUMNS, SITES, STATE, UNKNOWN_GREY, WHOLE, ZERO_BASED
from .model import Panel, legend, value_header, value_mappings
from .queries import OTHER_BOARDS, ROW_BOARD


@functools.cache
def boards() -> tuple:
    """The operator boards a link can reach. The House kiosk shares Homelab's UID on
    its own Grafana, so it's never a link target."""
    from .boards import catalogue  # the catalogue's boards import this module's callers
    return tuple(board for board in catalogue() if not board.kiosk)


def board_titles() -> dict[str, str]:
    return {board.uid: board.title for board in boards()} | OTHER_BOARDS


def link_title(uid: str) -> str:
    """A data link says where it goes: a click on data only ever goes deeper."""
    return "Open its board" if uid == ROW_BOARD else f"Open {board_titles()[uid]}"


def visualization(panel: Panel):
    cls = {"stat": stat.Visualization, "table": table.Visualization,
           "timeseries": timeseries.Visualization}[panel.kind]
    unit = {"state": "none", "coverage": "none", "bool": "none", "failures": "short"}.get(panel.unit, panel.unit)
    viz = cls().no_value(panel.empty)
    links = []
    if panel.link:
        uid, entity = (panel.link, None) if isinstance(panel.link, str) else panel.link
        if entity and panel.kind == "table":
            assert not set(entity.values()) & set(SCRAPE_COLUMNS), (panel.title, "table hides the entity's field")
        links.append(model.DataLink(title=link_title(uid), url=dashboard_link(uid, entity, panel.kind == "table")))
    if panel.unit in ("state", "coverage"):
        viz.thresholds(dashboard.ThresholdsConfig().mode(model.ThresholdsMode.ABSOLUTE)
                       .steps([model.Threshold(value=None, color=UNKNOWN_GREY)]))
    if panel.kind == "table":
        # Unit, words, colour and links belong to the value column alone: on the
        # defaults they reach the label columns too (a RouterOS version read
        # "7.25 s", and every cell was an underlined link).
        viz.min_width(70)
        # The panel's empty-state words are its noValue, and Grafana prints noValue in every
        # empty cell too: a Flux row in Needs attention read "Nothing needs attention" under
        # App. A label a row doesn't have is a dash.
        viz.override_by_field_type("string", [model.DynamicConfigValue(id_val="noValue", value="—")])
        header = value_header(panel)
        properties = [model.DynamicConfigValue(id_val="unit", value=unit)]
        if value_mappings(panel):
            properties.append(model.DynamicConfigValue(id_val="mappings", value=value_mappings(panel)))
        # Colored text failed WCAG contrast for most states on the light theme
        # (Degraded 2.0:1). State cells are colored backgrounds instead: Grafana
        # writes white text on the dark ones (5.1-5.5:1, the STATE palette) and
        # black on amber (10.2:1). The word carries the state, never the color.
        if panel.unit in ("state", "coverage"):
            properties.append(model.DynamicConfigValue(
                id_val="custom.cellOptions", value={"type": "color-background", "mode": "basic"}))
        if panel.link == ROW_BOARD:
            # The row's board is for its link, not for reading.
            viz.override_by_name("board", [model.DynamicConfigValue(id_val="custom.hidden", value=True)])
        if panel.per_site:
            # Every site column is a value: style them all by type, whatever the sites are.
            assert not panel.link, (panel.title, "a fleet table's row has no single entity to open")
        link_property = model.DynamicConfigValue(
            id_val="links", value=[json.loads(json.dumps(link, cls=JSONEncoder)) for link in links])
        if panel.per_site:
            # A pair with no series isn't at that site: a dash, not the panel's missing-evidence
            # words (a state that is genuinely Unknown still says so).
            properties.append(model.DynamicConfigValue(id_val="noValue", value="—"))
            # ...and without a state colour: a grey dash read as Unknown.
            blank = [{"type": "special", "options": {"match": match, "result": {"text": "—", "color": "transparent"}}}
                     for match in ("empty", "null")]
            for prop in properties:
                if prop.id_val == "mappings":
                    prop.value = prop.value + blank
            viz.override_by_field_type("number", properties + ([link_property] if links else []))
            return viz
        if header:
            viz.override_by_name(header, properties + ([link_property] if links else []))
        elif links:
            # No value column: the row's site carries the links.
            viz.override_by_name("Site", [link_property])
        return viz
    viz.unit(unit)
    if value_mappings(panel):
        viz.mappings([model.ValueMap(options={
            value: model.ValueMappingResult(text=result["text"], color=result.get("color"))
            for value, result in value_mappings(panel)[0]["options"].items()})])
    if panel.kind == "stat" and panel.unit in ("state", "coverage"):
        # Solid, not Grafana's gradient: a flat tile reads calmer, and its contrast can be
        # measured (axe can't judge text on a gradient).
        viz.color_mode(common.BigValueColorMode.BACKGROUND_SOLID)
    if panel.kind == "stat":
        viz.reduce_options(common_builder.ReduceDataOptions().calcs(["lastNotNull"]).values(False))
        if panel.unit == "failures":
            # A count that should be zero: quiet at zero, red at the first one.
            viz.decimals(0).color_scheme(dashboard.FieldColor().mode(model.FieldColorModeId.THRESHOLDS))
            viz.thresholds(dashboard.ThresholdsConfig().mode(model.ThresholdsMode.ABSOLUTE).steps([
                model.Threshold(value=None, color=NEUTRAL), model.Threshold(value=1, color=STATE["3"].color)]))
        elif panel.unit not in ("state", "coverage"):
            viz.color_scheme(dashboard.FieldColor().mode(model.FieldColorModeId.FIXED).fixed_color(NEUTRAL))
    if panel.kind == "timeseries":
        # The SDK leaves the legend off; without it a multi-series graph is unreadable.
        viz.legend(common_builder.VizLegendOptions().show_legend(True)
                   .display_mode(common.LegendDisplayMode.TABLE)
                   .placement(common.LegendPlacement.BOTTOM).calcs(["lastNotNull", "max"]))
        viz.tooltip(common_builder.VizTooltipOptions().mode(common.TooltipDisplayMode.MULTI)
                    .sort(common.SortOrder.DESCENDING))
        # Lines only where there's data: a point per sample turned a 1 degree
        # step into a wall of markers.
        viz.line_width(1).show_points(common.VisibilityMode.NEVER)
        if panel.unit in ZERO_BASED:
            viz.axis_soft_min(0)
        if panel.unit in WHOLE:
            viz.axis_soft_max(WHOLE[panel.unit])
        if legend(panel) == "{{k8s_cluster}}":
            # One series per site: each in its site's colour, on every board.
            for site, colour in SITES.items():
                viz.override_by_name(site, [model.DynamicConfigValue(
                    id_val="color", value={"mode": "fixed", "fixedColor": colour})])
    if links:
        viz.data_links(links)
    return viz
