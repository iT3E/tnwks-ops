# The browser tool

The Playwright MCP server of the vendor of the browser engine: an MCP
server over Streamable HTTP that drives a headless Chromium. It is the
browser of the main agent, through the route `main-browser` of the central
MCP gateway ([mcp-gateway.md](mcp-gateway.md)), with the rails of the
Design Doc of effort 0003, section 4.2.6 ("The browser, its rails").

**Deployed, not live for use.** The route, the flags, the proxy and the
policies are in, and the probe and the tests can prove them. The config of
the main agent names no server at the route yet, so the agent has no
browser tool. [The switch](#the-switch) turns it on, after the rows that a
probe can prove pass and the owner's test questions.

Manifests: `kubernetes/apps/ai/playwright-mcp/`, image
`mcr.microsoft.com/playwright/mcp` `v0.0.85`, pinned by digest, in
`app/helmrelease.yaml`. It runs at
homelab-wsl only, with one replica, beside the central MCP gateway and the
fetch proxy of the main agent. It does not move between the sites
([multi-site.md](../wsl/multi-site.md#apps)): at homelab-wsl-02 it would
need a global Service of the fetch proxy, so the egress of the main trust
domain would be reachable from the other cluster.
Upstream: [GitHub](https://github.com/microsoft/playwright-mcp)
(Apache-2.0).

## Pieces

| Piece | Where |
| --- | --- |
| App | Deployment `playwright-mcp`, port 8000 (`/mcp`, and the legacy `/sse`) |
| Service | `playwright-mcp`, local to homelab-wsl (not a global Service of the mesh) |
| Ingress | None: an agent reaches it through the gateway only |
| Gateway route | `main-browser`, the key of main only (`kubernetes/apps/ai/mcp-gateway/app/config/config.yaml`) |
| Tool dump | `kubernetes/apps/ai/mcp-gateway/tools/browser.json`, the tools of the release of the pinned image (its npm package and README); the daily probe checks the 21 names against the image |
| Network policy | CiliumNetworkPolicy `playwright-mcp` (`app/networkpolicy.yaml`) |
| Data | None: `--isolated` gives each session a fresh browser profile in memory, and `/tmp` and the home directory are `emptyDir` |

## Network policy

- **In:** port 8000 from the central MCP gateway, and from the host (the
  kubelet's TCP probes). No other pod, and no ingress.
- **Out:** the fetch proxy of the main agent on port 8080, and DNS for the
  name of the fetch proxy only. No other pod, no service of the cluster,
  no address of the house and no web site directly.
- **The fetch proxy** lets the browser in beside the agent pod of main
  (`agents/main/fetch-proxy/app/networkpolicy.yaml`). It takes a CONNECT to
  port 443 of a public address only, so the browser loads HTTPS pages of
  public sites only, and the log of the proxy has the host of each page.

The flags of origins of the server are not a security boundary (its own
docs say so), so the network policy is the wall.

The app, the gateway and the fetch proxy run at homelab-wsl only. Both
WSL clusters are one Cilium Cluster Mesh, so each peer names this cluster
(`${CLUSTER_NAME}`), as in
[agent-platform.md](agent-platform.md#network-policy-and-the-cluster-mesh).

## The flags

Each flag is in the README of the pinned release.

| Flag | Why |
| --- | --- |
| `--isolated` | A browser context of its own, in memory, for each MCP session: two sessions do not see the cookies or the pages of each other |
| `--headless` | No display |
| `--no-webmcp` | A page can not add a tool |
| `--block-service-workers` | A page leaves no worker behind |
| `--redact-url-params=...` | The values of the common names of a token, a code, a key or a password in a URL show as `<redacted>` in a tool answer |
| `--proxy-server=http://fetch-proxy.agents-main.svc.cluster.local:8080` | Each page through the fetch proxy |
| `--allowed-hosts=playwright-mcp.ai.svc.cluster.local:8000` | The server answers the Host of its Service only (a `403` for any other Host). The gateway is the one caller |

No `--caps`, so no capability adds a tool. No storage state, no secrets
file, no profile folder and no saved session: the browser holds no
credential (R2).

## The route

`main-browser` on the central MCP gateway takes the key of main only, like
the read routes of main, and sends the server no credential. Its rule
names, by exact name, the core tools of the dump of the pinned release, with
five left out:

- `browser_run_code_unsafe` and `browser_evaluate` run code in the server
  or the page.
- `browser_file_upload` and `browser_drop` send a file of the server pod to
  a page.
- `browser_install` installs a browser; this release has no such tool, and
  the rule does not name it.

So 21 of the 25 tools of the release pass. A tool of a later release, or of
a capability flag, is refused until a pull request names it.

**Stateful.** The other routes are stateless: the gateway opens a new
server session for each call. The browser keeps a page for each session,
so this route is `statefulMode: stateful`: the gateway gives the client a
session ID that holds the ID of the server session, and each call of that
session reaches the same server session. The session key of the gateway is
off (`config.session.key: disabled`), so its session ID is that ID in
base64, with no secret in it; only the key of main reaches the route, and
the server makes a random ID for each session.

## The prompt before a form

The host approval plugin of the main agent asks the approver before each
browser tool that can send a form (Design Doc, section 4.2.6, "A public
form"). Its `promptTools` name seven tools by exact name, as the agent
sees them (`<server>__<tool>`): `browser__browser_click`,
`browser__browser_drag`, `browser__browser_fill_form`,
`browser__browser_handle_dialog`, `browser__browser_press_key`,
`browser__browser_select_option` and `browser__browser_type`. A click can
send a form, a drag can drop an element on a target that sends, a key can
be Enter, a type can submit, a select can send a form on change, and a
dialog can be a confirm. The plugin matches the full tool name, and a call of one of them
waits for the card in the direct chat of the approver; a no or no answer
in 10 minutes refuses it. The tools that read a page (`browser_navigate`,
`browser_snapshot`, `browser_find`, the network reads) and the tools that
move in the page (`browser_hover`, `browser_tabs`) do not ask. A page can still send data on a load or a hover, as any public page
can; the egress goes to public sites through the fetch proxy only.

## The switch

The browser is live for use when the config of the main agent
(`agents/main/agent/config/openclaw.json`) has the entry `browser` at
`http://mcp-gateway.ai.svc.cluster.local:3000/main-browser/mcp`, with
`Bearer ${MCP_GATEWAY_KEY}` and a `toolFilter` of the 21 tools of the rule,
and `tools.sandbox.tools.alsoAllow` names each of them as
`browser__<tool>`, with no wildcard. That is one small pull request, the
last of the browser step; it opens after the rows below that a probe can
prove pass, and the owner's word. Its way back is its revert.
`observability/tests/test_main_browser.py` holds both states: no entry and
no browser tool, or the entry with exactly the tools of the rule.

| Row (Design Doc, section 4.2.6) | Before the switch | After the switch |
| --- | --- | --- |
| The route and the reach of the gateway to the browser | **The daily gateway probe of main** (item 8 of [the probe](mcp-gateway.md#the-probe)): a session opens through the gateway and the browser | The same |
| The browser tool rule: no `browser_run_code_unsafe`, `browser_evaluate`, `browser_file_upload`, `browser_drop` or `browser_install` | **The daily gateway probe of main**: the list of the route equals the rule; the tests read the rule against the dump | The owner asks the agent for its browser tools |
| Browser isolation: two sessions do not see the cookies of each other | The lab of the tests: two sessions through the gateway at its pin keep two pages (a fake browser, `--isolated` in the tests) | The owner's test: a page in one chat is not in the other |
| The browser egress: a navigation to a cluster service, a house address and a host fails; a public HTTPS site loads | The lab of the fetch proxy refuses each request inside (`test_main_fetch_proxy.py`); the tests read the proxy flag and the policies. No probe drives the browser | The owner asks for a public page, a cluster service and plain HTTP; the fetch proxy log counts the refusals |
| The prompt before a form | The tests read `promptTools`; the plugin lab of the agent image | The owner asks for a form; a card comes, and a no stops it |
| A page that registers a WebMCP tool adds no tool | `--no-webmcp` is in the tests | The owner's test with a page that does |
| A pod that is not the gateway can not reach the browser | The daily gateway probe of each domain (item 7 of [the probe](mcp-gateway.md#the-probe)) | The same |

## Updates

Renovate updates the tag and the digest. The test
`test_the_dump_is_of_the_pinned_image` then fails until the pull request
also has a new dump of the new image (`mcp_tools.py dump`) and, for a new
tool, a choice in the rule; a tool of a new release is refused at the
gateway until then.

## Monitoring

The AI board shows it as the row "Browser tool server" of the shared parts
(`AI_PARTS` in `observability/inventory.py`): Healthy at homelab-wsl when
its replica is ready, and Not applicable at homelab-wsl-02, which does not
run it by design. The alert `AgentPlatformWorkloadNotReady` does not name
it. The alerts of kube-prometheus-stack for a Deployment cover it, for
example `KubeDeploymentReplicasMismatch` after 15 minutes.

## What is left

- **Not live for use** until [the switch](#the-switch).
- **No signed-in site.** A signed-in session is a credential in this pod
  (R2); each site would get an account of its own, a Secret and a route of
  its own, on the owner's word.
- **One replica.** A restart closes each open page.
- **The Chromium sandbox.** The entrypoint of the image turns the sandbox of
  Chromium off. Code that leaves a renderer reaches a pod that holds no
  credential, behind its network policy, and that risk stays for the owner.
