"""The two walls in front of the finance app hold for the pinned MCP server.

The finance agent reaches the finance app (Firefly III) through the MCP
server fireflyiii-mcp 1.0.1 and the proxy of the app, which holds the token
(kubernetes/apps/ai/agents/finance/firefly-mcp, docs/apps/agent-platform.md,
"The finance app"). Two walls stop a delete and the other calls that the
agent must not make:

- the proxy lets through only the calls of its allow list
  (proxy/default.conf.template), so the app never sees a call off the list,
  whatever the server or a sandbox sends;
- the agent sees only the tools of its filter, and its deny list removes
  each delete, bulk, trigger, export, attachment and rule-write tool
  (agent/config/openclaw.json), so the model can not call one.

The tables below are from the release: TOOLS is the tool list of the image
for each group (tools/list of ghcr.io/daften/fireflyiii-mcp:1.0.1, one group
at a time), and CALLS the API calls of the source of each kept group (each
client.get, post, put and delete in src/tools/<group>.ts at the tag v1.0.1).
A new release of the server needs new tables.
"""

import json
import re
import urllib.parse
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/agents/finance/firefly-mcp/app"
TEMPLATE = APP / "proxy/default.conf.template"
MCP = APP / "mcp.yaml"
OPENCLAW = ROOT / "kubernetes/apps/ai/agents/finance/agent/config/openclaw.json"
SANDBOX_POLICY = ROOT / "kubernetes/apps/ai/agents/finance/agent/config/sandbox-policy.yaml"
PROBE = ROOT / "kubernetes/apps/ai/agents/finance/checks/app/firefly-probe.yaml"
SERVER = "firefly"

TOOLS = {
    "accounts": [
        "get_accounts", "get_account", "create_account", "update_account",
        "delete_account", "get_account_transactions", "search_accounts",
    ],
    "transactions": [
        "get_transactions", "get_transaction", "create_transaction",
        "update_transaction", "delete_transaction", "search_transactions",
        "create_split_transaction", "bulk_update_transactions",
    ],
    "budgets": [
        "get_budgets", "get_budget_limits", "create_budget", "update_budget",
        "delete_budget", "create_budget_limit", "update_budget_limit",
        "delete_budget_limit", "get_available_budgets", "get_available_budget",
        "get_budget_transactions", "get_transactions_without_budget",
    ],
    "categories": [
        "get_categories", "get_category_transactions", "create_category",
        "update_category", "delete_category",
    ],
    "bills": [
        "get_bills", "create_bill", "update_bill", "delete_bill",
        "get_bill_transactions",
    ],
    "piggy-banks": [
        "get_piggy_banks", "create_piggy_bank", "update_piggy_bank",
        "delete_piggy_bank", "get_piggy_bank_events",
    ],
    "reports": [
        "get_tags", "get_tag_transactions", "get_summary", "get_insight_expenses",
        "get_insight_income", "create_tag", "update_tag", "delete_tag",
        "get_insight_expenses_no_bill", "get_insight_expenses_no_budget",
        "get_insight_expenses_no_category", "get_insight_expenses_no_tag",
        "get_insight_income_no_category", "get_insight_income_no_tag",
        "get_insight_transfer_no_category", "get_insight_transfer_no_tag", "get_about",
        "get_net_worth_summary", "get_account_overview_chart", "get_balance_chart",
        "get_budget_chart", "get_category_chart", "get_exchange_rate",
        "get_insight_expenses_by_bill", "get_insight_expenses_by_budget",
        "get_insight_expenses_by_tag", "get_insight_expenses_by_asset",
        "get_insight_expenses_by_expense_account", "get_insight_expenses_total",
        "get_insight_income_by_revenue", "get_insight_income_by_tag",
        "get_insight_income_by_asset", "get_insight_income_total",
        "get_insight_transfers_by_category", "get_insight_transfers_by_tag",
        "get_insight_transfers_by_asset", "get_insight_transfers_total",
    ],
    "rules": [
        "get_rule_groups", "get_rule_group", "create_rule_group", "update_rule_group",
        "delete_rule_group", "get_rules", "get_rule", "create_rule", "update_rule",
        "delete_rule", "get_rule_group_rules", "trigger_rule_group", "trigger_rule",
        "test_rule_group", "test_rule",
    ],
    "recurring": [
        "get_recurring", "get_recurrence", "create_recurring", "update_recurring",
        "delete_recurring", "get_recurrence_transactions", "trigger_recurrence",
    ],
    "attachments": [
        "get_attachments", "get_attachment", "create_attachment", "update_attachment",
        "delete_attachment", "upload_attachment", "download_attachment",
    ],
    "currencies": [
        "get_currencies", "get_currency", "create_currency", "update_currency",
        "delete_currency", "enable_currency", "disable_currency",
        "set_primary_currency",
    ],
    "exports": [
        "export_transactions", "export_accounts", "export_bills", "export_budgets",
        "export_categories", "export_tags", "export_recurring", "export_rules",
        "export_piggy_banks",
    ],
    "object-groups": [
        "get_object_groups", "get_object_group", "update_object_group",
        "delete_object_group", "get_object_group_bills", "get_object_group_piggy_banks",
    ],
    "transaction-links": [
        "get_link_types", "get_transaction_links", "get_transaction_link",
        "create_transaction_link", "update_transaction_link", "delete_transaction_link",
    ],
}

# The API calls of the kept groups, as "<method> <path>" under /api/v1, each
# id as {id} and each tag as {tag}.
CALLS = {
    "accounts": [
        "GET /accounts", "GET /accounts/{id}", "POST /accounts", "PUT /accounts/{id}",
        "DELETE /accounts/{id}", "GET /accounts/{id}/transactions", "GET /search/accounts",
    ],
    "transactions": [
        "GET /transactions", "GET /transactions/{id}", "POST /transactions",
        "PUT /transactions/{id}", "DELETE /transactions/{id}", "GET /search/transactions",
    ],
    "categories": [
        "GET /categories", "GET /categories/{id}/transactions", "POST /categories",
        "PUT /categories/{id}", "DELETE /categories/{id}",
    ],
    "budgets": [
        "GET /budgets", "GET /budgets/{id}/limits", "POST /budgets", "PUT /budgets/{id}",
        "DELETE /budgets/{id}", "POST /budgets/{id}/limits", "PUT /budgets/{id}/limits/{id}",
        "DELETE /budgets/{id}/limits/{id}", "GET /available-budgets",
        "GET /available-budgets/{id}", "GET /budgets/{id}/transactions",
        "GET /budgets/transactions-without-budget",
    ],
    "bills": [
        "GET /bills", "POST /bills", "PUT /bills/{id}", "DELETE /bills/{id}",
        "GET /bills/{id}/transactions",
    ],
    "reports": [
        "GET /tags", "GET /tags/{tag}/transactions", "POST /tags", "PUT /tags/{tag}",
        "DELETE /tags/{tag}", "GET /summary/basic", "GET /about",
        "GET /exchange-rates/by-currencies/EUR/USD",
        "GET /chart/account/overview", "GET /chart/balance/balance",
        "GET /chart/budget/overview", "GET /chart/category/overview",
        "GET /insight/expense/asset", "GET /insight/expense/bill", "GET /insight/expense/budget",
        "GET /insight/expense/category", "GET /insight/expense/expense",
        "GET /insight/expense/no-bill", "GET /insight/expense/no-budget",
        "GET /insight/expense/no-category", "GET /insight/expense/no-tag",
        "GET /insight/expense/tag", "GET /insight/expense/total",
        "GET /insight/income/asset", "GET /insight/income/category",
        "GET /insight/income/no-category", "GET /insight/income/no-tag",
        "GET /insight/income/revenue", "GET /insight/income/tag", "GET /insight/income/total",
        "GET /insight/transfer/asset", "GET /insight/transfer/category",
        "GET /insight/transfer/no-category", "GET /insight/transfer/no-tag",
        "GET /insight/transfer/tag", "GET /insight/transfer/total",
    ],
    "rules": [
        "GET /rule-groups", "GET /rule-groups/{id}", "POST /rule-groups", "PUT /rule-groups/{id}",
        "DELETE /rule-groups/{id}", "GET /rules", "GET /rules/{id}", "POST /rules",
        "PUT /rules/{id}", "DELETE /rules/{id}", "POST /rule-groups/{id}/trigger",
        "POST /rules/{id}/trigger", "GET /rule-groups/{id}/test", "GET /rule-groups/{id}/rules",
        "GET /rules/{id}/test",
    ],
}

# The writes that the agent may make: create and update, never delete. No
# rule write: a rule can delete a transaction when a later write triggers it.
WRITE_TOOLS = {
    "create_account", "update_account",
    "create_transaction", "create_split_transaction", "update_transaction",
    "create_category", "update_category",
    "create_budget", "update_budget", "create_budget_limit", "update_budget_limit",
    "create_bill", "update_bill",
    "create_tag", "update_tag",
}

# Calls that the proxy must refuse, beyond the deletes of CALLS: the paths of
# secrets, settings and bulk changes of the app (Firefly III 6.5), and paths
# that leave the allow list after the proxy's own normalization.
REFUSED = [
    "GET /api/v1/preferences", "PUT /api/v1/preferences/x", "GET /api/v1/webhooks",
    "POST /api/v1/webhooks", "GET /api/v1/cron/x", "GET /api/v1/configuration",
    "PUT /api/v1/configuration/x", "GET /api/v1/users", "GET /api/v1/about/user",
    "GET /oauth/personal-access-tokens", "POST /oauth/clients", "GET /",
    "POST /api/v1/data/bulk/transactions", "DELETE /api/v1/data/destroy",
    "POST /api/v1/recurrences/1/trigger", "GET /api/v1/data/export/transactions",
    "GET /api/v1/attachments/1/download", "POST /api/v1/attachments/1/upload",
    "PATCH /api/v1/transactions/1", "HEAD /api/v1/about", "OPTIONS /api/v1/about",
    "GET /api/v1/accounts/../preferences", "GET /api/v1/accounts/%2e%2e/preferences",
    "GET /api/v1/accounts/1/..%2F..%2Fpreferences", "GET //api/v1/about",
    "GET /api/v1/about;x", "GET /api/v1/tags/a%2Fb/transactions",
    "GET /api/v1/accounts/1%00", "GET /api/v1/accounts/1%5c..%5cpreferences",
]


def nginx_maps(text: str) -> dict[str, tuple[str, list[tuple[re.Pattern, str]]]]:
    """The map blocks of the template: {variable: (default, [(regex, value)])}."""
    maps = {}
    for m in re.finditer(r'^map\s+(?:"[^"]*"|\S+)\s+\$(\w+)\s*\{(.*?)^\}', text, re.MULTILINE | re.DOTALL):
        default, entries = "", []
        for line in m.group(2).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("default "):
                default = line.split()[1].rstrip(";")
                continue
            key, value = re.fullmatch(r'"(.*)"\s+(\S+);', line).groups()
            if key.startswith("~*"):
                entries.append((re.compile(key[2:], re.IGNORECASE), value))
            elif key.startswith("~"):
                entries.append((re.compile(key[1:]), value))
            else:
                entries.append((re.compile(re.escape(key) + "$"), value))
        maps[m.group(1)] = (default, entries)
    return maps


MAPS = nginx_maps(TEMPLATE.read_text())


def lookup(name: str, value: str) -> str:
    default, entries = MAPS[name]
    return next((v for rx, v in entries if rx.search(value)), default)


def proxy_allows(call: str, content_type: str | None = None, port: int = 8080) -> bool:
    """The proxy's decision for "<method> <raw path>", as nginx makes it:
    the raw path, the method and the decoded path on the allow list, no
    query on a write (no method override), the content type (JSON for a
    write, the MCP server's own) and the port (8081 reads only). A GET has
    no content type."""
    method, raw = call.split(" ", 1)
    path, _, args = raw.partition("?")
    if content_type is None:
        content_type = "" if method == "GET" else "application/json"
    uri = re.sub(r"/+", "/", urllib.parse.unquote(path))
    flags = (lookup("firefly_raw_ok", raw)
             + lookup("firefly_allowed", f"{method} {uri}")
             # $is_args: "?" when the query is not empty.
             + lookup("firefly_args_ok", method + ("?" if args else ""))
             + lookup("firefly_type_ok", f"{method} {content_type}")
             + lookup("firefly_port_ok", f"{port} {method}"))
    return lookup("firefly_refused", flags) == "0"


def concrete(call: str) -> str:
    method, path = call.split(" ", 1)
    return f"{method} /api/v1" + path.replace("{id}", "12").replace("{tag}", "groceries")


def kept_write(call: str) -> bool:
    """A call that the agent may make: no delete, no trigger, no rule write."""
    method, path = call.split(" ", 1)
    if method == "DELETE" or path.endswith("/trigger"):
        return False
    return not (method in ("POST", "PUT") and path.startswith(("/rules", "/rule-groups")))


@pytest.mark.parametrize("call", [c for calls in CALLS.values() for c in calls])
def test_proxy_lets_through_exactly_the_kept_calls(call):
    assert proxy_allows(concrete(call)) == kept_write(call), call


@pytest.mark.parametrize("call", REFUSED)
def test_proxy_refuses_other_calls(call):
    assert not proxy_allows(call), call


def test_proxy_lets_through_dots_and_slashes_in_the_query_of_a_read():
    assert proxy_allows("GET /api/v1/search/transactions?query=a%2Fb..c")
    assert proxy_allows("PUT /api/v1/tags/my%20tag")


@pytest.mark.parametrize("call", [c for calls in CALLS.values() for c in calls])
def test_the_read_port_lets_through_the_reads_only(call):
    expected = kept_write(call) and call.startswith("GET ")
    assert proxy_allows(concrete(call), port=8081) == expected, call


# The framework of the app (Laravel 12.55 on Symfony http-foundation 7.4)
# routes a POST as the method that a "_method" argument names, in the query
# or the body (Request::getMethod), and PHP decodes the name. No kept write
# of the server sends a query, so a write with any query is refused.
@pytest.mark.parametrize("call", [
    "POST /api/v1/tags?_method=DELETE", "POST /api/v1/tags?_METHOD=DELETE",
    "POST /api/v1/tags?%5Fmethod=DELETE", "POST /api/v1/tags?a=1&_method=PUT",
    "POST /api/v1/tags?_method[]=DELETE", "POST /api/v1/tags?_method",
    "POST /api/v1/tags?_%6dethod=DELETE", "POST /api/v1/tags?%5F%6Dethod=DELETE",
    "POST /api/v1/tags?x=1", "PUT /api/v1/tags/1?_method=DELETE",
    "PUT /api/v1/tags/1?x=1",
])
def test_proxy_refuses_a_query_on_a_write(call):
    assert not proxy_allows(call), call


@pytest.mark.parametrize("content_type", [
    "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/plain",
    "", "application/jsonx", "application/xml",
])
@pytest.mark.parametrize("call", ["POST /api/v1/tags", "PUT /api/v1/tags/1"])
def test_proxy_lets_through_a_write_with_a_json_body_only(call, content_type):
    assert not proxy_allows(call, content_type), (call, content_type)
    assert proxy_allows(call, "application/json")
    assert proxy_allows(call, "application/json; charset=utf-8")
    assert proxy_allows(call, "Application/JSON")


def test_proxy_drops_the_method_override_headers():
    text = TEMPLATE.read_text()
    for header in ("X-HTTP-Method-Override", "X-HTTP-Method", "X-Method-Override"):
        assert f'proxy_set_header {header} "";' in text, header


def mcp_groups() -> list[str]:
    for doc in yaml.safe_load_all(MCP.read_text()):
        if doc and doc["kind"] == "Deployment":
            env = {e["name"]: e.get("value") for e in doc["spec"]["template"]["spec"]["containers"][0]["env"]}
            assert "FIREFLY_TOKEN" not in env, "the MCP server holds no token"
            return env["MCP_GROUPS"].split(",")
    raise AssertionError("no Deployment in mcp.yaml")


def mcp_filter_matches(pattern: str, value: str) -> bool:
    """OpenClaw 2026.11.3, src/agents/mcp-tool-filter.ts: exact text plus "*"."""
    return re.fullmatch(".*".join(map(re.escape, pattern.split("*"))), value) is not None


def openclaw() -> dict:
    return json.loads(OPENCLAW.read_text())


def agent_sees(tools: list[str], *, include: bool = True, deny: bool = True) -> list[str]:
    """The tools of the server that the agent can call: the MCP tool filter of
    the server entry, then the deny list of tools.deny on the name
    "<server>__<tool>" (src/agents/glob-pattern.ts: "*" is any text)."""
    config = openclaw()
    tool_filter = config["mcp"]["servers"][SERVER].get("toolFilter", {})
    denied = config["tools"]["deny"]
    seen = []
    for tool in tools:
        if include and tool_filter.get("include") and not any(
                mcp_filter_matches(p, tool) for p in tool_filter["include"]):
            continue
        if include and any(mcp_filter_matches(p, tool) for p in tool_filter.get("exclude", [])):
            continue
        if deny and any(mcp_filter_matches(p, f"{SERVER}__{tool}") for p in denied):
            continue
        seen.append(tool)
    return seen


def read(tool: str) -> bool:
    return tool.startswith(("get_", "search_", "test_rule"))


def forbidden(tool: str) -> bool:
    return (tool.startswith(("delete_", "bulk_", "trigger_", "create_rule", "update_rule"))
            or "export" in tool or "attachment" in tool)


def test_the_server_loads_only_the_kept_groups():
    assert sorted(mcp_groups()) == sorted(CALLS)


def test_the_agent_sees_reads_and_the_kept_writes_only():
    exposed = [t for g in mcp_groups() for t in TOOLS[g]]
    seen = agent_sees(exposed)
    assert {t for t in seen if not read(t)} == WRITE_TOOLS
    assert not [t for t in seen if forbidden(t)]


def test_the_filter_alone_lets_through_no_other_write():
    """Even with every group of the server loaded."""
    every = [t for tools in TOOLS.values() for t in tools]
    seen = agent_sees(every, deny=False)
    assert {t for t in seen if not read(t)} == WRITE_TOOLS


def test_the_deny_list_alone_hides_every_forbidden_tool():
    """Even with every group loaded and no filter: the attachment and export
    reads too."""
    every = [t for tools in TOOLS.values() for t in tools]
    assert not [t for t in agent_sees(every, include=False) if forbidden(t)]


def test_sandboxed_sessions_get_the_tools():
    assert f"{SERVER}__*" in openclaw()["tools"]["sandbox"]["tools"]["alsoAllow"]


def test_the_pieces_name_the_same_addresses():
    """The MCP entry of the agent names the MCP gateway (test_mcp_gateway.py)."""
    config = openclaw()
    policy = yaml.safe_load(SANDBOX_POLICY.read_text())
    (rule,) = policy["network_policies"].values()
    (endpoint,) = rule["endpoints"]
    # The read port.
    assert (endpoint["host"], endpoint["port"]) == ("firefly-proxy.agents-finance.svc.cluster.local", 8081)
    # Reads only: the scripts of the agent only read.
    assert {r["allow"]["method"] for r in endpoint["rules"]} == {"GET"}
    assert config["plugins"]["entries"]["openshell"]["config"]["policy"] == \
        "/home/node/config/sandbox-policy.yaml"
    cronjob = next(yaml.safe_load_all(PROBE.read_text()))
    container = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert "MCP_URL" not in env
    assert env["APP_PROXY"] == f"http://{endpoint['host']}:{endpoint['port']}"
    (mcp,) = [d for d in docs(APP / "mcp.yaml") if d["kind"] == "Deployment"]
    server_env = {e["name"]: e.get("value")
                  for e in mcp["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["APP_PROXY_WRITE"] == server_env["FIREFLY_URL"]
    # The probe's sandbox runs the image of the agent's sandboxes.
    assert env["PROBE_IMAGE"] == config["plugins"]["entries"]["openshell"]["config"]["from"]


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


NAMESPACE = ROOT / "kubernetes/apps/ai/agents/finance/namespace.yaml"
POLICY = APP / "networkpolicy.yaml"


def dns_names(policy: dict) -> list[str]:
    return [r.get("matchName") or r.get("matchPattern")
            for rule in policy["spec"].get("egress", [])
            for port in rule.get("toPorts", [])
            for r in port.get("rules", {}).get("dns", [])]


def test_the_app_pods_resolve_one_name_each():
    """DNS is a way out too. Cilium adds up the allows of the policies that
    select a pod, so the pods must be out of the namespace's rule for any
    name, and each has its own rule for one name."""
    (wide,) = [d for d in docs(NAMESPACE) if d["metadata"]["name"] == "allow-dns"]
    assert {"key": "agents.home.arpa/dns", "operator": "DoesNotExist"} in \
        wide["spec"]["endpointSelector"]["matchExpressions"]
    names = {d["spec"]["endpointSelector"]["matchLabels"]["app.kubernetes.io/name"]: dns_names(d)
             for d in docs(POLICY)}
    assert names == {"firefly-proxy": ["firefly.production.svc.cluster.local"],
                     "firefly-mcp": ["firefly-proxy.agents-finance.svc.cluster.local"]}
    for path in (APP / "mcp.yaml", APP / "proxy.yaml", PROBE):
        for doc in docs(path):
            spec = (doc["spec"]["template"]["spec"] if doc["kind"] == "Deployment" else
                    doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
                    if doc["kind"] == "CronJob" else None)
            if spec is None or doc["metadata"]["name"] == "platform-check-firefly-probe":
                continue
            labels = (doc["spec"]["template"]["metadata"]["labels"] if doc["kind"] == "Deployment"
                      else doc["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"])
            assert labels["agents.home.arpa/dns"] == "own-rule", doc["metadata"]["name"]
            assert {"name": "ndots", "value": "1"} in spec["dnsConfig"]["options"]


def test_each_client_reaches_its_own_port():
    (proxy,) = [d for d in docs(POLICY) if d["metadata"]["name"] == "firefly-proxy"]
    ports = {}
    for rule in proxy["spec"]["ingress"]:
        (peer,) = rule["fromEndpoints"]
        labels = peer["matchLabels"]
        who = labels.get("app.kubernetes.io/component") or labels.get("app.kubernetes.io/name") \
            or labels.get("openshell.ai/boundary-role")
        ports[who] = sorted(p["port"] for tp in rule["toPorts"] for p in tp["ports"])
    assert ports == {"firefly-mcp": ["8080"], "supervisor": ["8081"],
                     "firefly-probe": ["8080", "8081"]}


def test_the_probe_policy_adds_the_write_port_rule(monkeypatch, tmp_path):
    """The probe's sandbox gets the agent's policy with each method on the read
    port, and one rule for the write port, which the network policy must stop
    at the supervisor."""
    import importlib.util
    monkeypatch.setenv("OWN_NAMESPACE", "agents-finance")
    monkeypatch.setenv("TRUST_DOMAIN_NAMESPACES", "agents-finance")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SANDBOX_POLICY", str(SANDBOX_POLICY))
    monkeypatch.setenv("APP_PROXY_WRITE", "http://firefly-proxy.agents-finance.svc.cluster.local:8080")
    script = ROOT / "kubernetes/apps/ai/agents/checks/app/scripts/platform_checks.py"
    spec = importlib.util.spec_from_file_location("platform_checks_firefly", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = yaml.safe_load(Path(module.probe_policy()).read_text())
    agent = yaml.safe_load(SANDBOX_POLICY.read_text())
    assert {k: v for k, v in policy.items() if k != "network_policies"} == \
        {k: v for k, v in agent.items() if k != "network_policies"}
    probe_app, agent_app = policy["network_policies"]["finance_app"], agent["network_policies"]["finance_app"]
    # Each method on the read port, so the proxy must refuse the writes.
    assert agent_app["endpoints"][0]["rules"] == [{"allow": {"method": "GET", "path": "/api/v1/**"}}]
    assert probe_app["endpoints"][0]["rules"] == [{"allow": {"method": "*", "path": "/api/v1/**"}}]
    probe_app["endpoints"][0]["rules"] = agent_app["endpoints"][0]["rules"]
    assert probe_app == agent_app
    (endpoint,) = policy["network_policies"]["write_port_probe"]["endpoints"]
    assert (endpoint["host"], endpoint["port"]) == ("firefly-proxy.agents-finance.svc.cluster.local", 8080)


def test_no_sandbox_can_carry_the_labels_of_a_client_of_the_write_port():
    """A sandbox's pod gets a caller's label only when the gateway's
    allowed_pod_labels names it (OpenShell 0.1.8, driver.rs
    validate_pod_metadata; the default is empty). The finance gateway sets
    none, so no sandbox can carry the labels of the MCP pod or the probe."""
    release = ROOT / "kubernetes/apps/ai/agents/finance/sandbox-gateway/app/helmrelease.yaml"
    assert "allowed_pod_labels" not in release.read_text()
