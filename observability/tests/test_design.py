"""The visual rules the generator promises (notes: "Making Grafana beautiful", P0)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboards import LABELS, UNKNOWN_GREY, catalogue, resource, value_header  # noqa: E402


def panels():
    for board in catalogue():
        built = resource(board)["spec"]["elements"]
        declared = {p.title: p for ps in board.tabs.values() for p in ps}
        for element in built.values():
            spec = element["spec"]
            if spec["vizConfig"]["group"] == "text":
                continue
            yield board, declared[spec["title"]], spec


def test_no_answer_is_never_painted_healthy():
    """A state panel without a value takes its base threshold colour: Unknown's grey."""
    for board, panel, spec in panels():
        if panel.unit not in ("state", "coverage"):
            continue
        steps = spec["vizConfig"]["spec"]["fieldConfig"]["defaults"]["thresholds"]["steps"]
        assert steps == [{"value": None, "color": UNKNOWN_GREY}], (board.uid, panel.title, steps)


def test_table_values_are_named_and_formatted_on_their_own_column():
    for board, panel, spec in panels():
        if panel.kind != "table":
            continue
        config = spec["vizConfig"]["spec"]["fieldConfig"]
        assert "unit" not in config["defaults"], (panel.title, "a unit on the defaults reaches label columns")
        assert "links" not in config["defaults"], (panel.title, "links on the defaults underline every cell")
        assert "mappings" not in config["defaults"], panel.title
        organize = spec["data"]["spec"]["transformations"][0]["spec"]["options"]
        header = value_header(panel)
        if header is None:
            assert organize["excludeByName"]["Value"] is True, panel.title
        else:
            assert header != "Value" and organize["renameByName"]["Value"] == header, panel.title
        assert set(LABELS) <= set(organize["renameByName"]), panel.title


def test_booleans_are_words():
    for board, panel, spec in panels():
        if panel.unit != "bool":
            continue
        config = spec["vizConfig"]["spec"]["fieldConfig"]
        mappings = config["defaults"].get("mappings") or [
            prop["value"] for o in config["overrides"] for prop in o["properties"] if prop["id"] == "mappings"][0]
        texts = {v["text"] for v in mappings[0]["options"].values()}
        assert not texts & {"True", "False", "1", "0"}, (panel.title, texts)


def test_multi_series_graphs_show_their_legend():
    for board, panel, spec in panels():
        if panel.kind != "timeseries":
            continue
        options = spec["vizConfig"]["spec"]["options"]
        assert options["legend"]["showLegend"] is True, panel.title
        assert options["tooltip"]["mode"] == "multi", panel.title
