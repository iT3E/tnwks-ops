"""A declared board to a Grafana v2 dashboard resource: variables, tabs, grid and queries."""

from __future__ import annotations

import json

from grafana_foundation_sdk.builders import dashboardv2 as dashboard
from grafana_foundation_sdk.builders import prometheus, text
from grafana_foundation_sdk.cog.encoder import JSONEncoder
from grafana_foundation_sdk.models import dashboardv2 as model
from grafana_foundation_sdk.models import prometheus as prom_model

from conventions import OBSERVATION_BUDGET_SECONDS, runbook_path

from .design import LABELS, SCRAPE_COLUMNS
from .model import Board, legend, value_header
from .queries import CLUSTERS
from .recipes import visualization


def resource(board: Board) -> dict:
    builder = (dashboard.Dashboard(board.title).description(board.question)
               .editable(False).tags(["tnwks", board.level, board.domain])
               .cursor_sync(model.DashboardCursorSync.CROSSHAIR)
               .time_settings(dashboard.TimeSettings().timezone("browser").from_val("now-6h")
                              .to("now").auto_refresh("1m")
                              .auto_refresh_intervals(["1m", "5m", "15m", "30m", "1h"])))
    datasource = "prometheus-house" if board.kiosk else "${datasource}"
    if not board.kiosk:
        builder.variable(dashboard.DatasourceVariable("datasource").label("Metrics")
                         .plugin_id("prometheus").regex("/^(Global|Local|Site 1|Site 2)$/")
                         .current(model.VariableOption(text="Global", value="prometheus-global")))
        builder.variable(dashboard.CustomVariable("cluster").label("Cluster")
                         .query(",".join(CLUSTERS)).multi(True).include_all(True).all_value(".*")
                         .allow_custom_value(False)
                         .current(model.VariableOption(text="All", value="$__all")))
        builder.links([dashboard.DashboardLink().title("Dashboards").type(model.DashboardLinkType.DASHBOARDS)
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
        if count == 0:
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
            if panel.kind == "table":
                query.format(prom_model.PromQueryFormat.TABLE)
                header = value_header(panel)
                hidden = dict.fromkeys(SCRAPE_COLUMNS, True) | ({} if header else {"Value": True})
                data.transformation(dashboard.Transformation().group("organize").options({
                    "excludeByName": hidden,
                    # A state or yes/no is the answer you scan for: first, where a narrow panel
                    # can't clip it. Other values follow the thing they describe.
                    "indexByName": ({"Value": 0, "k8s_cluster": 1, "app": 2, "db_cluster": 2}
                                    if panel.unit in ("state", "coverage", "bool")
                                    else {"k8s_cluster": 0, "app": 1, "db_cluster": 1}),
                    "renameByName": LABELS | ({"Value": header} if header else {}),
                }))
            description = panel.description
            if not board.kiosk:
                description += (f"\n\nObservation budget: {OBSERVATION_BUDGET_SECONDS}s for current evidence; "
                                "backup/restore objectives and historical windows are separate. "
                                "Source contract: observability/generated/panel-contracts.json.")
            element = (dashboard.Panel().id(count + 1).title(panel.title).description(description)
                       .data(data)
                       .visualization(visualization(panel, None if board.kiosk else runbook_path(board.domain, panel.title))))
            builder.element(key, element)
            grid.with_item(key)
        tab_layout.tab(dashboard.Tab().title(tab_name).layout(grid))
    builder.layout(tab_layout)
    spec = json.loads(json.dumps(builder.build(), cls=JSONEncoder))
    return {"apiVersion": "dashboard.grafana.app/v2", "kind": "Dashboard",
            "metadata": {"name": board.uid}, "spec": spec}
