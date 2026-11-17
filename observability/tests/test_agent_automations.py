"""The scheduled jobs of the finance agent (docs/apps/agent-platform.md,
"Scheduled jobs"): automations.json of its config, and the
automations-sync container that makes the jobs of the product equal to it.

The container tests start the product of the agent image (deployment.yaml,
by digest) with the config of finance and test values, on an internal
network with a fake model gateway and a fake MCP server at the names of the
config. Then the sync script of the pod runs next to it, as the pod runs it,
with one change: a pass each few seconds, not each hour. They need Docker:
without it a test is skipped, except in CI, where it fails.
"""

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"
FINANCE = AGENTS / "finance/agent"
SCRIPT = AGENTS / "pilot/agent/config/scripts/automations-sync.sh"
LIST = FINANCE / "config/automations.json"
PROBE = AGENTS / "finance/checks/app/mcp-gateway-probe.yaml"

ZONE = "Europe/Vienna"
CHAT = "111111111"
TOKEN = "test-gateway-token"
EVERY = 5


def agent_pod() -> dict:
    (doc,) = [d for d in yaml.safe_load_all((FINANCE / "app/deployment.yaml").read_text()) if d]
    return doc["spec"]["template"]["spec"]


def container(name: str) -> dict:
    (only,) = [c for c in agent_pod()["containers"] if c["name"] == name]
    return only


IMAGE = container("agent")["image"]


# The files in git


def test_each_job_of_the_list_is_neutral_and_names_its_task_file():
    jobs = json.loads(LIST.read_text())["jobs"]
    keys = [j["key"] for j in jobs]
    assert len(keys) == len(set(keys))
    for job in jobs:
        assert set(job) == {"key", "cron", "message"}
        assert re.fullmatch(r"[a-z0-9-]+", job["key"])
        assert len(job["cron"].split()) == 5
        # The message is the same text for each job, with its key: the task
        # is in the workspace, never in git.
        file = f"automations/{job['key']}.md"
        assert job["message"] == (
            f"This is the scheduled job {job['key']}. Read the file {file} in your workspace and "
            f"do the task it describes. If that file is missing or empty, reply with one line: "
            f"the task file {file} is missing.")


def test_the_two_jobs_of_the_host_are_in_the_list():
    crons = {j["key"]: j["cron"] for j in json.loads(LIST.read_text())["jobs"]}
    assert crons == {"weekday-check": "0 9 * * 1-5", "weekly-review": "0 18 * * 0"}


def test_the_config_maps_hold_the_list_and_the_script():
    kustomization = yaml.safe_load((FINANCE / "config/kustomization.yaml").read_text())
    files = {m["name"]: m["files"] for m in kustomization["configMapGenerator"]}
    assert "automations.json=./automations.json" in files["agent-config"]
    assert ("automations-sync.sh=../../../pilot/agent/config/scripts/automations-sync.sh"
            in files["agent-scripts"])


def test_the_sync_container_gets_two_values_of_the_secret_and_the_zone_of_the_cluster():
    sync = container("automations-sync")
    assert sync["command"] == ["sh", "/scripts/automations-sync.sh"]
    assert "envFrom" not in sync
    env = {e["name"]: e for e in sync["env"]}
    secret = {n for n, e in env.items() if "valueFrom" in e}
    assert secret == {"OPENCLAW_GATEWAY_TOKEN", "TELEGRAM_ALLOW_FROM"}
    for name in secret:
        assert env[name]["valueFrom"]["secretKeyRef"] == {"name": "agent-secret", "key": name}
    assert env["TZ"]["value"] == "${TIMEZONE}"
    assert env["OPENCLAW_CONFIG_PATH"]["value"] == "/home/node/config/openclaw.json"
    mounts = {m["name"]: m for m in sync["volumeMounts"]}
    assert "state" not in mounts
    assert mounts["config"]["readOnly"] and mounts["scripts"]["readOnly"]


# The lab

FAKE = r'''
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading


def log(line):
    print(line, flush=True)


def events(model, block, delta, stop):
    yield "message_start", {"type": "message_start", "message": {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}
    yield "content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}
    yield "content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}
    yield "content_block_stop", {"type": "content_block_stop", "index": 0}
    yield "message_delta", {"type": "message_delta", "delta": {"stop_reason": stop},
                            "usage": {"output_tokens": 1}}
    yield "message_stop", {"type": "message_stop"}


class Model(BaseHTTPRequestHandler):
    """The model: it asks for a search of the finance tools, then answers."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if not self.path.endswith("/messages"):
            self.send_response(404)
            self.end_headers()
            return
        tools = sorted(t.get("name", "") for t in body.get("tools", []))
        results = [c for m in body.get("messages", []) if isinstance(m.get("content"), list)
                   for c in m["content"] if c.get("type") == "tool_result"]
        if results:
            text = json.dumps(results[-1].get("content"))
            found = sorted(set(__import__("re").findall(r"firefly__[a-z_]+", text)))
            log("search " + json.dumps(found))
            it = events(body.get("model"), {"type": "text", "text": ""},
                        {"type": "text_delta", "text": "test reply"}, "end_turn")
        else:
            log("tools " + json.dumps(tools))
            it = events(body.get("model"),
                        {"type": "tool_use", "id": "toolu_test", "name": "tool_search", "input": {}},
                        {"type": "input_json_delta", "partial_json": json.dumps({"query": "firefly"})},
                        "tool_use")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for name, data in it:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()


TOOLS = [{"name": n, "description": n, "inputSchema": {"type": "object", "properties": {}}}
         for n in ("get_summary", "create_transaction", "delete_transaction")]


class Mcp(BaseHTTPRequestHandler):
    """The MCP gateway: three tools, one of them a delete."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(405)
        self.end_headers()

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if "id" not in req:
            self.send_response(202)
            self.end_headers()
            return
        method = req.get("method")
        if method == "initialize":
            result = {"protocolVersion": req["params"].get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        else:
            result = {}
        out = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", 3000), Mcp).serve_forever, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", 4000), Model).serve_forever()
'''


def python_image() -> str:
    (doc,) = [d for d in yaml.safe_load_all(PROBE.read_text()) if d and d["kind"] == "CronJob"]
    return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["image"]


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    if check:
        assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return result


class Lab:
    def __init__(self, tool: str, work: Path, names: dict):
        self.tool, self.work, self.names = tool, work, names

    def cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return run([self.tool, "exec", self.names["gateway"], "node", "/app/openclaw.mjs", *args],
                   check=check)

    def jobs(self) -> dict:
        out = json.loads(self.cli("automations", "list", "--all", "--json").stdout)
        assert not out["hasMore"]
        return {j.get("declarationKey") or j["name"]: j for j in out["jobs"]}

    def log(self) -> list[str]:
        result = run([self.tool, "logs", self.names["sync"]])
        return (result.stdout + result.stderr).splitlines()

    def wait_lines(self, count: int, limit: float = 120) -> list[str]:
        """The sync log once it has at least count lines."""
        deadline = time.monotonic() + limit
        while len(self.log()) < count:
            assert time.monotonic() < deadline, self.log()
            time.sleep(1)
        return self.log()


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    for sub in ("config", "scripts", "fake"):
        (work / sub).mkdir()
        (work / sub).chmod(0o755)
    shutil.copy(FINANCE / "config/openclaw.json", work / "config/openclaw.json")
    shutil.copy(FINANCE / "config/sandbox-policy.yaml", work / "config/sandbox-policy.yaml")
    shutil.copy(LIST, work / "config/automations.json")
    text = SCRIPT.read_text()
    assert text.count("\nEVERY=3600\n") == 1
    (work / "scripts/automations-sync.sh").write_text(text.replace("\nEVERY=3600\n", f"\nEVERY={EVERY}\n"))
    (work / "fake/fake.py").write_text(FAKE)
    for path in work.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    tag = uuid.uuid4().hex[:8]
    net = f"automations-{tag}"
    names = {n: f"automations-{n}-{tag}" for n in ("fake", "gateway", "sync")}
    base = ["--read-only", "--cap-drop", "ALL", "--tmpfs", "/tmp:uid=1000,gid=1000",
            "--tmpfs", "/home/node/.config:uid=1000,gid=1000",
            "-v", f"{work}/config:/home/node/config:ro",
            "-e", "OPENCLAW_CONFIG_PATH=/home/node/config/openclaw.json", "-e", "OPENCLAW_CONFIG_READONLY=1",
            "-e", "OPENCLAW_NO_AUTO_UPDATE=1", "-e", "DO_NOT_TRACK=1", "-e", "OPENCLAW_OFFLINE=1",
            "-e", f"OPENCLAW_GATEWAY_TOKEN={TOKEN}", "-e", f"TELEGRAM_ALLOW_FROM={CHAT}"]
    try:
        run([tool, "network", "create", "--internal", net])
        run([tool, "run", "-d", "--quiet", "--name", names["fake"], "--network", net,
             "--network-alias", "litellm-app.ai.svc.cluster.local",
             "--network-alias", "mcp-gateway.ai.svc.cluster.local",
             "--read-only", "--cap-drop", "ALL", "--user", "65534",
             "-v", f"{work}/fake/fake.py:/fake.py:ro", python_image(), "python3", "-I", "-u", "/fake.py"])
        # The gateway as in the pod: no TZ, so it runs in UTC.
        run([tool, "run", "-d", "--quiet", "--name", names["gateway"], "--network", net, *base,
             "--tmpfs", "/home/node/data:uid=1000,gid=1000",
             "-e", "OPENCLAW_STATE_DIR=/home/node/data/state",
             "-e", "XDG_CACHE_HOME=/home/node/data/state/.cache",
             "-e", "LITELLM_API_KEY=test", "-e", "TELEGRAM_BOT_TOKEN=123456:test",
             "-e", "MCP_GATEWAY_KEY=test", "-e", "BRAVE_API_KEY=test", IMAGE])
        lab = Lab(tool, work, names)
        deadline = time.monotonic() + 300
        while lab.cli("automations", "status", check=False).returncode != 0:
            assert time.monotonic() < deadline, run([tool, "logs", names["gateway"]]).stderr[-4000:]
            time.sleep(3)
        # Before the sync: a job made in a chat (no key), and a job of the
        # list that git dropped since.
        lab.cli("automations", "add", "--agent", "main", "--name", "made-in-a-chat", "--cron", "0 7 * * *",
                "--session", "isolated", "--message", "a task of a chat", "--no-deliver")
        lab.cli("automations", "add", "--agent", "main", "--declaration-key", "flux:dropped", "--name", "dropped",
                "--cron", "0 8 * * *", "--session", "isolated", "--message", "an old task", "--no-deliver")
        # The sync container: its own env, the config and the scripts, no state.
        sync = container("automations-sync")
        run([tool, "run", "-d", "--quiet", "--name", names["sync"], "--network", f"container:{names['gateway']}",
             *base, "-e", f"TZ={ZONE}", "-v", f"{work}/scripts:/scripts:ro",
             "--entrypoint", sync["command"][0], IMAGE, *sync["command"][1:]])
        lab.wait_lines(3)
        yield lab
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def test_the_sync_makes_each_job_of_the_list(lab):
    jobs = lab.jobs()
    for job in json.loads(LIST.read_text())["jobs"]:
        made = jobs["flux:" + job["key"]]
        assert made["enabled"]
        assert made["agentId"] == "main"
        assert made["schedule"] == {"kind": "cron", "expr": job["cron"], "tz": ZONE}
        assert made["sessionTarget"] == "isolated"
        # Each run uses the tool policy of the agent as it is then: its
        # sandbox, its deny list and its MCP filter.
        assert made["payload"] == {"kind": "agentTurn", "message": job["message"], "toolsAllow": ["*"]}
        assert made["delivery"] == {"mode": "announce", "channel": "telegram", "to": CHAT}


def test_the_time_of_a_job_is_in_its_zone_not_in_the_zone_of_the_gateway(lab):
    job = lab.jobs()["flux:weekday-check"]
    run_at = time.gmtime(job["state"]["nextRunAtMs"] / 1000)
    # 09:00 in Vienna is 08:00 UTC in winter and 07:00 UTC in summer.
    assert (run_at.tm_hour, run_at.tm_min) in {(8, 0), (7, 0)}


def test_the_sync_removes_a_dropped_job_and_keeps_each_job_it_does_not_own(lab):
    jobs = lab.jobs()
    assert "flux:dropped" not in jobs
    assert "made-in-a-chat" in jobs
    assert "heartbeat:main" in jobs
    assert "automations-sync key=dropped result=removed" in lab.log()


def test_a_change_made_in_a_chat_goes_back_to_git_but_a_job_that_is_off_stays_off(lab):
    job = lab.jobs()["flux:weekday-check"]
    lab.cli("automations", "edit", job["id"], "--message", "a change of a chat", "--cron", "0 10 * * *")
    lab.cli("automations", "disable", job["id"])
    count = len(lab.log())
    lines = lab.wait_lines(count + 4)
    assert "automations-sync key=weekday-check result=ok enabled=false" in lines[count:]
    again = lab.jobs()["flux:weekday-check"]
    assert again["id"] == job["id"]
    assert again["schedule"] == job["schedule"]
    assert again["payload"] == job["payload"]
    assert not again["enabled"]
    lab.cli("automations", "enable", job["id"])


def test_the_log_holds_keys_and_results_only(lab):
    pattern = re.compile(
        r"automations-sync (key=[a-z0-9-]+ result=(ok|ok enabled=false|failed|failed reason=[a-z-]+|removed)"
        r"|result=(none|failed reason=[a-z-]+))")
    lines = lab.log()
    assert lines
    for line in lines:
        assert pattern.fullmatch(line), line
        assert CHAT not in line and TOKEN not in line


def test_a_scheduled_turn_gets_the_tools_of_a_chat_turn_and_the_same_finance_tools(lab):
    """One run of a declared job, then one chat turn of the agent. The fake
    model records the tools each is offered, and the finance tools that a
    search finds. The delivery fails here (no Telegram), and the turn is ok."""
    job = lab.jobs()["flux:weekly-review"]
    result = json.loads(lab.cli("automations", "run", job["id"], "--wait", "--wait-timeout", "4m",
                                "--json", check=False).stdout)
    assert result["status"] == "ok", result
    assert result["run"]["delivery"]["resolved"] == {"ok": True, "channel": "telegram", "to": CHAT,
                                                     "source": "explicit"}
    scheduled = run([lab.tool, "logs", lab.names["fake"]]).stdout.splitlines()
    lab.cli("agent", "--agent", "main", "--message", "a chat turn", "--json")
    chat = run([lab.tool, "logs", lab.names["fake"]]).stdout.splitlines()[len(scheduled):]

    def seen(lines, kind):
        (only,) = {line.split(" ", 1)[1] for line in lines if line.startswith(kind + " ")}
        return set(json.loads(only))

    tools = seen(scheduled, "tools")
    # The sandbox tools and the search of deferred tools, as in a chat.
    assert {"exec", "read", "tool_search", "dispatch_action"} <= tools
    assert tools <= seen(chat, "tools")
    # The MCP filter of the config holds in both: no delete.
    assert seen(scheduled, "search") == seen(chat, "search") == {
        "firefly__get_summary", "firefly__create_transaction"}
