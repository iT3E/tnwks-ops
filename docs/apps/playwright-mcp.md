# The browser tool

The Playwright MCP server of the vendor of the browser engine: an MCP
server over Streamable HTTP that drives a headless Chromium. No agent uses
it yet. A later step gives it to the main agent through a route of its own
on the central MCP gateway ([mcp-gateway.md](mcp-gateway.md)).

Manifests: `kubernetes/apps/ai/playwright-mcp/`, image
`mcr.microsoft.com/playwright/mcp` in `app/helmrelease.yaml`. It runs at
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

The app, the gateway and the fetch proxy run at homelab-wsl only. Both
WSL clusters are one Cilium Cluster Mesh, so each peer names this cluster
(`${CLUSTER_NAME}`), as in
[agent-platform.md](agent-platform.md#network-policy-and-the-cluster-mesh).

## Monitoring

The AI board shows it as the row "Browser tool server" of the shared parts
(`AI_PARTS` in `observability/inventory.py`): Healthy at homelab-wsl when
its replica is ready, and Not applicable at homelab-wsl-02, which does not
run it by design. The alert `AgentPlatformWorkloadNotReady` does not name
it. The alerts of kube-prometheus-stack for a Deployment cover it, for
example `KubeDeploymentReplicasMismatch` after 15 minutes.

## What is left

- **No way to a web site yet.** The fetch proxy lets in the agent pod of
  main only, so a page load fails. The step that gives the browser to the
  main agent adds the proxy flag of the browser, the rule of the fetch
  proxy for it, and the route and egress of the gateway.
- **No gateway route yet.** The gateway names no browser target, so the
  rule that lets it in carries no traffic until that step.
- **The Chromium sandbox.** The entrypoint of the image runs Chromium with
  `--no-sandbox`. A renderer that breaks out reaches a pod that holds no
  credential, behind its network policy.
