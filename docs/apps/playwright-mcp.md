# The browser tool

The Playwright MCP server of the vendor of the browser engine: an MCP
server over Streamable HTTP that drives a headless Chromium. No agent uses
it yet. A later step gives it to the main agent through a route of its own
on the central MCP gateway ([mcp-gateway.md](mcp-gateway.md)).

Manifests: `kubernetes/apps/ai/playwright-mcp/`, image
`mcr.microsoft.com/playwright/mcp` in `app/helmrelease.yaml`. It runs at
one site at a time (`PLAYWRIGHT_MCP_REPLICAS` in each site's cluster
settings; switching sites is in [multi-site.md](../wsl/multi-site.md)).
Upstream: [GitHub](https://github.com/microsoft/playwright-mcp)
(Apache-2.0).

## Pieces

| Piece | Where |
| --- | --- |
| App | Deployment `playwright-mcp`, port 8000 (`/mcp`, and the legacy `/sse`) |
| Service | `playwright-mcp`, global across the WSL sites (Cilium Cluster Mesh) |
| Ingress | None: an agent reaches it through the gateway only |
| Network policy | CiliumNetworkPolicy `playwright-mcp` (`app/networkpolicy.yaml`) |
| Data | None: `--isolated` gives each session a fresh browser profile in memory, and `/tmp` and the home directory are `emptyDir` |

## Network policy

- **In:** port 8000 from the central MCP gateway, and from the host (the
  kubelet's TCP probes). No other pod, and no ingress.
- **Out:** the fetch proxy of the main agent on port 8080, and DNS for the
  name of the fetch proxy only. No other pod, no service of the cluster,
  no address of the house and no web site directly.

The flags of origins of the server are not a security boundary (its own
docs say so), so the network policy is the wall.

The app moves between the sites, and the gateway and the fetch proxy run
at homelab-wsl only. So each peer is named at this cluster and at the
other one (`${CLUSTER_NAME}` and `${PEER_CLUSTER_NAME}`), as in
[agent-platform.md](agent-platform.md#network-policy-and-the-cluster-mesh).

## What is left

- **No way to a web site yet.** The fetch proxy lets in the agent pod of
  main only, and its Service is not global, so a page load fails. The step
  that gives the browser to the main agent adds the proxy flag of the
  browser, the rule of the fetch proxy for it, and the route and egress of
  the gateway.
- **No gateway route yet.** The gateway names no browser target, so the
  rule that lets it in carries no traffic until that step.
- **The Chromium sandbox.** The entrypoint of the image runs Chromium with
  `--no-sandbox`. A renderer that breaks out reaches a pod that holds no
  credential, behind its network policy.
