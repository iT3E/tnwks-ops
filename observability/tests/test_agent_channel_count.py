"""The count of the error lines of the chat channel of the agents
(docs/apps/agent-platform.md, "Logs" and "Metrics").

The log of the agent container can hold chat content, so it does not ship.
The platform agent of Vector
(kubernetes/apps/monitoring/vector/agent-platform/config/vector.yaml) reads it
from the files of the node only to count fixed error patterns, and only the
count leaves the node. Here:

- the lines of the file source reach the exporter and no other sink: not the
  aggregator, which feeds Loki;
- the count keeps no word of the line: its only fields are a namespace and a
  pattern, each from a fixed list of literals in the config;
- the zero series are each namespace and pattern of that list;
- the pods still exclude the agent container from kubernetes_logs;
- in a container, the pinned Vector with the source, the transforms and the
  exporter of the config: lines with content and chat IDs give the counts,
  and no word of them is in the output of the exporter.

`vector test` checks each pattern (observability/vector-tests/agent-platform-logs.yaml).
The container test needs Docker: without it the test is skipped, except in CI,
where it fails.
"""

import re
import subprocess
import time
import urllib.request
import uuid

import yaml
from test_hand_written_rules import FLUX_DEFAULT, FLUX_VARIABLE, ROOT, docker, scratch, vector_image

CONFIG = ROOT / "kubernetes/apps/monitoring/vector/agent-platform/config/vector.yaml"
AGENTS = ["main", "finance", "pilot"]
SOURCE = "agent_channel"
COUNT = "agent_channel_count"
METRIC = "homelab_agent_channel_error_lines_total"
NAMESPACES = ["agents-main", "agents-finance"]
PATTERNS = ["send_failed", "poll_stall", "poll_restart", "channel_restart"]
PORT = 13597


def load() -> dict:
    text = FLUX_DEFAULT.sub(r"\1", CONFIG.read_text())
    return yaml.safe_load(FLUX_VARIABLE.sub("test", text))


def downstream(config: dict, start: str) -> set[str]:
    """Each transform and sink that an event of `start` can reach."""
    nodes = {**config.get("transforms", {}), **config.get("sinks", {})}
    reached = {start}
    while True:
        more = {name for name, node in nodes.items() if name not in reached
                and {i.split(".", 1)[0] for i in node.get("inputs", [])} & reached}
        if not more:
            return reached - {start}
        reached |= more


def test_the_lines_of_the_agent_container_reach_only_the_exporter():
    config = load()
    source = config["sources"][SOURCE]
    assert source["type"] == "file"
    assert source["include"] == [f"/var/log/pods/{ns}_agent-*/agent/*.log" for ns in NAMESPACES]
    assert source["read_from"] == "end"
    reached = downstream(config, SOURCE)
    assert reached & set(config["sinks"]) == {"prometheus"}, reached
    assert "aggregator" not in reached
    assert reached == {COUNT, "agent_channel_metrics", "prometheus"}, reached
    # No other component reads the source, and the count reads only it.
    assert config["transforms"][COUNT]["inputs"] == [SOURCE]
    assert config["transforms"]["agent_channel_metrics"]["inputs"] == [COUNT]


def test_the_count_keeps_no_word_of_the_line():
    config = load()
    remap = config["transforms"][COUNT]
    assert remap["type"] == "remap"
    lines = [line.strip() for line in remap["source"].splitlines() if line.strip()]
    # The event is replaced once, by the two fields, at the end.
    assert [line for line in lines if re.match(r"\.\S* ?=", line)] == ['. = {"pod_namespace": ns, "pattern": pattern}']
    assert lines[-1] == '. = {"pod_namespace": ns, "pattern": pattern}'
    # ns and pattern take only string literals: each branch body is a literal.
    bodies = [line for line in lines if re.fullmatch(r'"[^"]*"', line)]
    assert sorted({b.strip('"') for b in bodies}) == sorted({"", *NAMESPACES, *PATTERNS})
    # Each of them is set once, by an if whose branches are those literals.
    assigned = [line.split(" = ", 1)[0] for line in lines if re.match(r"(ns|pattern) = ", line)]
    assert assigned == ["ns", "pattern"], assigned
    assert all(re.match(r"(ns|pattern) = if ", line) for line in lines if re.match(r"(ns|pattern) = ", line))
    metrics = config["transforms"]["agent_channel_metrics"]
    assert metrics["type"] == "log_to_metric"
    (metric,) = metrics["metrics"]
    assert metric["type"] == "counter" and metric["namespace"] == "homelab"
    assert metric["tags"] == {"pod_namespace": "{{ pod_namespace }}", "pattern": "{{ pattern }}"}


def test_each_namespace_and_pattern_has_a_zero_series():
    config = load()
    source = config["sources"]["channel_zero"]
    assert source["type"] == "static_metrics" and source["namespace"] == "homelab"
    assert source["interval_secs"] * 2 <= config["sinks"]["prometheus"].get("flush_period_secs", 60)
    assert all(m["name"] == "agent_channel_error_lines_total" and m["kind"] == "incremental"
               and m["value"] == {"counter": {"value": 0}} for m in source["metrics"])
    found = sorted((m["tags"]["pod_namespace"], m["tags"]["pattern"]) for m in source["metrics"])
    assert found == sorted((ns, p) for ns in NAMESPACES for p in PATTERNS)
    assert {"agent_channel_metrics", "channel_zero"} <= set(config["sinks"]["prometheus"]["inputs"])


def test_the_agent_pods_exclude_the_agent_container_from_kubernetes_logs():
    """The count does not replace this: the allowlist source never reads the
    agent container."""
    for agent in AGENTS:
        path = ROOT / f"kubernetes/apps/ai/agents/{agent}/agent/app/deployment.yaml"
        pod = yaml.safe_load(path.read_text())["spec"]["template"]["metadata"]
        assert pod["annotations"]["vector.dev/exclude-containers"] == "agent", agent


def cri(message: str) -> str:
    return f"2026-11-21T03:00:00.000000000Z stderr F {message}\n"


SECRET = "SECRET words of a chat"
CHAT = "-1009876543210"
LOGS = {
    "agents-main_agent-6d9f-abcde_11111111-2222-3333-4444-555555555555/agent/0.log": [
        f'2026-11-21T03:00:00.000Z telegram sendMessage failed: network request failed transport={{"code":"ETIMEDOUT"}} chat={CHAT} {SECRET}',
        f"\x1b[31mtelegram editMessageText failed: Call to 'editMessageText' failed! (400: Bad Request: {SECRET})\x1b[39m",
        f"telegram sendChatAction failed: {SECRET}",
        f"[telegram] Inbound message telegram:{CHAT} -> {SECRET}",
        "[telegram] Polling stall detected (no completed getUpdates for 2m 0.1s); forcing restart.",
        "[telegram] isolated polling ingress failed; restarting in 30s.",
        f"[telegram] fallback delivery failed: {SECRET}",
        f"[telegram] sendChatAction failed: {SECRET}",
    ],
    "agents-finance_agent-7c8b-fghij_66666666-7777-8888-9999-000000000000/agent/0.log": [
        "[health-monitor] [telegram:default] health-monitor: restarting (reason: stuck)",
        "[health-monitor] [telegram:default] health-monitor: hit 10 restarts/hour limit, skipping",
    ],
    # Not read: the pilot has no Telegram, and another container of the pod.
    "agents-pilot_agent-1a2b-klmno_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/agent/0.log": [
        f"telegram sendMessage failed: {SECRET}",
    ],
    "agents-main_agent-6d9f-abcde_11111111-2222-3333-4444-555555555555/backup/0.log": [
        f"telegram sendMessage failed: {SECRET}",
    ],
}
EXPECTED = {("agents-main", "send_failed"): 3, ("agents-main", "poll_stall"): 1,
            ("agents-main", "poll_restart"): 1, ("agents-main", "channel_restart"): 0,
            ("agents-finance", "send_failed"): 0, ("agents-finance", "poll_stall"): 0,
            ("agents-finance", "poll_restart"): 0, ("agents-finance", "channel_restart"): 2}


def lab_config() -> dict:
    """The source, the transforms and the exporter of the config as they are,
    with the exporter on a port of the host."""
    config = load()
    names = ("agent_channel_count", "agent_channel_metrics")
    sink = {**config["sinks"]["prometheus"], "address": f"127.0.0.1:{PORT}"}
    sink["inputs"] = [i for i in sink["inputs"] if i in names or i == "channel_zero"]
    return {"data_dir": "/tmp", "expire_metrics_secs": config["expire_metrics_secs"],
            "sources": {name: config["sources"][name] for name in (SOURCE, "channel_zero")},
            "transforms": {name: config["transforms"][name] for name in names},
            "sinks": {"prometheus": sink}}


def scrape() -> str:
    return urllib.request.urlopen(f"http://127.0.0.1:{PORT}/metrics", timeout=5).read().decode()


def counts(text: str) -> dict:
    found = {}
    for line in text.splitlines():
        if line.startswith(METRIC + "{"):
            labels, value = line[len(METRIC) + 1:].split("} ", 1)
            pairs = dict(re.findall(r'(\w+)="([^"]*)"', labels))
            assert set(pairs) == {"pod_namespace", "pattern"}, line
            found[(pairs["pod_namespace"], pairs["pattern"])] = float(value.split()[0])
    return found


def wait_for(binary: str, name: str, want: dict) -> str:
    deadline = time.monotonic() + 60
    text = ""
    while time.monotonic() < deadline:
        try:
            text = scrape()
            if counts(text) == want:
                return text
        except OSError:
            pass
        time.sleep(0.5)
    logs = subprocess.run([binary, "logs", name], capture_output=True, text=True, check=False)
    raise AssertionError((want, text[-3000:], (logs.stdout + logs.stderr)[-3000:]))


def test_in_the_pinned_vector_only_the_counts_leave():
    """The zero series come first; then the lines of new pods are counted by
    pattern, and the output of the exporter has no word of them."""
    binary = docker()
    work = scratch()
    pods = work / "pods"
    pods.mkdir()
    name = f"channel-count-{uuid.uuid4().hex[:8]}"
    try:
        (work / "vector.yaml").write_text(yaml.safe_dump(lab_config(), sort_keys=False))
        subprocess.run(["chmod", "-R", "a+rwX", str(work)], check=True)
        subprocess.run([binary, "run", "-d", "--name", name, "--network", "host", "--read-only", "--tmpfs", "/tmp",
                        "--mount", f"type=bind,src={work},dst=/w,readonly",
                        "--mount", f"type=bind,src={pods},dst=/var/log/pods,readonly",
                        vector_image(CONFIG), "--config", "/w/vector.yaml"],
                       check=True, capture_output=True, text=True, timeout=300)
        wait_for(binary, name, dict.fromkeys(EXPECTED, 0))
        # New pods: their files come after the start, so Vector reads them
        # from their first line.
        for path, lines in LOGS.items():
            target = pods / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(cri(line) for line in lines))
        subprocess.run(["chmod", "-R", "a+rX", str(pods)], check=True)
        text = wait_for(binary, name, EXPECTED)
        for word in ("SECRET", "chat", CHAT.lstrip("-"), "ETIMEDOUT", "stuck", "default", "telegram", "pilot"):
            assert word not in text, word
    finally:
        subprocess.run([binary, "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["rm", "-rf", str(work)], check=False)
