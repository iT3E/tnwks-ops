"""The Wazuh source of the converter keeps the promises of security/converter/README.md,
"The Wazuh source": the result of each SCA check, the vulnerabilities and the time of the
last report of each agent that maps to a system, read as counts with no document, and no
result and no time at all when a read fails.

A fake indexer on the loopback address answers each search from invented documents, with
the paging of a composite aggregation. Each agent, check and count here is invented. No
test reaches a cluster or an indexer."""

import base64
import json
import logging
import re
import ssl
import sys
import threading
import time
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
import converter
import generate

ROOT = Path(__file__).resolve().parents[2]
RESULT, LAST = generate.RESULT, generate.LAST_RESULT
NOW = 1_800_000_000.0
# A stand-in for the password file, invented for the tests.
STAND_IN = "an invented stand in"
REGISTRY = {
    "series": {"result": RESULT, "last_result": LAST, "accepted": generate.ACCEPTED},
    "sources": ["wazuh"],
    "systems": ["host-one", "distro-one", "host-two"],
    "prowler": {},
    "wazuh_agents": {"agent-one": "host-one", "agent-two": "distro-one", "agent-three": "host-two"},
}


def agent_doc(agent, name, seen_ago=60, at_ago=120):
    seen = None if seen_ago is None else int((NOW - seen_ago) * 1000)
    return {"@timestamp": NOW - at_ago,
            "wazuh": {"agent": {"id": agent, "name": name, "last_seen": seen, "status": "active"}}}


def sca_doc(agent, check, result):
    return {"wazuh": {"agent": {"id": agent}}, "check": {"id": check, "result": result},
            "policy": {"id": "example_policy"}}


def vulnerability_doc(agent, level):
    return {"wazuh": {"agent": {"id": agent}}, "vulnerability": {"id": "CVE-0000-0000", "severity": level}}


AGENTS = [
    agent_doc("000", "manager-node"),
    agent_doc("001", "agent-one", seen_ago=90), agent_doc("001", "agent-one", seen_ago=700, at_ago=700),
    agent_doc("002", "agent-two", seen_ago=30),
]
SCA = [
    sca_doc("001", "26001", "Failed"), sca_doc("001", "26002", "Passed"),
    sca_doc("001", "26003", "Not applicable"),
    sca_doc("002", "35500", "Passed"), sca_doc("002", "35501", "Failed"),
    # A removed agent whose state is still there: it is not in the agent list.
    sca_doc("009", "26001", "Failed"),
]
VULNERABILITIES = [
    vulnerability_doc("001", "High"), vulnerability_doc("001", "Low"), vulnerability_doc("001", "Low"),
    vulnerability_doc("001", "-"), vulnerability_doc("001", None),
]


def field(doc, path):
    for part in path.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


def composite_answer(docs, body):
    """The answer of OpenSearch to a search with one composite aggregation `items`, and
    its sub-aggregations `name` (terms) and `last_seen` (max), as the converter asks."""
    assert body["size"] == 0 and body["track_total_hits"] is False
    items = body["aggs"]["items"]
    spec = items["composite"]
    names = [next(iter(source)) for source in spec["sources"]]
    fields = {name: source[name]["terms"] for source, name in zip(spec["sources"], names, strict=True)}
    groups = {}
    for doc in docs:
        key = tuple(field(doc, fields[name]["field"]) for name in names)
        if any(value is None and not fields[name].get("missing_bucket") for name, value in zip(names, key,
                                                                                              strict=True)):
            continue
        groups.setdefault(key, []).append(doc)
    keys = sorted(groups, key=lambda key: tuple((value is not None, str(value)) for value in key))
    if "after" in spec:
        after = tuple(spec["after"][name] for name in names)
        keys = [key for key in keys if tuple((v is not None, str(v)) for v in key)
                > tuple((v is not None, str(v)) for v in after)]
    page = keys[:spec["size"]]
    buckets = []
    for key in page:
        bucket = {"key": dict(zip(names, key, strict=True)), "doc_count": len(groups[key])}
        for sub, agg in items.get("aggs", {}).items():
            values = [field(doc, next(iter(agg.values()))["field"]) for doc in groups[key]]
            if "terms" in agg:
                counts = {}
                for value in values:
                    if value is not None:
                        counts[value] = counts.get(value, 0) + 1
                bucket[sub] = {"buckets": [{"key": value, "doc_count": count}
                                           for value, count in sorted(counts.items())][:agg["terms"]["size"]]}
            else:
                present = [value for value in values if value is not None]
                bucket[sub] = {"value": float(max(present)) if present else None}
        buckets.append(bucket)
    answer = {"took": 1, "timed_out": False, "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
              "hits": {"hits": []}, "aggregations": {"items": {"buckets": buckets}}}
    if page:
        answer["aggregations"]["items"]["after_key"] = buckets[-1]["key"]
    return answer


STATS = {"_shards": {"total": 3, "successful": 3, "failed": 0}, "indices": {
    "wazuh-states-sca": {"uuid": "x", "total": {"store": {"size_in_bytes": 2048}}},
    ".ds-wazuh-events-v5-example-000001": {"uuid": "y", "total": {"store": {"size_in_bytes": 4096}}},
    ".wazuh-cti-consumers": {"uuid": "z", "total": {"store": {"size_in_bytes": 512}}},
}}


class FakeIndexer(ThreadingHTTPServer):
    """An indexer on the loopback address. `docs` maps an index to its documents, and
    `answers` maps a path to an answer in place of a search: a dict is sent as JSON, a
    number is an HTTP status, bytes are sent as they are, and "sleep" waits."""

    def __init__(self):
        self.docs = {converter.AGENTS_INDEX: list(AGENTS), converter.SCA_INDEX: list(SCA),
                     converter.VULNERABILITIES_INDEX: list(VULNERABILITIES)}
        self.answers, self.requests = {}, []
        super().__init__(("127.0.0.1", 0), self.Handler)
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def close(self):
        self.shutdown()
        self.server_close()

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            server = self.server
            server.requests.append((self.command, self.path, body, self.headers.get("Authorization")))
            path = self.path.split("?")[0]
            found = server.answers.get(path)
            if found == "sleep":
                time.sleep(1)
                found = None
            if found is None:
                index = path.strip("/").split("/")[0]
                if path.endswith("/_search") and index in server.docs:
                    found = composite_answer(server.docs[index], body)
                elif path.endswith("/_stats/store"):
                    found = STATS
                else:
                    found = 404
            if isinstance(found, int):
                self.send_response(found)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = found if isinstance(found, bytes) else json.dumps(found).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = respond

        def log_message(self, *_):
            pass


@pytest.fixture
def indexer():
    server = FakeIndexer()
    yield server
    server.close()


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def time(self):
        return NOW

    def monotonic(self):
        return self.elapsed


def source_for(indexer, tmp_path, clock=None, registry=REGISTRY, timeout=5):
    clock = clock or Clock()
    password = tmp_path / "password"
    if not password.exists():
        password.write_text(STAND_IN + "\n")
    client = converter.Indexer(indexer.url, "tnwks-converter", password, timeout=timeout)
    return converter.WazuhSource(client, registry, clock=clock.time, monotonic=clock.monotonic)


def served(part, name):
    found = {}
    for line in part.render().decode().splitlines():
        if line.startswith((name + "{", name + " ")):
            labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', line))
            found[tuple(sorted(labels.items()))] = float(line.rsplit(" ", 1)[1])
    return found


def results(part):
    """{(check, system): (value, severity)} of the source `wazuh`."""
    found = {}
    for key, value in served(part, RESULT).items():
        labels = dict(key)
        assert labels["source"] == "wazuh" and set(labels) == {"source", "check", "system", "severity"}
        found[(labels["check"], labels["system"])] = (value, labels["severity"])
    return found


def times(part):
    found = {}
    for key, value in served(part, LAST).items():
        labels = dict(key)
        assert labels["source"] == "wazuh" and set(labels) == {"source", "system"}
        found[labels["system"]] = value
    return found


def success(part):
    return {dict(key)["query"]: value for key, value in served(part, converter.WAZUH_QUERY_SUCCESS).items()}


# --- The results ------------------------------------------------------------------------


def test_each_sca_check_the_vulnerabilities_and_the_time_of_each_agent_that_maps(indexer, tmp_path):
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert results(part) == {
        ("26001", "host-one"): (1, "unknown"),
        ("26002", "host-one"): (0, "unknown"),
        # "Not applicable" has no result, as a check with no PASS and no FAIL.
        ("35500", "distro-one"): (0, "unknown"),
        ("35501", "distro-one"): (1, "unknown"),
        # The count at high or critical, always under the severity high.
        ("vulnerabilities", "host-one"): (1, "high"),
        # No document of the vulnerability state is no line: no evidence, not a pass.
    }
    # The time of the last report: the newest last_seen of the snapshots of the agent.
    assert times(part) == {"host-one": NOW - 90, "distro-one": NOW - 30}
    assert {(dict(key)["system"], dict(key)["severity"]): value
            for key, value in served(part, converter.WAZUH_VULNERABILITIES).items()} == {
        ("host-one", "high"): 1, ("host-one", "low"): 2, ("host-one", "unknown"): 2}
    assert success(part) == {"results": 1, "index-stats": 1}
    # The manager (000) is no agent of a system, and is not counted.
    assert served(part, converter.WAZUH_UNMAPPED) == {(): 0}
    assert served(part, converter.WAZUH_SCA_OTHER) == {(): 0}
    text = part.render().decode()
    assert f'{RESULT}{{check="26001",severity="unknown",source="wazuh",system="host-one"}} 1' in text
    assert f'{LAST}{{source="wazuh",system="host-one"}} {NOW - 90:.3f}' in text


@pytest.mark.parametrize("levels, line", [
    # Documents with none at high or critical: a line of 0, a pass.
    (["Low", "Low", "Medium"], (0, "high")),
    (["High", "Low", "Low", "Low"], (1, "high")),
    (["Critical", "High", "High", "Medium"], (3, "high")),
    (["-", None], (0, "high")),
], ids=["medium and lows", "a high and lows", "a critical and highs", "no severity"])
def test_the_vulnerabilities_line_is_the_count_at_high_or_critical(indexer, tmp_path, levels, line):
    """The line always has the severity high, as the line image-vulnerabilities of
    Trivy has one severity, and its value is the count at high or critical."""
    indexer.docs[converter.VULNERABILITIES_INDEX] = [vulnerability_doc("002", level) for level in levels]
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert results(part)[("vulnerabilities", "distro-one")] == line
    assert ("vulnerabilities", "host-one") not in results(part)
    by_severity = {dict(key)["severity"]: value for key, value in served(part, converter.WAZUH_VULNERABILITIES).items()}
    assert sum(by_severity.values()) == len(levels)


def test_the_vulnerabilities_line_rises_for_a_new_high_or_critical_and_not_for_a_new_low(indexer, tmp_path):
    """The values that observability/rules.py (wazuh_vulnerability_fixtures) runs through
    SecurityFindingNew."""
    part = source_for(indexer, tmp_path)
    seen = []
    for levels in (["High"], ["High", "Low"], ["High", "Low", "High"], ["High", "Low", "High", "Critical"],
                   # Each high and critical fixed, then a new high.
                   ["Low"], ["Low", "High"]):
        indexer.docs[converter.VULNERABILITIES_INDEX] = [vulnerability_doc("001", level) for level in levels]
        part.cycle()
        seen.append(results(part)[("vulnerabilities", "host-one")])
    assert seen == [(1, "high"), (1, "high"), (2, "high"), (3, "high"), (0, "high"), (1, "high")]


def test_the_severities_counted_together_are_those_of_the_rule_of_a_new_finding(monkeypatch):
    import importlib.util

    # rules.py imports its neighbours (inventory.py): last on the path.
    monkeypatch.setattr(sys, "path", [*sys.path, str(ROOT / "observability")])
    spec = importlib.util.spec_from_file_location("observability_rules_for_wazuh", ROOT / "observability/rules.py")
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    # The line counts the severities of the rule, under a label that the rule reads.
    assert tuple(rules.SECURITY_FINDING_SEVERITY.split("|")) == converter.FINDING_LEVELS
    assert converter.VULNERABILITIES_SEVERITY in converter.FINDING_LEVELS


def test_an_sca_result_that_is_not_a_value_of_the_release_is_counted_and_has_no_result(indexer, tmp_path, caplog):
    indexer.docs[converter.SCA_INDEX] = [sca_doc("001", "26001", "passed"), sca_doc("001", "26002", "Failed"),
                                         sca_doc("001", "26003", "Not applicable"), sca_doc("002", "35500", "Error")]
    part = source_for(indexer, tmp_path)
    with caplog.at_level(logging.INFO, logger="converter"):
        part.cycle()
        part.cycle()
    sca = {key: value for key, value in results(part).items() if key[0] != "vulnerabilities"}
    assert sca == {("26002", "host-one"): (1, "unknown")}
    assert served(part, converter.WAZUH_SCA_OTHER) == {(): 2}
    notes = [record for record in caplog.records if "not Passed, Failed or Not applicable" in record.getMessage()]
    assert len(notes) == 1 and "Error" not in notes[0].getMessage()


def test_the_size_of_each_index_of_wazuh(indexer, tmp_path):
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert {dict(key)["index"]: value for key, value in served(part, converter.WAZUH_INDEX_SIZE).items()} == {
        "wazuh-states-sca": 2048, ".ds-wazuh-events-v5-example-000001": 4096, ".wazuh-cti-consumers": 512}


def test_the_reads_go_page_by_page(indexer, tmp_path, monkeypatch):
    monkeypatch.setattr(converter, "PAGE_SIZE", 2)
    indexer.docs[converter.SCA_INDEX] = [sca_doc("001", str(26000 + n), "Failed" if n % 3 else "Passed")
                                         for n in range(11)]
    part = source_for(indexer, tmp_path)
    part.cycle()
    sca = {check: value for (check, system), (value, _) in results(part).items() if check != "vulnerabilities"}
    assert sca == {str(26000 + n): (1 if n % 3 else 0) for n in range(11)}
    pages = [body for method, path, body, _ in indexer.requests if path == f"/{converter.SCA_INDEX}/_search"]
    assert len(pages) == 6
    assert "after" not in pages[0]["aggs"]["items"]["composite"]
    assert all("after" in body["aggs"]["items"]["composite"] for body in pages[1:])


def test_an_agent_whose_name_the_registry_does_not_map_has_no_result(indexer, tmp_path, caplog):
    indexer.docs[converter.AGENTS_INDEX].append(agent_doc("003", "agent-nine"))
    indexer.docs[converter.SCA_INDEX].append(sca_doc("003", "26001", "Failed"))
    part = source_for(indexer, tmp_path)
    with caplog.at_level(logging.INFO, logger="converter"):
        part.cycle()
        part.cycle()
    assert {system for _, system in results(part)} == {"host-one", "distro-one"}
    assert served(part, converter.WAZUH_UNMAPPED) == {(): 1}
    # The log names the ID of the agent one time, and not its name.
    notes = [record.getMessage() for record in caplog.records if "does not map" in record.getMessage()]
    assert len(notes) == 1 and "003" in notes[0] and "agent-nine" not in notes[0]


def test_two_agents_with_the_name_of_one_system_give_it_no_result(indexer, tmp_path):
    indexer.docs[converter.AGENTS_INDEX].append(agent_doc("004", "agent-one"))
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert {system for _, system in results(part)} == {"distro-one"}
    assert set(times(part)) == {"distro-one"}


def test_an_agent_with_two_names_in_the_window_has_no_result(indexer, tmp_path):
    indexer.docs[converter.AGENTS_INDEX].append(agent_doc("002", "agent-three", at_ago=300))
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert set(times(part)) == {"host-one"}
    assert {system for _, system in results(part)} == {"host-one"}


@pytest.mark.parametrize("seen_ago", [None, -600, "text"], ids=["no report", "a report in the future",
                                                                  "a time that is not a number"])
def test_an_agent_with_no_time_of_a_report_has_no_result_and_no_time(indexer, tmp_path, monkeypatch, seen_ago):
    if seen_ago == "text":
        # The max aggregation gives a number or null: a text in its place is no time.
        real = converter.composite_page

        def text_time(payload, names):
            buckets, after = real(payload, names)
            for bucket in buckets:
                if bucket["key"].get("agent") == "001" and "last_seen" in bucket:
                    bucket["last_seen"]["value"] = "1800000000000"
            return buckets, after

        monkeypatch.setattr(converter, "composite_page", text_time)
        seen_ago = 60
    indexer.docs[converter.AGENTS_INDEX] = [agent_doc("001", "agent-one", seen_ago=seen_ago),
                                            agent_doc("002", "agent-two")]
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert set(times(part)) == {"distro-one"}
    assert {system for _, system in results(part)} == {"distro-one"}


def test_a_report_within_five_minutes_in_the_future_is_a_time(indexer, tmp_path):
    indexer.docs[converter.AGENTS_INDEX] = [agent_doc("001", "agent-one", seen_ago=-240)]
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert times(part) == {"host-one": NOW + 240}


# --- A read that fails ------------------------------------------------------------------


def not_whole(kind):
    answer = composite_answer(SCA, {"size": 0, "track_total_hits": False, "aggs": {"items": {"composite": {
        "size": 1000, "sources": [{"agent": {"terms": {"field": "wazuh.agent.id"}}},
                                  {"check": {"terms": {"field": "check.id"}}},
                                  {"result": {"terms": {"field": "check.result"}}}]}}}})
    if kind == "timed out":
        answer["timed_out"] = True
    elif kind == "a shard failed":
        answer["_shards"]["failed"] = 1
    elif kind == "no shards":
        del answer["_shards"]
    elif kind == "a key that names no source":
        answer["aggregations"]["items"]["buckets"][0]["key"] = {"agent": "001"}
    elif kind == "no aggregation":
        del answer["aggregations"]
    elif kind == "a check that is not a check ID":
        answer["aggregations"]["items"]["buckets"][0]["key"]["check"] = "a b"
    elif kind == "a check that ends in a line feed":
        answer["aggregations"]["items"]["buckets"][0]["key"]["check"] = "26001\n"
    elif kind == "a count that is not a number":
        answer["aggregations"]["items"]["buckets"][0]["doc_count"] = "1"
    return answer


FAULTS = {
    "an error": 500, "a refusal": 403, "no index": 404, "a redirect": 302, "not JSON": b"<html>",
    "not an object": b"[]", "no answer in time": "sleep",
    **{kind: not_whole(kind) for kind in ("timed out", "a shard failed", "no shards", "a key that names no source",
                                          "no aggregation", "a check that is not a check ID",
                                          "a check that ends in a line feed", "a count that is not a number")},
}
# The agent list: an agent ID that is not digits makes the whole read fail.
AGENT_FAULTS = {
    "an agent ID that is not an agent ID": "agent one", "an agent ID with a line feed": "001\n",
    "an agent ID that is not a text": 1,
}


@pytest.mark.parametrize("path", [converter.AGENTS_INDEX, converter.SCA_INDEX, converter.VULNERABILITIES_INDEX])
@pytest.mark.parametrize("fault", FAULTS.values(), ids=FAULTS.keys())
def test_a_read_that_fails_gives_no_result_and_no_time_for_any_agent(indexer, tmp_path, path, fault, caplog):
    if isinstance(fault, dict) and path != converter.SCA_INDEX:
        pytest.skip("the answers of this case are SCA answers")
    indexer.answers[f"/{path}/_search"] = fault
    part = source_for(indexer, tmp_path, timeout=0.3)
    with caplog.at_level(logging.INFO, logger="converter"):
        part.cycle()
    assert results(part) == {} and times(part) == {}
    assert served(part, converter.WAZUH_VULNERABILITIES) == {}
    assert success(part) == {"results": 0, "index-stats": 1}
    # The size read is apart: its result stays.
    assert len(served(part, converter.WAZUH_INDEX_SIZE)) == 3
    assert all("html" not in record.getMessage() and STAND_IN not in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("agent", AGENT_FAULTS.values(), ids=AGENT_FAULTS.keys())
def test_an_agent_id_that_does_not_have_the_form_gives_no_result(indexer, tmp_path, agent):
    indexer.docs[converter.AGENTS_INDEX] = [agent_doc("001", "agent-one"), agent_doc(agent, "agent-two")]
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert results(part) == {} and times(part) == {}
    assert success(part) == {"results": 0, "index-stats": 1}


@pytest.mark.parametrize("case", ["a full page with no key of the next page", "a page above the size asked"])
def test_a_page_that_does_not_fit_the_paging_gives_no_result(indexer, tmp_path, monkeypatch, case):
    monkeypatch.setattr(converter, "PAGE_SIZE", 2)
    indexer.docs[converter.SCA_INDEX] = [sca_doc("001", str(26000 + n), "Passed") for n in range(5)]
    real = composite_answer

    def answer(docs, body):
        found = real(docs, dict(body, aggs={"items": dict(body["aggs"]["items"], composite=dict(
            body["aggs"]["items"]["composite"], size=3 if case.startswith("a page above") else 2))}))
        if case.startswith("a full page"):
            found["aggregations"]["items"].pop("after_key", None)
        return found

    monkeypatch.setattr(sys.modules[__name__], "composite_answer", answer)
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert results(part) == {} and times(part) == {}
    assert success(part)["results"] == 0


def test_a_result_goes_away_in_the_cycle_in_which_a_read_fails(indexer, tmp_path):
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert results(part) and times(part)
    indexer.answers[f"/{converter.VULNERABILITIES_INDEX}/_search"] = 503
    part.cycle()
    assert results(part) == {} and times(part) == {}
    del indexer.answers[f"/{converter.VULNERABILITIES_INDEX}/_search"]
    part.cycle()
    assert results(part) and times(part)


def test_an_indexer_that_is_down_gives_no_result(tmp_path):
    server = FakeIndexer()
    server.close()
    part = source_for(server, tmp_path, timeout=0.3)
    part.cycle()
    assert results(part) == {} and times(part) == {}
    assert success(part) == {"results": 0, "index-stats": 0}


@pytest.mark.parametrize("bound, value", [("MAX_BUCKETS", 3), ("MAX_PAGES", 2), ("MAX_AGENTS", 1),
                                          ("MAX_CHECKS", 1), ("MAX_ANSWER_BYTES", 200)])
def test_a_read_above_a_bound_gives_no_result(indexer, tmp_path, monkeypatch, bound, value):
    monkeypatch.setattr(converter, "PAGE_SIZE", 2)
    monkeypatch.setattr(converter, bound, value)
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert results(part) == {} and times(part) == {}


def test_a_read_past_its_deadline_gives_no_result(indexer, tmp_path, monkeypatch):
    clock = Clock()
    part = source_for(indexer, tmp_path, clock=clock)
    calls = []
    real = part.indexer.call

    def slow(*args, **kwargs):
        calls.append(args[1])
        clock.elapsed += converter.WAZUH_DEADLINE_SECONDS / 2 + 1
        return real(*args, **kwargs)

    monkeypatch.setattr(part.indexer, "call", slow)
    part.cycle()
    assert results(part) == {} and times(part) == {}
    assert calls[:2] == [f"/{converter.AGENTS_INDEX}/_search", f"/{converter.SCA_INDEX}/_search"]


@pytest.mark.parametrize("stats", [
    403, b"x", {"_shards": STATS["_shards"], "indices": []},
    {"_shards": STATS["_shards"], "indices": {"Bad Name": {"total": {"store": {"size_in_bytes": 1}}}}},
    {"_shards": STATS["_shards"], "indices": {"wazuh-a": {"total": {"store": {"size_in_bytes": -1}}}}},
    {"_shards": STATS["_shards"], "indices": {"wazuh-a": {"total": {"store": {"size_in_bytes": True}}}}},
    {"_shards": {"total": 3, "successful": 2, "failed": 1}, "indices": STATS["indices"]},
    {"indices": STATS["indices"]},
], ids=["a refusal", "not JSON", "not a map", "a name that is not an index name", "a negative size", "a bool",
        "a shard failed", "no shards"])
def test_a_size_read_that_fails_gives_no_size_and_leaves_the_results(indexer, tmp_path, stats):
    indexer.answers["/" + converter.SIZE_PATTERNS + "/_stats/store"] = stats
    part = source_for(indexer, tmp_path)
    part.cycle()
    assert served(part, converter.WAZUH_INDEX_SIZE) == {}
    assert success(part) == {"results": 1, "index-stats": 0}
    assert len(times(part)) == 2


def test_a_fault_of_the_reader_itself_gives_no_result(indexer, tmp_path, monkeypatch, caplog):
    part = source_for(indexer, tmp_path)
    monkeypatch.setattr(converter, "sca_results", lambda buckets: 1 / 0)
    part.cycle()
    assert results(part) == {} and times(part) == {}
    assert "ZeroDivisionError" in caplog.text


def test_a_result_that_no_cycle_replaced_in_two_intervals_goes_away(indexer, tmp_path):
    clock = Clock()
    part = source_for(indexer, tmp_path, clock=clock)
    part.cycle()
    clock.elapsed += 2 * converter.INTERVAL_SECONDS + 1
    assert results(part) == {} and times(part) == {}


# --- What the converter asks, and how ---------------------------------------------------


def test_the_converter_asks_for_counts_only_as_its_user(indexer, tmp_path):
    part = source_for(indexer, tmp_path)
    part.cycle()
    token = "Basic " + base64.b64encode(f"tnwks-converter:{STAND_IN}".encode()).decode()
    paths = set()
    for method, path, body, authorization in indexer.requests:
        assert authorization == token
        paths.add((method, path.split("?")[0]))
        if method == "POST":
            # A search with an aggregation and no document.
            assert body["size"] == 0 and set(body["aggs"]) == {"items"}
            assert "_source" not in body and "docvalue_fields" not in body and "fields" not in body
    assert paths == {("POST", f"/{index}/_search") for index in (
        converter.AGENTS_INDEX, converter.SCA_INDEX, converter.VULNERABILITIES_INDEX)} | {
        ("GET", "/" + converter.SIZE_PATTERNS + "/_stats/store")}
    (agents,) = [body for _, path, body, _ in indexer.requests if path == f"/{converter.AGENTS_INDEX}/_search"]
    # Only the fields of the agent list that the user may read (accounts.json).
    assert agents["query"] == {"range": {"@timestamp": {"gte": "now-1h"}}}
    used = {"@timestamp"} | {agents["aggs"]["items"]["composite"]["sources"][0]["agent"]["terms"]["field"]} | {
        next(iter(agg.values()))["field"] for agg in agents["aggs"]["items"]["aggs"].values()}
    role = json.loads((ROOT / "kubernetes/apps/security/wazuh-settings/app/config/accounts.json").read_text())
    (fls,) = [entry["fls"] for entry in role["roles"]["tnwks-converter"]["index_permissions"] if "fls" in entry]
    assert used <= set(fls)


def test_each_index_that_the_converter_reads_is_one_that_its_role_may_read():
    role = json.loads((ROOT / "kubernetes/apps/security/wazuh-settings/app/config/accounts.json").read_text())
    permissions = role["roles"]["tnwks-converter"]["index_permissions"]
    readable = {pattern for entry in permissions if entry["allowed_actions"] == ["read"]
                for pattern in entry["index_patterns"]}
    assert {converter.AGENTS_INDEX, converter.SCA_INDEX, converter.VULNERABILITIES_INDEX} <= readable
    (stats,) = [entry["index_patterns"] for entry in permissions if entry["allowed_actions"] == ["indices:monitor/stats"]]
    assert converter.SIZE_PATTERNS.split(",") == stats


def test_a_new_password_counts_from_the_next_request(indexer, tmp_path):
    part = source_for(indexer, tmp_path)
    (tmp_path / "password").write_text("Another-invented-2")
    part.cycle()
    token = "Basic " + base64.b64encode(b"tnwks-converter:Another-invented-2").decode()
    assert {authorization for *_, authorization in indexer.requests} == {token}


@pytest.mark.parametrize("content", [None, "", "\n", "x" * 5000], ids=["no file", "empty", "a line feed", "too long"])
def test_with_no_password_nothing_is_asked_and_nothing_is_served(indexer, tmp_path, content):
    part = source_for(indexer, tmp_path)
    if content is None:
        (tmp_path / "password").unlink()
    else:
        (tmp_path / "password").write_text(content)
    part.cycle()
    assert indexer.requests == []
    assert results(part) == {} and times(part) == {}
    assert success(part) == {"results": 0, "index-stats": 0}


def test_the_address_of_the_indexer_is_http_or_https_and_https_checks_the_server(tmp_path):
    with pytest.raises(ValueError):
        converter.Indexer("file:///etc/passwd", "u", tmp_path / "p")
    client = converter.Indexer("https://wazuh-indexer.security.svc.cluster.local:9200", "u", tmp_path / "p")
    context = client.context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize("ca", ["missing", "not a certificate"])
def test_a_ca_file_that_can_not_be_read_gives_no_result_and_stops_nothing_else(indexer, tmp_path, ca):
    """The CA of the server is an optional mount: with no CA the Wazuh source has no
    result, and the converter starts, so its other parts go on."""
    path = tmp_path / "ca.crt"
    if ca != "missing":
        path.write_text("an invented text, not a certificate")
    (tmp_path / "password").write_text(STAND_IN)
    client = converter.Indexer(indexer.url, "tnwks-converter", tmp_path / "password", path)
    part = converter.WazuhSource(client, REGISTRY, clock=lambda: NOW, monotonic=lambda: 0.0)
    part.cycle()
    assert indexer.requests == []
    assert results(part) == {} and times(part) == {}
    assert success(part) == {"results": 0, "index-stats": 0}


def test_a_read_of_many_agents_and_checks_stays_small(tmp_path, monkeypatch):
    """About four times the checks of the Windows policy (482) for each of 20 agents:
    40 000 buckets on 40 pages. The converter keeps one page of the answer and the
    counts, so the read stays some MiB, far under the limit of the pod. The answers are
    made before the measure, as the text that the indexer sends."""
    agents = [f"{n:03d}" for n in range(1, 21)]
    checks = [str(26000 + n) for n in range(2000)]
    sca = [{"key": {"agent": agent, "check": check, "result": "Failed" if int(check) % 2 else "Passed"},
            "doc_count": 1} for agent in agents for check in checks]

    def page(buckets):
        # OpenSearch gives the key of the next page with each page that has a bucket.
        answer = {"timed_out": False, "_shards": {"failed": 0}, "aggregations": {"items": {"buckets": buckets}}}
        if buckets:
            answer["aggregations"]["items"]["after_key"] = buckets[-1]["key"]
        return json.dumps(answer).encode()

    agent_page = page([{"key": {"agent": agent}, "doc_count": 1,
                        "name": {"buckets": [{"key": f"agent-{agent}", "doc_count": 1}]},
                        "last_seen": {"value": (NOW - 60) * 1000}} for agent in agents])
    sca_pages = [page(sca[at:at + converter.PAGE_SIZE]) for at in range(0, len(sca), converter.PAGE_SIZE)]
    answers = {converter.AGENTS_INDEX: iter([agent_page]), converter.SCA_INDEX: iter([*sca_pages, page([])]),
               converter.VULNERABILITIES_INDEX: iter([page([])])}
    registry = REGISTRY | {"systems": [f"agent-{agent}" for agent in agents],
                           "wazuh_agents": {f"agent-{agent}": f"agent-{agent}" for agent in agents}}
    (tmp_path / "password").write_text(STAND_IN)
    client = converter.Indexer("http://127.0.0.1:1", "tnwks-converter", tmp_path / "password")
    part = converter.WazuhSource(client, registry, clock=lambda: NOW, monotonic=lambda: 0.0)

    def call(method, path, body=None):
        index = path.strip("/").split("/")[0]
        if index in answers:
            return json.loads(next(answers[index]))
        raise converter.IndexerError("no stats in this test")

    monkeypatch.setattr(client, "call", call)
    tracemalloc.start()
    part.cycle()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    # The SCA checks: the vulnerability state of this test has no document, so no line.
    assert len(results(part)) == 20 * 2000
    assert peak < 16 * 1024 * 1024, f"{peak / 2**20:.1f} MiB"


# --- The map of the agents, the deployment ----------------------------------------------


def test_the_registry_maps_each_agent_that_the_inventory_names_to_its_system():
    """The agent name is the rollout name that the inventory sets for the Ansible role
    of the agent, and the system of the same host has it as its wazuh_agent."""
    registry = generate.load()
    inventory = {}
    for path in sorted((ROOT / "infrastructure/ansible/inventory/host_vars").glob("*/main.yml")):
        values = yaml.safe_load(path.read_text()) or {}
        for key in ("windows_wazuh_rollout_name", "wsl_wazuh_rollout_name"):
            if key in values:
                inventory[values[key]] = path.parent.name
    assert inventory, "the inventory names no agent"
    assert generate.converter_registry(registry)["wazuh_agents"] == inventory
    names = json.loads((generate.CONVERTER_CONFIG / "registry.json").read_text())
    assert names["wazuh_agents"] == inventory


def test_the_generator_refuses_an_agent_two_times_or_on_a_type_with_no_wazuh_check():
    registry = generate.load()
    systems = registry["systems"]
    host = next(system for system in systems if system.get("wazuh_agent"))
    router = next(system for system in systems if system["type"] == "router")
    broken = registry | {"systems": [*systems, {**host, "name": "another-host"}]}
    assert any("two times" in error for error in generate.consistency(broken))
    broken = registry | {"systems": [{**system, "wazuh_agent": "agent-x"} if system is router else system
                                     for system in systems]}
    assert any("names a Wazuh agent" in error for error in generate.consistency(broken))


def docs(path):
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc]


def test_the_pod_reads_the_indexer_over_https_with_the_ca_only():
    app = ROOT / "kubernetes/apps/security/converter/app"
    (release,) = docs(app / "helmrelease.yaml")
    values = release["spec"]["values"]
    container = values["controllers"]["converter"]["containers"]["app"]
    assert container["env"]["WAZUH_INDEXER_URL"] == "https://wazuh-indexer.security.svc.cluster.local:9200"
    volumes = values["persistence"]
    assert volumes["wazuh"]["volumeSpec"]["secret"] == {
        "secretName": "wazuh-converter-reader", "items": [{"key": "password", "path": "password"}]}
    assert volumes["wazuh"]["globalMounts"] == [{"path": "/wazuh", "readOnly": True}]
    # The CA certificate only: never the key of the CA.
    # Optional: the Secret is of the Wazuh server, and the converter starts without it.
    assert volumes["wazuh-ca"]["volumeSpec"]["secret"] == {
        "secretName": "wazuh-ca", "optional": True, "items": [{"key": "ca.crt", "path": "ca.crt"}]}
    assert volumes["wazuh-ca"]["globalMounts"] == [{"path": "/wazuh-ca", "readOnly": True}]
    resources = yaml.safe_load((app / "kustomization.yaml").read_text())["resources"]
    assert "./secret-wazuh-converter-reader.sops.yaml" in resources


def test_the_network_policy_lets_the_converter_reach_the_indexer_on_9200_only():
    (policy,) = docs(ROOT / "kubernetes/apps/security/converter/app/networkpolicy.yaml")
    to_indexer = [rule for rule in policy["spec"]["egress"]
                  if rule["toEndpoints"][0]["matchLabels"].get("app") == "wazuh-indexer"]
    assert to_indexer == [{"toEndpoints": [{"matchLabels": {
        "io.kubernetes.pod.namespace": "security", "app": "wazuh-indexer",
        "io.cilium.k8s.policy.cluster": "${CLUSTER_NAME}"}}],
        "toPorts": [{"ports": [{"port": "9200", "protocol": "TCP"}]}]}]
    readers = [doc for doc in docs(ROOT / "kubernetes/apps/security/wazuh/app/networkpolicy.yaml")
               if doc["metadata"]["name"] == "wazuh-indexer-readers"]
    (rule,) = readers[0]["spec"]["ingress"]
    assert {"app.kubernetes.io/name": "security-converter"} in [item["matchLabels"] for item in rule["fromEndpoints"]]
