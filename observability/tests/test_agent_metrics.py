"""The metrics of the product (step 3 of the AI board; docs/apps/agent-platform.md, "Metrics").

The static tests read the files. The filter of Alloy keeps the 8 instruments of
the design, the transform keeps the labels of each one and its declared
values, the declared models and providers are the ones of the agent configs,
each personal agent sends metrics to Alloy and Alloy to the OTLP path of
Prometheus, and the network rule lets Alloy, and only Alloy, write there.

The container test runs the pinned Alloy with the config of the HelmRelease and
the pinned Prometheus with its OTLP path on. It sends metrics as the product
sends them, with a marker on a data point, on the resource and on the scope,
an instrument off the list, values that are not declared, and a service name
that is not one of the three. Then it reads each series of Prometheus: the
marker is nowhere, the instrument off the list is not there, the values read
other and undeclared, and target_info does not exist. It needs Docker:
without it the test is skipped, except in CI, where it fails.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "observability"))
from versions import ALLOY as ALLOY_IMAGE
from versions import PROMETHEUS

ALLOY = ROOT / "kubernetes/apps/monitoring/alloy/app/helmrelease.yaml"
PROMETHEUS_RELEASE = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml"
POLICY = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/networkpolicy.yaml"
AGENTS = ROOT / "kubernetes/apps/ai/agents"
DOMAINS = ("pilot", "finance", "main")

# The table of the design: each instrument and the labels that it keeps.
KEEP = {
    "openclaw.message.received": ["openclaw.channel", "openclaw.source"],
    "openclaw.message.processed": ["openclaw.channel", "openclaw.outcome"],
    "openclaw.message.delivery.started": ["openclaw.channel", "openclaw.delivery.kind"],
    "openclaw.message.delivery.duration_ms": ["openclaw.channel", "openclaw.delivery.kind", "openclaw.outcome",
                                              "openclaw.errorCategory"],
    "openclaw.tokens": ["openclaw.agent", "openclaw.channel", "openclaw.provider", "openclaw.model",
                        "openclaw.token"],
    "openclaw.session.turn.created": ["openclaw.agent", "openclaw.channel", "openclaw.trigger"],
    "openclaw.session.stuck": ["openclaw.state"],
    "openclaw.model.failover": ["openclaw.provider", "openclaw.model", "openclaw.failover.to_provider",
                                "openclaw.failover.to_model"],
}


def config_text() -> str:
    return yaml.safe_load(ALLOY.read_text())["spec"]["values"]["alloy"]["configMap"]["content"]


def block(name: str) -> str:
    text = config_text()
    start = text.index(name)
    return text[start:text.index("\n}\n", start)]


def statements() -> list[str]:
    return re.findall(r"`([^`]+)`", block('otelcol.processor.transform "agent_metrics"'))


def value_lists() -> dict:
    found = {}
    for s in statements():
        m = re.fullmatch(r'set\(attributes\["([^"]+)"\], "other"\) where attributes\["\1"\] != nil and '
                         r'not IsMatch\(attributes\["\1"\], "\^\(([^)]+)\)\$"\)', s)
        if m:
            found[m.group(1)] = m.group(2).split("|")
    return found


def agent_config(domain: str) -> dict:
    return json.loads((AGENTS / domain / "agent/config/openclaw.json").read_text())


def test_the_filter_keeps_the_8_instruments_of_the_design():
    (condition,) = re.findall(r"`([^`]+)`", block('otelcol.processor.filter "agent_metrics"'))
    pattern = re.fullmatch(r'not IsMatch\(name, "(.+)"\)', condition).group(1).replace("\\\\", "\\")
    for name in KEEP:
        assert re.fullmatch(pattern, name), name
    for name in ("openclaw.message.queued", "openclaw.cost.usd", "openclaw.skill.used", "gen_ai.client.token.usage",
                 "openclaw.tokensx", "xopenclaw.tokens", "openclaw.session.state"):
        assert not re.fullmatch(pattern, name), name
    assert 'error_mode = "propagate"' in block('otelcol.processor.filter "agent_metrics"')


def test_the_transform_keeps_the_labels_of_each_instrument_and_cuts_the_resource():
    kept = {}
    for s in statements():
        m = re.fullmatch(r'keep_keys\(attributes, \[([^\]]*)\]\) where metric\.name == "([^"]+)"', s)
        if m:
            kept[m.group(2)] = re.findall(r'"([^"]+)"', m.group(1))
    assert kept == KEEP
    text = block('otelcol.processor.transform "agent_metrics"')
    assert 'error_mode = "propagate"' in text
    assert '`keep_keys(attributes, ["service.name"])`' in text
    assert '"^openclaw-agents-(pilot|finance|main)$"' in text
    assert '`keep_keys(attributes, [])`' in text and '`set(version, "")`' in text
    # Each kept label has a value list, and nothing else gets one.
    assert set(value_lists()) == {label for labels in KEEP.values() for label in labels}
    assert statements()[-1] == 'set(attributes["k8s_cluster"], "${CLUSTER_NAME}")'


def test_the_declared_models_and_providers_are_the_ones_of_the_agent_configs():
    lists = value_lists()
    providers, models, channels = {"unknown"}, {"unknown"}, set()
    for domain in DOMAINS:
        config = agent_config(domain)
        for provider, entry in config["models"]["providers"].items():
            providers.add(provider)
            models.update(m["id"] for m in entry["models"])
        channels.update(config.get("channels", {}))
        assert not config.get("agents", {}).get("list"), "a named agent needs its id in the list of openclaw.agent"
    assert set(lists["openclaw.provider"]) == set(lists["openclaw.failover.to_provider"]) == providers
    assert set(lists["openclaw.model"]) == set(lists["openclaw.failover.to_model"]) == models
    assert channels <= set(lists["openclaw.channel"])
    assert lists["openclaw.agent"] == ["main", "unknown"]


# The agents that send metrics. They turn on one at a time, each in its own
# merge, because a change of openclaw.json restarts the pod of that agent:
# pilot, then finance, then main (docs/apps/agent-platform.md, "Metrics").
METRICS_ON = ("pilot", "finance", "main")


def test_each_personal_agent_sends_its_metrics_to_alloy_and_no_text():
    for domain in DOMAINS:
        otel = agent_config(domain)["diagnostics"]["otel"]
        assert otel["metrics"] is (domain in METRICS_ON), domain
        assert otel["traces"] is True and otel["logs"] is False
        assert otel["serviceName"] == f"openclaw-agents-{domain}"
        assert otel["endpoint"] == "http://alloy.monitoring.svc.cluster.local:4318"
        assert not otel.get("captureContent")
        assert "metricNamePrefix" not in otel
    workflows = AGENTS / "workflows"
    for path in workflows.rglob("openclaw.json"):
        assert json.loads(path.read_text()).get("diagnostics", {}).get("otel", {}).get("metrics") is not True


def test_alloy_writes_to_the_otlp_path_of_this_site_and_prometheus_takes_it():
    text = block('otelcol.exporter.otlphttp "prometheus"')
    assert 'endpoint = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090/api/v1/otlp"' in text
    spec = yaml.safe_load(PROMETHEUS_RELEASE.read_text())["spec"]["values"]["prometheus"]["prometheusSpec"]
    assert spec["enableOTLPReceiver"] is True
    assert not spec.get("enableRemoteWriteReceiver"), "the remote write path stays off"


def test_the_network_rule_lets_alloy_and_only_alloy_write():
    (doc,) = [d for d in yaml.safe_load_all(POLICY.read_text()) if d]
    writers = []
    for rule in doc["spec"]["ingress"]:
        for to in rule["toPorts"]:
            for h in (to.get("rules") or {}).get("http", []):
                if h["method"] == "POST" and re.fullmatch(h["path"], "/api/v1/otlp/v1/metrics"):
                    writers.append((rule.get("fromEndpoints"), h))
    assert writers == [([{"matchLabels": {"io.kubernetes.pod.namespace": "monitoring",
                                          "app.kubernetes.io/name": "alloy",
                                          "app.kubernetes.io/instance": "alloy",
                                          "io.cilium.k8s.policy.cluster": "${CLUSTER_NAME}"}}],
                         {"method": "POST", "path": "/api/v1/otlp/v1/metrics"})]


# The histogram of the delivery time has the default bounds of the SDK (15,
# service-constants.ts at 2026.11.3, OTEL_DEFAULT_HISTOGRAM_BUCKETS): 16
# buckets, then the count and the sum, so 18 series for each set of labels.
HISTOGRAM_SERIES = 18
GATE = 5000


def estimate() -> int:
    """The method of the design (section 4.2.8): the count of the label values
    of each metric, times the buckets plus two for each histogram. The values
    are the ones that the three configs can make: the channels of each config,
    one agent, one provider and the model of the domain, each value of the
    lists for the rest, and the class of the error only with outcome error."""
    lists = value_lists()

    def real(label):
        return len([v for v in lists[label] if v not in ("unknown", "other")])
    total = 0
    for domain in DOMAINS:
        config = agent_config(domain)
        channels = len([c for c, entry in config.get("channels", {}).items() if entry.get("enabled", True)]) or 1
        outcomes = real("openclaw.outcome") - 1 + real("openclaw.errorCategory")
        total += (channels * real("openclaw.source") + channels * real("openclaw.outcome")
                  + channels * real("openclaw.delivery.kind")
                  + channels * real("openclaw.delivery.kind") * outcomes * HISTOGRAM_SERIES
                  + channels * real("openclaw.token") + channels * real("openclaw.trigger")
                  + real("openclaw.state") + real("openclaw.model"))
    return total


def worst_case() -> int:
    """Each value of each list, other and absent too, for each name of a sender
    and undeclared: the bound that the lists set, which real traffic does not
    reach (one model and one agent for each domain)."""
    lists = value_lists()
    total = 0
    for name, labels in KEEP.items():
        series = 1
        for label in labels:
            series *= len(set(lists[label]) | {"other"}) + 1
        total += series * (HISTOGRAM_SERIES if name.endswith("duration_ms") else 1)
    return total * (len(DOMAINS) + 1)


def test_the_estimate_of_the_series_is_under_the_gate():
    assert estimate() < GATE / 4, estimate()
    print("estimate", estimate(), "worst case of the lists", worst_case())


# The container test

MARKER = "agent-metrics-marker-" + secrets.token_hex(4)
ALLOY_HTTP, OTLP, PROM = 13511, 13518, 13590


def docker() -> str:
    if shutil.which("docker"):
        return "docker"
    if os.environ.get("CI"):
        pytest.fail("Docker is needed to run the container test of the agent metrics")
    pytest.skip("docker not installed")


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()


def eventually(function, timeout: float = 60):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return function()
        except (OSError, ValueError, AssertionError, KeyError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def lab_config() -> str:
    text = config_text()
    text = text.replace("${SECRET_DOMAIN}", "example.invalid").replace("${CLUSTER_NAME}", "homelab-test")
    text = text.replace('endpoint = "0.0.0.0:4318"', f'endpoint = "127.0.0.1:{OTLP}"')
    text = text.replace("listen_address           = \"0.0.0.0\"", 'listen_address           = "127.0.0.1"')
    text = text.replace("http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090",
                        f"http://127.0.0.1:{PROM}")
    assert "${" not in text
    return text


def attrs(values: dict) -> list:
    out = []
    for key, value in values.items():
        if isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        elif isinstance(value, list):
            out.append({"key": key, "value": {"arrayValue": {"values": [{"stringValue": v} for v in value]}}})
        else:
            out.append({"key": key, "value": {"stringValue": value}})
    return out


def sum_metric(name: str, points: list[dict]) -> dict:
    now = str(time.time_ns())
    start = str(time.time_ns() - 60 * 10**9)
    return {"name": name, "unit": "1", "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": [
        {"attributes": attrs(p), "startTimeUnixNano": start, "timeUnixNano": now, "asInt": "3"} for p in points]}}


def histogram(name: str, points: list[dict]) -> dict:
    now = str(time.time_ns())
    start = str(time.time_ns() - 60 * 10**9)
    return {"name": name, "unit": "ms", "histogram": {"aggregationTemporality": 2, "dataPoints": [
        {"attributes": attrs(p), "startTimeUnixNano": start, "timeUnixNano": now, "count": "2", "sum": 300,
         "explicitBounds": [100, 1000], "bucketCounts": ["1", "1", "0"]} for p in points]}}


def payload(service: str, metrics: list[dict], resource_extra: dict | None = None) -> bytes:
    resource = {"service.name": service, "host.name": MARKER, "process.command_args": ["node", MARKER],
                "process.pid": 4242, "marker.resource": MARKER, **(resource_extra or {})}
    return json.dumps({"resourceMetrics": [{
        "resource": {"attributes": attrs(resource)},
        "scopeMetrics": [{"scope": {"name": "openclaw", "version": MARKER,
                                    "attributes": attrs({"scope.marker": MARKER})},
                          "metrics": metrics}]}]}).encode()


def send(body: bytes) -> None:
    request = urllib.request.Request(f"http://127.0.0.1:{OTLP}/v1/metrics", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200


ALL = {"openclaw.channel": "telegram", "openclaw.source": "dispatchInboundMessage", "openclaw.outcome": "completed",
       "openclaw.delivery.kind": "text", "openclaw.errorCategory": "Error", "openclaw.agent": "main",
       "openclaw.provider": "litellm", "openclaw.model": "agents-pilot", "openclaw.token": "input",
       "openclaw.trigger": "user", "openclaw.state": "processing", "openclaw.failover.to_provider": "litellm",
       "openclaw.failover.to_model": "agents-main",
       # Never kept (the design): each one carries the marker.
       "openclaw.sessionKey": MARKER, "openclaw.session_key": MARKER, "openclaw.chatId": MARKER,
       "openclaw.messageId": MARKER, "openclaw.skill.name": MARKER, "openclaw.security.actor.id_hash": MARKER,
       "openclaw.reason": MARKER, "openclaw.failover.reason": MARKER, "openclaw.lane": MARKER,
       "openclaw.failover.suspended": MARKER, "openclaw.marker": MARKER, "k8s_cluster": MARKER}


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    names = [f"obs-test-{secrets.token_hex(4)}-{part}" for part in ("prom", "alloy")]
    base = Path(tempfile.mkdtemp(prefix="agent-metrics-"))
    base.chmod(0o755)
    (base / "config.alloy").write_text(lab_config())
    (base / "prometheus.yml").write_text("global:\n  scrape_interval: 1m\n")
    for f in base.iterdir():
        f.chmod(0o644)
    try:
        subprocess.run([tool, "run", "-d", "--name", names[0], "--network", "host", "--read-only",
                        "--memory", "512m", "--cpus", "1", "--tmpfs", "/prometheus:uid=65534",
                        "--mount", f"type=bind,src={base},dst=/etc/test,readonly", PROMETHEUS,
                        "--config.file=/etc/test/prometheus.yml", "--storage.tsdb.path=/prometheus",
                        f"--web.listen-address=127.0.0.1:{PROM}", "--web.enable-otlp-receiver"],
                       check=True, capture_output=True, text=True, timeout=300)
        subprocess.run([tool, "run", "-d", "--name", names[1], "--network", "host", "--read-only",
                        "--memory", "512m", "--cpus", "1", "--cap-drop", "ALL", "--tmpfs", "/tmp",
                        "--mount", f"type=bind,src={base},dst=/etc/alloy,readonly",
                        "--entrypoint", "/bin/alloy", ALLOY_IMAGE, "run",
                        f"--server.http.listen-addr=127.0.0.1:{ALLOY_HTTP}", "--storage.path=/tmp/alloy",
                        "/etc/alloy/config.alloy"], check=True, capture_output=True, text=True, timeout=300)
        eventually(lambda: get(f"http://127.0.0.1:{PROM}/-/ready"))
        eventually(lambda: get(f"http://127.0.0.1:{ALLOY_HTTP}/-/ready"))

        def components():
            found = json.loads(get(f"http://127.0.0.1:{ALLOY_HTTP}/api/v0/web/components"))
            states = {c["localID"]: c["health"]["state"] for c in found}
            assert all(s == "healthy" for s in states.values()), states
            return states
        eventually(components)
        good = [sum_metric(name, [ALL]) for name in KEEP if name != "openclaw.message.delivery.duration_ms"]
        good.append(histogram("openclaw.message.delivery.duration_ms", [{**ALL, "openclaw.outcome": "error"}]))
        # Off the list: an instrument of the product, and one of no product.
        good.append(sum_metric("openclaw.message.queued", [ALL]))
        good.append(sum_metric(f"{MARKER.replace('-', '_')}_total", [ALL]))
        eventually(lambda: send(payload("openclaw-agents-pilot", good)))
        # Values that are not declared, from a name that is not one of the three.
        bad = {**ALL, "openclaw.channel": MARKER, "openclaw.model": MARKER, "openclaw.token": MARKER,
               "openclaw.agent": MARKER, "openclaw.trigger": MARKER, "openclaw.outcome": MARKER}
        send(payload(MARKER, [sum_metric("openclaw.tokens", [bad]), sum_metric("openclaw.session.turn.created", [bad]),
                              sum_metric("openclaw.message.processed", [bad])]))
        # A value that is not a string.
        send(payload("openclaw-agents-finance", [sum_metric("openclaw.session.stuck", [{"openclaw.state": 7}])]))

        def series():
            answer = json.loads(get(f"http://127.0.0.1:{PROM}/api/v1/series?" + urllib.parse.urlencode(
                {"match[]": '{__name__=~".+"}', "start": str(time.time() - 3600)})))
            found = answer["data"]
            assert {s.get("job") for s in found} >= {"openclaw-agents-pilot", "undeclared"}
            return found
        found = eventually(series, timeout=90)
        time.sleep(5)
        found = series()
        logs = subprocess.run([tool, "logs", names[1]], capture_output=True, text=True, check=False)
    except Exception:
        for name in names:
            out = subprocess.run([tool, "logs", name], capture_output=True, text=True, check=False)
            print(name, (out.stdout + out.stderr)[-3000:])
        clean(tool, names, base)
        raise
    try:
        yield found, logs.stdout + logs.stderr
    finally:
        clean(tool, names, base)


def clean(tool: str, names: list[str], base: Path) -> None:
    for name in names:
        subprocess.run([tool, "rm", "-f", name], capture_output=True, check=False)
    shutil.rmtree(base, ignore_errors=True)


def test_no_marker_and_no_target_info_reach_prometheus(lab):
    found, _ = lab
    assert MARKER not in json.dumps(found)
    assert "target_info" not in {s["__name__"] for s in found}
    assert not [s for s in found if s["__name__"].startswith("openclaw_message_queued")]


def test_each_kept_instrument_has_its_labels_and_no_other(lab):
    found, _ = lab
    pilot = [s for s in found if s.get("job") == "openclaw-agents-pilot"]
    names = {s["__name__"] for s in pilot}
    assert names == {"openclaw_message_received_total", "openclaw_message_processed_total",
                     "openclaw_message_delivery_started_total", "openclaw_tokens_total",
                     "openclaw_session_turn_created_total", "openclaw_session_stuck_total",
                     "openclaw_model_failover_total",
                     "openclaw_message_delivery_duration_ms_milliseconds_bucket",
                     "openclaw_message_delivery_duration_ms_milliseconds_count",
                     "openclaw_message_delivery_duration_ms_milliseconds_sum"}, names
    for s in pilot:
        base = re.sub(r"(_milliseconds)?_(total|bucket|count|sum)$", "", s["__name__"])
        instrument = next(n for n in KEEP if n.replace(".", "_") == base)
        expected = {"__name__", "job", "k8s_cluster"} | {k.replace(".", "_") for k in KEEP[instrument]}
        if s["__name__"].endswith("_bucket"):
            expected.add("le")
        assert set(s) == expected, (s["__name__"], sorted(set(s) ^ expected))
        assert s["k8s_cluster"] == "homelab-test"
        assert "instance" not in s


def test_a_value_that_is_not_declared_reads_other_and_a_name_reads_undeclared(lab):
    found, _ = lab
    bad = [s for s in found if s.get("job") == "undeclared"]
    assert {s["__name__"] for s in bad} == {"openclaw_tokens_total", "openclaw_session_turn_created_total",
                                           "openclaw_message_processed_total"}
    for s in bad:
        for label in ("openclaw_channel", "openclaw_model", "openclaw_token", "openclaw_agent", "openclaw_trigger",
                      "openclaw_outcome"):
            if label in s:
                assert s[label] == "other", (s["__name__"], label, s[label])


def test_a_value_that_is_not_a_string_never_passes_as_it_is(lab):
    found, _ = lab
    stuck = [s for s in found if s.get("job") == "openclaw-agents-finance"]
    assert all(s.get("openclaw_state") == "other" for s in stuck), stuck


def test_without_the_rule_the_daily_check_fails_and_writes_nothing(lab):
    """The daily check of the rule (prometheus_rule_check.py) against a
    Prometheus with no rule in front: the query passes, each write answers
    something other than 403, so the check fails, and nothing is written."""
    import importlib.util
    path = ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/scripts/prometheus_rule_check.py"
    spec = importlib.util.spec_from_file_location("rule_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.URL = f"http://127.0.0.1:{PROM}"
    got = {name: module.status(method, path_, body, kind) for name, method, path_, body, kind, _ in module.REQUESTS}
    assert got["query"] == 200
    assert got["otlp-write"] in (200, 204), got
    assert got["remote-write"] == 404, got
    assert module.main() == 1
    before = {s["__name__"] for s in lab[0]}
    answer = json.loads(get(f"http://127.0.0.1:{PROM}/api/v1/series?" + urllib.parse.urlencode(
        {"match[]": '{__name__=~".+"}', "start": str(time.time() - 3600)})))
    assert {s["__name__"] for s in answer["data"]} == before
