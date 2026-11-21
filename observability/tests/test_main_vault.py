"""The share of the main trust domain (task 3.37; docs/apps/agent-platform.md,
"The vault shares"), in the form of the share of finance: the vault server
of main is the vault server of finance in agents-main, and its route on the
central MCP gateway is the route finance-vault with the clients of main.
Each trust domain reaches its own vault only (R6).

test_vault_server.py holds the server and the route of finance; these tests
hold that main is the same form, so a change of one that is not made in the
other fails here.
"""

import copy
import hashlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_hand_written_rules import docker, scratch

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "kubernetes/apps/ai/agents"
MAIN = AGENTS / "main"
FINANCE = AGENTS / "finance"
CONFIG = ROOT / "kubernetes/apps/ai/mcp-gateway/app/config/config.yaml"
GATEWAY_POLICY = ROOT / "kubernetes/apps/ai/mcp-gateway/app/networkpolicy.yaml"
HOST = "http://vault-server.agents-main.svc.cluster.local:8080/mcp"
PLACEHOLDER = re.compile(r"\Asha256:0{63}[0-9]\Z")
HASH = re.compile(r"\Asha256:[0-9a-f]{64}\Z")


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def routes() -> dict[str, dict]:
    return {r["name"]: r for r in yaml.safe_load(CONFIG.read_text())["routes"]}


def as_finance(node):
    """A copy of a document of main with each name of main put as finance."""
    text = yaml.safe_dump(node)
    return yaml.safe_load(text.replace("agents-main", "agents-finance").replace("main-vault", "finance-vault"))


# The vault server


def test_the_server_of_main_is_the_server_of_finance_in_its_namespace():
    for name in ("app/deployment.yaml", "app/networkpolicy.yaml", "app/kustomization.yaml"):
        main = docs(MAIN / "vault-server" / name)
        finance = docs(FINANCE / "vault-server" / name)
        assert [as_finance(d) for d in main] == finance, name


def test_the_server_comes_after_the_share_of_main():
    (main,) = docs(MAIN / "vault-server/ks.yaml")
    (finance,) = docs(FINANCE / "vault-server/ks.yaml")
    assert main["metadata"]["name"] == "agents-main-vault-server"
    assert main["spec"]["targetNamespace"] == "agents-main"
    assert main["spec"]["path"] == "./kubernetes/apps/ai/agents/main/vault-server/app"
    assert main["spec"]["dependsOn"] == [{"name": "agents-main-vault-share"}]
    other = copy.deepcopy(main)
    other["metadata"]["name"] = finance["metadata"]["name"]
    other["spec"]["targetNamespace"] = "agents-finance"
    other["spec"]["path"] = finance["spec"]["path"]
    other["spec"]["dependsOn"] = finance["spec"]["dependsOn"]
    assert other == finance
    resources = yaml.safe_load((MAIN / "kustomization.yaml").read_text())["resources"]
    assert resources.index("./vault-server/ks.yaml") > resources.index("./vault-share/ks.yaml")


def test_the_server_is_the_one_pod_of_main_on_the_claim_to_write():
    users = []
    for path in MAIN.rglob("*.yaml"):
        if path.name.endswith(".sops.yaml"):
            continue
        for d in docs(path):
            if d.get("kind") in {"Deployment", "StatefulSet", "Job", "CronJob", "Pod"}:
                text = yaml.safe_dump(d)
                if "claimName: vault-rw" in text:
                    users.append(str(path.relative_to(MAIN)))
    assert users == ["vault-server/app/deployment.yaml"]


def test_the_server_gets_no_dns_rule_of_the_namespace_of_main():
    (allow_dns,) = [d for d in docs(MAIN / "namespace.yaml") if d["metadata"]["name"] == "allow-dns"]
    assert {"key": "agents.home.arpa/dns", "operator": "DoesNotExist"} in \
        allow_dns["spec"]["endpointSelector"]["matchExpressions"]
    (deployment,) = docs(MAIN / "vault-server/app/deployment.yaml")[:1]
    assert deployment["spec"]["template"]["metadata"]["labels"]["agents.home.arpa/dns"] == "none"


def test_no_other_pod_of_main_has_the_dns_label():
    """The label takes a pod out of the DNS of the namespace: only the vault
    server, which calls nothing, may carry it."""
    holders = []
    for path in MAIN.rglob("*.yaml"):
        if path.name.endswith(".sops.yaml"):
            continue
        for d in docs(path):
            spec = d.get("spec") or {}
            template = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get("template") or {}
            if "agents.home.arpa/dns" in template.get("metadata", {}).get("labels", {}):
                holders.append(str(path.relative_to(MAIN)))
    assert holders == ["vault-server/app/deployment.yaml"]


# The route of the gateway


def test_the_route_of_main_is_the_route_of_finance_with_the_clients_of_main():
    r = routes()
    main, finance = r["main-vault"], r["finance-vault"]
    assert main["matches"] == [{"path": {"pathPrefix": "/main-vault/"}}]
    (backend,) = main["backends"]
    (target,) = backend["mcp"]["targets"]
    assert target["mcp"]["host"] == HOST
    (rule,) = main["policies"]["mcpAuthorization"]["rules"]
    (finance_rule,) = finance["policies"]["mcpAuthorization"]["rules"]
    tools = rule["allow"].split("]")[0]
    assert tools == finance_rule["allow"].split("]")[0]
    clients = set(re.findall(r'apiKey\.client == "([^"]+)"', rule["allow"]))
    assert clients == {k["metadata"]["client"] for k in main["policies"]["apiKey"]["keys"]}
    for key in ("apiKey", "mcpAuthorization", "transformations", "timeout"):
        assert key in main["policies"]
    same = copy.deepcopy(main)
    del same["policies"]["apiKey"]["keys"]
    del same["policies"]["mcpAuthorization"]
    other = copy.deepcopy(finance)
    del other["policies"]["apiKey"]["keys"]
    del other["policies"]["mcpAuthorization"]
    assert as_finance(same) == other


def test_the_route_of_main_takes_the_key_of_the_main_agent():
    keys = routes()["main-vault"]["policies"]["apiKey"]["keys"]
    assert keys[0]["metadata"] == {"client": "main", "route": "main-vault"}
    for k in keys:
        assert PLACEHOLDER.match(k["keyHash"]) or HASH.match(k["keyHash"])
        assert k["metadata"]["route"] == "main-vault"
        assert k["metadata"]["client"] in {"main", "main-probe"}


def test_no_key_of_one_trust_domain_is_on_the_vault_route_of_the_other():
    """A key of finance gets a 401 on the route of main, and the reverse
    (Design Doc, section 4.2.5, "One trust domain can not reach the other"):
    no hash and no client is on both routes."""
    r = routes()

    def keys(*names):
        return {(k["keyHash"], k["metadata"]["client"])
                for n in names for k in r[n]["policies"]["apiKey"]["keys"]}

    main = keys("main-vault")
    finance = keys("finance", "finance-vault")
    assert not {h for h, _ in main} & {h for h, _ in finance}
    assert not {c for _, c in main} & {c for _, c in finance}
    assert all(c == "main" or c.startswith("main-") for _, c in main)


def test_the_gateway_reaches_the_server_of_main_by_its_name():
    (policy,) = [d for d in docs(GATEWAY_POLICY) if d["kind"] == "CiliumNetworkPolicy"]
    out = [(p["matchLabels"]["io.kubernetes.pod.namespace"], p["matchLabels"].get("app.kubernetes.io/name"),
            [x["port"] for tp in rule["toPorts"] for x in tp["ports"]])
           for rule in policy["spec"]["egress"] for p in rule["toEndpoints"]]
    assert ("agents-main", "vault-server", ["8080"]) in out
    names = [d["matchName"] for rule in policy["spec"]["egress"] for tp in rule["toPorts"]
             for d in tp.get("rules", {}).get("dns", [])]
    assert "vault-server.agents-main.svc.cluster.local" in names


# The gateway probe of main (step B)


PROBE = MAIN / "checks/app/mcp-gateway-probe.yaml"
FINANCE_PROBE = FINANCE / "checks/app/mcp-gateway-probe.yaml"
CHECKS = ROOT / "kubernetes/apps/ai/agents/checks/app/scripts/platform_checks.py"


def probe_container(path: Path) -> dict:
    (cron,) = [d for d in docs(path) if d["kind"] == "CronJob"]
    (container,) = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    return container


def probe_env(path: Path) -> dict:
    return {e["name"]: e.get("value") for e in probe_container(path)["env"]}


def egress(path: Path) -> list[tuple]:
    (policy,) = [d for d in docs(path) if d["kind"] == "CiliumNetworkPolicy"]
    return [(p["matchLabels"]["io.kubernetes.pod.namespace"], p["matchLabels"]["app.kubernetes.io/name"],
             [x["port"] for tp in r["toPorts"] for x in tp["ports"]])
            for r in policy["spec"]["egress"] for p in r["toEndpoints"]]


def test_the_checks_of_main_are_the_shared_checks_and_the_probe():
    (ks,) = docs(MAIN / "checks/ks.yaml")
    assert ks["spec"]["path"] == "./kubernetes/apps/ai/agents/main/checks/app"
    app = yaml.safe_load((MAIN / "checks/app/kustomization.yaml").read_text())
    # The key of the probe (PROBE_KEY) is a SOPS Secret of these checks.
    assert app["resources"] == ["../../../checks/app", "./mcp-gateway-probe.yaml",
                                "./secret-mcp-gateway-probe.sops.yaml"]
    (secret,) = docs(MAIN / "checks/app/secret-mcp-gateway-probe.sops.yaml")
    assert secret["metadata"] == {"name": "mcp-gateway-probe-secret", "namespace": "agents-main"}
    assert set(secret["stringData"]) == {"PROBE_KEY"}
    assert secret["stringData"]["PROBE_KEY"].startswith("ENC[")
    (gen,) = app["configMapGenerator"]
    assert gen["files"] == ["config.yaml=../../../../mcp-gateway/app/config/config.yaml"]


def test_the_probe_of_main_is_the_probe_of_finance_for_the_vault_route_only():
    main, finance = probe_container(PROBE), probe_container(FINANCE_PROBE)
    for key in ("image", "command", "securityContext", "resources", "volumeMounts"):
        assert main[key] == finance[key], key
    env = probe_env(PROBE)
    assert "MCP_GATEWAY_URL" not in env and "MCP_SERVER" not in env
    assert env["VAULT_GATEWAY_URL"] == "http://mcp-gateway.ai.svc.cluster.local:3000/main-vault/mcp"
    assert "http://" + env["VAULT_SERVER"] + "/mcp" == HOST
    assert env["VAULT_ROUTE"] == "main-vault"
    assert env["TRUST_DOMAIN_NAMESPACES"] == "agents-main"
    # The Secret stays optional: with no Secret the probe runs with no key
    # and says "skipped" for the checks that need one.
    assert main["envFrom"] == [{"secretRef": {"name": "mcp-gateway-probe-secret", "optional": True}}]


def test_each_probe_tries_the_vault_of_the_other_trust_domain():
    main, finance = probe_env(PROBE), probe_env(FINANCE_PROBE)
    assert main["OTHER_VAULT_GATEWAY_URL"] == finance["VAULT_GATEWAY_URL"]
    assert main["OTHER_VAULT_SERVER"] == finance["VAULT_SERVER"]
    assert finance["OTHER_VAULT_GATEWAY_URL"] == main["VAULT_GATEWAY_URL"]
    assert finance["OTHER_VAULT_SERVER"] == main["VAULT_SERVER"]
    for path in (PROBE, FINANCE_PROBE):
        out = egress(path)
        assert ("agents-main", "vault-server", ["8080"]) in out
        assert ("agents-finance", "vault-server", ["8080"]) in out


def test_the_probe_key_of_main_may_list_and_read_the_facts_of_a_path_only():
    r = routes()["main-vault"]
    keys = [k["metadata"] for k in r["policies"]["apiKey"]["keys"]]
    assert keys == [{"client": "main", "route": "main-vault"}, {"client": "main-probe", "route": "main-vault"}]
    (rule,) = r["policies"]["mcpAuthorization"]["rules"]
    assert '(apiKey.client == "main-probe" &&\n' in rule["allow"]
    assert '(mcp.methodName == "tools/list" || mcp.tool.name == "get_file_info")' in rule["allow"]


def test_the_gateway_takes_the_probe_of_main_and_its_agent():
    (policy,) = [d for d in docs(GATEWAY_POLICY) if d["kind"] == "CiliumNetworkPolicy"]
    into = [(p["matchLabels"]["io.kubernetes.pod.namespace"],
             p["matchLabels"].get("app.kubernetes.io/component") or p["matchLabels"].get("app.kubernetes.io/name"))
            for rule in policy["spec"]["ingress"] for p in rule.get("fromEndpoints", [])]
    assert ("agents-main", "mcp-gateway-probe") in into
    assert ("agents-main", "agent") in into


# The container test: the gateway of the cluster with this config, the vault
# server of main on an empty share, and the probe of main on another network.


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    if check:
        assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return result


def image(path: Path, kind: str = "Deployment") -> str:
    (d,) = [x for x in docs(path) if x["kind"] == kind]
    return d["spec"]["template"]["spec"]["containers"][0]["image"]


@pytest.fixture(scope="module")
def vault_lab():
    tool = docker()
    work = scratch()
    (work / "vault").mkdir()
    (work / "vault").chmod(0o777)
    (work / "gateway").mkdir()
    key = f"test-main-probe-{uuid.uuid4().hex}"
    text = CONFIG.read_text()
    (old,) = [k["keyHash"] for k in routes()["main-vault"]["policies"]["apiKey"]["keys"]
              if k["metadata"]["client"] == "main-probe"]
    assert text.count(old) == 1
    (work / "gateway/config.yaml").write_text(
        text.replace(old, "sha256:" + hashlib.sha256(key.encode()).hexdigest()))
    for path in (work / "gateway", work / "gateway/config.yaml"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    tag = uuid.uuid4().hex[:8]
    front, back = f"mainvault-front-{tag}", f"mainvault-back-{tag}"
    names = {n: f"mainvault-{n}-{tag}" for n in ("server", "gateway")}
    python = probe_container(PROBE)["image"]
    try:
        for net in (front, back):
            run([tool, "network", "create", "--internal", net])
        run([tool, "run", "-d", "--name", names["server"], "--network", back,
             "--network-alias", "vault-server.agents-main.svc.cluster.local",
             "--read-only", "--cap-drop", "ALL", "--user", "1000:1000",
             "-v", f"{work / 'vault'}:/vault", image(MAIN / "vault-server/app/deployment.yaml")])
        gateway = image(ROOT / "kubernetes/apps/ai/mcp-gateway/app/deployment.yaml")
        run([tool, "create", "--name", names["gateway"], "--network", back,
             "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--user", "65532",
             "-v", f"{work / 'gateway'}:/config:ro", gateway, "-f", "/config/config.yaml"])
        run([tool, "network", "connect", "--alias", "mcp-gateway.ai.svc.cluster.local",
             front, names["gateway"]])
        run([tool, "start", names["gateway"]])
        wait = ("import urllib.request,urllib.error,time\n"
                "for i in range(90):\n"
                "    try:\n"
                "        urllib.request.urlopen(urllib.request.Request("
                "'http://mcp-gateway.ai.svc.cluster.local:3000/main-vault/mcp', method='POST', data=b'{}'), timeout=5)\n"
                "    except urllib.error.HTTPError as e:\n"
                "        if e.code == 401: break\n"
                "    except Exception:\n"
                "        pass\n"
                "    time.sleep(1)\n"
                "else:\n"
                "    raise SystemExit('gateway not ready')\n")
        run([tool, "run", "--rm", "--network", front, python, "python3", "-I", "-c", wait])
        time.sleep(2)
        yield {"tool": tool, "work": work, "key": key, "front": front, "python": python}
    finally:
        for name in names.values():
            subprocess.run([tool, "container", "remove", "-f", name], capture_output=True, check=False)
        for net in (front, back):
            subprocess.run([tool, "network", "remove", net], capture_output=True, check=False)


def run_probe(lab: dict, key: str | None) -> tuple[int, dict[str, str]]:
    env = {k: v for k, v in probe_env(PROBE).items() if v is not None}
    env["OWN_NAMESPACE"] = "agents-main"
    if key is not None:
        env["PROBE_KEY"] = key
    flags = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
    result = run([lab["tool"], "run", "--rm", "--network", lab["front"], "--read-only", "--cap-drop", "ALL",
                  "--user", "65534", "-v", f"{CHECKS.parent}:/scripts:ro",
                  "-v", f"{lab['work'] / 'gateway'}:/gateway:ro", *flags, lab["python"],
                  "python3", "/scripts/platform_checks.py", "mcp-gateway"], check=False)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert all(line["ok"] for line in lines), result.stdout + result.stderr
    return result.returncode, {line["target"]: line["got"] for line in lines}


def test_the_probe_of_main_passes_with_its_key(vault_lab):
    code, got = run_probe(vault_lab, vault_lab["key"])
    assert code == 0
    assert got["vault server, direct from a pod that is not the gateway"].startswith("blocked")
    assert got["vault: no key: tools/list"] == "refused: HTTP 401"
    assert got["vault: a key that is not in the config: tools/list"] == "refused: HTTP 401"
    assert got["vault: probe key: lists the 9 tools of the rule"] == "same"
    assert got["vault: probe key: the facts of the root (get_file_info /vault)"] == "reached: ok"
    assert got["vault: probe key: a write (write_file), not the probe's"] == "refused: gateway"
    assert got["vault: probe key: a move (move_file), off every rule"] == "refused: gateway"
    assert got["other vault server, direct from this trust domain"].startswith("blocked")
    assert got["other vault: probe key: tools/list"] == "refused: HTTP 401"
    # No route of a domain, and no key with no tool: main has neither.
    assert not [t for t in got if t.startswith(("probe key", "no key", "key with no tool", "MCP server"))]
    assert not [t for t in got if "vault: key with no tool" in t]
    assert list((vault_lab["work"] / "vault").iterdir()) == []


def test_the_probe_of_main_skips_and_passes_with_no_key(vault_lab):
    code, got = run_probe(vault_lab, None)
    assert code == 0
    assert got["vault: probe key"] == got["other vault: probe key"] == "skipped: the key is pending"
    assert got["vault: no key: tools/list"] == "refused: HTTP 401"


# The main agent and its share (step D)


AGENT = MAIN / "agent/app/deployment.yaml"
OPENCLAW = MAIN / "agent/config/openclaw.json"


def agent_pod() -> dict:
    (d,) = [x for x in docs(AGENT) if x["kind"] == "Deployment"]
    return d["spec"]["template"]["spec"]


def test_the_agent_mounts_the_share_read_only_at_its_root_where_finance_does():
    """`ro` on the volume is not enough: the kubelet takes the flag from the
    pod, so the claim and each mount say readOnly. No subPath on the share."""
    main = agent_pod()
    (fin,) = [x for x in docs(FINANCE / "agent/app/deployment.yaml") if x["kind"] == "Deployment"]
    finance = fin["spec"]["template"]["spec"]
    (volume,) = [v for v in main["volumes"] if v["name"] == "vault"]
    assert volume == {"name": "vault", "persistentVolumeClaim": {"claimName": "vault-ro", "readOnly": True}}

    def users(spec):
        return {c["name"]: [m for m in c.get("volumeMounts", []) if m["name"] == "vault"]
                for c in spec.get("initContainers", []) + spec["containers"]}

    mains, fins = users(main), users(finance)
    assert {n for n, m in mains.items() if m} == {n for n, m in fins.items() if m}
    for name, mounts in mains.items():
        for m in mounts:
            assert m == {"name": "vault", "mountPath": "/vault", "readOnly": True}, name
    # The share of finance never: R6.
    assert "agents-finance" not in AGENT.read_text()


def test_the_index_of_the_agent_polls_and_reads_the_share():
    c = json.loads(OPENCLAW.read_text())
    paths = [p["path"] for p in c["memory"]["search"]["extraPaths"]]
    assert paths == ["/vault"]
    # The gateway watches the share for the index; a change from the vault
    # server gives no file event on NFS, so its watch polls.
    (agent,) = [c for c in agent_pod()["containers"] if c["name"] == "agent"]
    env = {e["name"]: e.get("value") for e in agent["env"]}
    assert env["CHOKIDAR_USEPOLLING"] == "1"


# The tools that write the share. Until the seed of the share (step F, at the
# cutover) the agent lists the six read tools only: the seed stops at a share
# that is not empty (share-not-empty), so a note of the agent before it would
# stop the seed. The step of the seed adds these three again.
WRITES = {"write_file", "edit_file", "create_directory"}


def test_the_agent_calls_the_vault_route_of_main_with_its_key_and_the_read_tools():
    c = json.loads(OPENCLAW.read_text())
    entry = c["mcp"]["servers"]["vault"]
    assert entry["url"] == "http://mcp-gateway.ai.svc.cluster.local:3000/main-vault/mcp"
    assert entry["headers"] == {"Authorization": "Bearer ${MCP_GATEWAY_KEY}"}
    (rule,) = routes()["main-vault"]["policies"]["mcpAuthorization"]["rules"]
    nine = re.findall(r'"([a-z_]+)"', rule["allow"].split("]")[0])
    assert WRITES <= set(nine)
    assert sorted(entry["toolFilter"]["include"]) == sorted(set(nine) - WRITES)
    assert "vault__*" in c["tools"]["sandbox"]["tools"]["alsoAllow"]


def test_the_values_check_of_main_finds_one_hash_the_one_of_the_client_main():
    pod = agent_pod()
    (check,) = [c for c in pod["initContainers"] if c["name"] == "values-check"]
    (program,) = re.findall(r"awk '([^']+)' /gateway/config.yaml", check["command"][2])
    found = subprocess.run(["awk", program, str(CONFIG)], capture_output=True, text=True,
                           check=True).stdout.split()
    (agent,) = [k for k in routes()["main-vault"]["policies"]["apiKey"]["keys"]
                if k["metadata"]["client"] == "main"]
    assert found == [agent["keyHash"]]


def test_a_real_probe_key_of_main_has_its_secret_listed():
    """The hash of main-probe and the Secret of its key go in one commit: a
    real hash with no Secret would fail each run of the probe with a 401,
    and a Secret with the placeholder would be a key that no route takes."""
    (probe,) = [k["keyHash"] for k in routes()["main-vault"]["policies"]["apiKey"]["keys"]
                if k["metadata"]["client"] == "main-probe"]
    resources = yaml.safe_load((MAIN / "checks/app/kustomization.yaml").read_text())["resources"]
    listed = "./secret-mcp-gateway-probe.sops.yaml" in resources
    assert HASH.match(probe)
    assert listed is not bool(PLACEHOLDER.match(probe)), "the hash of main-probe and its Secret go together"
    if listed:
        (secret,) = docs(MAIN / "checks/app/secret-mcp-gateway-probe.sops.yaml")
        assert secret["metadata"]["name"] == "mcp-gateway-probe-secret"
        assert set(secret["stringData"]) == {"PROBE_KEY"}
