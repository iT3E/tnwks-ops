"""The agent pod of the main agent, in the form of the finance agent
(docs/apps/agent-platform-runbook.md, "The cutover of the main agent"):
the pod is the finance pod with no MCP client and no scheduled job, the
config has the tools of the first step of M7a, the network policy is the
pilot's with the fetch proxy, the restore Job is the one of finance with no vault, the values
check refuses a placeholder and a wrong group file, and the steps of
kustomization.yaml never meet in a wrong order.

The three Secret files of the owner's values are in git, not listed until
step 4: test_the_secrets_of_the_owner_hold_each_value_the_pod_names holds
their names and keys.

The container tests need Docker. Without it a test is skipped, except in
CI, where it fails.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"
APP = AGENTS / "main/agent/app"
FINANCE = AGENTS / "finance/agent/app"
CONFIG = AGENTS / "main/agent/config/openclaw.json"
SECRETS = {
    "secret.sops.yaml": ("agent-secret", {"OPENCLAW_GATEWAY_TOKEN", "LITELLM_API_KEY", "TELEGRAM_BOT_TOKEN",
                                          "TELEGRAM_ALLOW_FROM"}),
    "secret-search.sops.yaml": ("agent-search-secret", {"BRAVE_API_KEY"}),
    "secret-telegram-groups.sops.yaml": ("agent-telegram-groups", {"telegram-groups.json"}),
}


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def doc(path: Path) -> dict:
    (only,) = docs(path)
    return only


def pod(path: Path) -> dict:
    return doc(path)["spec"]["template"]["spec"]


def listed() -> set[str]:
    resources = yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]
    return {r.removeprefix("./") for r in resources}


def names(containers: list[dict]) -> list[str]:
    return [c["name"] for c in containers]


MAIN = pod(APP / "deployment.yaml")
FIN = pod(FINANCE / "deployment.yaml")


def test_the_pod_is_the_finance_pod_with_no_mcp_client_and_no_scheduled_job():
    assert names(MAIN["initContainers"]) == names(FIN["initContainers"])
    assert names(MAIN["containers"]) == [n for n in names(FIN["containers"]) if n != "automations-sync"]
    assert {v["name"] for v in MAIN["volumes"]} == {v["name"] for v in FIN["volumes"]} - {"mcp-gateway-config"}
    assert MAIN["nodeSelector"] == FIN["nodeSelector"]
    assert MAIN["securityContext"] == FIN["securityContext"]
    for a, b in zip(MAIN["containers"], FIN["containers"], strict=False):
        assert a["image"] == b["image"]
        assert a["securityContext"] == b["securityContext"], a["name"]
    text = (APP / "deployment.yaml").read_text()
    body = text[text.index("apiVersion:"):]
    assert "finance" not in body and "mcp" not in body.lower()
    assert "openshell.agents-main.svc.cluster.local" in body


def test_the_config_volume_joins_the_group_file_of_the_owner():
    (config,) = [v for v in MAIN["volumes"] if v["name"] == "config"]
    sources = config["projected"]["sources"]
    assert sources == [
        {"configMap": {"name": "agent-config"}},
        {"secret": {"name": "agent-telegram-groups",
                    "items": [{"key": "telegram-groups.json", "path": "telegram-groups.json"}]}},
    ]
    # The repair step works on a copy of the config: the group file goes
    # with it, so the relative include resolves there too.
    (doctor,) = [c for c in MAIN["initContainers"] if c["name"] == "doctor"]
    assert "/home/node/config/telegram-groups.json /tmp/doctor/" in doctor["command"][2]


def test_the_config_has_the_tools_of_the_first_step_of_m7a():
    c = json.loads(CONFIG.read_text())
    assert "mcp" not in c
    assert "firefly" not in CONFIG.read_text() and "finance" not in CONFIG.read_text()
    assert c["agents"]["defaults"]["model"]["primary"] == "litellm/agents-main"
    assert c["agents"]["defaults"]["modelPolicy"]["allow"] == ["litellm/agents-main"]
    assert [m["id"] for m in c["models"]["providers"]["litellm"]["models"]] == ["agents-main"]
    assert c["memory"]["search"]["model"] == "agents-main-embeddings"
    assert "extraPaths" not in c["memory"]["search"]
    # D16: the agent keeps the message tool, in its sandboxed sessions too.
    # The owner's decisions of 2026-11-19: the progress card, and each session
    # of main sees the other sessions of main, as on the host. Web fetch goes
    # through the fetch proxy (test_main_fetch_proxy.py).
    assert c["tools"]["sandbox"]["tools"]["alsoAllow"] == ["memory_search", "memory_get", "web_search", "web_fetch",
                                                           "message", "progress_card"]
    assert c["tools"]["sessions"] == {"visibility": "all"}
    assert c["agents"]["defaults"]["sandbox"]["sessionToolsVisibility"] == "all"
    telegram = c["channels"]["telegram"]
    assert telegram["dmPolicy"] == "allowlist" and telegram["allowFrom"] == ["${TELEGRAM_ALLOW_FROM}"]
    # D108: one group, from the file of the owner, and no chat id in git.
    assert telegram["groupPolicy"] == "allowlist"
    assert telegram["groups"] == {"$include": "./telegram-groups.json"}
    # As the account of main on the host: the last 15 messages of a group as
    # context, and the reply streamed as it is written.
    assert telegram["historyLimit"] == 15 and telegram["streaming"] == {"mode": "partial"}
    assert "groupAllowFrom" not in telegram  # the senders of the group: allowFrom
    assert c["agents"]["defaults"]["thinkingDefault"] == "off"
    assert not re.search(r"-100\d{6,}", CONFIG.read_text())
    assert c["plugins"]["entries"]["openshell"]["config"]["gatewayEndpoint"] == \
        "https://openshell.agents-main.svc.cluster.local:8080"
    assert "policy" not in c["plugins"]["entries"]["openshell"]["config"]
    assert c["diagnostics"]["otel"]["serviceName"] == "openclaw-agents-main"
    # Every other setting is the one of finance.
    f = json.loads((FINANCE.parent / "config/openclaw.json").read_text())
    for key in ("gateway", "update", "telemetry", "acp", "commands", "session"):
        assert c[key] == f[key], key
    assert c["plugins"]["allow"] == f["plugins"]["allow"]


def test_the_network_policy_is_the_pilots_in_agents_main_and_the_fetch_proxy():
    main = doc(APP / "networkpolicy.yaml")
    pilot = doc(AGENTS / "pilot/agent/app/networkpolicy.yaml")
    # The one rule more: the fetch proxy of main (test_main_fetch_proxy.py).
    proxy = [r for r in main["spec"]["egress"]
             if r.get("toEndpoints", [{}])[0].get("matchLabels", {}).get("app.kubernetes.io/name") == "fetch-proxy"]
    assert len(proxy) == 1
    main["spec"]["egress"].remove(proxy[0])
    text = json.dumps(main["spec"]).replace("agents-main", "agents-pilot")
    assert json.loads(text) == pilot["spec"]
    # No route to the MCP gateway in this step.
    assert "mcp-gateway" not in json.dumps(main)


def test_the_restore_job_is_the_one_of_finance_with_no_vault():
    main = pod(APP / "restore-job.yaml")
    fin = pod(FINANCE / "restore-job.yaml")
    assert main["containers"][0]["args"] == fin["containers"][0]["args"]
    assert main["nodeSelector"] == fin["nodeSelector"]
    env = {e["name"]: e["value"] for e in main["containers"][0]["env"]}
    assert env["VAULT"] == ""
    assert env["ARCHIVE"].startswith("start/openclaw-state-")


def test_the_steps_never_meet_in_a_wrong_order():
    now = listed()
    replicas = doc(APP / "deployment.yaml")["spec"]["replicas"]
    secrets = set(SECRETS) & now
    for name in secrets:
        assert (APP / name).exists(), name
    if "restore-job.yaml" in now:
        # Step 4: the inbox is out, the Secrets are in, no pod, and the name
        # of the owner's copy is set.
        assert replicas == 0 and "state-inbox.yaml" not in now
        assert secrets == set(SECRETS)
        env = {e["name"]: e["value"] for e in pod(APP / "restore-job.yaml")["containers"][0]["env"]}
        assert "SET-ME" not in env["ARCHIVE"]
    if replicas:
        # Step 5: the restore Job is out before the agent starts.
        assert "restore-job.yaml" not in now and "state-inbox.yaml" not in now
        assert secrets == set(SECRETS)
    assert replicas <= 1


def test_the_secrets_of_the_owner_hold_each_value_the_pod_names():
    """The three files are in git with the name and the keys the pod reads."""
    for name, (secret, keys) in SECRETS.items():
        assert (APP / name).exists(), f"{name}: the owner's file is not in git yet"
        d = doc(APP / name)
        assert d["metadata"]["name"] == secret
        assert set(d["stringData"]) == keys
    named = set(re.findall(r"\$\{([A-Z0-9_]+)\}", CONFIG.read_text()))
    assert named <= SECRETS["secret.sops.yaml"][1] | SECRETS["secret-search.sops.yaml"][1]


def test_the_model_gateway_has_the_embeddings_of_main_on_the_host_model():
    text = (ROOT / "kubernetes/apps/ai/litellm/app/configmap.yaml").read_text()
    config = yaml.safe_load(yaml.safe_load(text)["data"]["config.yaml"])
    entries = {m["model_name"]: m["litellm_params"] for m in config["model_list"]}
    assert entries["agents-main-embeddings"] == entries["agents-finance-embeddings"]
    assert config["router_settings"]["model_group_alias"]["agents-main"] == "claude-opus-bridge-main"


# The values check, in the agent image.

VALUES = sorted(SECRETS["secret.sops.yaml"][1] | SECRETS["secret-search.sops.yaml"][1])
VALUE = "a-real-looking-value"
GROUP = '{"-1000000000001": {"requireMention": false}}'


def run_values(env, group=GROUP):
    (check,) = [c for c in MAIN["initContainers"] if c["name"] == "values-check"]
    tmp = scratch()
    if group is not None:
        (tmp / "telegram-groups.json").write_text(group)
        (tmp / "telegram-groups.json").chmod(0o644)
    tmp.chmod(0o755)
    image = os.environ.get("RESTORE_TEST_IMAGE", check["image"])
    args = [docker(), "run", "--rm", "--quiet", "--network", "none", "-v", f"{tmp}:/home/node/config:ro"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    result = subprocess.run([*args, "--entrypoint", "sh", image, "-c", check["command"][2]],
                            capture_output=True, text=True, timeout=120, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def test_the_values_check_is_the_first_step_and_reads_each_secret():
    first = MAIN["initContainers"][0]
    assert first["name"] == "values-check"
    assert {s["secretRef"]["name"] for s in first["envFrom"]} == {"agent-secret", "agent-search-secret"}
    assert all(key in first["command"][2] for key in VALUES)
    assert first["volumeMounts"] == [{"name": "config", "mountPath": "/home/node/config", "readOnly": True}]


def test_the_values_check_passes_with_every_value():
    assert run_values(dict.fromkeys(VALUES, VALUE)) == (0, "values-check result=ok")


@pytest.mark.parametrize(("value", "reason"), [("pending", "placeholder"), ("", "empty")])
def test_the_values_check_refuses_a_placeholder(value, reason):
    env = dict.fromkeys(VALUES, VALUE)
    env["TELEGRAM_BOT_TOKEN"] = value
    code, out = run_values(env)
    assert code != 0
    assert out == f"values-check result=failed key=TELEGRAM_BOT_TOKEN reason={reason}"


@pytest.mark.parametrize(("group", "reason"), [
    (None, "empty"),
    ("pending", "placeholder"),
    ('{"-1000000000001": {}, "-1000000000002": {}}', "form"),
    ('{"1000000000001": {}}', "form"),
    ('{"*": {}}', "form"),
    ("[]", "form"),
    ("not json", "form"),
])
def test_the_values_check_refuses_a_group_file_that_is_not_one_group(group, reason):
    code, out = run_values(dict.fromkeys(VALUES, VALUE), group)
    assert code != 0
    assert out == f"values-check result=failed key=telegram-groups.json reason={reason}"
    assert "100000000000" not in out
