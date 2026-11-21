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
    assert {"plan_metrics", "token_metrics"} <= set(config["sinks"]["prometheus"]["inputs"])
    # Each value of each list, and other.
    bound = len(plan) * (len(WINDOWS) + 1) + len(TOKENS) * (len(listed("domain")) + 1) * (len(listed("model")) + 1)
    assert bound == 60, bound


def lab_config(path: str) -> dict:
    """The transforms of the config as they are, fed from a file in place of
    the logs of the pod, and the exporter on a port of the host."""
    config = load()
    transforms = {name: config["transforms"][name]
                  for name in ("platform_gauges", "platform_gauge_route", "plan_metrics", "token_metrics",
                               "bridge_metrics", "instruction_metrics")}
    transforms["as_pod"] = {"type": "remap", "inputs": ["lines"], "source": (
        '.kubernetes.pod_namespace = "ai"\n.kubernetes.container_name = "model-bridge"\n')}
    transforms["platform_gauges"] = {**transforms["platform_gauges"], "inputs": ["as_pod"]}
    return {"data_dir": "/tmp", "expire_metrics_secs": config["expire_metrics_secs"],
            "sources": {"lines": {"type": "file", "include": [path], "read_from": "beginning"}},
            "transforms": transforms,
            "sinks": {"prometheus": {"type": "prometheus_exporter", "address": f"127.0.0.1:{PORT}",
                                     "inputs": ["plan_metrics", "token_metrics", "bridge_metrics"]}}}


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


def scrape() -> dict:
    text = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/metrics", timeout=5).read().decode()
    found = {}
    for line in text.splitlines():
        if line.startswith("homelab_"):
            key, value = line.rsplit(" ", 2)[0], line.rsplit(" ", 2)[1]
            found[key] = float(value)
    return found


def test_the_newest_line_sets_the_gauges_and_the_counts_add_up():
    binary = docker()
    work = scratch()
    name = f"plan-usage-{uuid.uuid4().hex[:8]}"
    try:
        (work / "lines.log").write_text("\n".join(LINES) + "\n")
        (work / "vector.yaml").write_text(yaml.safe_dump(lab_config("/w/lines.log"), sort_keys=False))
        subprocess.run(["chmod", "-R", "a+rX", str(work)], check=True)
        subprocess.run([binary, "run", "-d", "--name", name, "--network", "host", "--read-only", "--tmpfs", "/tmp",
                        "--mount", f"type=bind,src={work},dst=/w,readonly", vector_image(CONFIG),
                        "--config", "/w/vector.yaml"], check=True, capture_output=True, text=True, timeout=300)
        want = 'homelab_model_bridge_tokens_total{domain="finance",model="claude-opus-5-5",token="cache_write"}'
        deadline = time.monotonic() + 60
        found = {}
        while time.monotonic() < deadline:
            try:
                found = scrape()
                if want in found:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        logs = subprocess.run([binary, "logs", name], capture_output=True, text=True, check=False)
        assert want in found, (found, (logs.stdout + logs.stderr)[-3000:])
        five = '{window="five_hour"}'
        assert found["homelab_model_bridge_plan_utilization_ratio" + five] == 0.91
        assert found["homelab_model_bridge_plan_status" + five] == 1
        assert found["homelab_model_bridge_plan_reset_timestamp_seconds" + five] == 1795224121
        assert found['homelab_model_bridge_plan_utilization_ratio{window="seven_day"}'] == 0.10
        # One series for each window and gauge: no domain label.
        plan = [k for k in found if k.startswith("homelab_model_bridge_plan_")]
        assert len(plan) == 8, plan
        assert all("domain" not in k for k in plan)
        main = 'homelab_model_bridge_tokens_total{domain="main",model="claude-opus-5-5",token="%s"}'
        assert [found[main % t] for t in TOKENS] == [111, 222, 300, 5]
        finance = 'homelab_model_bridge_tokens_total{domain="finance",model="claude-opus-5-5",token="%s"}'
        assert [found[finance % t] for t in TOKENS] == [1, 2, 3, 4]
        assert len([k for k in found if k.startswith("homelab_model_bridge_tokens_total")]) == 8
    finally:
        subprocess.run([binary, "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["rm", "-rf", str(work)], check=False)
