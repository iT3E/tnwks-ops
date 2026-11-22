# The host server

The SSH MCP server `ssh-mcp` `v2.26.0`, built with no change of code by
iT3E/tnwks-host-mcp `0.1.0` and pinned by digest. It is the host tool of
the main agent, behind the route `main-host` of the central MCP gateway
([mcp-gateway.md](mcp-gateway.md)), with the host approval plugin of the
agent as its prompt (Design Doc of effort 0003, section 4.2.6, "The host
server"; task 6.22).

**Not applied yet.** The manifests are in git, and
`kubernetes/apps/ai/agents/main/kustomization.yaml` does not list them
until the values of their Secret exist ([What waits](#what-waits)). The
agent has no host tool until then, and after it only through the plugin.

Manifests: `kubernetes/apps/ai/agents/main/host-mcp/`.

## Pieces

| Piece | Where |
| --- | --- |
| Pod | Deployment `host-mcp` in `agents-main`: the step `files`, then two containers, `server` (port 3000, loopback use only) and `proxy` (nginx, port 8080) |
| Service | `host-mcp`, port 8080, the proxy only |
| Secret | `host-mcp-secret`: `config.toml`, `agent_ed25519` and `HOST_MCP_TOKEN` (not in git yet) |
| Gateway route | `main-host`, a key of its own (`client: main-host`), the six read tools |
| Tool dump | `kubernetes/apps/ai/mcp-gateway/tools/host.json`, the tool names of the pinned image |
| Network policy | CiliumNetworkPolicy `host-mcp` |

## The server

- HTTP mode with a bearer token, its own approval off
  (`approvalMode = "auto"`): the plugin of the agent is the one prompt.
- `--hostKeyMode=strict`: each profile pins the host key of its target.
- `--allowedHosts` names its Service only.
- The server takes its config and its key only with mode 0600 and its own
  uid; it stops at its start with any other mode. A Secret volume gives
  files of root, so the step `files` copies them, as uid 65532 with mode
  0600, into volumes in memory.
- The token is a flag of the server, so its command line holds it. The pod
  shares no process namespace, the spec holds only the reference to the
  Secret, and no account of an agent has `pods/exec` in the namespace
  (Design Doc, section 7, row 46).

**The proxy** takes each request of the gateway, replaces its
`Authorization` with the bearer token of the server, sets the Host that the
server takes, and sends it to the server on loopback. Only `/mcp` and
`/healthz` answer. So the gateway and the agent hold no token of the
server.

## The route

`main-host` takes the key of the host approval plugin of the main agent:
`MCP_HOST_KEY` in the agent pod, a key of its own and never the key of
main. The plugin refuses each call when another server entry of the config
of the agent carries that key. The hash is a placeholder until the lead
makes the key, so each request gets a 401. When the key is made, the
route still refuses the key of main: that key is not a key of the route,
and the rule lets in the client `main-host` only.

Its rule names the six read tools of the read step: `list-connections`,
`list-sessions`, `read-command`, `read-session-output`, `sftp-download`
and `sftp-list`. The writes (`run-command`, `privileged-command`,
`open-session`, `close-session`, `signal-process`, `sftp-upload`),
`reload-config` and the two tools that move a file of the server pod are
refused at the gateway.

## Network policy

- **In:** port 8080 from the central MCP gateway, and ports 8080 and 3000
  from the kubelet for the probes.
- **Out:** port 22 of `WINDOWS_HOST_IP` and of `HOST_MCP_DISTRO_ADDR`,
  and nothing else: no DNS (the profiles name addresses), no pod, no
  other address.

## Tests

`observability/tests/test_main_host_server.py` reads the files, and runs
the gateway at its pin with the config of git, and the proxy and the
server from their pinned images in one network namespace, as in the pod.
The route lists the six reads, a read reaches the server, each other tool
is refused at the gateway, the key of main gets a 401 on this route and
passes on a route of main, the server refuses a call with no token, the
proxy sets it, and no log holds the token or a key.

## What waits

| Item | Who | Where |
| --- | --- | --- |
| The agent user on the Windows host and in the WSL distro of each site, with its file rights | The owner, by the Ansible role of task 6.21 (D145) | The host |
| The `Match` block of the agent user in the SSH server of Windows | The owner, by the role `windows_sshd` (task 6.21) | The host |
| The SSH key pair of the agent user, the public key on each target, held for the source address of the cluster only | The owner | The host, and `agent_ed25519` of the Secret |
| The source address of the cluster at each host, and the address at which the pod reaches the SSH server of the distro | A read on the host, then the lead | `HOST_MCP_DISTRO_ADDR` in cluster-settings |
| The pinned host key of each target | A read on each host | `config.toml` of the Secret |
| The profiles: one for each target, the user of the agent | The lead, from the reads above | `config.toml` of the Secret |
| The bearer token of the server | The lead (a key inside the cluster) | `HOST_MCP_TOKEN` of the Secret |
| `MCP_HOST_KEY` and its hash on `main-host` | The lead (a key inside the cluster) | `agent-secret` of main, and the route |
| The read list of the plugin | The design (the fixed lines of the ten checks) | `readList` in the config of the agent |
| The pod at site 2 | A later step: site 2 runs no part of the AI domain yet | The cluster of site 2, with a global Service |

When the Secret exists, a pull request adds `secret-host-mcp.sops.yaml`
to `app/kustomization.yaml` and lists `./host-mcp/ks.yaml`.

## What is left

- **No probe yet.** The daily gateway probe does not call this route; a
  key of the probe for `tools/list` only, as on the browser route, is a
  later step.
- **One maintainer upstream**, so its upkeep is a risk to watch.
