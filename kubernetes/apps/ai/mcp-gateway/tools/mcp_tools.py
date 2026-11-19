"""The tool list of an MCP server behind the MCP gateway, and the tool rule
of the finance client (docs/apps/mcp-gateway.md, "The tool rule").

The gateway has no pin of the tools of a server: it lets a client call each
tool whose name its rule names. So the rule names the kept tools one by one,
and the names come from a dump of the pinned image, kept in git next to this
script. observability/tests/test_mcp_gateway.py fails when the rule, the
dump, the agent's filter and the image differ.

  python3 mcp_tools.py dump URL IMAGE GROUPS > firefly.json
      tools/list of the MCP server at URL (straight to the server, with the
      bearer that the gateway sends), as JSON, with the image and the groups
      it ran with. Run it against the pinned image with no app behind it:
      the list does not need the app.
  python3 mcp_tools.py rule firefly.json
      The names of the kept tools, one a line, as the rule lists them.
  python3 mcp_tools.py write firefly.json CONFIG
      Puts those names into the list of the rule in CONFIG (the gateway's
      config.yaml).

Standard library only.
"""

import fnmatch
import json
import re
import sys
import urllib.request

# The tools that the finance agent keeps: the reads, the two rule tests, and
# the creates and updates of its job. The same lists as the agent's filter
# and deny list (finance/agent/config/openclaw.json), and the test checks
# that both give the same names.
KEEP = [
    "get_*", "search_*", "test_rule", "test_rule_group",
    "create_account", "update_account",
    "create_transaction", "create_split_transaction", "update_transaction",
    "create_category", "update_category",
    "create_budget", "update_budget", "create_budget_limit", "update_budget_limit",
    "create_bill", "update_bill",
    "create_tag", "update_tag",
]
# Never kept, whatever KEEP says: each delete, bulk change, rule trigger,
# export, attachment and rule write.
DROP = ["delete_*", "bulk_*", "trigger_*", "*export*", "*attachment*",
        "create_rule*", "update_rule*"]
# The list of the rule: `mcp.tool.name in [ ... ]`, the only such list in
# the route finance. The route finance-vault has a list of its own, of the
# tools of the vault server, which this script does not write.
RULE_LIST = re.compile(r"(mcp\.tool\.name in \[)(.*?)(\])", re.DOTALL)
FINANCE_ROUTE = re.compile(r"(?ms)^  - name: finance\n.*?(?=^  - name: |\Z)")


def kept(names: list[str]) -> list[str]:
    return sorted(n for n in names
                  if any(fnmatch.fnmatchcase(n, p) for p in KEEP)
                  and not any(fnmatch.fnmatchcase(n, p) for p in DROP))


def tools_list(url: str) -> list[dict]:
    request = urllib.request.Request(
        url, method="POST",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                         "params": {}}).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": "Bearer none"})
    with urllib.request.urlopen(request, timeout=30) as r:
        raw = r.read().decode()
    events = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    tools = json.loads(events[-1] if events else raw)["result"]["tools"]
    return sorted(tools, key=lambda t: t["name"])


def finance_route(config_text: str) -> re.Match:
    route = FINANCE_ROUTE.search(config_text)
    if route is None:
        raise SystemExit("no route finance in the config")
    return route


def rule_names(config_text: str) -> list[str]:
    found = RULE_LIST.findall(finance_route(config_text).group(0))
    if len(found) != 1:
        raise SystemExit(f"want one tool list in the config, found {len(found)}")
    return re.findall(r'"([^"]+)"', found[0][1])


def rule_lines(names: list[str]) -> str:
    return "".join(f'\n                "{n}",' for n in names).rstrip(",") + "\n              "


def main() -> None:
    command = sys.argv[1]
    if command == "dump":
        url, image, groups = sys.argv[2:5]
        json.dump({"image": image, "groups": groups, "tools": tools_list(url)},
                  sys.stdout, indent=1, sort_keys=True)
        sys.stdout.write("\n")
    elif command == "rule":
        with open(sys.argv[2]) as f:
            print("\n".join(kept([t["name"] for t in json.load(f)["tools"]])))
    elif command == "write":
        with open(sys.argv[2]) as f:
            names = kept([t["name"] for t in json.load(f)["tools"]])
        with open(sys.argv[3]) as f:
            text = f.read()
        rule_names(text)
        route = finance_route(text)
        written = RULE_LIST.sub(lambda m: m.group(1) + rule_lines(names) + m.group(3), route.group(0))
        text = text[:route.start()] + written + text[route.end():]
        with open(sys.argv[3], "w") as f:
            f.write(text)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
