"""The use of the Claude plan (docs/apps/agent-platform.md, "Plan use").

The model bridge logs a plan_limit line for each limit window of the plan, and
the token counts of each call on its call line. The platform agent of Vector
(kubernetes/apps/monitoring/vector/agent-platform/config/vector.yaml) turns
them into series; `vector test` checks each transform
(observability/vector-tests/agent-platform-logs.yaml). Here:

- the value lists of the labels are the ones of the model gateway's config:
  the trust domains and the models of the entries that call the bridge;
- the bound of the series;
- in a container, the pinned Vector with the transforms of the config and its
  exporter: the newest plan line of any trust domain sets the gauges of its
  window, and the token counts of the calls add up.

The container test needs Docker: without it the test is skipped, except in CI,
where it fails.
"""

import json
import re
import subprocess
import time
import urllib.request
import uuid

import yaml
from test_hand_written_rules import FLUX_DEFAULT, FLUX_VARIABLE, ROOT, docker, scratch, vector_image

CONFIG = ROOT / "kubernetes/apps/monitoring/vector/agent-platform/config/vector.yaml"
LITELLM = ROOT / "kubernetes/apps/ai/litellm/app/configmap.yaml"
BRIDGE = "http://model-bridge.ai.svc.cluster.local:8080"
# The bridge's contract (its README, "plan_limit").
WINDOWS = ["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "seven_day_overage_included", "overage"]
TOKENS = ["input", "output", "cache_read", "cache_write"]
PORT = 13598
# The lab's exporter drops a metric after 2 s with no event, not 60 s, and
# token_zero sends every 1 s, not 30 s: the same ratio as the config (the zero
# interval at most half the flush period, test_each_declared_token_series_has_a_zero_series),
# so a drop on the config of main and the keep on this one show in seconds.
LAB_FLUSH_SECONDS = 2
LAB_ZERO_SECONDS = 1


def load():
    text = FLUX_DEFAULT.sub(r"\1", CONFIG.read_text())
    return yaml.safe_load(FLUX_VARIABLE.sub("test", text))


def gauges_source() -> str:
    return load()["transforms"]["platform_gauges"]["source"]


def listed(name: str) -> list[str]:
    """The list of values of a label in platform_gauges: `includes([...], name)`."""
    (found,) = re.findall(rf"includes\(\[([^\]]*)\], {name}\)", gauges_source())
    return re.findall(r'"([^"]+)"', found)


def bridge_entries() -> list[dict]:
    config = yaml.safe_load(yaml.safe_load(LITELLM.read_text())["data"]["config.yaml"])
    return [m for m in config["model_list"] if m["litellm_params"].get("api_base") == BRIDGE]


def test_the_domains_and_models_are_the_ones_of_the_model_gateway():
    entries = bridge_entries()
    domains = {re.fullmatch(r"claude-[a-z0-9-]+-bridge-([a-z]+)", m["model_name"]).group(1) for m in entries}
    models = {m["litellm_params"]["model"].removeprefix("anthropic/") for m in entries}
    assert set(listed("domain")) == domains
    assert set(listed("model")) == models
    assert listed("window") == WINDOWS


def test_the_series_and_their_bound():
    config = load()
    plan = config["transforms"]["plan_metrics"]["metrics"]
    tokens = config["transforms"]["token_metrics"]["metrics"]
    assert [m["name"] for m in plan] == ["model_bridge_plan_utilization_ratio",
                                         "model_bridge_plan_reset_timestamp_seconds",
                                         "model_bridge_plan_status", "model_bridge_plan_observed_timestamp_seconds"]
    assert all(m["tags"] == {"window": "{{ window }}"} and m["type"] == "gauge" for m in plan)
    assert [m["tags"]["token"] for m in tokens] == TOKENS
    assert [m["field"] for m in tokens] == ["input", "output", "cache_read", "cache_write"]
    assert all(m["type"] == "counter" and m["increment_by_value"] for m in tokens)
    assert all(set(m["tags"]) == {"domain", "model", "token"} for m in tokens)
    assert {"plan_metrics", "token_now", "token_zero"} <= set(config["sinks"]["prometheus"]["inputs"])
    assert config["transforms"]["token_now"]["inputs"] == ["token_metrics"]
    # Each value of each list, and other.
    bound = len(plan) * (len(WINDOWS) + 1) + len(TOKENS) * (len(listed("domain")) + 1) * (len(listed("model")) + 1)
    assert bound == 60, bound


def lab_config(path: str) -> dict:
    """The config as it is, from the lines to the exporter, fed from a file in
    place of the logs of the pod: its internal_metrics, token_zero,
    agent_channel and channel_zero sources, the transforms, and its exporter
    with each of its settings and inputs, on a port of the host. The exporter drops a metric only when an event comes
    in (internal_metrics sends them), so a lab without those inputs never
    drops one. Only the flush period and the zero interval are shorter."""
    config = load()
    sink = {**config["sinks"]["prometheus"], "address": f"127.0.0.1:{PORT}",
            "flush_period_secs": LAB_FLUSH_SECONDS}
    transforms = {name: config["transforms"][name]
                  for name in ("platform_gauges", "platform_gauge_route", "plan_metrics", "token_metrics",
                               "token_now", "bridge_metrics", "instruction_metrics", "agent_channel_count",
                               "agent_channel_metrics") if name in config["transforms"]}
    transforms["as_pod"] = {"type": "remap", "inputs": ["lines"], "source": (
        '.kubernetes.pod_namespace = "ai"\n.kubernetes.container_name = "model-bridge"\n')}
    transforms["platform_gauges"] = {**transforms["platform_gauges"], "inputs": ["as_pod"]}
    sources = {"lines": {"type": "file", "include": [path], "read_from": "beginning"},
               **{name: config["sources"][name] for name in ("internal_metrics", "token_zero", "agent_channel",
                                                             "channel_zero") if name in config["sources"]}}
    if "token_zero" in sources:
        sources["token_zero"] = {**sources["token_zero"], "interval_secs": LAB_ZERO_SECONDS}
    assert set(sink["inputs"]) <= set(sources) | set(transforms), sink["inputs"]
    return {"data_dir": "/tmp", "expire_metrics_secs": config["expire_metrics_secs"],
            "sources": sources, "transforms": transforms, "sinks": {"prometheus": sink}}


def test_each_declared_token_series_has_a_zero_series():
    """token_zero holds one series at 0 for each declared domain, model and kind."""
    source = load()["sources"]["token_zero"]
    assert source["type"] == "static_metrics" and source["namespace"] == "homelab"
    # Shorter than the flush period of the exporter, so the exporter never drops one.
    flush = load()["sinks"]["prometheus"].get("flush_period_secs", 60)
    assert source["interval_secs"] * 2 <= flush
    found = sorted((m["tags"]["domain"], m["tags"]["model"], m["tags"]["token"]) for m in source["metrics"])
    assert all(m["name"] == "model_bridge_tokens_total" and m["kind"] == "incremental"
               and m["value"] == {"counter": {"value": 0}} for m in source["metrics"])
    assert found == sorted((d, m, t) for d in listed("domain") for m in listed("model") for t in TOKENS)


def plan(domain, window, status, utilization, resets_at, threshold=None, limiting=True):
    return json.dumps({"event": "plan_limit", "domain": domain, "window": window, "limiting": limiting,
                       "status": status, "utilization": utilization, "resets_at": resets_at,
                       "threshold": threshold})


def call(domain, status=200, **tokens):
    return json.dumps({"event": "call", "domain": domain, "model": "claude-opus-5-5", "status": status,
                       **{f"{kind}_tokens": count for kind, count in tokens.items()}})


LINES = [
    plan("finance", "five_hour", "allowed", 0.30, 1795224121),
    plan("finance", "seven_day", "allowed", 0.10, 1795652521, limiting=False),
    call("main", input=11, output=22, cache_read=0, cache_write=5),
    # A newer line of another trust domain: the same plan, so it sets the gauges.
    plan("main", "five_hour", "allowed_warning", 0.91, 1795224121, threshold=0.9),
    call("main", input=100, output=200, cache_read=300, cache_write=0),
    call("finance", status=502),
    call("finance", input=1, output=2, cache_read=3, cache_write=4),
]


def scrape(stamps: bool = False) -> dict:
    """Each homelab_ sample of the exporter: its value, or (value, time in seconds)."""
    text = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/metrics", timeout=5).read().decode()
    found = {}
    for line in text.splitlines():
        if line.startswith("homelab_"):
            key, rest = line.split("} ", 1) if "}" in line else line.split(" ", 1)
            key += "}" if "{" in key else ""
            value, *stamp = rest.split()
            found[key] = (float(value), int(stamp[0]) / 1000 if stamp else None) if stamps else float(value)
    return found


def start(binary: str, work, name: str, lines: list[str]) -> None:
    (work / "lines.log").write_text("".join(line + "\n" for line in lines))
    (work / "vector.yaml").write_text(yaml.safe_dump(lab_config("/w/lines.log"), sort_keys=False))
    subprocess.run(["chmod", "-R", "a+rwX", str(work)], check=True)
    subprocess.run([binary, "run", "-d", "--name", name, "--network", "host", "--read-only", "--tmpfs", "/tmp",
                    "--mount", f"type=bind,src={work},dst=/w", vector_image(CONFIG),
                    "--config", "/w/vector.yaml"], check=True, capture_output=True, text=True, timeout=300)


def wait_for(binary: str, name: str, key: str, value: float | None = None) -> dict:
    deadline = time.monotonic() + 60
    found = {}
    while time.monotonic() < deadline:
        try:
            found = scrape()
            if key in found and (value is None or found[key] == value):
                return found
        except OSError:
            pass
        time.sleep(0.5)
    logs = subprocess.run([binary, "logs", name], capture_output=True, text=True, check=False)
    raise AssertionError((key, value, found, (logs.stdout + logs.stderr)[-3000:]))


def append(work, *lines: str) -> None:
    with (work / "lines.log").open("a") as log:
        log.writelines(line + "\n" for line in lines)


def test_in_the_pinned_vector_the_newest_line_wins_and_the_token_counter_lives():
    """One start of the lab. The counter is there at 0 before any call; the
    newest plan line of any trust domain sets the gauges of its window; the
    token counts add up; the counter keeps its count past two flush periods
    with no call, with a sample time of now; a later call adds to it.

    The live fault of the first build: the exporter dropped the counter 1 to 2
    minutes after its last line, and its sample kept the time of the line, so
    Prometheus showed it for 5 minutes. increase() then counted the first call
    as 0, and each later call only when its count was above the last one. On
    that config this test fails at its first wait."""
    binary = docker()
    work = scratch()
    name = f"plan-usage-{uuid.uuid4().hex[:8]}"
    token = 'homelab_model_bridge_tokens_total{domain="%s",model="claude-opus-5-5",token="%s"}'
    try:
        start(binary, work, name, [])
        # Before any call: each declared series is there, at 0.
        wait_for(binary, name, token % ("main", "input"), 0)
        append(work, *LINES)
        found = wait_for(binary, name, token % ("finance", "cache_write"), 4)
        five = '{window="five_hour"}'
        assert found["homelab_model_bridge_plan_utilization_ratio" + five] == 0.91
        assert found["homelab_model_bridge_plan_status" + five] == 1
        assert found["homelab_model_bridge_plan_reset_timestamp_seconds" + five] == 1795224121
        assert found['homelab_model_bridge_plan_utilization_ratio{window="seven_day"}'] == 0.10
        # One series for each window and gauge: no domain label.
        plan = [k for k in found if k.startswith("homelab_model_bridge_plan_")]
        assert len(plan) == 8, plan
        assert all("domain" not in k for k in plan)
        assert [found[token % ("main", t)] for t in TOKENS] == [111, 222, 300, 5]
        assert [found[token % ("finance", t)] for t in TOKENS] == [1, 2, 3, 4]
        assert [found[token % ("workflows", t)] for t in TOKENS] == [0, 0, 0, 0]
        assert len([k for k in found if k.startswith("homelab_model_bridge_tokens_total")]) == 12
        # Two flush periods and more with no call: the counter is there at each
        # look, at its count, with a sample time of now (not of the line).
        until = time.monotonic() + 2 * LAB_FLUSH_SECONDS + 2
        while time.monotonic() < until:
            value, stamp = scrape(stamps=True).get(token % ("main", "input"), (None, None))
            assert value == 111, value
            assert stamp is None or time.time() - stamp < LAB_ZERO_SECONDS + 2, time.time() - stamp
            time.sleep(0.25)
        # A later call adds to it.
        append(work, call("main", input=50, output=1, cache_read=0, cache_write=0))
        wait_for(binary, name, token % ("main", "input"), 161)
    finally:
        subprocess.run([binary, "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["rm", "-rf", str(work)], check=False)
