"""The scheduled jobs of the finance agent (docs/apps/agent-platform.md,
"Scheduled jobs"): automations.json of its config, and the
automations-sync container that makes the jobs of the product equal to it.

The container tests start the product of the agent image (deployment.yaml,
by digest) with the config of finance and test values, on an internal
network with a fake model gateway and a fake MCP server at the names of the
config. Then the sync script of the pod runs next to it, as the pod runs it,
with one change: a pass each few seconds, not each hour. The tests change
the list between passes. They need Docker: without it a test is skipped,
except in CI, where it fails.
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


def message(key: str) -> str:
    file = f"automations/{key}.md"
    return (f"This is the scheduled job {key}. Read the file {file} in your workspace and "
            f"do the task it describes. If that file is missing or empty, reply with one line: "
            f"the task file {file} is missing.")


# The files in git


def test_each_job_of_the_list_is_neutral_and_names_its_task_file():
    jobs = json.loads(LIST.read_text())["jobs"]
    keys = [j["key"] for j in jobs]
    assert len(keys) == len(set(keys))
    for job in jobs:
        assert set(job) == {"key", "cron", "message", "enabled"}
        assert re.fullmatch(r"[a-z0-9-]+", job["key"])
        assert len(job["cron"].split()) == 5
        # The same text for each job, with its key: the task is in the
        # workspace, never in git.
        assert job["message"] == message(job["key"])


def test_the_two_jobs_of_the_host_are_in_the_list_and_off_until_the_task_files_are_written():
    jobs = {j["key"]: (j["cron"], j["enabled"]) for j in json.loads(LIST.read_text())["jobs"]}
    assert jobs == {"weekday-check": ("0 9 * * 1-5", False), "weekly-review": ("0 18 * * 0", False)}


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
    # The config and the scripts, read only: no state, and not the client
    # certificate of the sandbox gateway (user-config).
    mounts = {m["name"]: m for m in sync["volumeMounts"]}
    assert set(mounts) == {"config", "scripts", "tmp"}
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


# The list of the lab: one job on and one off.
JOBS = [{"key": "weekday-check", "cron": "0 9 * * 1-5", "message": message("weekday-check"), "enabled": True},
        {"key": "weekly-review", "cron": "0 18 * * 0", "message": message("weekly-review"), "enabled": False}]


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

    def passes(self) -> list[str]:
        return [line for line in self.log() if line.startswith("automations-sync result=")]

    def write_list(self, text: str) -> None:
        path = self.work / "config/automations.json"
        path.write_text(text)
        path.chmod(0o644)

    def next_pass(self, limit: float = 120) -> list[str]:
        """The lines of the second pass that starts after now (the first may
        have read the list before the change)."""
        count = len(self.passes())
        deadline = time.monotonic() + limit
        while len(self.passes()) < count + 2:
            assert time.monotonic() < deadline, self.log()
            time.sleep(1)
        lines = self.log()
        ends = [i for i, line in enumerate(lines) if line.startswith("automations-sync result=")]
        start = ends[count] + 1
        return lines[start:ends[count + 1] + 1]


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    for sub in ("config", "scripts", "fake", "nolist"):
        (work / sub).mkdir()
        (work / sub).chmod(0o755)
    for config in ("config", "nolist"):
        shutil.copy(FINANCE / "config/openclaw.json", work / config / "openclaw.json")
        shutil.copy(FINANCE / "config/sandbox-policy.yaml", work / config / "sandbox-policy.yaml")
    (work / "config/automations.json").write_text(json.dumps({"jobs": JOBS}))
    text = SCRIPT.read_text()
    assert text.count("\nEVERY=3600\n") == 1
    (work / "scripts/automations-sync.sh").write_text(text.replace("\nEVERY=3600\n", f"\nEVERY={EVERY}\n"))
    (work / "fake/fake.py").write_text(FAKE)
    for path in work.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    tag = uuid.uuid4().hex[:8]
    net = f"automations-{tag}"
    names = {n: f"automations-{n}-{tag}" for n in ("fake", "gateway", "sync", "nolist")}
    env = ["-e", "OPENCLAW_CONFIG_PATH=/home/node/config/openclaw.json", "-e", "OPENCLAW_CONFIG_READONLY=1",
           "-e", "OPENCLAW_NO_AUTO_UPDATE=1", "-e", "DO_NOT_TRACK=1", "-e", "OPENCLAW_OFFLINE=1",
           "-e", f"OPENCLAW_GATEWAY_TOKEN={TOKEN}", "-e", f"TELEGRAM_ALLOW_FROM={CHAT}"]
    base = ["--read-only", "--cap-drop", "ALL", "--tmpfs", "/tmp:uid=1000,gid=1000"]
    try:
        run([tool, "network", "create", "--internal", net])
        run([tool, "run", "-d", "--quiet", "--name", names["fake"], "--network", net,
             "--network-alias", "litellm-app.ai.svc.cluster.local",
             "--network-alias", "mcp-gateway.ai.svc.cluster.local",
             "--read-only", "--cap-drop", "ALL", "--user", "65534",
             "-v", f"{work}/fake/fake.py:/fake.py:ro", python_image(), "python3", "-I", "-u", "/fake.py"])
        # The gateway as in the pod: no TZ, so it runs in UTC.
        run([tool, "run", "-d", "--quiet", "--name", names["gateway"], "--network", net, *base, *env,
             "--tmpfs", "/home/node/.config:uid=1000,gid=1000",
             "-v", f"{work}/config:/home/node/config:ro",
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
        # The sync container as in the pod: its env, the config and the
        # scripts, /tmp, and nothing else writable (no user-config).
        sync = container("automations-sync")
        run([tool, "run", "-d", "--quiet", "--name", names["sync"], "--network", f"container:{names['gateway']}",
             *base, *env, "-e", f"TZ={ZONE}", "-v", f"{work}/config:/home/node/config:ro",
             "-v", f"{work}/scripts:/scripts:ro",
             "--entrypoint", sync["command"][0], IMAGE, *sync["command"][1:]])
        deadline = time.monotonic() + 120
        while not lab.passes():
            assert time.monotonic() < deadline, lab.log()
            time.sleep(1)
        lab.first = lab.log()
        yield lab
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def test_the_sync_makes_each_job_of_the_list(lab):
    jobs = lab.jobs()
    for job in JOBS:
        made = jobs["flux:" + job["key"]]
        assert made["enabled"] is job["enabled"]
        assert made["agentId"] == "main"
        assert made["description"] == f"git enabled={str(job['enabled']).lower()}"
        assert made["schedule"] == {"kind": "cron", "expr": job["cron"], "tz": ZONE}
        assert made["sessionTarget"] == "isolated"
        # Each run uses the tool policy of the agent as it is then: its
        # sandbox, its deny list and its MCP filter.
        assert made["payload"] == {"kind": "agentTurn", "message": job["message"], "toolsAllow": ["*"]}
        assert made["delivery"] == {"mode": "announce", "channel": "telegram", "to": CHAT}
    assert "automations-sync key=weekday-check result=ok state=on" in lab.first
    assert "automations-sync key=weekly-review result=ok state=off" in lab.first
    assert "automations-sync result=ok jobs=2" in lab.first


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
    assert "automations-sync key=dropped result=removed" in lab.first


def test_a_change_made_in_a_chat_goes_back_to_git(lab):
    job = lab.jobs()["flux:weekday-check"]
    lab.cli("automations", "edit", job["id"], "--message", "a change of a chat", "--cron", "0 10 * * *")
    lab.next_pass()
    again = lab.jobs()["flux:weekday-check"]
    assert again["id"] == job["id"]
    assert again["schedule"] == job["schedule"]
    assert again["payload"] == job["payload"]


def test_git_decides_on_and_off_in_one_way(lab):
    """false: off at each pass. true: on when the job is made or git changes
    to true; a stop in a chat holds until then."""
    jobs = lab.jobs()
    on, off = jobs["flux:weekday-check"], jobs["flux:weekly-review"]
    # A stop in a chat of a job that git has on: it stays off.
    lab.cli("automations", "disable", on["id"])
    # A start in a chat of a job that git has off: git turns it off again.
    lab.cli("automations", "enable", off["id"])
    lines = lab.next_pass()
    assert "automations-sync key=weekday-check result=ok state=stopped" in lines
    assert "automations-sync key=weekly-review result=ok state=off" in lines
    jobs = lab.jobs()
    assert not jobs["flux:weekday-check"]["enabled"]
    assert not jobs["flux:weekly-review"]["enabled"]
    # The next pass does not change that.
    lines = lab.next_pass()
    assert "automations-sync key=weekday-check result=ok state=stopped" in lines
    assert not lab.jobs()["flux:weekday-check"]["enabled"]
    # git turns weekly-review on, and weekday-check off: each follows.
    swapped = [{**JOBS[0], "enabled": False}, {**JOBS[1], "enabled": True}]
    lab.write_list(json.dumps({"jobs": swapped}))
    lines = lab.next_pass()
    assert "automations-sync key=weekday-check result=ok state=off" in lines
    assert "automations-sync key=weekly-review result=ok state=on" in lines
    jobs = lab.jobs()
    assert not jobs["flux:weekday-check"]["enabled"]
    assert jobs["flux:weekly-review"]["enabled"]
    assert jobs["flux:weekday-check"]["id"] == on["id"]
    # git turns weekday-check on again: it starts, the stop of the chat is over.
    lab.write_list(json.dumps({"jobs": JOBS}))
    lines = lab.next_pass()
    assert "automations-sync key=weekday-check result=ok state=on" in lines
    assert lab.jobs()["flux:weekday-check"]["enabled"]


def test_a_job_with_no_git_value_it_can_read_is_a_stop_of_the_owner(lab):
    """A chat can edit the description that holds the git value. A job that
    is off and has no value the sync can read stays off: one line says so,
    and the sync writes the value."""
    job = lab.jobs()["flux:weekday-check"]
    assert job["enabled"]
    start = len(lab.log())
    lab.cli("automations", "disable", job["id"])
    lab.cli("automations", "edit", job["id"], "--description", "a note of a chat")
    try:
        # The first pass after the change says so once and writes the
        # value; the passes after it say stopped.
        lines = lab.next_pass()
        since = lab.log()[start:]
        assert since.count("automations-sync key=weekday-check result=ok state=stopped note=no-git-value") == 1
        assert "automations-sync key=weekday-check result=ok state=stopped" in lines
        again = lab.jobs()["flux:weekday-check"]
        assert not again["enabled"]
        assert again["description"] == "git enabled=true"
    finally:
        lab.cli("automations", "enable", job["id"])


@pytest.mark.parametrize("missing", ["TZ", "TELEGRAM_ALLOW_FROM"])
def test_a_missing_zone_or_chat_fails_at_each_pass_and_changes_nothing(lab, missing):
    """The failed line comes again at each pass, so the alert holds until
    the fix (the lab runs a pass each EVERY seconds)."""
    before = {k: (j["id"], j["enabled"]) for k, j in lab.jobs().items()}
    sync = container("automations-sync")
    env = {"TZ": ZONE, "OPENCLAW_GATEWAY_TOKEN": TOKEN, "TELEGRAM_ALLOW_FROM": CHAT}
    del env[missing]
    result = run([lab.tool, "run", "--rm", "--name", lab.names["nolist"],
                  "--network", f"container:{lab.names['gateway']}", "--read-only", "--cap-drop", "ALL",
                  *[f"-e{k}={v}" for k, v in env.items()],
                  "-v", f"{lab.work}/config:/home/node/config:ro", "-v", f"{lab.work}/scripts:/scripts:ro",
                  "--entrypoint", "timeout", IMAGE, str(2 * EVERY + 2), *sync["command"]], check=False)
    reason = {"TZ": "no-zone", "TELEGRAM_ALLOW_FROM": "no-chat"}[missing]
    lines = result.stdout.splitlines()
    assert len(lines) >= 2, result
    assert set(lines) == {f"automations-sync result=failed reason={reason}"}
    assert {k: (j["id"], j["enabled"]) for k, j in lab.jobs().items()} == before


def test_a_scheduled_turn_gets_the_tools_of_a_chat_turn_and_the_same_finance_tools(lab):
    """One run of a declared job, then one chat turn of the agent. The fake
    model records the tools each is offered, and the finance tools that a
    search finds. The delivery fails here (no Telegram), and the turn is ok."""
    job = lab.jobs()["flux:weekday-check"]
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


@pytest.mark.parametrize("text", [
    "{not json",
    "",
    json.dumps({"jobs": "none"}),
    json.dumps({"jobs": [JOBS[0], {**JOBS[1], "enabled": "yes"}]}),
    json.dumps({"jobs": [JOBS[0], JOBS[0]]}),
])
def test_a_list_that_does_not_parse_changes_nothing(lab, text):
    before = {k: (j["id"], j["enabled"]) for k, j in lab.jobs().items()}
    lab.write_list(text)
    try:
        lines = lab.next_pass()
        assert lines == ["automations-sync result=failed reason=bad-list"]
        assert {k: (j["id"], j["enabled"]) for k, j in lab.jobs().items()} == before
    finally:
        lab.write_list(json.dumps({"jobs": JOBS}))
        lab.next_pass()


def test_a_missing_list_declares_nothing_and_changes_nothing(lab):
    before = {k: j["id"] for k, j in lab.jobs().items()}
    sync = container("automations-sync")
    result = run([lab.tool, "run", "--rm", "--name", lab.names["nolist"],
                  "--network", f"container:{lab.names['gateway']}", "--read-only", "--cap-drop", "ALL",
                  "-e", f"TZ={ZONE}", "-e", f"OPENCLAW_GATEWAY_TOKEN={TOKEN}", "-e", f"TELEGRAM_ALLOW_FROM={CHAT}",
                  "-v", f"{lab.work}/nolist:/home/node/config:ro", "-v", f"{lab.work}/scripts:/scripts:ro",
                  "--entrypoint", "timeout", IMAGE, "10", *sync["command"]], check=False)
    assert result.stdout.splitlines() == ["automations-sync result=none"]
    assert {k: j["id"] for k, j in lab.jobs().items()} == before


def test_an_empty_list_on_purpose_removes_each_job_of_git(lab):
    start = len(lab.log())
    lab.write_list(json.dumps({"jobs": []}))
    try:
        # The pass that first reads the empty list removes the two jobs;
        # the next one finds nothing to do.
        assert lab.next_pass() == ["automations-sync result=ok jobs=0"]
        since = lab.log()[start:]
        for key in ("weekday-check", "weekly-review"):
            assert since.count(f"automations-sync key={key} result=removed") == 1
        jobs = lab.jobs()
        assert not [k for k in jobs if k.startswith("flux:")]
        assert "made-in-a-chat" in jobs
    finally:
        lab.write_list(json.dumps({"jobs": JOBS}))
        lab.next_pass()


def test_the_log_holds_keys_and_results_only(lab):
    pattern = re.compile(
        r"automations-sync (key=[a-z0-9-]+ result=(ok state=(on|off|stopped( note=no-git-value)?)|failed|removed)"
        r"|result=(ok jobs=\d+|none|failed reason=[a-z-]+( jobs=\d+ failed=\d+)?))")
    lines = lab.log()
    assert lines
    for line in lines:
        assert pattern.fullmatch(line), line
        assert CHAT not in line and TOKEN not in line
