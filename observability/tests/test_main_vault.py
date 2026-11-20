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
import re
from pathlib import Path

import yaml

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
