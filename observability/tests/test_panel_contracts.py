import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_sources import summarize
from contracts import metric_names, panel_contracts, source_contract
from conventions import dashboard_link
from dashboards import catalogue, legend, resource
from rules import records


def test_source_index_distinguishes_labels_functions_and_real_metric_selectors():
    expr = ('histogram_quantile(0.95, sum by (le, app) (rate(http_duration_bucket{app=~"foo|bar"}[5m]))) '
            'or on (app) label_replace(missing_metric, "app", "$1", "pod", "(.+)-[0-9]+")')
    assert metric_names(expr) == ["http_duration_bucket", "missing_metric"]
    with pytest.raises(ValueError, match="No source contract"):
        source_contract("invented_metric")


def test_every_panel_has_a_source_budget_runbook_and_explicit_unimplemented_evidence():
    panels = panel_contracts()
    assert len(panels) == sum(len(ps) for board in catalogue() for ps in board.tabs.values())
    recording = records()
    for panel in panels:
        assert panel["sources"] and panel["inputs"]
        assert panel["observation_budget_seconds"] == 180
        assert panel["missing"] and panel["query_error"] and panel["owner"]
        assert all(source["metric"] not in recording for source in panel["sources"])
        assert (Path(__file__).resolve().parents[2] / panel["runbook"]).is_file()
    planned = {s["metric"] for p in panels for s in p["sources"] if s["availability"] == "planned"}
    assert planned == {"homelab_house_state"}


def test_operator_links_preserve_multiple_sites_and_time_without_exposing_operator_links_to_house():
    assert dashboard_link("tnwks-sites") == (
        '/d/tnwks-sites/view?from=${__from}&to=${__to}'
        '&${cluster:queryparam}&var-datasource=${datasource:percentencode}')
    for board in catalogue():
        for element in resource(board)["spec"]["elements"].values():
            spec = element["spec"]
            if spec["vizConfig"]["group"] == "text":
                continue
            config = spec["vizConfig"]["spec"]["fieldConfig"]
            # A table's links sit on its value (or site) column, not the defaults.
            data = config["defaults"].get("links", []) + [
                link for override in config.get("overrides", []) for prop in override["properties"]
                if prop["id"] == "links" for link in prop["value"]]
            header = spec.get("links", [])
            if board.kiosk:
                assert not data and not header
                continue
            # The runbook is the panel header's link; a click on data only goes deeper
            # into Grafana, in one click, and says where.
            assert [link["title"] for link in header] == ["Runbook"], spec["title"]
            assert header[0]["targetBlank"] is True
            for link in data:
                assert link["url"].startswith("/d/"), (spec["title"], link["url"])
                assert link["title"].startswith("Open ") and link["oneClick"] is True, (spec["title"], link)
            assert "180s" in spec["description"]


def test_every_level_links_up_and_down():
    """Homelab is the top; every other board links up to it, an L3 board to its
    domain board too, and a domain board down to its diagnose boards."""
    boards = {board.uid: board for board in catalogue()}
    for board in boards.values():
        if board.kiosk:
            continue
        bar = [link["title"] for link in resource(board)["spec"]["links"]]
        expected = [] if board.level == "l1" else ["↑ Homelab"]
        if board.parent:
            assert board.level == "l3" and boards[board.parent].level == "l2", board.uid
            expected.append(f"↑ {boards[board.parent].title}")
        expected += [f"↓ {child.title}" for child in boards.values() if child.parent == board.uid]
        assert bar == [*expected, "Dashboards"], (board.uid, bar)
    # Every L3 board has a way up, and its domain board a panel that drills into it.
    for board in boards.values():
        if board.level == "l3":
            assert board.parent, board.uid
            parent_links = [panel.link for panels in boards[board.parent].tabs.values() for panel in panels]
            assert any(link == board.uid or (isinstance(link, tuple) and link[0] == board.uid)
                       for link in parent_links), board.uid


def test_series_are_named_by_the_labels_their_query_keeps():
    named = {panel.title: legend(panel) for board in catalogue() for ps in board.tabs.values() for panel in ps}
    assert named["HTTP requests"] == "{{k8s_cluster}} {{ingress}}"
    assert named["HTTP p95 duration"] == "{{k8s_cluster}} {{ingress}}"
    assert named["Site tunnel traffic"] == "{{interface}}"
    assert named["Node memory available"] == "{{k8s_cluster}} {{app}} {{instance}}"


def test_entity_links_open_the_clicked_series_and_keep_the_incident_time():
    assert dashboard_link("home-wifi", {"ap": "name"}, table=True) == (
        '/d/home-wifi/view?from=${__from}&to=${__to}&var-datasource=${datasource:percentencode}'
        '&var-ap=${__data.fields.name:percentencode}')
    assert dashboard_link("nginx", {"ingress": "ingress"}).endswith(
        '&var-ingress=${__field.labels.ingress:percentencode}')
    targets = {}
    for board in catalogue():
        for panels in board.tabs.values():
            for panel in panels:
                if isinstance(panel.link, tuple):
                    uid, entity = panel.link
                    targets.setdefault(uid, set()).add(panel.title)
                    # Every entity label must survive the panel's own aggregation.
                    for label in entity.values():
                        assert label in panel.expr or "by (" not in panel.expr, (panel.title, label)
    assert set(targets) == {"nginx", "200ac8fdbfbb74b39aff88118e4d1c2c", "home-wifi",
                            "tnwks-host", "tnwks-wifi-clients", "tnwks-wan-clients"}


def test_every_drilldown_lands_on_a_board_that_takes_its_variables():
    """A link to a generated board names one of its pickers; nothing points at a retired UID."""
    boards = {board.uid: board for board in catalogue()}
    root = Path(__file__).resolve().parents[2]
    pinned = {entry["uid"] for entry in json.loads(
        (root / "kubernetes/apps/monitoring/grafana/upstream/catalogue.json").read_text())}
    kept = {json.loads(path.read_text())["uid"]
            for path in (root / "kubernetes/apps/monitoring/grafana/app/dashboard").glob("*.json")}
    # Shipped by kube-prometheus-stack's own dashboards (its k8s-resources-node ConfigMap).
    chart = {"200ac8fdbfbb74b39aff88118e4d1c2c"}
    for board in boards.values():
        for panels in board.tabs.values():
            for panel in panels:
                if not panel.link:
                    continue
                uid, entity = (panel.link, {}) if isinstance(panel.link, str) else panel.link
                assert uid in set(boards) | pinned | kept | chart, (board.uid, panel.title, uid)
                if uid in boards:
                    pickers = {variable.name for variable in boards[uid].variables}
                    assert set(entity) <= pickers, (board.uid, panel.title, set(entity) - pickers)


def test_entity_pickers_read_label_values_from_the_board_datasource():
    for board in catalogue():
        if not board.variables:
            continue
        assert board.level == "l3", board.uid
        spec = {v["spec"]["name"]: v["spec"] for v in resource(board)["spec"]["variables"]}
        for variable in board.variables:
            picker = spec[variable.name]
            assert picker["query"]["spec"]["query"] == variable.query.strip()
            assert picker["query"]["datasource"]["name"] == "${datasource}"
            assert variable.query.startswith("label_values(")
            assert picker["includeAll"] == variable.multi
            # Every panel that filters on the picker uses it.
        used = " ".join(panel.expr for panels in board.tabs.values() for panel in panels)
        assert all(f"${variable.name}" in used for variable in board.variables), board.uid


def test_live_audit_drops_private_labels_and_preserves_partial_query_failure():
    response = {"status": "success", "warnings": ["private upstream error"], "data": {"result": [
        {"metric": {"k8s_cluster": "homelab-wsl", "private_identifier": "redacted"}, "value": [1, "3"]},
        {"metric": {"cluster": "homelab-wsl-02"}, "value": [1, "2"]},
    ]}}
    assert summarize(response) == {"status": "partial", "series_by_site": {"homelab-wsl": 3, "homelab-wsl-02": 2}}
