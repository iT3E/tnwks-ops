# The host server

The SSH MCP server `ssh-mcp` `v2.27.1`, built with no change of code by
iT3E/tnwks-host-mcp `0.2.0` and pinned by digest. It is the host tool of
the main agent, behind the route `main-host` of the central MCP gateway
([mcp-gateway.md](mcp-gateway.md)), with the host approval plugin of the
agent as its prompt (Design Doc of effort 0003, section 4.2.6, "The host
server"; task 6.22).

**Listed.** `kubernetes/apps/ai/agents/main/kustomization.yaml` lists
`./host-mcp/ks.yaml`, so Flux applies the pod, the Service, the policy and
the Secret ([The go-live](#the-go-live)). The agent has host tools only
through the plugin: in a turn of the owner on his direct chat, a read of
the list runs with no card, and each other call waits for his yes.

Manifests: `kubernetes/apps/ai/agents/main/host-mcp/`.

## Pieces

| Piece | Where |
| --- | --- |
| Pod | Deployment `host-mcp` in `agents-main`: the step `files`, then two containers, `server` (port 3000, loopback use only) and `proxy` (nginx, port 8080) |
| Service | `host-mcp`, port 8080, the proxy only |
| Secret | `host-mcp-secret`: `config.toml`, `agent_ed25519` and `HOST_MCP_TOKEN`, in `app/secret-host-mcp.sops.yaml` |
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
no other container reads it. The design keeps it so for the read step:
before the write tools go on the route, the design names how the audit
line ships, its retention and its privacy boundary. In the read step the
records of a host call are:

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
of the agent carries that key. The store step of the lead makes the key
and sets its hash on the route ([The go-live](#the-go-live)); with the
placeholder hash each request gets a 401. With the key, the route still
refuses the key of main: that key is not a key of the route, and the rule
lets in the client `main-host` only.

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
fails by the file rights of the host; with the server listed, the hash of
the key of the route is not the placeholder and matches no other key in
use; each
other tool is refused at the gateway; the key of main gets a 401 on this
route and passes on a route of main; the server refuses a call with no
token, the proxy sets it; and no log holds the token or a key. The fake
host is built from its Dockerfile (the recipe of the update check of the
plugin in the agent image repository), or taken from
`HOST_MCP_LAB_SSHD_IMAGE` for a run with no network. The file also checks
the nine lines of the read list and that each gateway probe tries the
server straight; `test_main_host_approval.py` checks the plugin entry.

## State

| Item | State | Where |
| --- | --- | --- |
| The Secret `host-mcp-secret`: the profiles with the pinned host key of each target, the private key of the agent user, the bearer token | In git, encrypted to the recipients of the other Secrets of `agents-main`; Flux applies it with the folder. Each value was made or read by a script, and none was typed or shown | `app/secret-host-mcp.sops.yaml`, a resource of `app/kustomization.yaml` |
| The listing | `./host-mcp/ks.yaml` is listed | `kubernetes/apps/ai/agents/main/kustomization.yaml` |
| The read list of the plugin | The nine lines of the design, for the profile `agent` (the user in the distro of site 1) | `readList` in the config of the agent |
| `MCP_HOST_KEY` and its hash on `main-host` | Made by the store step of the lead, in the go-live pull request | `agent-secret` of main, and the route |
| The values of the agent user at site 1: its name, its public key and the source network of its key | In the host secrets of the Windows host and of the distro, with the switch of the role off | The host variables of the Ansible tree |
| The address at which the pod reaches the SSH server of the distro | Set | `HOST_MCP_DISTRO_ADDR` in the cluster settings of site 1 |

## What waits

| Item | Who | Where |
| --- | --- | --- |
| The owner's test questions after the go-live: each line of the read list, a read with no card, a call with a card, a call of another sender | The owner | His direct chat with the agent |
| The shipping of the audit line, its retention and its privacy boundary | The design, before the write tools go on the route | [The audit log](#the-audit-log) |
| The lines of the Windows profile | The write step | The read list |
| A probe key on the route, for `initialize` and `tools/list` | A later step | [What is left](#what-is-left) |
| The pod at site 2 | A later step: site 2 runs no part of the AI domain yet | The cluster of site 2, with a global Service |

## The go-live

One pull request (tasks 6.21 and 6.22). It merges only after these, in
this order:

1. The agent user on the Windows host and in the distro of site 1, with the
   `Match` block of the SSH server of Windows, is applied (the roles of
   task 6.21, the switch on).
2. A clean final check of the host by the lead.
3. The merge of the pull request that turns the switch on.
4. A read in the distro that confirms the units of the `systemctl` line of
   the read list: a unit that the site does not have leaves the line. The
   line holds the units that the read confirmed.

**What it changes:**

- The line `./host-mcp/ks.yaml` in the folder of main, and the class list
  of the Flux admission policy and of `cluster-apps` of site 1
  (`classlist.py write`): the new child `agents-main-host-mcp`.
- `readList` in the config of the agent: the nine lines of the design.
- Each gateway probe tries the host server straight, as the read servers
  ([mcp-gateway.md](mcp-gateway.md#the-probe), item 7).
- **The store step** of the lead, a commit on top of the branch:
  `host-mcp-store.py gateway-key` adds `MCP_HOST_KEY` to `agent-secret`
  (`kubernetes/apps/ai/agents/main/agent/app/secret.sops.yaml`, in the
  editor step of `bin/sops`) and sets its hash on `main-host`
  (`kubernetes/apps/ai/mcp-gateway/app/config/config.yaml`). It prints
  names and `ok` only. Until it runs, three tests fail:
  `test_the_route_has_a_key_of_its_own_once_the_server_is_listed`
  (`test_main_host_server.py`),
  `test_the_host_key_is_in_agent_secret_once_the_server_is_listed`
  (`test_main_host_approval.py`) and
  `test_the_secrets_of_the_owner_hold_each_value_the_pod_names`
  (`test_main_agent.py`).

**What rolls** (a compare of the render of main and of the branch, with a
test hash in place of the store step): the new pod `host-mcp` starts; the
hash on the route changes the config of the gateway, so the gateway and
the agent pods of main and of finance roll (each agent mounts that config
for its values check); the main agent also takes its new config, and
Reloader may start it once more for `agent-secret`; the two gateway probes
take the new config at their next run. The agents have no tools while the
gateway rolls, about 2 minutes.

**After the merge,** first the order of the admission policy. This is the
first new child of the class with the policy at Deny. Flux applies the
lists (`flux-admission`) and the new child (`cluster-apps`) at the same new
revision, and nothing orders the two. Read `flux-admission` and then
`cluster-apps` Ready at the commit of the merge. If `cluster-apps` reports
a refusal of `flux-child-scope` for `agents-main-host-mcp`, it applies at
its next run, in 30 minutes at most (its interval, with no retry
interval), and nothing is lost. In that time `cluster-apps` is not Ready,
a Flux event may alert, and a host call gets an error of the gateway and
runs nothing. The short cut, which is a write: when `flux-admission` is
Ready at the commit of the merge,
`flux -n flux-system reconcile kustomization cluster-apps`. The way back
is safe at Deny: the policy matches create and update only, so a prune is
never refused.

Then, read only:

| Read | Pass rule |
| --- | --- |
| `flux -n flux-system get kustomization flux-admission cluster-apps` | Both Ready at the commit of the merge |
| `flux -n flux-system get kustomization agents-main-host-mcp` | Ready, at the commit of the merge |
| `kubectl -n agents-main get deploy host-mcp` | 1 of 1 ready, the image with the digest of git |
| `kubectl -n agents-main logs deploy/agent -c values-check` | `values-check result=ok` |
| `kubectl -n agents-main logs deploy/agent -c agent \| grep -c 'host-approval: loaded, 9 read lines'` | 1 for each start of the pod, and no line `host-approval: refusing each tool call` |
| The next run of `platform-check-mcp-gateway-probe` in `agents-main` and in `agents-finance` | The line `host-mcp in agents-main, direct from a pod that is not the gateway` with `"ok": true` |
| The owner asks the agent for one line of the read list | The answer comes with no card, and the access log of the gateway has the call ([below](#read-the-record-of-a-call)) |

**The owner's test questions** come next: each line of the read list, a
line that is not on the list (a card, and no run before his yes), the same
line on the Windows profile (a card), and a turn of another sender (no
host tool).

### Read the record of a call

- **The gateway** (Loki, 14 days, or the pod for its life):
  `kubectl -n ai logs deploy/mcp-gateway --since=24h | grep '"route":"default/main-host"'`.
  Each line has the tool (`gen_ai.tool.name`), the MCP method, the status
  and the client `main-host`; a refused tool has `"reason":"Authorization"`.
  It never has the command or the result.
- **The session store of the main agent:** the call with its arguments,
  its result and any yes of the owner, in the session of the turn on the
  state volume and its backups.
- **The audit log of the server:** one JSON line for each call that
  reaches a command or a file, with the command, its class, the decision
  and the exit code. It is a file in the pod
  (`/audit/ssh-mcp/audit.log`), lost at each new pod, and no account of
  an agent can read it ([The audit log](#the-audit-log)).
- **The log of the plugin** is not a record of a call: its start line and
  each refusal only.

### The way back

Remove the line `./host-mcp/ks.yaml` from the folder of main, in one pull
request, with `classlist.py write` (the class list loses the child). Flux
prunes the pod, the Service, the policy and the Secret (`prune: true`).
The route stays with no backend, so a call gets an error from the gateway
and runs nothing. The key, its hash and the read list can stay; the tests
take that. A revert of the squash commit of the go-live does the same and
also takes back the key, its hash and the read list. Until a way back
merges, the owner answers no card, and a read of the list still runs.

### The limits

- **A read of the list runs with no card,** in a turn of the owner on his
  direct chat only. The nine lines are exact: no other argument, no other
  profile.
- **The audit log of the server is lost at each new pod** until the design
  names how it ships.
- **No probe calls the route itself:** the gateway probe proves only that
  the server takes no straight connection ([What is left](#what-is-left)).
- **No values check reads `MCP_HOST_KEY`:** the check of the pod compares
  the key of main with its hash only. A key with a wrong hash gives a 401
  on each host call, and the agent has no host tool; nothing else breaks.
- **Site 1 only.** The Windows profile has no line of the list, so each
  call there makes a card.

## What is left

- **No probe of the route yet.** The daily gateway probe tries the server
  straight (item 7 of [the probe](mcp-gateway.md#the-probe)), and does not
  call the route; a key of the probe for `tools/list` only, as on the
  browser route, is a later step.
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
