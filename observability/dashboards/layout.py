"""A declared board to a Grafana v2 dashboard resource: variables, tabs, grid and queries."""

from __future__ import annotations

import json

from grafana_foundation_sdk.builders import dashboardv2 as dashboard
from grafana_foundation_sdk.builders import prometheus, text
from grafana_foundation_sdk.cog import builder as cogbuilder
from grafana_foundation_sdk.cog.encoder import JSONEncoder
from grafana_foundation_sdk.models import dashboardv2 as model
from grafana_foundation_sdk.models import prometheus as prom_model

from conventions import OBSERVATION_BUDGET_SECONDS, runbook_path

from .design import LABELS, SCRAPE_COLUMNS, SECTIONS
from .model import Board, Variable, legend, role, value_header
from .queries import CLUSTERS
from .recipes import board_titles, boards, visualization


def rows(sections: dict[str, list[str]]):
    """A tab as design.SECTIONS rows, each its own grid sized for what it holds.
    A tab with one kind of content needs no section headers."""
    filled = [name for name, keys in sections.items() if keys]
    one = len([name for name in filled if name != "about"]) == 1
    layout = dashboard.Rows()
    for name in filled:
        title, columns, height, collapsed = SECTIONS[name]
        grid = (dashboard.AutoGrid().max_column_count(columns).column_width_mode("narrow")
                .row_height_mode(height))
        for key in sections[name]:
            grid.with_item(key)
        row = dashboard.Row().layout(grid).collapse(collapsed)
        if title is None or (one and name != "about"):
            row.hide_header(True)
        else:
            row.title(title)
        layout.row(row)
    return layout


class LabelValues(cogbuilder.Builder[model.DataQueryKind]):
    """A Prometheus variable query: the SDK's Query builder only knows panel fields
    (expr), and Grafana reads a variable's label_values() from `query`."""

    def __init__(self, query: str, datasource: str):
        self.query, self.datasource = query, datasource

    def build(self) -> model.DataQueryKind:
        return model.DataQueryKind(group="prometheus", version="v0",
                                   datasource=model.Dashboardv2DataQueryKindDatasource(name=self.datasource),
                                   spec={"qryType": 5, "query": self.query,
                                         "refId": "PrometheusVariableQueryEditor-VariableQuery"})


def entity(variable: Variable, datasource: str):
    picker = (dashboard.QueryVariable(variable.name).label(variable.label)
              .query(LabelValues(variable.query, datasource)).definition(variable.query)
              # The entities seen in the selected range: a client that left an hour ago
              # is still pickable while the range covers it.
              .refresh(model.VariableRefresh.ON_TIME_RANGE_CHANGED)
              .sort(model.VariableSort.ALPHABETICAL_CASE_INSENSITIVE_ASC)
              .multi(variable.multi).include_all(variable.multi).allow_custom_value(False))
    if variable.multi:
        picker.all_value(".*").current(model.VariableOption(text="All", value="$__all"))
    return picker


def hierarchy(board: Board) -> list:
    """The top bar's way up (Homelab, then the parent board) and down (the boards
    whose parent this is), keeping the time range, sites and datasource."""
    def link(uid: str, arrow: str, tooltip: str):
        return (dashboard.DashboardLink().title(f"{arrow} {board_titles()[uid]}").type(model.DashboardLinkType.LINK)
                .url(f"/d/{uid}/view").icon("dashboard").tooltip(tooltip).keep_time(True).include_vars(True))
    up = [] if board.level == "l1" else [link("home", "↑", "Up to the overview")]
    if board.parent:
        up.append(link(board.parent, "↑", "Up to the domain board"))
    down = [link(child.uid, "↓", "Down to the detail for one host or device")
            for child in boards() if child.parent == board.uid]
    return up + down


def one_click(spec: dict) -> dict:
    """A value has at most one data link, and a click follows it: no menu to pick from."""
    for element in spec["elements"].values():
        config = element["spec"]["vizConfig"]["spec"].get("fieldConfig", {})
        lists = [config.get("defaults", {}).get("links", [])] + [
            prop["value"] for override in config.get("overrides", []) for prop in override["properties"]
            if prop["id"] == "links"]
        for links in lists:
            assert len(links) <= 1, (element["spec"]["title"], links)
            for item in links:
                item["oneClick"] = True
    return spec


def resource(board: Board) -> dict:
    builder = (dashboard.Dashboard(board.title).description(board.question)
               .editable(False).tags(["tnwks", board.level, board.domain])
               .cursor_sync(model.DashboardCursorSync.CROSSHAIR)
               .time_settings(dashboard.TimeSettings().timezone("browser").from_val("now-6h")
                              .to("now").auto_refresh("1m")
                              .auto_refresh_intervals(["1m", "5m", "15m", "30m", "1h"])
                              # The household reads now, not a range: no picker to wander into.
                              .hide_timepicker(board.kiosk)))
    datasource = "prometheus-house" if board.kiosk else "${datasource}"
    if not board.kiosk:
        builder.variable(dashboard.DatasourceVariable("datasource").label("Metrics")
                         .plugin_id("prometheus").regex("/^(Global|Local|Site 1|Site 2)$/")
                         .current(model.VariableOption(text="Global", value="prometheus-global")))
        builder.variable(dashboard.CustomVariable("cluster").label("Cluster")
                         .query(",".join(CLUSTERS)).multi(True).include_all(True).all_value(".*")
                         .allow_custom_value(False)
                         .current(model.VariableOption(text="All", value="$__all")))
        for variable in board.variables:
            builder.variable(entity(variable, datasource))
        builder.links([*hierarchy(board),
                       dashboard.DashboardLink().title("Dashboards").type(model.DashboardLinkType.DASHBOARDS)
                       .tags(["tnwks"]).as_dropdown(True).keep_time(True).include_vars(True)])
    tab_layout = dashboard.Tabs()
    count = 0
    about = (dashboard.Panel().id(1).title("About this view")
             .description("Purpose, scope and the interpretation of missing evidence.")
             .visualization(text.Visualization().content(board.question + "\n\n"
                 + ("Missing evidence is Unknown."
                    if board.kiosk else "Missing or partial evidence is Unknown. "
                       + f"[Runbook](https://github.com/iT3E/tnwks-ops/blob/main/{runbook_path(board.domain)})"))))
    builder.element("about", about)
    for tab_name, panels in board.tabs.items():
        grid = (dashboard.AutoGrid().max_column_count(3).column_width_mode("narrow")
                .row_height_mode("standard" if board.level == "l2" else "short"))
        sections: dict[str, list[str]] = {name: [] for name in SECTIONS}
        if count == 0:
            if board.sections:
                sections["about"].append("about")
            else:
                grid.with_item("about")
        for panel in panels:
            count += 1
            key = f"panel-{count}"
            query = (prometheus.Query().expr(panel.expr).legend_format(legend(panel))
                     .datasource(dashboard.Dashboardv2DataQueryKindDatasource().name(datasource)))
            if panel.kind in ("stat", "table"):
                query.instant()
            else:
                query.range()
            data = dashboard.QueryGroup().target(dashboard.Target().ref_id("A").query(query))
            if panel.kind == "table" and panel.per_site:
                query.format(prom_model.PromQueryFormat.TABLE)
                # Rows by the label, a column per site: which site has what, at a glance.
                data.transformation(dashboard.Transformation().group("groupingToMatrix").options({
                    "rowField": panel.per_site, "columnField": "k8s_cluster", "valueField": "Value",
                    # No series for a pair: blank, as the row table had no row.
                    "emptyValue": "empty"}))
                data.transformation(dashboard.Transformation().group("organize").options({
                    "indexByName": {f"{panel.per_site}\\k8s_cluster": 0} | {site: i + 1 for i, site in enumerate(CLUSTERS)},
                    "renameByName": {f"{panel.per_site}\\k8s_cluster": LABELS[panel.per_site]}}))
                # Rows in name order, so tables of the same things line up.
                data.transformation(dashboard.Transformation().group("sortBy").options({
                    "sort": [{"field": LABELS[panel.per_site]}]}))
            elif panel.kind == "table":
                query.format(prom_model.PromQueryFormat.TABLE)
                header = value_header(panel)
                hidden = dict.fromkeys(SCRAPE_COLUMNS, True) | ({} if header else {"Value": True})
                data.transformation(dashboard.Transformation().group("organize").options({
                    "excludeByName": hidden,
                    # A state or yes/no is the answer you scan for: first, where a narrow panel
                    # can't clip it. Other values follow the thing they describe.
                    "indexByName": ({"Value": 0, "k8s_cluster": 1, "app": 2, "db_cluster": 2}
                                    if panel.unit in ("state", "coverage", "bool")
                                    else {"k8s_cluster": 0, "client": 0, "app": 1, "db_cluster": 1}),
                    "renameByName": LABELS | ({"Value": header} if header else {}),
                }))
                if panel.sort:
                    assert header, (panel.title, "sorting needs the value column")
                    data.transformation(dashboard.Transformation().group("sortBy").options({
                        "sort": [{"field": header, "desc": panel.sort == "desc"}]}))
                if panel.order:
                    assert panel.order not in LABELS, (panel.title, "the ordering label keeps its raw name")
                    data.transformation(dashboard.Transformation().group("sortBy").options({
                        "sort": [{"field": panel.order}]}))
            description = panel.description
            if not board.kiosk:
                description += (f"\n\nObservation budget: {OBSERVATION_BUDGET_SECONDS}s for current evidence; "
                                "backup/restore objectives and historical windows are separate. "
                                "Source contract: observability/generated/panel-contracts.json.")
            element = (dashboard.Panel().id(count + 1).title(panel.title).description(description)
                       .data(data)
                       .visualization(visualization(panel)))
            if not board.kiosk:
                # The runbook is a header link, apart from the data: a click on a value
                # only ever goes deeper into Grafana.
                element.links([dashboard.DataLink().title("Runbook").target_blank(True)
                               .url(f"https://github.com/iT3E/tnwks-ops/blob/main/{runbook_path(board.domain, panel.title)}")])
            builder.element(key, element)
            grid.with_item(key)
            sections[role(panel)].append(key)
        tab_layout.tab(dashboard.Tab().title(tab_name).layout(rows(sections) if board.sections else grid))
    builder.layout(tab_layout)
    spec = one_click(json.loads(json.dumps(builder.build(), cls=JSONEncoder)))
    return {"apiVersion": "dashboard.grafana.app/v2", "kind": "Dashboard",
            "metadata": {"name": board.uid}, "spec": spec}
