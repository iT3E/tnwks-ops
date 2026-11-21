"""The agent pod of the main agent, in the form of the finance agent
(docs/apps/agent-platform-runbook.md, "The cutover of the main agent"):
the pod is the finance pod with no scheduled job, the config has the tools
of the first step of M7a and the vault of main (task 3.37), the network
policy is the pilot's with the fetch proxy and the MCP gateway, the restore
Job is the one of finance with no vault, the values check refuses a
placeholder, a key whose hash is not the one of the client main and a wrong
group file, and the steps of kustomization.yaml never meet in a wrong
order.

The three Secret files of the owner's values are in git, not listed until
step 4: test_the_secrets_of_the_owner_hold_each_value_the_pod_names holds
their names and keys.

The container tests need Docker. Without it a test is skipped, except in
CI, where it fails.
"""

import hashlib
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
GATEWAY_CONFIG = ROOT / "kubernetes/apps/ai/mcp-gateway/app/config/config.yaml"
SECRETS = {
    "secret.sops.yaml": ("agent-secret", {"OPENCLAW_GATEWAY_TOKEN", "LITELLM_API_KEY", "TELEGRAM_BOT_TOKEN",
                                          "TELEGRAM_ALLOW_FROM", "MCP_GATEWAY_KEY"}),
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


def test_the_pod_is_the_finance_pod_with_no_scheduled_job():
    assert names(MAIN["initContainers"]) == names(FIN["initContainers"])
    assert names(MAIN["containers"]) == [n for n in names(FIN["containers"]) if n != "automations-sync"]
    # Each pod mounts the share of its own trust domain (task 3.37), and
    # has the config of the MCP gateway for its values check.
    assert {v["name"] for v in MAIN["volumes"]} == {v["name"] for v in FIN["volumes"]}
    assert MAIN["nodeSelector"] == FIN["nodeSelector"]
    assert MAIN["securityContext"] == FIN["securityContext"]
    for a, b in zip(MAIN["containers"], FIN["containers"], strict=False):
        assert a["image"] == b["image"]
        assert a["securityContext"] == b["securityContext"], a["name"]
    text = (APP / "deployment.yaml").read_text()
    body = text[text.index("apiVersion:"):]
    assert "finance" not in body
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
    f = json.loads((FINANCE.parent / "config/openclaw.json").read_text())
    # The vault of main, as finance calls its own vault (task 3.37), then the
    # read servers of M7b (test_main_read_servers.py). No finance app.
    assert list(c["mcp"]["servers"])[0] == "vault"
    assert set(c["mcp"]["servers"]) <= {"vault", "cluster", "flux", "terraform", "github"}
    vault = dict(c["mcp"]["servers"]["vault"])
    assert vault.pop("url") == "http://mcp-gateway.ai.svc.cluster.local:3000/main-vault/mcp"
    other = dict(f["mcp"]["servers"]["vault"])
    other.pop("url")
    # Until the seed of the share, the read tools of finance only
    # (test_main_vault.py, WRITES).
    other["toolFilter"] = {"include": [t for t in other["toolFilter"]["include"]
                                       if t not in ("write_file", "edit_file", "create_directory")]}
    assert vault == other
    assert "firefly" not in CONFIG.read_text() and "finance" not in CONFIG.read_text()
    assert c["agents"]["defaults"]["model"]["primary"] == "litellm/agents-main"
    assert c["agents"]["defaults"]["modelPolicy"]["allow"] == ["litellm/agents-main"]
    assert [m["id"] for m in c["models"]["providers"]["litellm"]["models"]] == ["agents-main"]
    assert c["memory"]["search"]["model"] == "agents-main-embeddings"
    # The index reads the share of main, Markdown only.
    assert c["memory"]["search"]["extraPaths"] == [{"path": "/vault", "pattern": "**/*.md"}]
    # D16: the agent keeps the message tool, in its sandboxed sessions too.
    # The owner's decisions of 2026-11-19: the progress card, and each session
    # of main sees the other sessions of main, as on the host. Web fetch goes
    # through the fetch proxy (test_main_fetch_proxy.py).
    # The read tools of M7b follow, each by its exact name (test_main_read_servers.py).
    assert c["tools"]["sandbox"]["tools"]["alsoAllow"][:7] == ["memory_search", "memory_get", "web_search",
                                                               "web_fetch", "message", "progress_card", "vault__*"]
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
    # The owner set "high" on 2026-11-20. The host has "off", and the model of
    # the platform does not take "off": the product then used "low" and said
    # so in each run.
    assert c["agents"]["defaults"]["thinkingDefault"] == "high"
    assert not re.search(r"-100\d{6,}", CONFIG.read_text())
    assert c["plugins"]["entries"]["openshell"]["config"]["gatewayEndpoint"] == \
        "https://openshell.agents-main.svc.cluster.local:8080"
    assert "policy" not in c["plugins"]["entries"]["openshell"]["config"]
    assert c["diagnostics"]["otel"]["serviceName"] == "openclaw-agents-main"
    # Every other setting is the one of finance.
    for key in ("gateway", "update", "telemetry", "acp", "commands", "session"):
        assert c[key] == f[key], key
    assert c["plugins"]["allow"] == f["plugins"]["allow"]
    # Active memory as finance has it, on the model of the session (the
    # decision of the owner for the first run).
    assert c["plugins"]["entries"]["active-memory"] == f["plugins"]["entries"]["active-memory"]


def test_the_network_policy_is_the_pilots_in_agents_main_the_fetch_proxy_and_the_mcp_gateway():
    main = doc(APP / "networkpolicy.yaml")
    pilot = doc(AGENTS / "pilot/agent/app/networkpolicy.yaml")
    finance = doc(FINANCE / "networkpolicy.yaml")

    def rule(policy, name):
        found = [r for r in policy["spec"]["egress"]
                 if r.get("toEndpoints", [{}])[0].get("matchLabels", {}).get("app.kubernetes.io/name") == name]
        assert len(found) == 1, name
        return found[0]

    # The two rules more: the fetch proxy of main (test_main_fetch_proxy.py),
    # and the MCP gateway for the vault route, the rule of finance.
    assert rule(main, "mcp-gateway") == rule(finance, "mcp-gateway")
    main["spec"]["egress"].remove(rule(main, "fetch-proxy"))
    main["spec"]["egress"].remove(rule(main, "mcp-gateway"))
    text = json.dumps(main["spec"]).replace("agents-main", "agents-pilot")
    assert json.loads(text) == pilot["spec"]
    # Not the vault server straight: the gateway stands between.
    assert "vault-server" not in json.dumps(main)


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


def with_hash(client, key):
    """The config of the gateway with the hash of key for client on the route
    main-vault, in place of the hash in git (a placeholder or a real one)."""
    text = GATEWAY_CONFIG.read_text()
    (route,) = [r for r in yaml.safe_load(text)["routes"] if r["name"] == "main-vault"]
    (old,) = [k["keyHash"] for k in route["policies"]["apiKey"]["keys"] if k["metadata"]["client"] == client]
    # The key of main is on each route of main (the read routes of M7b too,
    # bin/main-mcp-key): each copy changes, as the script sets each one.
    routes = {r["name"] for r in yaml.safe_load(text)["routes"]
              for k in r["policies"]["apiKey"]["keys"] if k["keyHash"] == old}
    assert text.count(old) == len(routes) and "main-vault" in routes
    return text.replace(old, "sha256:" + hashlib.sha256(key.encode()).hexdigest())


def run_values(env, group=GROUP, config=None):
    (check,) = [c for c in MAIN["initContainers"] if c["name"] == "values-check"]
    tmp = scratch()
    if group is not None:
        (tmp / "telegram-groups.json").write_text(group)
        (tmp / "telegram-groups.json").chmod(0o644)
    gateway = scratch()
    (gateway / "config.yaml").write_text(with_hash("main", VALUE) if config is None else config)
    (gateway / "config.yaml").chmod(0o644)
    for path in (tmp, gateway):
        path.chmod(0o755)
    image = os.environ.get("RESTORE_TEST_IMAGE", check["image"])
    args = [docker(), "run", "--rm", "--quiet", "--network", "none", "-v", f"{tmp}:/home/node/config:ro",
            "-v", f"{gateway}:/gateway:ro"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    result = subprocess.run([*args, "--entrypoint", "sh", image, "-c", check["command"][2]],
                            capture_output=True, text=True, timeout=120, check=False)
    return result.returncode, (result.stdout + result.stderr).strip()


def test_the_official_plugins_step_reads_the_config_with_the_group_file():
    """The step of the image copies only openclaw.json to its work directory, so
    the include of the group file broke the first start of main. The pod fills
    the include in first; the step then installs the plugin, with no network."""
    (step,) = [c for c in MAIN["initContainers"] if c["name"] == "official-plugins"]
    assert step["command"][:2] == ["sh", "-c"] and "install-official-plugins" in step["command"][2]
    assert '"$include": "./telegram-groups.json"' in CONFIG.read_text()
    tmp = scratch()
    (tmp / "openclaw.json").write_text(CONFIG.read_text())
    (tmp / "telegram-groups.json").write_text(GROUP)
    for path in tmp.iterdir():
        path.chmod(0o644)
    image = os.environ.get("RESTORE_TEST_IMAGE", step["image"])
    args = [docker(), "run", "--rm", "--quiet", "--network", "none", "--user", "1000:1000",
            "-v", f"{tmp}:/home/node/config:ro",
            "--tmpfs", "/home/node/data:uid=1000,gid=1000,mode=0700", "--tmpfs", "/tmp:mode=1777"]
    for e in step["env"]:
        args += ["-e", f"{e['name']}={e['value']}"]
    result = subprocess.run([*args, "--entrypoint", "sh", image, "-c", step["command"][2]],
                            capture_output=True, text=True, timeout=600, check=False)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-4000:]
    assert "Failed to read include file" not in output
    assert re.search(r"official-plugins id=brave version=\S+ result=installed", output), output[-4000:]


def test_the_values_check_is_the_first_step_and_reads_each_secret():
    first = MAIN["initContainers"][0]
    assert first["name"] == "values-check"
    assert {s["secretRef"]["name"] for s in first["envFrom"]} == {"agent-secret", "agent-search-secret"}
    assert all(key in first["command"][2] for key in VALUES)
    assert first["volumeMounts"] == [{"name": "config", "mountPath": "/home/node/config", "readOnly": True},
                                     {"name": "mcp-gateway-config", "mountPath": "/gateway", "readOnly": True}]
    (volume,) = [v for v in MAIN["volumes"] if v["name"] == "mcp-gateway-config"]
    generated = yaml.safe_load((APP / "kustomization.yaml").read_text())["configMapGenerator"]
    (config,) = [g for g in generated if g["name"] == volume["configMap"]["name"]]
    (source,) = config["files"]
    name, path = source.split("=")
    assert name == "config.yaml"
    assert (APP / path).resolve() == GATEWAY_CONFIG


@pytest.mark.parametrize("config", ["git", "other key", "probe key"])
def test_the_values_check_refuses_a_key_whose_hash_is_not_the_one_of_main(config):
    """The key and its hash in the gateway's config stay in step: the hash in
    git (a placeholder, or the hash of the real key, never of the test
    value), the hash of another key, and the right hash on another client
    each stop the start."""
    text = {
        "git": GATEWAY_CONFIG.read_text(),
        "other key": with_hash("main", "another-value"),
        "probe key": with_hash("main-probe", VALUE),
    }[config]
    code, out = run_values(dict.fromkeys(VALUES, VALUE), config=text)
    assert code != 0
    assert out == "values-check result=failed key=MCP_GATEWAY_KEY reason=hash"
    assert hashlib.sha256(VALUE.encode()).hexdigest() not in out


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
