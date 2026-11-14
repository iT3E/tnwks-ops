"""Make the rules, the scan lists, the Wazuh map, the board input, the list of gaps and
the maps of the converter from the control registry, or reject stale copies (--check).

The contract of the series and of the registry is in security/README.md. The
script uses the Python standard library and PyYAML, from the pinned project of
observability/: `uv run --project observability python security/generate.py`.
"""

import argparse
import copy
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "security/registry"
CATALOG = ROOT / "security/catalog"
GENERATED = ROOT / "security/generated"
RULES = ROOT / "kubernetes/apps/monitoring/security-registry/rules/rules.yaml"
# The converter (security/converter/README.md). Its pod reads its code and the stream map
# from a ConfigMap, and kustomize takes a file only from the directory of the app.
CONVERTER = ROOT / "security/converter/converter.py"
CONVERTER_CONFIG = ROOT / "kubernetes/apps/security/converter/app/config"
# The CronJobs of Prowler (kubernetes/apps/security/prowler) read the scan list of
# their provider from a ConfigMap of their app, for the same reason.
PROWLER_CONFIG = ROOT / "kubernetes/apps/security/prowler/app/config"

# The series of the contract (security/README.md).
RESULT = "homelab_security_check_result"
LAST_RESULT = "homelab_security_last_result_timestamp_seconds"
SYSTEM_INFO = "homelab:security_system_info"
CONTROL = "homelab:security_control_state"
SAFEGUARD = "homelab:security_safeguard_state"
ACCEPTED = "homelab_security_accepted_until_timestamp_seconds"
HAS_RESULT = "homelab:security_safeguard_has_result"
NO_EVIDENCE, PASS, FAIL = 0, 1, 3
# The Loki label of a security stream. Its value is the check ID of the stream.
STREAM_LABEL = "security_stream"

# Each rule reads the newest sample in this window, so one missed scrape or a
# restart of a source is no gap. A series with no sample in the window is absent.
WINDOW = "10m"
# A time of the last result that is this far in the future is no evidence.
CLOCK_SKEW_SECONDS = 300
LATE_FOR = "10m"
BOARD = "tnwks-security"

TYPES = ("aws-account", "cloudflare-account", "github-organization",
         "terraform-cloud-organization", "cluster", "windows-host", "wsl-distro", "laptop",
         "router", "other")
# The name of each type in the `--checks-file` of Prowler.
PROWLER_PROVIDERS = {"aws-account": "aws", "cloudflare-account": "cloudflare",
                     "github-organization": "github"}
UNITS = {"m": 60, "h": 3600, "d": 86400}


def seconds(duration):
    return int(duration[:-1]) * UNITS[duration[-1]]


# --- The schema check -------------------------------------------------------------------


def validate(schema, value, defs, path="$"):
    """The errors of `value` against the part of JSON Schema that schema.json uses."""
    if "$ref" in schema:
        return validate(defs[schema["$ref"].rsplit("/", 1)[1]], value, defs, path)
    errors = []
    kinds = {"object": dict, "array": list, "string": str}
    kind = schema.get("type")
    if kind and not isinstance(value, kinds[kind]):
        return [f"{path}: is not of the type {kind}"]
    if "enum" in schema and (value not in schema["enum"] or isinstance(value, bool)):
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not fit {schema['pattern']}")
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
            errors.append(f"{path}: the length of {value!r} is out of range")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: has fewer than {schema['minItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) < len(value):
            errors.append(f"{path}: has the same item two times")
        for index, item in enumerate(value):
            errors += validate(schema.get("items", {}), item, defs, f"{path}[{index}]")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        errors += [f"{path}: has no `{key}`" for key in schema.get("required", []) if key not in value]
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path}: the key {key!r} is not a string")
            elif key in properties:
                errors += validate(properties[key], item, defs, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: `{key}` is not a field of the schema")
            else:
                errors += validate(schema.get("propertyNames", {}), key, defs, f"{path}.{key} (key)")
                if isinstance(schema.get("additionalProperties"), dict):
                    errors += validate(schema["additionalProperties"], item, defs, f"{path}.{key}")
    return errors


class RegistryError(Exception):
    """The registry does not fit its schema, or its files do not agree."""


def load(registry=REGISTRY, catalog=CATALOG):
    defs = json.loads((registry / "schema.json").read_text())["$defs"]
    data = {name: yaml.safe_load((registry / f"{name}.yaml").read_text())
            for name in ("systems", "sources", "controls")}
    errors = [f"{name}.yaml {error}" for name in data
              for error in validate(defs[name], data[name], defs)]
    if errors:
        raise RegistryError("\n".join(errors))
    data["catalog"] = {path.stem: json.loads(path.read_text()) for path in sorted(catalog.glob("*.json"))}
    errors = consistency(data)
    if errors:
        raise RegistryError("\n".join(errors))
    return data


def safeguard_key(identifier):
    return tuple(int(part) for part in identifier.split("."))


def named(registry):
    """(safeguard, type, source, check) for each check that a line names."""
    return [(line["id"], system_type, entry["source"], entry["check"])
            for line in registry["controls"] for system_type, entries in line["checks"].items()
            for entry in entries]


def sources_of(registry, system_type):
    """The sources that have a check for the type, in the order of sources.yaml."""
    used = {source for _, checked, source, _ in named(registry) if checked == system_type}
    return [source["name"] for source in registry["sources"] if source["name"] in used]


def consistency(registry):
    """What the schema can not say: the three files and the catalogues agree."""
    errors = []
    for title, values in (("system", [s["name"] for s in registry["systems"]]),
                          ("source", [s["name"] for s in registry["sources"]]),
                          ("safeguard", [line["id"] for line in registry["controls"]])):
        errors += [f"the {title} {value} is in the registry two times"
                   for value in sorted({v for v in values if values.count(v) > 1})]
    ids = [line["id"] for line in registry["controls"]]
    if ids != sorted(ids, key=safeguard_key):
        errors.append("controls.yaml is not in the order of the safeguard IDs")
    sources = {source["name"]: source for source in registry["sources"]}
    for source in registry["sources"]:
        if (source["kind"] == "stream") != ("checks" in source):
            errors.append(f"the source {source['name']}: `checks` is for the kind stream, and only for it")
    for safeguard, system_type, source, check in named(registry):
        where = f"safeguard {safeguard}, {system_type}: the check {check} of {source}"
        if source not in sources:
            errors.append(f"{where} names a source that sources.yaml does not have")
        elif sources[source]["kind"] == "stream" and check not in sources[source]["checks"]:
            errors.append(f"{where} is not a stream of sources.yaml")
        elif source in registry["catalog"] and check not in registry["catalog"][source]["checks"].get(system_type, []):
            errors.append(f"{where} is not in security/catalog/{source}.json for that type")
    for system in registry["systems"]:
        for source in system.get("sources", []):
            if source not in sources_of(registry, system["type"]):
                errors.append(f"the system {system['name']} lists the source {source}, "
                              f"which has no check for the type {system['type']}")
    return errors


# --- The rules --------------------------------------------------------------------------


def late_after(registry, source, system_type, check=None):
    entry = next(item for item in registry["sources"] if item["name"] == source)
    if entry["kind"] == "stream":
        return seconds(entry["checks"][check]["late_after"])
    return seconds(entry.get("by_type", {}).get(system_type, entry)["late_after"])


def matcher(label, values):
    """An equality matcher for one value, and a regular expression for more."""
    if len(values) == 1:
        return f'{label}="{values[0]}"'
    # A dot is the one character of a check name that a regular expression reads
    # as more than itself. PromQL needs the backslash two times in a string.
    return f'{label}=~"{"|".join(value.replace(".", "\\\\.") for value in values)}"'


def fresh(selector, limit):
    """The age of the newest time of the last result, where it is inside its limit."""
    age = f"time() - max by (system) (last_over_time({selector}[{WINDOW}]))"
    return f"(({age}) <= {limit}) >= -{CLOCK_SKEW_SECONDS}"


def evidence(registry, system_type, entries):
    """The state of each system from the checks of one line: 1 pass or 3 fail, for the
    sources whose newest result is fresh. No series for a system with no fresh result."""
    kinds = {source["name"]: source["kind"] for source in registry["sources"]}
    groups = []
    for source in dict.fromkeys(entry["source"] for entry in entries):
        checks = [entry["check"] for entry in entries if entry["source"] == source]
        if kinds[source] == "stream":
            for check in checks:
                selector = f'{LAST_RESULT}{{source="{source}",check="{check}"}}'
                groups.append(f"0 * ({fresh(selector, late_after(registry, source, system_type, check))}) + {PASS}")
            continue
        result = f'{RESULT}{{source="{source}",{matcher("check", checks)}}}'
        failing = f"max by (system) (last_over_time({result}[{WINDOW}])) > bool 0"
        selector = f'{LAST_RESULT}{{source="{source}"}}'
        groups.append(f"({PASS} + {FAIL - PASS} * ({failing})) and on (system) "
                      f"({fresh(selector, late_after(registry, source, system_type))})")
    if len(groups) == 1:
        return groups[0]
    return "max by (system) (" + " or ".join(
        f'label_replace({group}, "evidence", "{index}", "", "")' for index, group in enumerate(groups, 1)) + ")"


def control_expr(registry, system_type, entries):
    """Pass or fail where a fresh result exists, and "no evidence" for each other system
    of the type. The last term is the registry itself, so a system never has no line."""
    info = f'{SYSTEM_INFO}{{system_type="{system_type}"}}'
    none = f"{NO_EVIDENCE} * {info}"
    if not entries:
        return f"max by (system) ({none})"
    return (f"max by (system) ((({evidence(registry, system_type, entries)}) and on (system) {info})"
            f" or on (system) ({none}))")


def present_types(registry):
    """The types that have a system, in the order of TYPES."""
    return [system_type for system_type in TYPES
            if any(system["type"] == system_type for system in registry["systems"])]


def counted_types(registry, line):
    """The types whose systems count for a line: a system is there, and the line has a
    check for the type. A system of a type with an empty list has nothing to run."""
    return [system_type for system_type in present_types(registry) if line["checks"].get(system_type)]


def safeguard_expr(registry, line):
    """The state of a line over the systems that count: a fail when one fails, else
    "no evidence" when one has no fresh result, else a pass. So a line is a pass only
    when each system that has a check for it passes. A line with no check for any
    system stays "no evidence"."""
    every = f'{CONTROL}{{safeguard="{line["id"]}"}}'
    counted = counted_types(registry, line)
    if not counted:
        return f"{NO_EVIDENCE} * max({every})"
    states = f'{CONTROL}{{safeguard="{line["id"]}",{matcher("system_type", counted)}}}'
    return f"(max({states}) == {FAIL}) or clamp_max(min({states}), {PASS})"


def has_result_expr(line):
    """1 when one system or more has a fresh result for the line, a pass or a fail."""
    return f'max({CONTROL}{{safeguard="{line["id"]}"}}) > bool {NO_EVIDENCE}'


def expected(registry):
    """(source, system, stream or None, late after) for each source that reports on a
    system today: what the rule for a late result watches."""
    kinds = {source["name"]: source["kind"] for source in registry["sources"]}
    result = []
    for system in registry["systems"]:
        for source in system.get("sources", []):
            if kinds[source] != "stream":
                result.append((source, system["name"], None, late_after(registry, source, system["type"])))
                continue
            streams = dict.fromkeys(check for _, checked, name, check in named(registry)
                                    if checked == system["type"] and name == source)
            result += [(source, system["name"], check, late_after(registry, source, system["type"], check))
                       for check in streams]
    return result


def late_expr(registry):
    terms = []
    for source, system, check, limit in expected(registry):
        labels = f'source="{source}",system="{system}"' + (f',check="{check}"' if check else "")
        window = f"{LAST_RESULT}{{{labels}}}[{WINDOW}]"
        # absent_over_time is absent() with a stated window: it gives one series, with
        # the labels of the matchers, when the source served no time in the window.
        terms.append(f"max by (source, system) (time() - last_over_time({window}) > {limit})"
                     f" or max by (source, system) (absent_over_time({window}))")
    return " or ".join(terms)


def site_labels():
    return {"k8s_cluster": "${CLUSTER_NAME}"}


def line_number(identifier):
    """A key that sorts as the safeguard IDs do: 4.1 before 10.1."""
    return "{:02d}.{:02d}".format(*safeguard_key(identifier))


# The link of each alert: the security board, on the site whose Prometheus raised it.
BOARD_LINK = f"https://grafana.${{INTERNAL_DOMAIN}}/d/{BOARD}?var-cluster={{{{ $externalLabels.cluster }}}}"


def late_rule(expr):
    """The one alert for a late result. Its text has a system, a source, a severity and a
    link, and no result."""
    return {"alert": "SecurityResultLate", "expr": expr, "for": LATE_FOR,
            "labels": {"severity": "warning"},
            "annotations": {
                "summary": "A security source is late for {{ $labels.system }}",
                "description": ("The newest result of {{ $labels.source }} for {{ $labels.system }} is older "
                                "than its limit, or the source serves no time. The board shows no evidence "
                                "for it."),
                "dashboard_url": BOARD_LINK}}


def accepted_rule():
    """The alert for an accepted finding whose end date has passed. Its text has a system,
    a severity and a link. It names no check, so it names no finding."""
    ends = f"max by (source, check, system) (last_over_time({ACCEPTED}[{WINDOW}]))"
    return {"alert": "SecurityAcceptedFindingExpired", "expr": f"count by (system) ({ends} < time())",
            "for": LATE_FOR, "labels": {"severity": "warning"},
            "annotations": {
                "summary": "An accepted finding of {{ $labels.system }} has passed its end date",
                "description": ("{{ $labels.system }} has an accepted finding whose end date has passed. "
                                "Renew it or remove it in the encrypted list of accepted findings."),
                "dashboard_url": BOARD_LINK}}


def alerts(registry):
    late = [late_rule(late_expr(registry))] if expected(registry) else []
    return [*late, accepted_rule()]


def rule_resource(registry):
    present = present_types(registry)
    records = [{"record": SYSTEM_INFO, "expr": "vector(1)",
                "labels": {"system": system["name"], "system_type": system["type"],
                           "site": system["site"], "domain": system["domain"], **site_labels()}}
               for system in registry["systems"]]
    for line in registry["controls"]:
        for system_type in present:
            records.append({"record": CONTROL,
                            "expr": control_expr(registry, system_type, line["checks"].get(system_type, [])),
                            "labels": {"safeguard": line["id"], "system_type": system_type, **site_labels()}})
        records.append({"record": SAFEGUARD, "expr": safeguard_expr(registry, line),
                        "labels": {"safeguard": line["id"], "line": line_number(line["id"]),
                                   "title": line["title"], "ig": str(line["ig"]),
                                   "function": line["function"], **site_labels()}})
        records.append({"record": HAS_RESULT, "expr": has_result_expr(line),
                        "labels": {"safeguard": line["id"], "ig": str(line["ig"]), **site_labels()}})
    groups = [{"name": "homelab.security.controls", "interval": "1m", "rules": records},
              {"name": "homelab.security.alerts", "interval": "1m", "rules": alerts(registry)}]
    return {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
            "metadata": {"name": "homelab-security", "namespace": "monitoring"},
            "spec": {"groups": groups}}


# --- The other outputs ------------------------------------------------------------------


def scan_lists(registry):
    """The checks that Prowler runs for each provider, in the form of its `--checks-file`."""
    return {"prowler": {provider: sorted({check for _, checked, source, check in named(registry)
                                          if source == "prowler" and checked == system_type})
                        for system_type, provider in PROWLER_PROVIDERS.items()}}


def wazuh_map(registry):
    """The safeguards of each host check: the CIS tags of the pinned release, carried to
    the same check IDs, with the lines of the registry. And the checks with no tag."""
    catalog = registry["catalog"]["wazuh"]
    result = {"release": catalog["release"], "checks": {}, "untagged": {}}
    for system_type, checks in catalog["checks"].items():
        tags = {check: set(catalog["tags"].get(check, [])) for check in checks}
        for safeguard, checked, source, check in named(registry):
            if source == "wazuh" and checked == system_type:
                tags[check].add(safeguard)
        result["checks"][system_type] = {check: sorted(found, key=safeguard_key)
                                         for check, found in tags.items() if found}
        result["untagged"][system_type] = [check for check, found in tags.items() if not found]
    return result


def gaps(registry):
    """Each check of a tool that no line of the registry names, for each type."""
    result = {}
    for source, catalog in registry["catalog"].items():
        result[source] = {"release": catalog["release"], "checks": {}}
        for system_type, checks in catalog["checks"].items():
            used = {check for _, checked, name, check in named(registry)
                    if name == source and checked == system_type}
            result[source]["checks"][system_type] = [check for check in checks if check not in used]
    return result


def board_input(registry):
    """The safeguards, the systems and the cycles that the board shows."""
    sources = []
    for source in registry["sources"]:
        entry = {"name": source["name"], "title": source["title"], "kind": source["kind"],
                 "cycle": source["cycle"], "late_after": source["late_after"],
                 "late_after_seconds": seconds(source["late_after"])}
        if "checks" in source:
            entry["checks"] = {check: times | {"late_after_seconds": seconds(times["late_after"])}
                               for check, times in source["checks"].items()}
        if "by_type" in source:
            entry["by_type"] = {system_type: times | {"late_after_seconds": seconds(times["late_after"])}
                                for system_type, times in source["by_type"].items()}
        sources.append(entry)
    return {
        "board": BOARD,
        "series": {"result": RESULT, "last_result": LAST_RESULT, "system": SYSTEM_INFO,
                   "control": CONTROL, "safeguard": SAFEGUARD, "has_result": HAS_RESULT,
                   "accepted": ACCEPTED},
        "states": {"no evidence": NO_EVIDENCE, "pass": PASS, "fail": FAIL},
        "safeguards": [{"id": line["id"], "line": line_number(line["id"]), "title": line["title"],
                        "ig": line["ig"], "function": line["function"]}
                       for line in registry["controls"]],
        "systems": [{"name": system["name"], "type": system["type"], "site": system["site"],
                     "domain": system["domain"], "sources": system.get("sources", [])}
                    for system in registry["systems"]],
        "sources": sources,
        "expected": [{"source": source, "system": system, "late_after_seconds": limit}
                     | ({"check": check} if check else {})
                     for source, system, check, limit in expected(registry)],
    }


def stream_map(registry):
    """The map of the stream source of the converter: one entry for each security stream
    and each system that has it as a check, with the Loki selector of its lines. A stream
    with a `system_label` has one stream in Loki for each system, and the value of that
    label is the name of the system. A stream with none is one stream, and its newest
    line is the result of each system that has it as a check."""
    entries = []
    for source in registry["sources"]:
        if source["kind"] != "stream":
            continue
        for system in registry["systems"]:
            streams = dict.fromkeys(check for _, checked, name, check in named(registry)
                                    if checked == system["type"] and name == source["name"])
            for check in streams:
                label = source["checks"][check].get("system_label")
                entries.append({
                    "source": source["name"], "check": check, "system": system["name"],
                    "late_after_seconds": late_after(registry, source["name"], system["type"], check),
                    "selector": {STREAM_LABEL: check} | ({label: system["name"]} if label else {})})
    return {"series": LAST_RESULT, "streams": entries}


def converter_registry(registry):
    """The names of the registry that the converter takes a value of a file against: a
    line of the list of accepted findings names a source and a system of the registry."""
    return {"series": {"accepted": ACCEPTED},
            "sources": [source["name"] for source in registry["sources"]],
            "systems": [system["name"] for system in registry["systems"]]}


# --- The tests of the rules -------------------------------------------------------------


def every_source(registry):
    """The registry with each source that has a check for a system in its `sources`:
    the rules when each source runs. The tests of the rule for a late result use it."""
    full = copy.deepcopy(registry)
    for system in full["systems"]:
        system["sources"] = sources_of(full, system["type"])
    return full


def first_system(registry, system_type):
    return next((system["name"] for system in registry["systems"] if system["type"] == system_type), None)


def pick(registry, want):
    """The first line and type that has a system and whose checks fit `want`."""
    kinds = {source["name"]: source["kind"] for source in registry["sources"]}
    for line in registry["controls"]:
        for system_type, entries in line["checks"].items():
            found = [kinds[entry["source"]] for entry in entries]
            if first_system(registry, system_type) and want(entries, found):
                return line["id"], system_type, first_system(registry, system_type), entries
    raise RegistryError("the registry has no line for a test of the rules")


def series(name, value, **labels):
    text = ",".join(f'{key}="{val}"' for key, val in labels.items())
    return {"series": f"{name}{{{text}}}", "values": value}


def fixtures(registry):
    """Tests of the rules of the registry as it is. The values of a time series are Unix
    times, and a test starts at 0, so a time of 0 is as old as the test is long."""
    kinds = {source["name"]: source["kind"] for source in registry["sources"]}
    lines, systems = len(registry["controls"]), len(registry["systems"])

    def sample(safeguard, system_type, system, value, at):
        labels = f'safeguard="{safeguard}",system="{system}",system_type="{system_type}",k8s_cluster="site"'
        return {"expr": f'{CONTROL}{{safeguard="{safeguard}",system="{system}"}}', "eval_time": at,
                "exp_samples": [{"labels": f"{CONTROL}{{{labels}}}", "value": value}]}

    one = lambda entries, found: found and set(found) == {"results"} and len({e["source"] for e in entries}) == 1
    safeguard, system_type, system, entries = pick(registry, one)
    source, check = entries[0]["source"], entries[0]["check"]
    limit = late_after(registry, source, system_type)
    late = f"{limit // 60 + 30}m"
    long = f"0+0x{limit // 60 + 60}"
    tests = [
        {"name": "with no source, each line of the registry is no evidence", "interval": "1m",
         "input_series": [], "promql_expr_test": [
             {"expr": f"count({SAFEGUARD} == {NO_EVIDENCE})", "eval_time": "5m",
              "exp_samples": [{"labels": "{}", "value": lines}]},
             {"expr": f"count({CONTROL} == {NO_EVIDENCE})", "eval_time": "5m",
              "exp_samples": [{"labels": "{}", "value": lines * systems}]},
             {"expr": f"{CONTROL} != {NO_EVIDENCE}", "eval_time": "5m", "exp_samples": []},
             {"expr": f"{SAFEGUARD} != {NO_EVIDENCE}", "eval_time": "5m", "exp_samples": []},
             {"expr": f"count({HAS_RESULT} == 0)", "eval_time": "5m",
              "exp_samples": [{"labels": "{}", "value": lines}]},
         ]},
        {"name": "a result with no time of the last result is no evidence, and never a pass",
         "interval": "1m",
         "input_series": [series(RESULT, "0+0x10", source=source, check=check, system=system)],
         "promql_expr_test": [sample(safeguard, system_type, system, NO_EVIDENCE, "5m")]},
        {"name": "a fresh result is a pass at 0 and a fail above 0, and a late one is no evidence",
         "interval": "1m",
         "input_series": [series(RESULT, f"0+0x9 2+0x{limit // 60 + 50}", source=source, check=check, system=system),
                          series(LAST_RESULT, long, source=source, system=system)],
         "promql_expr_test": [sample(safeguard, system_type, system, PASS, "5m"),
                              sample(safeguard, system_type, system, FAIL, "30m"),
                              sample(safeguard, system_type, system, NO_EVIDENCE, late),
                              {"expr": f'{SAFEGUARD}{{safeguard="{safeguard}"}}', "eval_time": "30m",
                               "exp_samples": [{"labels": safeguard_labels(registry, safeguard), "value": FAIL}]}]},
        {"name": "a time of the last result in the future is no evidence", "interval": "1m",
         "input_series": [series(RESULT, "0+0x10", source=source, check=check, system=system),
                          series(LAST_RESULT, "100000+0x10", source=source, system=system)],
         "promql_expr_test": [sample(safeguard, system_type, system, NO_EVIDENCE, "5m")]},
        {"name": "a result of a system that the registry does not name gives no line", "interval": "1m",
         "input_series": [series(RESULT, "0+0x10", source=source, check=check, system="not-in-the-registry"),
                          series(LAST_RESULT, "0+0x10", source=source, system="not-in-the-registry")],
         "promql_expr_test": [{"expr": f'{CONTROL}{{system="not-in-the-registry"}}', "eval_time": "5m",
                               "exp_samples": []},
                              {"expr": f"count({CONTROL} == {NO_EVIDENCE})", "eval_time": "5m",
                               "exp_samples": [{"labels": "{}", "value": lines * systems}]}]},
    ]
    two = lambda entries, found: len({e["source"] for e in entries if kinds[e["source"]] == "results"}) > 1
    safeguard, system_type, system, entries = pick(registry, two)
    first, second = list(dict.fromkeys(e["source"] for e in entries if kinds[e["source"]] == "results"))[:2]
    check_of = {name: next(e["check"] for e in entries if e["source"] == name) for name in (first, second)}
    tests.append(
        {"name": "with two sources on one line, a fail wins, and one fresh source is enough",
         "interval": "1m",
         "input_series": [series(RESULT, "0+0x20", source=first, check=check_of[first], system=system),
                          series(LAST_RESULT, "0+0x20", source=first, system=system),
                          series(RESULT, "_x10 1+0x10", source=second, check=check_of[second], system=system),
                          series(LAST_RESULT, "_x10 600+0x10", source=second, system=system)],
         "promql_expr_test": [sample(safeguard, system_type, system, PASS, "5m"),
                              sample(safeguard, system_type, system, FAIL, "15m")]})
    stream = lambda entries, found: found and set(found) == {"stream"}
    safeguard, system_type, system, entries = pick(registry, stream)
    source, check = entries[0]["source"], entries[0]["check"]
    limit = late_after(registry, source, system_type, check)
    tests.append(
        {"name": "a stream with a fresh line is a pass, and a silent stream is no evidence", "interval": "1m",
         "input_series": [series(LAST_RESULT, f"0+60x5 300+0x{limit // 60 + 30}",
                                 source=source, check=check, system=system)],
         "promql_expr_test": [sample(safeguard, system_type, system, PASS, "5m"),
                              sample(safeguard, system_type, system, NO_EVIDENCE, f"{limit // 60 + 10}m")]})
    system = registry["systems"][0]["name"]
    source = registry["sources"][0]["name"]
    tests.append(
        {"name": "an accepted finding past its end date raises one alert for its system, with no check in its text",
         "interval": "1m", "external_labels": {"cluster": "site"},
         "input_series": [series(ACCEPTED, "1800+0x60", source=source, check="sample-check", system=system),
                          series(ACCEPTED, "1200+0x60", source=source, check="other-check", system=system),
                          series(ACCEPTED, "864000+0x60", source=source, check="later-check", system=system)],
         "alert_rule_test": [
             {"eval_time": "15m", "alertname": "SecurityAcceptedFindingExpired", "exp_alerts": []},
             {"eval_time": "45m", "alertname": "SecurityAcceptedFindingExpired",
              "exp_alerts": [alert_sample(accepted_rule(), system=system)]}]})
    tests += line_fixtures(registry)
    return {"rule_files": ["rules-for-test.yaml"], "evaluation_interval": "1m", "tests": tests}


def passing(registry, line, system):
    """The input series that give one system a fresh pass for a line, from the first
    check of its type. With `value` above 0 on the result series it is a fail."""
    kinds = {source["name"]: source["kind"] for source in registry["sources"]}
    entry = line["checks"][system["type"]][0]
    if kinds[entry["source"]] == "stream":
        return [series(LAST_RESULT, "0+0x10", source=entry["source"], check=entry["check"], system=system["name"])]
    return [series(RESULT, "0+0x10", source=entry["source"], check=entry["check"], system=system["name"]),
            series(LAST_RESULT, "0+0x10", source=entry["source"], system=system["name"])]


def line_fixtures(registry):
    """Tests of the state of a line over its systems: on the line with the fewest
    systems that count, two or more, whose first one has a result series."""
    kinds = {source["name"]: source["kind"] for source in registry["sources"]}

    def counted(line):
        types = counted_types(registry, line)
        return [system for system in registry["systems"] if system["type"] in types]

    def fits(line):
        systems = counted(line)
        return len(systems) > 1 and kinds[line["checks"][systems[0]["type"]][0]["source"]] == "results"

    candidates = [line for line in registry["controls"] if fits(line)]
    if not candidates:
        raise RegistryError("the registry has no line for a test of the state of a line")
    line = min(candidates, key=lambda item: len(counted(item)))
    systems = counted(line)
    first, rest = systems[0], systems[1:]
    failing = passing(registry, line, first)
    failing[0] = failing[0] | {"values": "1+0x10"}

    def state(value):
        return {"expr": f'{SAFEGUARD}{{safeguard="{line["id"]}"}}', "eval_time": "5m",
                "exp_samples": [{"labels": safeguard_labels(registry, line["id"]), "value": value}]}

    def has_result(value):
        labels = f'{HAS_RESULT}{{safeguard="{line["id"]}",ig="{line["ig"]}",k8s_cluster="site"}}'
        return {"expr": f'{HAS_RESULT}{{safeguard="{line["id"]}"}}', "eval_time": "5m",
                "exp_samples": [{"labels": labels, "value": value}]}

    def control(system, value):
        return {"expr": f'{CONTROL}{{safeguard="{line["id"]}",system="{system["name"]}"}}', "eval_time": "5m",
                "exp_samples": [{"labels": f'{CONTROL}{{safeguard="{line["id"]}",system="{system["name"]}",'
                                 f'system_type="{system["type"]}",k8s_cluster="site"}}', "value": value}]}

    return [
        {"name": "one system passes and a second with a check has no evidence: the line is no evidence, "
                 "and it has a result", "interval": "1m",
         "input_series": passing(registry, line, first),
         "promql_expr_test": [control(first, PASS), control(rest[0], NO_EVIDENCE),
                              state(NO_EVIDENCE), has_result(1)]},
        {"name": "each system with a check passes: the line is a pass, and a system of a type "
                 "with no check does not count", "interval": "1m",
         "input_series": [item for system in systems for item in passing(registry, line, system)],
         "promql_expr_test": [*(control(system, PASS) for system in systems), state(PASS), has_result(1)]},
        {"name": "one system fails: the line is a fail, with or without a result of the others",
         "interval": "1m",
         "input_series": failing,
         "promql_expr_test": [control(first, FAIL), control(rest[0], NO_EVIDENCE),
                              state(FAIL), has_result(1)]},
        {"name": "one system fails and each other system passes: the line is a fail", "interval": "1m",
         "input_series": failing + [item for system in rest for item in passing(registry, line, system)],
         "promql_expr_test": [state(FAIL), has_result(1)]},
    ]


def safeguard_labels(registry, safeguard):
    line = next(item for item in registry["controls"] if item["id"] == safeguard)
    return (f'{SAFEGUARD}{{safeguard="{safeguard}",line="{line_number(safeguard)}",title="{line["title"]}",'
            f'ig="{line["ig"]}",function="{line["function"]}",k8s_cluster="site"}}')


def alert_sample(rule, **labels):
    """The alert that promtool must see for a sample: each label and the whole text."""
    def render(text):
        for label, value in labels.items():
            text = text.replace(f"{{{{ $labels.{label} }}}}", value)
        return text.replace("{{ $externalLabels.cluster }}", "site")
    return {"exp_labels": rule["labels"] | labels,
            "exp_annotations": {key: render(value) for key, value in rule["annotations"].items()}}


def late_alert(source, system):
    return alert_sample(late_rule(""), source=source, system=system)


def late_fixtures(registry):
    """Tests of the rule for a late result, on the registry with each source running."""
    full = every_source(registry)
    watched = expected(full)
    source, system, _, limit = next(item for item in watched if item[2] is None)
    stream = next(item for item in watched if item[2] is not None)
    others = [series(LAST_RESULT, "0+60x3000", source=name, system=target, **({"check": check} if check else {}))
              for name, target, check, _ in watched]
    quiet = lambda *skip: [item for item in others if not any(
        f'source="{name}",system="{target}"' in item["series"] for name, target in skip)]
    minutes = limit // 60
    # A stream that Loki holds one time for each system: it has a line for the first
    # system, and no line for the second.
    by_system = {check for entry in full["sources"] for check, times in entry.get("checks", {}).items()
                 if "system_label" in times}
    silent = [item for item in watched if item[2] in by_system][1]
    one_only = [item for item in others
                if f'source="{silent[0]}",system="{silent[1]}",check="{silent[2]}"' not in item["series"]]
    return {"rule_files": ["rules-every-source-for-test.yaml"], "evaluation_interval": "1m", "tests": [
        {"name": "each source serves a fresh time: no alert", "interval": "1m", "external_labels": {"cluster": "site"},
         "input_series": others,
         "alert_rule_test": [{"eval_time": "1h", "alertname": "SecurityResultLate", "exp_alerts": []}]},
        {"name": "a source that serves no time reads as late", "interval": "1m",
         "external_labels": {"cluster": "site"},
         "input_series": quiet((source, system)),
         "alert_rule_test": [
             {"eval_time": "5m", "alertname": "SecurityResultLate", "exp_alerts": []},
             {"eval_time": "30m", "alertname": "SecurityResultLate", "exp_alerts": [late_alert(source, system)]}]},
        {"name": "a time that stops is late after the limit of its source, and a time that goes away is late too",
         "interval": "1m", "external_labels": {"cluster": "site"},
         "input_series": [*quiet((source, system), (stream[0], stream[1])),
                          series(LAST_RESULT, f"0+0x{minutes + 60}", source=source, system=system),
                          series(LAST_RESULT, "0+60x30 _x3000", source=stream[0], system=stream[1], check=stream[2])],
         "alert_rule_test": [
             {"eval_time": f"{minutes - 5}m", "alertname": "SecurityResultLate",
              "exp_alerts": [late_alert(stream[0], stream[1])] if minutes - 5 > 60 else []},
             {"eval_time": f"{minutes + 30}m", "alertname": "SecurityResultLate",
              "exp_alerts": [late_alert(source, system), late_alert(stream[0], stream[1])]}]},
        {"name": "a stream with a line for one system only reads as late for the other system",
         "interval": "1m", "external_labels": {"cluster": "site"},
         "input_series": one_only,
         "alert_rule_test": [
             {"eval_time": "5m", "alertname": "SecurityResultLate", "exp_alerts": []},
             {"eval_time": "30m", "alertname": "SecurityResultLate",
              "exp_alerts": [late_alert(silent[0], silent[1])]}]},
    ]}


# --- The files --------------------------------------------------------------------------


HEADER = "# Generated by security/generate.py from security/registry. Do not edit.\n"


def dump_yaml(data):
    return "---\n" + HEADER + yaml.safe_dump(data, sort_keys=False, width=110)


def dump_json(data):
    return json.dumps(data, indent=2) + "\n"


def for_test(resource):
    # Flux substitutes the site at reconcile time; promtool sees the name of the tests.
    return dump_yaml({"groups": resource["spec"]["groups"]}).replace("${CLUSTER_NAME}", "site")


def artifacts(registry=None):
    registry = registry or load()
    resource = rule_resource(registry)
    return {
        RULES: dump_yaml(resource),
        GENERATED / "rules-for-test.yaml": for_test(resource),
        GENERATED / "rules-every-source-for-test.yaml": for_test(rule_resource(every_source(registry))),
        GENERATED / "rule-tests.yaml": dump_yaml(fixtures(registry)),
        GENERATED / "rule-tests-every-source.yaml": dump_yaml(late_fixtures(registry)),
        GENERATED / "scan-lists.json": dump_json(scan_lists(registry)),
        GENERATED / "wazuh-map.json": dump_json(wazuh_map(registry)),
        GENERATED / "board.json": dump_json(board_input(registry)),
        GENERATED / "gaps.json": dump_json(gaps(registry)),
        CONVERTER_CONFIG / "streams.json": dump_json(stream_map(registry)),
        CONVERTER_CONFIG / "registry.json": dump_json(converter_registry(registry)),
        CONVERTER_CONFIG / "converter.py": CONVERTER.read_text(),
        **{PROWLER_CONFIG / f"checks-{provider}.json": dump_json({provider: checks})
           for provider, checks in scan_lists(registry)["prowler"].items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when an output is stale")
    args = parser.parse_args()
    try:
        files = artifacts()
    except RegistryError as error:
        raise SystemExit(f"The registry does not fit:\n{error}") from None
    stale = []
    for path, expected_text in files.items():
        if path.exists() and path.read_text() == expected_text:
            continue
        stale.append(path.relative_to(ROOT))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected_text)
    if args.check and stale:
        for path in stale:
            print(f"stale generated artifact: {path}")
        raise SystemExit(1)
    print(f"{'Checked' if args.check else 'Generated'} {len(files)} reproducible artifacts")


if __name__ == "__main__":
    main()
