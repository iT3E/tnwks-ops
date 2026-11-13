"""The registry fits its schema, and the generator keeps the promises of security/README.md."""

import copy
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate
from generate import CONTROL, LAST_RESULT, NO_EVIDENCE, RESULT, SAFEGUARD, SYSTEM_INFO, TYPES


@pytest.fixture(scope="module")
def registry():
    return generate.load()


def schema_errors(name, value):
    defs = json.loads((generate.REGISTRY / "schema.json").read_text())["$defs"]
    return generate.validate(defs[name], value, defs)


def test_the_registry_has_the_48_safeguards_of_ig1_in_order(registry):
    ids = [line["id"] for line in registry["controls"]]
    assert len(ids) == len(set(ids)) == 48
    assert all(line["ig"] == 1 for line in registry["controls"])
    assert not any(identifier.startswith("14.") for identifier in ids), "Control 14 does not apply"
    assert ids == sorted(ids, key=generate.safeguard_key)


def test_the_types_of_the_schema_are_the_types_of_the_generator():
    defs = json.loads((generate.REGISTRY / "schema.json").read_text())["$defs"]
    assert tuple(defs["systemType"]["enum"]) == TYPES
    checked = defs["controls"]["items"]["properties"]["checks"]
    assert tuple(checked["required"]) == tuple(checked["properties"]) == TYPES[:-1], "`other` has no check"


@pytest.mark.parametrize("name, change, message", [
    ("systems", lambda data: data[0].update(type="mainframe"), "is not one of"),
    ("systems", lambda data: data[0].update(site="site-3"), "is not one of"),
    ("systems", lambda data: data[0].pop("domain"), "has no `domain`"),
    ("systems", lambda data: data[0].update(result="fail"), "`result` is not a field"),
    ("systems", lambda data: data[0].update(name="a name"), "does not fit"),
    ("sources", lambda data: data[0].update(late_after="soon"), "does not fit"),
    ("sources", lambda data: data[0].update(kind="guess"), "is not one of"),
    ("controls", lambda data: data[0].update(id="4"), "does not fit"),
    ("controls", lambda data: data[0].update(ig=True), "is not one of"),
    ("controls", lambda data: data[0].update(function="prevent"), "is not one of"),
    ("controls", lambda data: data[0]["checks"].pop("laptop"), "has no `laptop`"),
    ("controls", lambda data: data[0]["checks"].update(other=[]), "`other` is not a field"),
    ("controls", lambda data: data[0]["checks"].update(router=[{"source": "prowler"}]), "has no `check`"),
    ("controls", lambda data: data[0]["checks"].update(router=[{"source": "x", "check": "y", "state": 1}]),
     "`state` is not a field"),
    ("controls", lambda data: data[0].update(finding="open"), "`finding` is not a field"),
])
def test_a_line_that_does_not_fit_the_schema_fails(registry, name, change, message):
    data = copy.deepcopy(registry[name])
    assert schema_errors(name, data) == []
    change(data)
    errors = schema_errors(name, data)
    assert errors and message in errors[0], errors


@pytest.mark.parametrize("change, message", [
    (lambda r: r["systems"].append(dict(r["systems"][0])), "two times"),
    (lambda r: r["controls"].reverse(), "not in the order"),
    (lambda r: r["controls"][0]["checks"]["router"].append({"source": "nobody", "check": "x"}),
     "a source that sources.yaml does not have"),
    (lambda r: r["controls"][0]["checks"]["aws-account"].append({"source": "prowler", "check": "not_a_check"}),
     "is not in security/catalog/prowler.json"),
    (lambda r: r["controls"][0]["checks"]["router"].append({"source": "stream", "check": "not-a-stream"}),
     "is not a stream of sources.yaml"),
    (lambda r: next(s for s in r["systems"] if s["type"] == "other").update(sources=["prowler"]),
     "which has no check for the type other"),
    (lambda r: r["sources"][0].update(checks={}), "`checks` is for the kind stream"),
])
def test_the_three_files_and_the_catalogues_must_agree(registry, change, message):
    data = copy.deepcopy(registry)
    assert generate.consistency(data) == []
    change(data)
    assert any(message in error for error in generate.consistency(data)), generate.consistency(data)


def test_a_file_that_does_not_fit_stops_the_generator(tmp_path, registry):
    for path in generate.REGISTRY.iterdir():
        (tmp_path / path.name).write_text(path.read_text())
    systems = copy.deepcopy(registry["systems"])
    systems[0]["type"] = "mainframe"
    (tmp_path / "systems.yaml").write_text(yaml.safe_dump(systems))
    with pytest.raises(generate.RegistryError, match="systems.yaml .*mainframe"):
        generate.load(tmp_path)


def test_the_registry_holds_no_result(registry):
    """The registry names controls, systems and checks. The schema has no field for a result."""
    schema = (generate.REGISTRY / "schema.json").read_text()
    assert '"additionalProperties": true' not in schema
    for line in registry["controls"]:
        assert set(line) <= {"id", "title", "ig", "function", "nist_800_53", "attack", "checks"}
        for entries in line["checks"].values():
            assert all(set(entry) == {"source", "check"} for entry in entries)
    assert all(set(system) <= {"name", "type", "site", "domain", "sources"} for system in registry["systems"])


def rules(registry):
    return [rule for group in generate.rule_resource(registry)["spec"]["groups"] for rule in group["rules"]]


def test_one_rule_for_each_safeguard_and_type_and_each_ends_in_no_evidence(registry):
    present = {system["type"] for system in registry["systems"]}
    controls = [rule for rule in rules(registry) if rule.get("record") == CONTROL]
    keys = [(rule["labels"]["safeguard"], rule["labels"]["system_type"]) for rule in controls]
    assert len(keys) == len(set(keys)) == len(registry["controls"]) * len(present)
    for rule in controls:
        system_type = rule["labels"]["system_type"]
        # The last term of each rule is the registry itself: a system with no result
        # gets a line, and its value is "no evidence". No rule has a pass as a default.
        term = f'({NO_EVIDENCE} * {SYSTEM_INFO}{{system_type="{system_type}"}})'
        assert rule["expr"].endswith((term, term + ")")), rule
        assert "vector(1)" not in rule["expr"]
        assert rule["labels"]["k8s_cluster"] == "${CLUSTER_NAME}"
    lines = [rule for rule in rules(registry) if rule.get("record") == SAFEGUARD]
    assert [rule["labels"]["safeguard"] for rule in lines] == [line["id"] for line in registry["controls"]]
    assert [rule["labels"]["line"] for rule in lines] == sorted(rule["labels"]["line"] for rule in lines)


def test_a_line_is_a_pass_only_when_each_system_with_a_check_passes(registry):
    present = {system["type"] for system in registry["systems"]}
    lines = {rule["labels"]["safeguard"]: rule["expr"] for rule in rules(registry) if rule.get("record") == SAFEGUARD}
    with_check = 0
    for line in registry["controls"]:
        counted = [kind for kind in TYPES if kind in present and line["checks"].get(kind)]
        every = f'{CONTROL}{{safeguard="{line["id"]}"}}'
        if not counted:
            # No system has a check for the line: it stays "no evidence".
            assert lines[line["id"]] == f"{NO_EVIDENCE} * max({every})"
            continue
        with_check += 1
        states = f'{CONTROL}{{safeguard="{line["id"]}",{generate.matcher("system_type", counted)}}}'
        # A fail of one system wins. Else the least state of the systems that count
        # decides, so one system with no evidence keeps the line from a pass.
        assert lines[line["id"]] == f"(max({states}) == {generate.FAIL}) or clamp_max(min({states}), {generate.PASS})"
        # A type with an empty list is not in the selector: its systems do not count.
        for kind in present - set(counted):
            assert kind not in re.findall(r'system_type=~?"([^"]+)"', lines[line["id"]])[0].split("|")
    assert 0 < with_check < len(registry["controls"])
    results = [rule for rule in rules(registry) if rule.get("record") == generate.HAS_RESULT]
    assert [rule["labels"]["safeguard"] for rule in results] == [line["id"] for line in registry["controls"]]
    for rule in results:
        assert rule["expr"] == f'max({CONTROL}{{safeguard="{rule["labels"]["safeguard"]}"}}) > bool {NO_EVIDENCE}'
        assert set(rule["labels"]) == {"safeguard", "ig", "k8s_cluster"}


def test_a_rule_counts_a_result_only_with_a_fresh_time_of_its_source(registry):
    for rule in rules(registry):
        if rule.get("record") != CONTROL:
            continue
        for source in re.findall(rf'{RESULT}{{source="([a-z-]+)"', rule["expr"]):
            assert f'{LAST_RESULT}{{source="{source}"}}' in rule["expr"], rule["labels"]
    line = next(item for item in registry["controls"] if item["checks"]["laptop"] == [])
    assert generate.control_expr(registry, "laptop", line["checks"]["laptop"]) == (
        f'max by (system) ({NO_EVIDENCE} * {SYSTEM_INFO}{{system_type="laptop"}})')


def test_a_new_system_of_a_known_type_is_one_line(registry):
    data = copy.deepcopy(registry)
    before = rules(data)
    data["systems"].append({"name": "aws-new", "type": "aws-account", "site": "none", "domain": "infrastructure"})
    assert generate.consistency(data) == []
    after = rules(data)
    added = [rule for rule in after if rule not in before]
    assert [rule["labels"]["system"] for rule in added] == ["aws-new"], "the rules select by the type"


def test_the_late_rule_watches_each_source_that_runs_and_no_other(registry):
    full = generate.every_source(registry)
    watched = generate.expected(full)
    assert len(watched) == len(set(watched)) > 0
    (alert,) = [rule for rule in generate.alerts(full) if rule["alert"] == "SecurityResultLate"]
    for source, system, check, limit in watched:
        labels = f'source="{source}",system="{system}"' + (f',check="{check}"' if check else "")
        window = f"{LAST_RESULT}{{{labels}}}[{generate.WINDOW}]"
        # absent_over_time is absent() with a stated window.
        assert f"absent_over_time({window})" in alert["expr"]
        assert f"time() - last_over_time({window}) > {limit}" in alert["expr"]
    assert alert["expr"].count("absent_over_time(") == len(watched)
    no_source = copy.deepcopy(registry)
    for system in no_source["systems"]:
        system.pop("sources", None)
    assert "SecurityResultLate" not in [rule["alert"] for rule in generate.alerts(no_source)]


def test_the_limits_come_from_sources_yaml(registry):
    by_name = {source["name"]: source for source in registry["sources"]}
    assert generate.late_after(registry, "prowler", "aws-account") == generate.seconds(by_name["prowler"]["late_after"])
    assert generate.late_after(registry, "wazuh", "laptop") == 7 * 86400
    assert generate.late_after(registry, "wazuh", "windows-host") == 30 * 60
    assert generate.late_after(registry, "stream", "aws-account", "cloudtrail") == 2 * 3600
    assert generate.late_after(registry, "stream", "cluster", "k8s-audit") == 15 * 60
    data = copy.deepcopy(registry)
    next(source for source in data["sources"] if source["name"] == "prowler")["late_after"] = "48h"
    assert "<= 172800" in json.dumps(generate.rule_resource(data))


def test_the_scan_lists_hold_the_prowler_checks_of_the_registry_and_no_other(registry):
    lists = generate.scan_lists(registry)["prowler"]
    assert set(lists) == {"aws", "cloudflare", "github"}
    used = {check for _, _, source, check in generate.named(registry) if source == "prowler"}
    assert set().union(*lists.values()) == used
    assert all(checks == sorted(set(checks)) for checks in lists.values())


def test_the_gaps_and_the_named_checks_make_the_catalogue(registry):
    gaps = generate.gaps(registry)
    for source, catalog in registry["catalog"].items():
        for system_type, checks in catalog["checks"].items():
            used = {check for _, checked, name, check in generate.named(registry)
                    if name == source and checked == system_type}
            assert used <= set(checks)
            assert sorted(used | set(gaps[source]["checks"][system_type])) == sorted(checks)
            assert not used & set(gaps[source]["checks"][system_type])


def test_the_wazuh_map_carries_each_tag_and_lists_the_checks_with_none(registry):
    result = generate.wazuh_map(registry)
    catalog = registry["catalog"]["wazuh"]
    assert result["release"] == catalog["release"]
    for system_type, checks in catalog["checks"].items():
        tagged, untagged = result["checks"][system_type], result["untagged"][system_type]
        assert sorted([*tagged, *untagged]) == sorted(checks)
        for check, safeguards in tagged.items():
            assert set(catalog["tags"].get(check, [])) <= set(safeguards)
    for safeguard, system_type, source, check in generate.named(registry):
        if source == "wazuh":
            assert safeguard in result["checks"][system_type][check]


def test_the_board_input_has_each_line_each_system_and_each_cycle(registry):
    board = generate.board_input(registry)
    assert [item["id"] for item in board["safeguards"]] == [line["id"] for line in registry["controls"]]
    assert [item["name"] for item in board["systems"]] == [system["name"] for system in registry["systems"]]
    assert {item["name"]: (item["cycle"], item["late_after"]) for item in board["sources"]} == {
        source["name"]: (source["cycle"], source["late_after"]) for source in registry["sources"]}
    assert board["states"] == {"no evidence": 0, "pass": 1, "fail": 3}
    assert board["series"]["has_result"] == generate.HAS_RESULT


def test_the_outputs_in_git_are_the_outputs_of_the_registry(registry):
    """What `generate.py --check` enforces in CI."""
    files = generate.artifacts(registry)
    assert len(files) >= 9
    for path, text in files.items():
        assert path.read_text() == text, f"stale: {path.relative_to(generate.ROOT)}"
    assert generate.artifacts(registry) == files, "the outputs are deterministic"


def test_a_change_of_the_registry_without_its_outputs_is_stale(registry, monkeypatch, capsys):
    data = copy.deepcopy(registry)
    data["controls"][0]["title"] = "A new title"
    monkeypatch.setattr(generate, "load", lambda: data)
    monkeypatch.setattr(sys, "argv", ["generate.py", "--check"])
    with pytest.raises(SystemExit) as stop:
        generate.main()
    assert stop.value.code == 1
    assert "stale generated artifact" in capsys.readouterr().out


def test_the_rule_tests_cover_no_evidence_and_the_late_rule(registry):
    names = [test["name"] for test in generate.fixtures(registry)["tests"]]
    assert "with no source, each line of the registry is no evidence" in names
    assert "a result with no time of the last result is no evidence, and never a pass" in names
    # The state of a line over its systems.
    assert sum(name.startswith(("one system passes and a second with a check has no evidence",
                                "each system with a check passes", "one system fails")) for name in names) == 4
    late = generate.late_fixtures(registry)["tests"]
    assert any(test["name"] == "a source that serves no time reads as late" for test in late)


ALLOWED_LABELS = {"SecurityResultLate": {"source", "system"}, "SecurityAcceptedFindingExpired": {"system"}}


def test_the_text_of_each_alert_is_a_system_a_severity_and_a_link_and_no_raw_event(registry):
    """The Telegram template prints the description, each label of the alert and the
    dashboard link (kubernetes/apps/monitoring/alertmanager/app/config/telegram.tmpl).
    So the labels and the annotations of a rule are the whole text of its alert."""
    found = generate.alerts(generate.every_source(registry))
    assert {rule["alert"] for rule in found} == set(ALLOWED_LABELS)
    for rule in found:
        allowed = ALLOWED_LABELS[rule["alert"]]
        assert rule["labels"] == {"severity": "warning"}
        assert set(rule["annotations"]) == {"summary", "description", "dashboard_url"}
        assert rule["annotations"]["dashboard_url"] == generate.BOARD_LINK
        text = " ".join(rule["annotations"].values())
        used = set(re.findall(r"\{\{ *(\$[A-Za-z.]+) *\}\}", text))
        assert used <= {f"$labels.{label}" for label in allowed} | {"$externalLabels.cluster"}, used
        assert "$value" not in text and "{{ ." not in text, "no value and no raw field in the text"
        # Each term of the expression keeps only the labels of the text: a check, a pod
        # or a path of a result can not reach the alert.
        kept = set(re.findall(r"\bby \(([a-z_, ]+)\) \((?:time\(\) - last_over_time|absent_over_time|max by)", rule["expr"]))
        assert kept == {", ".join(sorted(allowed))}, kept
    template = (generate.ROOT / "kubernetes/apps/monitoring/alertmanager/app/config/telegram.tmpl").read_text()
    printed = set(re.findall(r"\$a\.Annotations\.([a-z_]+)", template))
    assert printed == {"description", "summary", "message", "runbook_url", "dashboard_url"}, printed


def test_a_sample_of_each_alert_has_its_whole_text_in_the_rule_tests(registry):
    """promtool compares each label and each annotation of the sample, so one more
    label on an alert fails CI."""
    late = generate.late_alert("prowler", "aws-prod")
    assert late["exp_labels"] == {"severity": "warning", "source": "prowler", "system": "aws-prod"}
    assert late["exp_annotations"] == {
        "summary": "A security source is late for aws-prod",
        "description": ("The newest result of prowler for aws-prod is older than its limit, or the source "
                        "serves no time. The board shows no evidence for it."),
        "dashboard_url": "https://grafana.${INTERNAL_DOMAIN}/d/tnwks-security?var-cluster=site"}
    accepted = generate.alert_sample(generate.accepted_rule(), system="aws-prod")
    assert accepted["exp_labels"] == {"severity": "warning", "system": "aws-prod"}
    assert "check" not in json.dumps(accepted) and "prowler" not in json.dumps(accepted)
    samples = json.dumps([generate.fixtures(registry), generate.late_fixtures(registry)])
    assert samples.count('"alertname": "SecurityAcceptedFindingExpired"') == 2
    assert samples.count('"alertname": "SecurityResultLate"') >= 4
