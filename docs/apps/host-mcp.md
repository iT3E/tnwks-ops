# The host server

The SSH MCP server `ssh-mcp` `v2.27.1`, built with no change of code by
iT3E/tnwks-host-mcp `0.2.0` and pinned by digest. It is the host tool of
the main agent, behind the route `main-host` of the central MCP gateway
([mcp-gateway.md](mcp-gateway.md)), with the host approval plugin of the
agent as its prompt (Design Doc of effort 0003, section 4.2.6, "The host
server"; task 6.22).

**Not applied yet.** The manifests and the encrypted Secret are in git, and
`kubernetes/apps/ai/agents/main/kustomization.yaml` does not list them
until the owner's apply of the agent user and his gates
([What waits](#what-waits)). The agent has no host tool until then, and
after it only through the plugin.

Manifests: `kubernetes/apps/ai/agents/main/host-mcp/`.

## Pieces

| Piece | Where |
| --- | --- |
| Pod | Deployment `host-mcp` in `agents-main`: the step `files`, then two containers, `server` (port 3000, loopback use only) and `proxy` (nginx, port 8080) |
| Service | `host-mcp`, port 8080, the proxy only |
| Secret | `host-mcp-secret`: `config.toml`, `agent_ed25519` and `HOST_MCP_TOKEN`, in `app/secret-host-mcp.sops.yaml` (in git, not applied) |
| Audit log | `/audit/ssh-mcp/audit.log` in the server container (`XDG_DATA_HOME=/audit`), an emptyDir `audit` of its own, 64Mi ([below](#the-audit-log)) |
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
  files of root, so the step `files` copies them, as uid 65532 (the user of
  the image) with mode 0600, into volumes in memory. It also stops when the
  folder of the config is open to others, and the root of an emptyDir is
  (mode 0777, with the group of fsGroup). So the config is
  `/config/ssh-mcp/config.toml`, in a folder of mode 0700 that the step
  makes.
- **The profiles** (`config.toml`): `agent`, the user of the host server in
  the distro of site 1, and `agent-windows`, the same user on the Windows
  host, each with the address of its target, port 22, the key
  `/keys/agent_ed25519` and its pinned host key. Both take
  `role = "viewer"` and `group = "prod"`: in the role bindings of the
  server, a viewer on a prod group runs read-only commands only. The denylist names
  `dig`, `nslookup`, `host`, `ping` and `traceroute`.
- The token is a flag of the server, so its command line holds it. The pod
  shares no process namespace, the spec holds only the reference to the
  Secret, and no account of an agent has `pods/exec` in the namespace
  (Design Doc, section 7, row 46).

**The proxy** takes each request of the gateway, replaces its
`Authorization` with the bearer token of the server, sets the Host that the
server takes, and sends it to the server on loopback. Only `/mcp` and
`/healthz` answer. So the gateway and the agent hold no token of the
server.

## The audit log

The server writes one JSON line for each tool call that reaches a command
or a file to `$XDG_DATA_HOME/ssh-mcp/audit.log`: the time, the profile and
user, the command (with the secrets that its redactor finds removed), its
class and binary, the decision and its rule, the exit code, the duration and
the error. The root of the pod is read only, and the server answers a call
with the error of that write and runs no command when it has no writable
place for the line. So `XDG_DATA_HOME` is `/audit`, an emptyDir of its own
with a limit of 64Mi. In the lab a line was 270 to 330 bytes, so the limit
holds about 200,000 calls; past it, the kubelet evicts the pod.

**The log is lost at each new pod.** It is a file on an emptyDir, and
no other container reads it. The approved design text says that the log
agent ships the audit log of the server. This manifest does not do that, so
it is an open item at the owner's gate before the apply. Until his answer,
the records of a host call are:

- **The session store of the main agent**, on its own volume: each tool
  call with its arguments, its result and any yes of the owner.
- **The access log of the central MCP gateway**, in Loki for 14 days (its
  pod ships its log, `logs.home.arpa/ship`): one line for each request with
  the route, the MCP method, the target, the tool, the status and the client
  of the key. It never holds the arguments or the result.

The log of the host approval plugin is not a record of a host call: the
plugin writes a line when it loads and for each call that it refuses, and
the agent container of main does not ship its log
(`vector.dev/exclude-containers: agent`).

The lab of the pod (all six read tools, the user, the read-only root, the
environment and the volumes of the manifest) found three writes and no
other: the step `files` writes `/config/ssh-mcp/config.toml` and
`/keys/agent_ed25519`, and the server writes `/audit/ssh-mcp/audit.log`.
Nothing goes to `/tmp` or to the root.

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
the gateway at its pin with the config of git, the proxy and the server
from their pinned images in one network namespace, as in the pod, and a
fake Linux host (`observability/tests/host-mcp-sshd/`: OpenSSH with one
normal user, new keys and a pinned host key each run). The server runs with
the user, the read-only root, the environment and the volumes of the
manifest, each emptyDir with the owner and mode that the kubelet gives it,
so a path that the server needs and the manifest does not give fails the
test. The route lists the six reads; a read through the gateway comes back
with no error and with the output of the command, as viewer on prod; the
server refuses a command of another class and one of the denylist; a file
list and a file that the user may read come back, and a file of root only
fails by the file rights of the host; the hash of the key of the route is
the placeholder, which matches no key in use; each
other tool is refused at the gateway; the key of main gets a 401 on this
route and passes on a route of main; the server refuses a call with no
token, the proxy sets it; and no log holds the token or a key. The fake
host is built from its Dockerfile (the recipe of the update check of the
plugin in the agent image repository), or taken from
`HOST_MCP_LAB_SSHD_IMAGE` for a run with no network.

## State

| Item | State | Where |
| --- | --- | --- |
| The Secret `host-mcp-secret`: the profiles with the pinned host key of each target, the private key of the agent user, the bearer token | In git, encrypted to the recipients of the other Secrets of `agents-main`; not applied, because no Kustomization that Flux applies lists the folder. Each value was made or read by a script, and none was typed or shown | `app/secret-host-mcp.sops.yaml`, a resource of `app/kustomization.yaml` |
| The values of the agent user at site 1: its name, its public key and the source network of its key | In the host secrets of the Windows host and of the distro, with the switch of the role off | The host variables of the Ansible tree |
| The address at which the pod reaches the SSH server of the distro | Set | `HOST_MCP_DISTRO_ADDR` in the cluster settings of site 1 |

## What waits

| Item | Who | Where |
| --- | --- | --- |
| The run of the agent user on the Windows host and in the distro of site 1, with its file rights and its public key. The role of task 6.21 is in main with its switch off; its run waits | The owner, by a pull request that turns the switch on, then his apply | The host |
| The `Match` block of the agent user in the SSH server of Windows | The owner, by the role `windows_sshd` (task 6.21) | The host |
| The answer on the audit log: the design text says that the log agent ships it, and the manifest keeps it on an emptyDir ([The audit log](#the-audit-log)) | The owner, at his gate before the apply | This doc and the manifest |
| The read list of the plugin | Empty until the owner's gate | `readList` in the config of the agent |
| The line that lists `./host-mcp/ks.yaml` | The lead, after the owner's apply and his gates | `kubernetes/apps/ai/agents/main/kustomization.yaml` |
| `MCP_HOST_KEY` and its hash on `main-host` | The lead (a key inside the cluster), with the line above | `agent-secret` of main, and the route |
| The pod at site 2 | A later step: site 2 runs no part of the AI domain yet | The cluster of site 2, with a global Service |

After the owner's apply and his gates, one pull request lists
`./host-mcp/ks.yaml` in the folder of main and sets the hash of the key of
the route.

## What is left

- **No probe yet.** The daily gateway probe does not call this route; a
  key of the probe for `tools/list` only, as on the browser route, is a
  later step.
- **One maintainer upstream**, so its upkeep is a risk to watch.
- **Updates are reviewed.** A new release of the image opens a Renovate
  pull request 7 days after the release at the earliest, with
  `impact/high` and no automerge, as for the other MCP servers
  ([renovate.md](../operations/renovate.md)). That pull request changes the
  pin in the Deployment only; the review sets the same digest in the tool
  dump and in the test, and checks the new tool dump.
- **A security release gets no fast path.** Renovate finds no advisory for
  a container image, so the update waits 7 days like each other release.
  To move it at once, tick its box under "Pending Status Checks" on the
  Renovate dashboard, or move the pin by hand. Either way a person reviews
  it, with the tool dump.
