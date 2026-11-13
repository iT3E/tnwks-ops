"""The stream source of the converter keeps the promises of security/converter/README.md:
it serves the time of the newest line of each security stream, and never an old time."""

import ast
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
import converter
import generate

NOW = 1_800_000_000.0
SERIES = generate.LAST_RESULT


def entry(check, system, limit=900, **labels):
    return {"source": "stream", "check": check, "system": system, "late_after_seconds": limit,
            "selector": {"security_stream": check, **labels}}


AUDIT_ONE = entry("k8s-audit", "site-one", k8s_cluster="site-one")
AUDIT_TWO = entry("k8s-audit", "site-two", k8s_cluster="site-two")
ROUTER = entry("routeros", "router-one")
TRAIL = entry("cloudtrail", "account-one", limit=7200)


def streams(*entries):
    return {"series": SERIES, "streams": list(entries)}


def answer(labels, *seconds_ago, line="-"):
    """An answer of Loki to a log query: one stream with `labels`, one line for each age."""
    values = [[str(int((NOW - age) * converter.NANOSECONDS)), line] for age in seconds_ago]
    return {"status": "success", "data": {"resultType": "streams",
                                          "result": [{"stream": labels, "values": values}] if values else []}}


EMPTY = {"status": "success", "data": {"resultType": "streams", "result": []}}


class FakeLoki(ThreadingHTTPServer):
    """A Loki on the loopback address. `answers` maps the selector of a query to an
    answer: a dict is sent as JSON, a number is an HTTP status with no body, bytes are
    sent as they are, and "sleep" gives no answer in the time that the converter waits."""

    def __init__(self, answers=None):
        self.answers, self.requests = answers or {}, []
        super().__init__(("127.0.0.1", 0), self.Handler)
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def close(self):
        self.shutdown()
        self.server_close()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            params = {key: value[0] for key, value in urllib.parse.parse_qs(parts.query).items()}
            self.server.requests.append((self.command, parts.path, params))
            found = self.server.answers.get(params.get("query", "").split(" | ")[0], EMPTY)
            if found == "sleep":
                time.sleep(1)
                found = EMPTY
            if isinstance(found, int):
                self.send_response(found)
                self.send_header("Content-Length", "0")
                if found in (301, 302):
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                self.end_headers()
                return
            body = found if isinstance(found, bytes) else json.dumps(found).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_PUT = do_DELETE = do_GET

        def log_message(self, *_):
            pass


@pytest.fixture
def loki():
    server = FakeLoki()
    yield server
    server.close()


class Clock:
    """The wall clock stays at NOW, and the test moves the monotonic clock."""

    def __init__(self):
        self.elapsed = 0.0

    def time(self):
        return NOW

    def monotonic(self):
        return self.elapsed


def source_for(loki, *entries, clock=None, timeout=5):
    clock = clock or Clock()
    return converter.StreamSource(streams(*entries), converter.Loki(loki.url, timeout=timeout),
                                  clock=clock.time, monotonic=clock.monotonic)


def served(source, name=SERIES):
    """{(check, system): value} of one series in what the source serves."""
    found = {}
    for line in source.render().decode().splitlines():
        if line.startswith((name + "{", name + " ")):
            labels = dict(part.split("=", 1) for part in line[line.index("{") + 1:line.index("}")].split(",")
                          ) if "{" in line else {}
            key = (labels.get("check", "").strip('"'), labels.get("system", "").strip('"'))
            found[key] = float(line.rsplit(" ", 1)[1])
    return found


def sel(item):
    return converter.selector(item["selector"])


# --- A line, no line, an error and no answer --------------------------------------------


def test_the_time_of_the_newest_line_of_each_stream_is_the_time_of_the_last_result(loki):
    loki.answers = {sel(AUDIT_ONE): answer(AUDIT_ONE["selector"], 12.5),
                    sel(AUDIT_TWO): answer(AUDIT_TWO["selector"], 40),
                    sel(ROUTER): answer(ROUTER["selector"], 3),
                    sel(TRAIL): answer(TRAIL["selector"], 1800)}
    source = source_for(loki, AUDIT_ONE, AUDIT_TWO, ROUTER, TRAIL)
    source.cycle()
    assert served(source) == {("k8s-audit", "site-one"): NOW - 12.5, ("k8s-audit", "site-two"): NOW - 40,
                              ("routeros", "router-one"): NOW - 3, ("cloudtrail", "account-one"): NOW - 1800}
    text = source.render().decode()
    # The labels of the contract for a stream: source, check and system.
    assert f'{SERIES}{{check="k8s-audit",source="stream",system="site-one"}} {NOW - 12.5:.3f}' in text
    assert set(served(source, converter.QUERY_SUCCESS).values()) == {1.0}


def test_a_time_older_than_the_limit_is_served_as_it_is(loki):
    """The rule compares the time with the limit. The converter does not make it newer."""
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], 2400)}
    source = source_for(loki, ROUTER)
    source.cycle()
    assert served(source) == {("routeros", "router-one"): NOW - 2400}


def test_a_stream_with_no_line_has_no_time(loki):
    loki.answers = {sel(ROUTER): EMPTY}
    source = source_for(loki, ROUTER)
    source.cycle()
    assert served(source) == {}
    assert served(source, converter.QUERY_SUCCESS) == {("routeros", "router-one"): 1.0}, "Loki did answer"


@pytest.mark.parametrize("bad", [
    500, 404, 429, 301,
    b"not json", b"[]", b'{"status":"error","data":{"resultType":"streams","result":[]}}',
    {"status": "success", "data": {"resultType": "matrix", "result": []}},
    {"status": "success", "data": {"resultType": "streams", "result": [{"stream": {}, "values": [["soon", "-"]]}]}},
    {"status": "success", "data": {"resultType": "streams", "result": [{"values": [["1", "-"]]}]}},
    {"status": "success"},
])
def test_an_error_or_an_answer_that_does_not_fit_gives_no_time(loki, bad):
    loki.answers = {sel(ROUTER): bad}
    source = source_for(loki, ROUTER)
    source.cycle()
    assert served(source) == {}
    assert served(source, converter.QUERY_SUCCESS) == {("routeros", "router-one"): 0.0}


def test_a_loki_that_does_not_answer_gives_no_time(loki):
    loki.answers = {sel(ROUTER): "sleep"}
    source = source_for(loki, ROUTER, timeout=0.2)
    source.cycle()
    assert served(source) == {}
    assert served(source, converter.QUERY_SUCCESS) == {("routeros", "router-one"): 0.0}


def test_a_loki_that_is_down_gives_no_time():
    down = FakeLoki()
    down.close()
    clock = Clock()
    source = converter.StreamSource(streams(ROUTER, AUDIT_ONE), converter.Loki(down.url, timeout=1),
                                    clock=clock.time, monotonic=clock.monotonic)
    source.cycle()
    assert served(source) == {}
    assert set(served(source, converter.QUERY_SUCCESS).values()) == {0.0}
    assert source.healthy(), "the loop runs while Loki is down"


def test_an_answer_above_the_size_limit_gives_no_time(loki, monkeypatch):
    monkeypatch.setattr(converter, "MAX_ANSWER_BYTES", 200)
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], 5, line="x" * 400)}
    source = source_for(loki, ROUTER)
    source.cycle()
    assert served(source) == {}


# --- Never an old time as a new one -----------------------------------------------------


def test_after_a_start_the_converter_serves_no_time_before_its_first_cycle(loki):
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], 5)}
    source = source_for(loki, ROUTER)
    text = source.render().decode()
    assert SERIES not in text and converter.QUERY_SUCCESS not in text and converter.LAST_CYCLE not in text
    assert f"{converter.STREAMS} 1" in text
    assert loki.requests == [], "the state is in memory only, so a start has nothing to serve"


def test_a_time_goes_away_in_the_cycle_in_which_loki_stops_to_answer(loki):
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], 5), sel(AUDIT_ONE): answer(AUDIT_ONE["selector"], 7)}
    source = source_for(loki, ROUTER, AUDIT_ONE)
    source.cycle()
    assert len(served(source)) == 2
    loki.answers = {sel(ROUTER): 503, sel(AUDIT_ONE): answer(AUDIT_ONE["selector"], 7)}
    source.cycle()
    assert served(source) == {("k8s-audit", "site-one"): NOW - 7}, "no time of the cycle before"
    loki.answers = {sel(ROUTER): EMPTY, sel(AUDIT_ONE): EMPTY}
    source.cycle()
    assert served(source) == {}


def test_a_result_that_no_cycle_replaced_in_two_intervals_goes_away(loki):
    """A loop that stopped must not serve its last result for ever."""
    clock = Clock()
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], 5)}
    source = source_for(loki, ROUTER, clock=clock)
    source.cycle()
    clock.elapsed = 2 * converter.INTERVAL_SECONDS
    assert served(source) == {("routeros", "router-one"): NOW - 5}
    assert converter.LAST_CYCLE in source.render().decode()
    clock.elapsed = 2 * converter.INTERVAL_SECONDS + 1
    text = source.render().decode()
    assert SERIES not in text and converter.QUERY_SUCCESS not in text and converter.LAST_CYCLE not in text
    assert source.healthy()
    clock.elapsed = 3 * converter.INTERVAL_SECONDS + 1
    assert not source.healthy(), "the liveness probe restarts a converter whose loop stopped"


def test_a_stream_that_exists_for_one_cluster_only_gives_no_time_to_the_other(loki):
    loki.answers = {sel(AUDIT_ONE): answer(AUDIT_ONE["selector"], 9)}
    source = source_for(loki, AUDIT_ONE, AUDIT_TWO)
    source.cycle()
    assert served(source) == {("k8s-audit", "site-one"): NOW - 9}
    asked = [params["query"] for _, _, params in loki.requests]
    assert asked == ['{k8s_cluster="site-one",security_stream="k8s-audit"} | line_format "-"',
                     '{k8s_cluster="site-two",security_stream="k8s-audit"} | line_format "-"']


def test_an_answer_with_the_stream_of_another_system_gives_no_time(loki):
    """Loki answers the query for the second cluster with a line of the first."""
    loki.answers = {sel(AUDIT_TWO): answer(AUDIT_ONE["selector"], 9)}
    source = source_for(loki, AUDIT_TWO)
    source.cycle()
    assert served(source) == {}
    assert served(source, converter.QUERY_SUCCESS) == {("k8s-audit", "site-two"): 0.0}


@pytest.mark.parametrize("age", [-60, 3 * 900 + 3600])
def test_a_time_outside_the_window_of_the_query_gives_no_time(loki, age):
    """A time in the future, or older than the start of the query, is not its answer."""
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], age)}
    source = source_for(loki, ROUTER)
    source.cycle()
    assert served(source) == {}


def test_a_fault_in_one_query_does_not_stop_the_cycle_or_keep_a_time():
    class Faulty:
        def __init__(self):
            self.fail = False

        def newest(self, labels, start, end):
            if self.fail and "k8s_cluster" not in labels:
                raise RuntimeError("a fault")
            return NOW - 4

    clock, loki = Clock(), Faulty()
    source = converter.StreamSource(streams(ROUTER, AUDIT_ONE), loki, clock=clock.time, monotonic=clock.monotonic)
    source.cycle()
    assert len(served(source)) == 2
    loki.fail = True
    source.cycle()
    assert served(source) == {("k8s-audit", "site-one"): NOW - 4}


# --- What the converter asks ------------------------------------------------------------


def test_the_converter_only_reads_and_asks_for_one_time_in_a_window_from_the_limit(loki):
    source = source_for(loki, ROUTER, TRAIL)
    source.cycle()
    (_, _, router), (_, _, trail) = loki.requests
    assert {(method, path) for method, path, _ in loki.requests} == {("GET", "/loki/api/v1/query_range")}
    assert router["query"] == '{security_stream="routeros"} | line_format "-"', "a time, and no text of a line"
    assert (router["limit"], router["direction"]) == ("1", "backward")
    assert int(router["end"]) == int(NOW * converter.NANOSECONDS)
    # Three times the limit of the stream, and one hour at least.
    assert int(router["end"]) - int(router["start"]) == 3600 * converter.NANOSECONDS
    assert int(trail["end"]) - int(trail["start"]) == 3 * 7200 * converter.NANOSECONDS


def test_a_value_of_a_label_can_not_change_the_selector():
    assert converter.selector({"security_stream": 'a"} or {b="c', "k": "x\\"}) == \
        '{k="x\\\\",security_stream="a\\"} or {b=\\"c"}'


def test_the_address_of_loki_is_http_only():
    with pytest.raises(ValueError):
        converter.Loki("file:///etc/passwd")


def test_the_line_of_an_answer_is_not_served_and_not_logged(loki, caplog):
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], 5, line="the-text-of-an-event"),
                    sel(AUDIT_ONE): b'{"status":"success","data":"the-text-of-an-event"}'}
    source = source_for(loki, ROUTER, AUDIT_ONE)
    with caplog.at_level("DEBUG"):
        source.cycle()
    assert "the-text-of-an-event" not in source.render().decode()
    assert "the-text-of-an-event" not in caplog.text
    assert "no answer for the stream k8s-audit of site-one" in caplog.text


# --- The map of the registry ------------------------------------------------------------


def test_the_converter_serves_each_time_that_the_late_rule_can_watch(loki):
    """The stream map in git, with a Loki that has a line for each stream: the series of
    the converter are the series that the rule watches when each system lists the source."""
    registry = generate.load()
    stream_map = json.loads((generate.CONVERTER_CONFIG / "streams.json").read_text())
    assert stream_map == generate.stream_map(registry)
    loki.answers = {sel(item): answer(item["selector"], 30) for item in stream_map["streams"]}
    source = source_for(loki, *stream_map["streams"])
    source.cycle()
    watched = {(check, system) for source_name, system, check, _ in generate.expected(generate.every_source(registry))
               if check is not None}
    assert set(served(source)) == watched and watched
    for line in source.render().decode().splitlines():
        if line.startswith(SERIES):
            assert 'source="stream"' in line


def test_the_copy_that_the_pod_runs_is_the_tested_file():
    assert (generate.CONVERTER_CONFIG / "converter.py").read_text() == generate.CONVERTER.read_text()


def test_the_converter_uses_the_standard_library_only():
    tree = ast.parse(generate.CONVERTER.read_text())
    modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    modules |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)


# --- The service ------------------------------------------------------------------------


def test_the_service_serves_the_metrics_and_its_health(loki):
    loki.answers = {sel(ROUTER): answer(ROUTER["selector"], 5)}
    clock = Clock()
    source = source_for(loki, ROUTER, clock=clock)
    server = ThreadingHTTPServer(("127.0.0.1", 0), converter.handler(source))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        stop = threading.Event()
        loop = threading.Thread(target=source.run, args=(stop,), daemon=True)
        loop.start()
        for _ in range(100):
            if source.last_cycle:
                break
            time.sleep(0.02)
        stop.set()
        with urllib.request.urlopen(base + "/metrics", timeout=5) as response:
            assert response.headers["Content-Type"].startswith("text/plain; version=0.0.4")
            assert f'{SERIES}{{check="routeros",source="stream",system="router-one"}}' in response.read().decode()
        with urllib.request.urlopen(base + "/healthz", timeout=5) as response:
            assert response.status == 200
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(base + "/other", timeout=5)
        assert missing.value.code == 404
        clock.elapsed = 3 * converter.INTERVAL_SECONDS + 1
        with pytest.raises(urllib.error.HTTPError) as stopped:
            urllib.request.urlopen(base + "/healthz", timeout=5)
        assert stopped.value.code == 503
    finally:
        server.shutdown()
        server.server_close()
