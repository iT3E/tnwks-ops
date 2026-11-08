"""The visual rules the generator promises (notes: "Making Grafana beautiful", P0)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboards import CLUSTERS, LABELS, STATE, UNKNOWN_GREY, catalogue, resource, value_header
from dashboards.design import SECTIONS, SITES


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
        mappings = config["defaults"].get("mappings") or next(
            prop["value"] for o in config["overrides"] for prop in o["properties"] if prop["id"] == "mappings")
        texts = {v["text"] for v in mappings[0]["options"].values()}
        assert not texts & {"True", "False", "1", "0"}, (panel.title, texts)


def test_multi_series_graphs_show_their_legend():
    for board, panel, spec in panels():
        if panel.kind != "timeseries":
            continue
        options = spec["vizConfig"]["spec"]["options"]
        assert options["legend"]["showLegend"] is True, panel.title
        assert options["tooltip"]["mode"] == "multi", panel.title


def contrast(a: str, b: str) -> float:
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_every_site_has_a_colour_visible_on_both_themes():
    """Graphics need 3:1 (WCAG 1.4.11) against Grafana 13's dark and light panel backgrounds."""
    assert set(SITES) == set(CLUSTERS)
    for site, colour in SITES.items():
        for background in ("#181b1f", "#ffffff"):
            assert contrast(colour, background) >= 3, (site, background, round(contrast(colour, background), 2))
    assert not set(SITES.values()) & {r.color for r in STATE.values()}, "a site colour must not read as a state"


def test_operator_tabs_lead_with_the_answer():
    """Sectioned tabs run headline, status, trends, detail, in that order, with About
    folded away at the end."""
    order = [title for title, *_ in SECTIONS.values() if title]
    for board in catalogue():
        if not board.sections:
            continue
        for tab in resource(board)["spec"]["layout"]["spec"]["tabs"]:
            rows = tab["spec"]["layout"]["spec"]["rows"]
            titles = [row["spec"]["title"] for row in rows if not row["spec"].get("hideHeader")]
            assert titles == sorted(titles, key=order.index), (board.uid, tab["spec"]["title"], titles)
            if "About this view" in titles:
                assert rows[-1]["spec"]["title"] == "About this view" and rows[-1]["spec"]["collapse"] is True


def test_house_is_a_wall_of_words():
    """The household kiosk: every tile a state word on the wall grid, no time picker
    to wander into, the About text folded away."""
    house = next(board for board in catalogue() if board.kiosk)
    spec = resource(house)["spec"]
    assert spec["timeSettings"]["hideTimepicker"] is True
    tiles = [panel for panels in house.tabs.values() for panel in panels]
    assert tiles and all(panel.kind == "stat" and panel.unit == "state" and panel.role == "wall" for panel in tiles)
    rows = spec["layout"]["spec"]["tabs"][0]["spec"]["layout"]["spec"]["rows"]
    assert rows[0]["spec"]["layout"]["spec"]["maxColumnCount"] == SECTIONS["wall"][1]
    assert rows[-1]["spec"]["title"] == "About this view" and rows[-1]["spec"]["collapse"] is True
