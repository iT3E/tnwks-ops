"""The browser of the main agent (Design Doc of effort 0003, section 4.2.6,
"The browser, its rails"; task 6.24; docs/apps/playwright-mcp.md).

- The route main-browser of the central MCP gateway takes the key of main
  only, sets no credential and names the core tools of the dump of the
  pinned image by exact name, with five left out. It is stateful: the
  calls of one session reach the same server session, and so the same page.
- The browser runs with the flags of the design, pinned by digest, and its
  pages go through the fetch proxy of main, whose policy lets it in.
- The gateway reaches the browser by its name; both probes try it straight,
  and only a time out passes.
- The host approval plugin asks the approver before each browser tool that
  can send a form.
- The switch: the browser is deployed, not live for use, until the config
  of the agent names a server at the route, with its tools in the filter
  and the sandbox allow list. test_the_switch_names_the_route_or_nothing
  holds both states.

The lab runs the gateway at its pin with the config of git (a key of the
test in place of the hash of main) and a fake browser that keeps a page for
each session. It needs Docker: without it a test is skipped, except in CI.
"""

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch
from test_main_read_servers import (
    CONFIG,
    GATEWAY,
    PROBES,
    PYTHON,
    docs,
    main_hash,
    peers,
    ports,
    probe_container,
    route,
)
from test_mcp_gateway import gateway_image, run

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/playwright-mcp/app"
RELEASE = APP / "helmrelease.yaml"
AGENT_CONFIG = ROOT / "kubernetes/apps/ai/agents/main/agent/config/openclaw.json"
PROXY_POLICY = ROOT / "kubernetes/apps/ai/agents/main/fetch-proxy/app/networkpolicy.yaml"
GATEWAY_POLICY = GATEWAY / "app/networkpolicy.yaml"
ROUTE = "main-browser"
SERVICE = "playwright-mcp.ai.svc.cluster.local"
PORT = 8000
# Left out of the rule: code in the page or the server, a file of the server
# pod to a page, and a browser install.
NEVER = ["browser_run_code_unsafe", "browser_evaluate", "browser_file_upload", "browser_drop", "browser_install"]
# The tools that can send a form: the plugin asks the approver first (D133).
# A drag can drop an element on a target that sends, so it asks too.
PROMPT = ["browser_click", "browser_drag", "browser_fill_form", "browser_handle_dialog", "browser_press_key",
          "browser_select_option", "browser_type"]
# The tools that read a page or move in it, with no card. Each tool of the
# rule is in one of the two lists, so a tool that a later PR adds to the rule
# needs a choice.
NO_PROMPT = ["browser_close", "browser_console_messages", "browser_emulate_media", "browser_find",
             "browser_hover", "browser_navigate", "browser_navigate_back", "browser_network_request",
             "browser_network_requests", "browser_resize", "browser_snapshot", "browser_tabs",
             "browser_take_screenshot", "browser_wait_for"]


def dump() -> dict:
    return json.loads((GATEWAY / "tools/browser.json").read_text())


def app() -> dict:
    (release,) = docs(RELEASE)
    return release["spec"]["values"]["controllers"]["playwright-mcp"]["containers"]["app"]


def rule() -> list[str]:
    (r,) = route(ROUTE)["policies"]["mcpAuthorization"]["rules"]
    text = r["allow"]
    return [n.strip().strip('"') for n in text[text.index("[") + 1:text.index("]")].split(",") if n.strip()]


def kept() -> list[str]:
    return sorted(t["name"] for t in dump()["tools"] if t["name"] not in NEVER)


def agent() -> dict:
    return json.loads(AGENT_CONFIG.read_text())


# The image, the dump and the rule


def test_the_dump_is_of_the_pinned_image():
    image = app()["image"]
    assert dump()["image"] == f"{image['repository']}:{image['tag']}"
    assert "@sha256:" in image["tag"]


def test_the_rule_names_the_core_tools_of_the_dump_but_five():
    names = [t["name"] for t in dump()["tools"]]
    assert rule() == kept()
    assert not set(NEVER) & set(rule())
    # Each left-out tool that the release has is in the dump.
    assert {n for n in NEVER if n != "browser_install"} <= set(names)


def test_the_route_takes_the_main_key_only():
    r = route(ROUTE)
    (key,) = r["policies"]["apiKey"]["keys"]
    assert key == {"keyHash": main_hash(), "metadata": {"client": "main", "route": ROUTE}}
    assert r["policies"]["apiKey"]["mode"] == "strict"
    allow = " ".join(r["policies"]["mcpAuthorization"]["rules"][0]["allow"].split())
    assert allow.endswith('] && apiKey.client == "main"')


def test_the_route_is_stateful_and_sets_no_credential():
    r = route(ROUTE)
    assert r["policies"]["transformations"] == route("main-terraform")["policies"]["transformations"]
    (backend,) = r["backends"]
    assert backend["mcp"]["statefulMode"] == "stateful"
    assert backend["mcp"]["failureMode"] == "failClosed"
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == f"http://{SERVICE}:{PORT}/mcp"
    assert "backendAuth" not in target.get("policies", {})


def test_no_other_route_names_the_browser():
    for r in yaml.safe_load(CONFIG.read_text())["routes"]:
        if r["name"] != ROUTE:
            assert SERVICE not in json.dumps(r), r["name"]


# The browser pod


def test_the_browser_has_the_flags_of_the_design():
    args = app()["args"]
    assert {"--isolated", "--headless", "--no-webmcp", "--block-service-workers"} <= set(args)
    assert f"--allowed-hosts={SERVICE}:{PORT}" in args
    assert "--proxy-server=http://fetch-proxy.agents-main.svc.cluster.local:8080" in args
    assert any(a.startswith("--redact-url-params=") for a in args)
    # No capability, no shared context, no state, no secrets, no saved session.
    for flag in ("--caps", "--shared-browser-context", "--storage-state", "--secrets", "--user-data-dir",
                 "--save-session", "--allowed-hosts=*", "--extension"):
        assert not [a for a in args if a.startswith(flag)], flag


def test_the_fetch_proxy_lets_in_the_browser():
    (policy,) = docs(PROXY_POLICY)
    rules = policy["spec"]["ingress"]
    who = [(p["io.kubernetes.pod.namespace"], p["app.kubernetes.io/name"]) for r in rules for p in peers(r)]
    assert ("ai", "playwright-mcp") in who
    for r in rules:
        for p in peers(r):
            assert p["io.cilium.k8s.policy.cluster"] == "${CLUSTER_NAME}"


def test_the_gateway_reaches_the_browser_by_its_name():
    (policy,) = docs(GATEWAY_POLICY)
    out = [(p.get("io.kubernetes.pod.namespace"), p.get("app.kubernetes.io/name"), ports(r))
           for r in policy["spec"]["egress"] for p in peers(r)]
    assert ("ai", "playwright-mcp", [str(PORT)]) in out
    assert SERVICE in GATEWAY_POLICY.read_text()


@pytest.mark.parametrize("probe", PROBES)
def test_each_probe_tries_the_browser_straight(probe):
    env = {e["name"]: e.get("value") for e in probe_container(PROBES[probe])["env"]}
    assert f"{SERVICE}:{PORT}" in env["GATEWAY_ONLY_SERVERS"].split(",")
    (policy,) = [d for d in docs(PROBES[probe]) if d["kind"] == "CiliumNetworkPolicy"]
    out = [(p["io.kubernetes.pod.namespace"], p["app.kubernetes.io/name"], ports(r))
           for r in policy["spec"]["egress"] for p in peers(r)]
    assert ("ai", "playwright-mcp", [str(PORT)]) in out


# The prompt and the switch


def test_the_plugin_asks_before_each_tool_that_can_send_a_form():
    prompt = agent()["plugins"]["entries"]["host-approval"]["config"]["promptTools"]
    assert prompt == [f"browser__{t}" for t in PROMPT]
    assert set(PROMPT) <= set(rule())


def test_each_tool_of_the_rule_asks_or_is_named_as_one_that_does_not():
    assert sorted(PROMPT + NO_PROMPT) == sorted(rule())
    assert not set(PROMPT) & set(NO_PROMPT)


def test_the_switch_names_the_route_or_nothing():
    """Off: no server entry of the agent reaches the route, and no browser
    tool is in the sandbox allow list. On: one entry `browser` at the route
    with the key of main and the tools of the rule, each by its exact name in
    the sandbox allow list, never a wildcard."""
    c = agent()
    entries = {n: s for n, s in c["mcp"]["servers"].items() if f"/{ROUTE}/" in s["url"]}
    allow = c["tools"]["sandbox"]["tools"]["alsoAllow"]
    browser = [a for a in allow if a.startswith("browser")]
    if not entries:
        assert browser == []
        return
    assert list(entries) == ["browser"]
    entry = entries["browser"]
    assert entry["url"] == f"http://mcp-gateway.ai.svc.cluster.local:3000/{ROUTE}/mcp"
    assert entry["headers"] == {"Authorization": "Bearer ${MCP_GATEWAY_KEY}"}
    assert entry["toolFilter"] == {"include": rule()}
    assert browser == [f"browser__{t}" for t in rule()]


# The lab: the gateway at its pin, a fake browser with a page for each session

FAKE_BROWSER = r'''
import json, itertools, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
TOOLS = json.loads(sys.argv[1])
PAGES, LOCK, IDS = {}, threading.Lock(), itertools.count(1)
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass
    def reply(self, obj, sid=None, code=200):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if sid:
            self.send_header("mcp-session-id", sid)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self.reply(None, code=405)
    def do_DELETE(self):
        self.reply(None)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        sid, method = self.headers.get("mcp-session-id"), body.get("method")
        if method == "initialize":
            new = f"page{next(IDS)}"
            with LOCK:
                PAGES[new] = None
            return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {
                "protocolVersion": body["params"]["protocolVersion"], "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-browser", "version": "1"}}}, new)
        if "id" not in body:
            return self.reply(None, code=202)
        if method == "tools/list":
            return self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                {"name": t, "inputSchema": {"type": "object"}} for t in TOOLS]}}, sid)
        name, args = body["params"]["name"], body["params"].get("arguments") or {}
        with LOCK:
            if name == "browser_navigate":
                PAGES[sid] = args.get("url")
            text = f"{sid} {PAGES.get(sid)}"
        self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": text}]}}, sid)
ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
'''

CLIENT = r'''
import json, sys, urllib.request, urllib.error
URL, KEY = sys.argv[1], sys.argv[2]
def post(body, sid=None, key=KEY):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key:
        h["Authorization"] = "Bearer " + key
    if sid:
        h["mcp-session-id"] = sid
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, json.dumps(body).encode(), h), timeout=30) as r:
            code, raw, new = r.status, r.read().decode(), r.headers.get("mcp-session-id")
    except urllib.error.HTTPError as e:
        code, raw, new = e.code, e.read().decode(errors="replace"), None
    ev = [l[5:].strip() for l in raw.splitlines() if l.startswith("data:")]
    try:
        return code, json.loads(ev[-1] if ev else raw), new
    except ValueError:
        return code, None, new
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
        "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
def session():
    code, _, sid = post(INIT)
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    return sid
def call(sid, name, args=None):
    code, body, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": name, "arguments": args or {}}}, sid)
    if code == 400 and (body or {}).get("error", {}).get("message") == "unknown resource":
        return "refused: gateway"
    if code == 200 and body and "result" in body:
        return " ".join(c["text"] for c in body["result"]["content"])
    return f"other: {code}"
out = {}
a, b = session(), session()
out["two sessions"] = bool(a) and bool(b) and a != b
code, body, _ = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, a)
out["list"] = sorted(t["name"] for t in body["result"]["tools"])
call(a, "browser_navigate", {"url": "https://a.example/"})
out["a"] = call(a, "browser_snapshot")
out["b before"] = call(b, "browser_snapshot")
call(b, "browser_navigate", {"url": "https://b.example/"})
out["b"] = call(b, "browser_snapshot")
out["a again"] = call(a, "browser_snapshot")
for name in json.loads(sys.argv[3]):
    out[name] = call(a, name)
out["no key"] = post(INIT, key=None)[0]
out["other key"] = post(INIT, key="test-other-" + KEY[-8:])[0]
print(json.dumps(out))
'''


@pytest.fixture(scope="module")
def lab():
    tool = docker()
    work = scratch()
    key = f"test-browser-{uuid.uuid4().hex}"
    (work / "config.yaml").write_text(CONFIG.read_text().replace(
        main_hash(), "sha256:" + hashlib.sha256(key.encode()).hexdigest()))
    (work / "config.yaml").chmod(0o644)
    tag = uuid.uuid4().hex[:8]
    net = f"browser-{tag}"
    names = [f"browser-gw-{tag}", f"browser-fake-{tag}"]
    try:
        run([tool, "network", "create", "--internal", net])
        run([tool, "run", "-d", "--name", names[1], "--network", net, "--network-alias", SERVICE,
             "--read-only", "--cap-drop", "ALL", "--user", "65534", PYTHON, "python3", "-I", "-c", FAKE_BROWSER,
             json.dumps([t["name"] for t in dump()["tools"]])])
        run([tool, "run", "-d", "--name", names[0], "--network", net,
             "--network-alias", "mcp-gateway.ai.svc.cluster.local", "--read-only", "--tmpfs", "/tmp",
             "--cap-drop", "ALL", "--user", "65532", "-v", f"{work}/config.yaml:/config/config.yaml:ro",
             gateway_image(), "-f", "/config/config.yaml"])
        yield {"tool": tool, "net": net, "key": key, "gateway": names[0]}
    finally:
        for name in names:
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def client(lab: dict) -> dict:
    code = ("import time, urllib.request\n"
            "for i in range(60):\n"
            "    try:\n"
            "        urllib.request.urlopen('http://mcp-gateway.ai.svc.cluster.local:15021/healthz/ready', timeout=2)\n"
            "        break\n"
            "    except Exception:\n"
            "        time.sleep(1)\n")
    result = run([lab["tool"], "run", "--rm", "--network", lab["net"], "--read-only", "--cap-drop", "ALL",
                  "--user", "65534", PYTHON, "sh", "-c",
                  'python3 -I -c "$0" && exec python3 -I -c "$1" "$2" "$3" "$4"', code, CLIENT,
                  f"http://mcp-gateway.ai.svc.cluster.local:3000/{ROUTE}/mcp", lab["key"],
                  json.dumps([*NEVER, "zz_made_up_tool_name"])])
    return json.loads(result.stdout)


def test_the_lab_route_keeps_a_page_for_each_session_and_the_rule(lab):
    out = client(lab)
    assert out["two sessions"] is True
    assert out["list"] == rule()
    a_page, a_url = out["a"].split()
    b_page, b_url = out["b"].split()
    assert a_url == "https://a.example/" and b_url == "https://b.example/"
    assert a_page != b_page
    # The second session does not see the page of the first.
    assert out["b before"].split()[1] == "None"
    assert out["a again"] == out["a"]
    for name in [*NEVER, "zz_made_up_tool_name"]:
        assert out[name] == "refused: gateway", name
    assert out["no key"] == 401 and out["other key"] == 401


def test_the_lab_gateway_log_has_no_key_and_no_page(lab):
    client(lab)
    result = run([lab["tool"], "logs", lab["gateway"]])
    log = result.stdout + result.stderr
    assert lab["key"] not in log
    assert "a.example" not in log and "b.example" not in log
