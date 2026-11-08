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
            links = config["defaults"].get("links", []) + [
                link for override in config.get("overrides", []) for prop in override["properties"]
                if prop["id"] == "links" for link in prop["value"]]
            if board.kiosk:
                assert not links
            else:
                assert any(link["title"] == "Runbook" for link in links)
                assert "180s" in spec["description"]


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
    assert set(targets) == {"nginx", "200ac8fdbfbb74b39aff88118e4d1c2c", "IV0hu1m7z", "home-wifi"}


def test_live_audit_drops_private_labels_and_preserves_partial_query_failure():
    response = {"status": "success", "warnings": ["private upstream error"], "data": {"result": [
        {"metric": {"k8s_cluster": "homelab-wsl", "private_identifier": "redacted"}, "value": [1, "3"]},
        {"metric": {"cluster": "homelab-wsl-02"}, "value": [1, "2"]},
    ]}}
    assert summarize(response) == {"status": "partial", "series_by_site": {"homelab-wsl": 3, "homelab-wsl-02": 2}}
