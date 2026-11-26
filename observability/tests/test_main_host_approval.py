"""The host approval plugin of the main agent (Design Doc of effort 0003,
section 4.2.6, "The prompt, our one own part"; task 6.22).

The agent image 0.5.0 has the plugin in
/opt/openclaw/local-plugins/host-approval. The config of main loads it, with
the approval form that the plugin checks at its load:

- the plugin cards go to the direct chat of the approver only
  (approvals.plugin), and only he answers them (execApprovals.approvers);
- commands.allowFrom names the members of the approval channel, the same
  list as the allow list of the channel, and an empty list for each other
  channel ("*"); commands.ownerAllowFrom names the approver;
- the deny list names openclaw, plugins, terminal and portal.

The approver is TELEGRAM_ALLOW_FROM, the one sender of the allow list of
main. No chat ID is in git.

With a fault of that form the plugin refuses each tool call of the agent,
the vault and the read routes too. So the values check of the pod runs the
code of the plugin in the image on the config, with the values of the env,
and stops the start first (test_main_agent.py has the other rows of the
values check).

The plugin names the route main-host of the host server (task 6.22) and
the env var MCP_HOST_KEY, which the store step of the lead puts in
agent-secret with its hash on the route. With no key in the env the
resolver of the plugin gives no connection, so the agent has no host tool
and the server gets no call. The read list holds the nine lines of the
design (test_main_host_server.py).

The container tests need Docker. Without it a test is skipped, except in
CI, where it fails. RESTORE_TEST_IMAGE names a local build of the image.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from test_main_agent import APP, CONFIG, GATEWAY_CONFIG, MAIN, good, run_values
from test_main_host_server import READ_LIST, READS

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = "/opt/openclaw/local-plugins/host-approval"
HOST_ROUTE = "main-host"
KEY_ENV = "MCP_HOST_KEY"


def config() -> dict:
    return json.loads(CONFIG.read_text())


def entry() -> dict:
    return config()["plugins"]["entries"]["host-approval"]


def test_the_image_has_the_plugin():
    images = {c["image"] for c in MAIN["initContainers"] + MAIN["containers"]}
    assert len(images) == 1
    (image,) = images
    assert re.fullmatch(r"ghcr\.io/it3e/tnwks-agent-image:0\.5\.0@sha256:[0-9a-f]{64}", image)
    for name in ("restore-job.yaml", "state-inbox.yaml"):
        assert image in (APP / name).read_text(), name


def test_the_config_loads_the_plugin_from_the_image():
    c = config()
    assert PLUGIN_PATH in c["plugins"]["load"]["paths"]
    assert "host-approval" in c["plugins"]["allow"]
    assert entry()["enabled"] is True


def test_the_plugin_config_is_the_form_of_the_design():
    assert entry()["config"] == {
        "serverPrefixes": ["host__"],
        "readProfile": "agent",
        # The nine lines of the design for the user in the distro of site 1
        # (test_main_host_server.py); the PowerShell lines of the ten checks
        # come with the write step.
        "readList": READ_LIST,
        # The browser tools that can send a form (test_main_browser.py).
        "promptTools": ["browser__browser_click", "browser__browser_drag", "browser__browser_fill_form",
                        "browser__browser_handle_dialog", "browser__browser_press_key",
                        "browser__browser_select_option", "browser__browser_type"],
        "approverId": "${TELEGRAM_ALLOW_FROM}",
        "approvalChannel": "telegram",
        "timeoutMs": 600000,
        "mcpUrl": f"http://mcp-gateway.ai.svc.cluster.local:3000/{HOST_ROUTE}/mcp",
        "mcpKeyEnv": KEY_ENV,
    }


def test_the_approval_form_names_the_approver_only():
    c = config()
    telegram = c["channels"]["telegram"]
    approver = "${TELEGRAM_ALLOW_FROM}"
    assert telegram["allowFrom"] == [approver]
    assert c["approvals"] == {"plugin": {"enabled": True, "mode": "targets",
                                         "targets": [{"channel": "telegram", "to": approver}]}}
    assert telegram["execApprovals"] == {"enabled": True, "approvers": [approver]}
    # The members of the approval channel keep their commands: the same list
    # as the allow list of the channel. Each other channel: none.
    assert c["commands"]["allowFrom"] == {"telegram": telegram["allowFrom"], "*": []}
    assert c["commands"]["ownerAllowFrom"] == [f"telegram:{approver}"]


def test_the_tools_that_change_the_product_are_denied():
    assert {"openclaw", "plugins", "terminal", "portal"} <= set(config()["tools"]["deny"])


def host_listed() -> bool:
    k = yaml.safe_load((ROOT / "kubernetes/apps/ai/agents/main/kustomization.yaml").read_text())
    return "./host-mcp/ks.yaml" in k["resources"]


def test_the_host_key_is_in_agent_secret_once_the_server_is_listed():
    """With the host server listed, the key of the plugin is in agent-secret
    of main as ciphertext, and its hash is on the route
    (test_main_host_server.py): the store step of the lead makes both, on
    top of the go-live commit, so this test fails until then. With the
    server not listed (the way back), the key and its hash may stay, or
    neither: a placeholder hash and no file of the pod that names the key.
    No server entry of the config points at the route, and the config names
    the key in the plugin entry only."""
    (route,) = [r for r in yaml.safe_load(GATEWAY_CONFIG.read_text())["routes"] if r["name"] == HOST_ROUTE]
    ((key,),) = [route["policies"]["apiKey"]["keys"]]
    # Each file below the folder, the folder of the sign-in check too.
    holders = sorted(str(p.relative_to(APP)) for p in APP.rglob("*") if p.is_file() and KEY_ENV in p.read_text())
    stored = holders == ["secret.sops.yaml"]
    if host_listed():
        assert stored, f"the key is not stored yet: host-mcp-store.py gateway-key ({holders})"
    if stored:
        (secret,) = [d for d in yaml.safe_load_all((APP / "secret.sops.yaml").read_text()) if d]
        # No value is printed in a failure message: the assert is on a bool.
        assert str(secret["stringData"][KEY_ENV]).startswith("ENC[AES256_GCM,data:")
        assert not re.fullmatch(r"sha256:0{63}[0-9]", key["keyHash"])
    else:
        assert holders == []
        assert re.fullmatch(r"sha256:0{63}[0-9]", key["keyHash"])
    c = config()
    for name, server in c["mcp"]["servers"].items():
        assert f"/{HOST_ROUTE}/" not in server["url"], name
    text = CONFIG.read_text()
    assert text.count(KEY_ENV) == 1  # the plugin entry only


def test_the_sandbox_allow_list_names_the_six_reads_once_the_server_is_listed():
    """In a sandboxed session an MCP tool also passes the sandbox allow list
    (test_main_read_servers.py). The manifest of the plugin declares the
    server `host`, so its tools are `host__<tool>`. With the server listed,
    the list names the six read tools of the route by their exact names and
    no other tool of the server: without them the agent lists the tools and
    never sees one. With the server not listed (the way back), the names may
    stay, as the read list, or go."""
    allow = config()["tools"]["sandbox"]["tools"]["alsoAllow"]
    host = [t for t in allow if t.startswith("host__")]
    assert len(host) == len(set(host))
    six = sorted(f"host__{tool}" for tool in READS)
    assert sorted(host) == six or (not host_listed() and host == [])


def test_the_values_check_runs_the_check_of_the_plugin():
    (check,) = [c for c in MAIN["initContainers"] if c["name"] == "values-check"]
    script = check["command"][2]
    assert f"{PLUGIN_PATH}/lib.js" in script
    for call in ("parseConfig", "checkProductConfig", "checkServerRoutes"):
        assert f"lib.{call}(" in script
    # Flux substitutes a dollar sign before a brace in this file.
    assert "${" not in script


def changed(change) -> str:
    c = config()
    change(c)
    return json.dumps(c)


def drop(path: str):
    def change(c):
        *parents, last = path.split(".")
        node = c
        for p in parents:
            node = node[p]
        del node[last]
    return change


def setter(path: str, value):
    def change(c):
        *parents, last = path.split(".")
        node = c
        for p in parents:
            node = node[p]
        node[last] = value
    return change


def host_entry(c):
    c["mcp"]["servers"]["other"] = {"url": f"http://elsewhere.example:8080/{HOST_ROUTE}/mcp",
                                    "transport": "streamable-http"}


BAD_FORMS = {
    "no command rule": drop("commands.allowFrom"),
    "commands of another channel": setter("commands.allowFrom", {"telegram": ["${TELEGRAM_ALLOW_FROM}"],
                                                                 "*": ["1000000002"]}),
    "a wildcard on the approval channel": setter("commands.allowFrom", {"telegram": ["*"], "*": []}),
    "no plugin cards": drop("approvals"),
    "cards to another chat": setter("approvals.plugin.targets", [{"channel": "telegram", "to": "1000000002"}]),
    "no approver rule": drop("channels.telegram.execApprovals"),
    "a second approver": setter("channels.telegram.execApprovals.approvers",
                                ["${TELEGRAM_ALLOW_FROM}", "1000000002"]),
    "another owner": setter("commands.ownerAllowFrom", ["telegram:1000000002"]),
    "the plugin off": setter("plugins.entries.host-approval.enabled", False),
    "an unknown plugin key": setter("plugins.entries.host-approval.config.extra", True),
    "a server entry at the host route": host_entry,
    "the key in another place": setter("gateway.note", "${MCP_HOST_KEY}"),
}


def test_the_values_check_passes_the_config_of_git():
    assert run_values(good()) == (0, "values-check result=ok")


@pytest.mark.parametrize("form", sorted(BAD_FORMS))
def test_the_values_check_stops_a_config_that_the_plugin_refuses(form):
    code, out = run_values(good(), agent_config=changed(BAD_FORMS[form]))
    assert code != 0
    assert out == "values-check result=failed key=host-approval reason=form"
    assert "1000000001" not in out and "1000000002" not in out


@pytest.mark.parametrize("value", ["telegram:1000000001", "1000000001,1000000002", "@someone", "-1000000001"])
def test_the_values_check_stops_an_approver_that_is_not_one_chat_id(value):
    env = good()
    env["TELEGRAM_ALLOW_FROM"] = value
    code, out = run_values(env)
    assert code != 0
    assert out == "values-check result=failed key=TELEGRAM_ALLOW_FROM reason=form"
