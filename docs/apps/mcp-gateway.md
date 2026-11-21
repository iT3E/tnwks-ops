# The MCP gateway

Each MCP call of an agent of the platform goes through one gateway: it
checks the key of the agent, lets the agent see and call only the tools
that its rule names, and sends the call on to the MCP server of the trust
domain. It holds no credential of an app. Today it has one client domain,
finance; the others get a route when they get a tool.

| | |
| --- | --- |
| Product | [agentgateway](https://agentgateway.dev) v1.7.0, standalone mode, by digest |
| Where | namespace `ai`, `kubernetes/apps/ai/mcp-gateway`, homelab-wsl only |
| Config | `app/config/config.yaml`, one file in git; no database, no UI |
| Address | `http://mcp-gateway.ai.svc.cluster.local:3000/<domain>/mcp` |
| Clients | `finance` (the finance agent), `finance-probe` and `no-tools` (the gateway probe) |
| Logs | Loki, `{namespace="ai", app="mcp-gateway"}` |
| Metrics | Prometheus, the request and connection series; no tool name (the tool of each request is in the log) |

## Why here

The gateway is shared by all trust domains, as the model gateway
(`ai/litellm`) and the model bridge (`ai/model-bridge`) are, so it sits next
to them in `ai`, with its own Flux Kustomization. Like the bridge, the
cluster's app list (`kubernetes/clusters/wsl/apps/kustomization.yaml`) names
it, and not `apps/ai/kustomization.yaml`, which MS-01 reads. A trust domain
gets a route (`/<domain>/`), not a gateway of its own: one place holds every
tool rule, and each route has its own keys, its own rule and its own MCP
servers, so a key of one domain is not a key of another.

## What it holds

- **The config file** (a ConfigMap; the name has a hash of the file, so a
  change rolls the pod). It holds SHA-256 hashes of the keys of the clients,
  the tool rules, the address of each MCP server, and the bearer that the
  gateway sends to the MCP server of finance, `none`. That bearer is no
  secret: the MCP server of finance passes it to the proxy of the app, and
  the proxy puts the token of the agent in its place
  ([agent-platform.md](agent-platform.md), "The finance app").
- **No Secret, no app token, no model key, no database.** The values of the
  keys are in the Secrets of the clients only, and the gateway sees each key
  only in the request that carries it.
- **No service account token** (`automountServiceAccountToken: false`).
  The pod meets the `restricted` level: non-root, a read-only root file
  system, no capability, the runtime's seccomp profile.

The admin port (config dump, shutdown, the UI) listens on loopback only
(`config.adminAddr: localhost:15000`, the default) and no Service and no
network rule names it. The metrics port 15020 is for Prometheus, the
readiness port 15021 for the kubelet, and the MCP port 3000 is the only port
of the Service.

## Clients and rules

Deny by default, on each route: a request with no key or with a key that
is not in the config gets a 401 (`apiKey.mode: strict`). A client sees in
`tools/list` and may call only the tools that an `allow` rule names for it;
the gateway refuses another tool with a 400 (JSON-RPC error `unknown
resource`, the same as for a tool that does not exist) and sends nothing on.
The rules are `allow` rules only: a `deny` rule whose expression fails does
not deny.

The client is the one of the key, never one that a header names. After the
key check, the route keeps only the headers that MCP names (`Accept`,
`Content-Type`, `Mcp-Protocol-Version`, `Mcp-Session-Id`, `Last-Event-Id`,
a `transformations` policy of the route) and drops each other header of the
client (a cookie, a key of another kind, a forwarded address, a trace). The
backend is stateless: for each request the gateway opens its own session
with the MCP server (`initialize`, then the call) with its own headers and
its own `Authorization`, so a session id of the client never reaches the
server either. What arrives there is `Accept`, `Content-Type`, the host,
the bearer `none`, and the method and tool name that the gateway sets
(`Mcp-Method`, `Mcp-Name`). The container test puts a recorder in front of
the MCP server and checks the headers that arrive.

| Route | Client | May list | May call |
| --- | --- | --- | --- |
| `/finance/` | `finance` | the 73 kept tools | the 73 kept tools |
| `/finance/` | `finance-probe` | the 73 kept tools | `get_about` only |
| `/finance/` | `no-tools` | nothing | nothing |
| `/finance-vault/` | `finance` | the nine tools of the vault | the nine tools of the vault |
| `/finance-vault/` | `finance-probe` | the nine tools of the vault | `get_file_info` only |
| `/main-vault/` | `main` | the nine tools of the vault | the nine tools of the vault |
| `/main-vault/` | `main-probe` | the nine tools of the vault | `get_file_info` only |
| `/main-cluster/` | `main` | the eight kept reads of the cluster server | the eight kept reads of the cluster server |
| `/main-flux/` | `main` | the eight kept reads of the Flux server | the eight kept reads of the Flux server |
| `/main-terraform/` | `main` | the nine tools of the registry | the nine tools of the registry |

**The vault of finance** has a route of its own, `/finance-vault/`, so the
tool names of the finance app do not change. Its target is the vault server
of finance ([agent-platform.md](agent-platform.md), "The vault server"),
with no credential. Its rule names nine tools of the file server (six reads
and `write_file`, `edit_file`, `create_directory`); the server has no delete
tool, and the gateway refuses its move, copy, append and the other tools.
The gateway waits 10 s for an MCP target unless the target sets a time
(agentgateway 1.7.0), so each target sets the 60 s of its route
(`policies.http.requestTimeout`), by the choice of the owner at the gate of
Design Doc v3.42. A write that waits for the NFS server gets a `504` after
60 s, not an error after 10 s for a write that lands later. The cost: a
hung call of the vault or of the finance app holds a turn of the finance
agent up to 60 s. The vault route takes the key of the finance agent, so a
leak of that key also gives the writes of the vault
([agent-platform.md](agent-platform.md), "The vault server").

**The vault of main** has the same form: the route `/main-vault/`, the
nine tools, the 60 s, and the vault server of main as its target. Its one
client is the main agent, so its key is a key of its own and no key of
finance is on it (a test holds the two routes apart). The main agent calls
it from its gateway process (`mcp.servers.vault` of its config, with
`MCP_GATEWAY_KEY`), as the finance agent calls its own.

**The read servers of main** (Design Doc of effort 0003, section 4.2.6)
each get a route of their own with the key of the main agent only, the
same hash as on `/main-vault/`, with `route` as the second field of its
metadata. Each route names its kept tools one by one, from a dump of the
pinned image in `tools/`, sets 30 s on the route and on the target, and
sets no credential on the target: the credential of each read server stays
in its pod. In git today:

- `/main-cluster/`: the cluster server of main
  (`kubernetes/apps/ai/agents/main/cluster-mcp`), the Kubernetes MCP server
  of the containers organization, read only, as its own service account.
  That account has the role `view` in each namespace of this cluster but
  `agents-finance` and `ai`, and the list of the namespaces
  (`agents/main/read-access`). The server refuses Secrets and
  exposed-secret reports, and the gateway refuses each tool that the route
  does not name: no exec, no run, no write, no list across all namespaces,
  no node log. `tools/cluster.json` is the dump.

- `/main-flux/`: the Flux server of main
  (`kubernetes/apps/ai/agents/main/flux-mcp`), the Flux MCP server of the
  Flux Operator, unmodified (AGPL-3.0), in its read-only mode, as its own
  service account with the same read bindings. Its pod has no DNS: it
  calls the API server by the address in its environment, and its docs
  are in the image. The route keeps the reads and the docs, and refuses
  the diff (it needs `patch`) and `get_flux_instance` (no Flux Operator
  here). The reconcile is a later step. `tools/flux.json` is the dump.

- `/main-terraform/`: the Terraform server of main
  (`kubernetes/apps/ai/agents/main/terraform-mcp`), the Terraform MCP
  server of HashiCorp with the registry toolset and no token (the free plan
  of HCP Terraform has no read token). Its pod has no account at the API
  server and reaches the public registry only: not HCP Terraform, and not
  the host of the style guide resources of the server. The gateway sends
  no header of a client, so a client can not give it a token either.
  `tools/terraform.json` is the dump.

`observability/tests/test_main_read_servers.py` checks the routes, the
pods, the network rules and the bindings, and runs the pinned gateway with
the pinned servers in containers.

**The pilot and the workflow agents get no key.** No key is the safer
default. A key with no tool still authenticates: it opens a connection,
gets the server's name and its instructions, and becomes a key with tools
as soon as someone writes a rule that does not name its client. With no
key, each request of theirs ends at the 401, before any rule. The container
test proves both: a key that is not in the config gets a 401, and the key
`no-tools` lists nothing and is refused (`observability/tests/test_mcp_gateway.py`).
No network rule lets their agent pods reach the gateway either.

## The tool rule

agentgateway has no pin of the tools of a server: no description pin and no
schema pin. So the rule of finance names the kept tools one by one, and the
names come from a dump of the pinned image of the MCP server, in git:

- `tools/firefly.json` is the `tools/list` of
  `ghcr.io/daften/fireflyiii-mcp:1.0.1` (by digest) with the groups of
  `mcp.yaml`: 89 tools, each with its description and its input schema.
- `tools/mcp_tools.py` keeps 73 of them (the reads, the two rule tests and
  the fifteen creates and updates; never a delete, bulk change, trigger,
  export, attachment or rule write) and writes them into the rule
  (`mcp_tools.py write tools/firefly.json app/config/config.yaml`).
- `observability/tests/test_mcp_gateway.py` fails when the dump is not of
  the image of `mcp.yaml`, when the rule and the kept tools of the dump
  differ, or when the agent's filter and deny list give other tools than
  the rule. Its container test lists the tools of the pinned image and
  compares each one, descriptions and schemas included, with the dump.

What this covers: a new image of the server (a Renovate PR) fails the test
until someone dumps it again, and the diff of the dump shows each new,
changed or removed tool, with its description and its schema, for the
review. A new tool is refused until its name is in the rule.

What it does not cover: the gateway passes the descriptions of the server
as the server sends them, at run time. A server that changes a description
of a kept tool (an injection in the text) is not stopped by the gateway;
the image digest is what keeps the code fixed. The gateway does not check
the arguments of a call either; the proxy of the app limits what a call can
do at the HTTP level.

## The keys

Each key is 32 random bytes, as 64 hex characters. The config holds its
hash, `sha256:<hex>`; a hash of such a key tells nothing about it, so the
hash can be in a public repo. A hash of the form `sha256:000...0N` is a
placeholder: no key has it, so its client gets a 401 until the owner's
values step. The tests take either for each client: they find the hash of a
client by its `metadata.client`, require a placeholder or 64 hex characters
and a hash of its own for each client, and put the hash of a test key in
its place for the container tests.

| Client | Value (SOPS) | Hash |
| --- | --- | --- |
| `finance` | `MCP_GATEWAY_KEY` of `agent-secret` (`agents/finance/agent/app/secret.sops.yaml`, for the agent pod only) | `config.yaml`, routes `finance` and `finance-vault` (the same hash) |
| `finance-probe` | `PROBE_KEY` of `agents/finance/checks/app/secret-mcp-gateway-probe.sops.yaml` | `config.yaml`, routes `finance` and `finance-vault` (the same hash) |
| `no-tools` | `NO_TOOLS_KEY` of the same file | `config.yaml`, route `finance` |
| `main` | `MCP_GATEWAY_KEY` of `agent-secret` of main (`agents/main/agent/app/secret.sops.yaml`, for the agent pod only) | `config.yaml`, routes `main-vault` and `main-cluster` (the same hash; `metadata: {client: main, route: <the route>}`) |
| `main-probe` | `PROBE_KEY` of `agents/main/checks/app/secret-mcp-gateway-probe.sops.yaml`, made on 2026-11-20 and listed in that `kustomization.yaml` | `config.yaml`, route `main-vault` |

**The hash and the value stay in step** in two ways: the owner changes both
in one commit (below), and a check finds a difference. The values check of
the finance agent pod (its first start step) mounts this same config file
and stops the start with `values-check result=failed key=MCP_GATEWAY_KEY
reason=hash` when the SHA-256 of its key is not the hash of the client
`finance`; the gateway probe fails when one of its own keys gets a 401.
The check reads the line `metadata: {client: finance}` with awk, so the key
of the route `finance-vault` has a second field
(`metadata: {client: finance, route: finance-vault}`): one hash for the
check, and a test checks that both routes have the same one. A new value of
the key changes the hash on both routes in the same commit.

The keys of main are new: the main agent had no key at the gateway before
its vault. The lead makes each value and its hash in one commit (D121 for
the key of the agent; the go of task 3.37 for the probe key, as D120 for
the probe of finance). The values check of the main agent reads the line
`metadata: {client: main, route: main-vault}`, and two tests keep each
value and its hash together: a key that the Secret names must be in git
with it, and a real hash of `main-probe` needs its Secret listed.

## Network policy

`app/networkpolicy.yaml`:

- **In:** the MCP port from the agent pod of each domain that has a client
  (`agents-finance` and `agents-main`, `app.kubernetes.io/name: agent`) and
  from the gateway probe of each of those domains; the metrics port from Prometheus; the readiness port
  from the host. No sandbox: neither the workload pod nor the supervisor of
  a sandbox carries the labels of a client, and the sandboxes of finance
  keep their one way to the app, the read port of the proxy.
- **Out:** the MCP server of each route (`firefly-mcp` in `agents-finance`,
  port 3000, and `vault-server` in `agents-finance` and in `agents-main`,
  port 8080), and DNS for those names only. The pod sets `ndots` to 1, so it
  asks for the full name as it is.
- **The MCP server of finance** takes the gateway only. The agent pod has no
  way to it any more, and the gateway probe checks that a pod that is not
  the gateway can not reach it.

**A compromise of the gateway** gives the tools of each domain, inside each
wall behind it. For finance: every tool of the MCP server (the gateway's
rule no longer holds), but the proxy still refuses each delete, trigger,
rule write and each call off its allow list, and the app token stays in the
proxy. For the vault of finance: each tool of the vault server, so
each read and write of the finance share, but no delete (the server has no
such tool) and no path outside the share; the same for the vault of main.
It gives no model key and no app token. The config holds only hashes
of the client keys, but each client sends its key in each request, so a
compromised gateway sees each key that arrives while it is compromised, and
can call the gateway as that client. It also sees each tool call of each
domain while the call passes through it, with its arguments and its result:
the finance data of a call passes through this pod. A domain added later
adds its tools, its key and its data to this. After a compromise, the owner
makes new keys for every client (as in the steps below).

## Traffic in the cluster

The MCP traffic is plain HTTP: from the agent pod to the gateway, and from
the gateway to the MCP server. Traffic inside the cluster is not encrypted
between nodes, as for the model gateway today (the agents call it over plain
HTTP too). So the key of a client and the arguments and results of its tool
calls cross the network between the nodes in clear. The network policies
decide which pods may connect; they do not hide the content from a party on
the network between the nodes.

## Logs and metrics

One JSON line for each request, from the default fields of agentgateway
and one field added by a CEL expression (`frontendPolicies.accessLog.add:
client: apiKey.client`): the route, the HTTP status, the MCP method, the
target, the tool, the client, an error and its reason, and the time. Never
the arguments or the result of a call, and never a key: the container test
looks for the keys and the argument values in the log and finds none. A
refused tool:

```json
{"level":"error","scope":"request","route":"default/finance","http.status":400,"protocol":"mcp","mcp.method.name":"tools/call","mcp.target":"firefly","gen_ai.tool.name":"delete_transaction","error":"mcp: authorization denied for tool: delete_transaction","reason":"Authorization","client":"finance"}
```

The pod has the label `logs.home.arpa/ship: "true"`, so the platform log
agent ships its lines to Loki as they are, like the lines of the model
bridge. The PodMonitor scrapes the request series (`agentgateway_requests_total`
and the durations, by route and status) and the connection series. The MCP
request counter is not made (`config.metrics.remove`) and the scrape drops
it too: its label `resource` is the tool name that the client sends, also a
refused one, so a client could make any number of series. The tool of each
request is in the access log. The container test calls a made-up tool and
finds no tool name in the metrics. The label `method` of the request series
is the HTTP method that a client sends, any word, also with no key: the
scrape keeps the series of the standard methods only and drops the others
(one value for all of them would make two series one, which Prometheus
refuses as duplicate samples). A container test runs Prometheus with the
relabelings of the PodMonitor against odd methods. The alert `AgentPlatformWorkloadNotReady`
covers the Deployment.

**A 405 at level error on each start of a client.** After `initialize`, the
MCP client of the agent (the MCP TypeScript SDK 1.32.1 in the agent image)
opens the optional stream of the protocol: a `GET` to the route. A
stateless route offers no stream, so the gateway answers `405` and logs it
at level `error` (`"http.method":"GET"`, `"http.status":405`, `"error":"mcp:
method not allowed; must be GET, POST, or DELETE"`). The protocol allows
that answer, and the client goes on with no stream. It is noise, not a
fault:

- No setting of the client stops it: the SDK opens the stream after each
  `notifications/initialized` that gets a `202`, with no option, and the
  config of the product has no field for it.
- A route of the gateway could answer the `GET` itself (`directResponse`,
  status 405): a lab with agentgateway v1.7.0 logs it at level `info`. But
  that route has no key check, against the rule that each route denies by
  default, so it is not done.
- No alert counts it: no rule reads the log lines of the gateway, and no
  rule reads `agentgateway_requests_total` (a test checks the rules of
  git for it).

## The probe

`platform-check-mcp-gateway-probe` (`agents/finance/checks/app/`, daily at
05:37, `platform_checks.py mcp-gateway`) calls the route of finance as the
agent does, with two keys of its own and none of the agent's:

1. A straight connection from its pod to the MCP server fails.
2. No key, and a random key that is not in the config: a 401 each.
3. The probe key lists exactly the tools of the rule of finance (it reads
   the list from the same config file); its `get_about` reaches the app
   through the MCP server and the proxy (a 401 of the app with the
   placeholder token, an answer with the real one); `delete_transaction`,
   `trigger_rule` and `create_tag` are refused at the gateway, and
   `create_tag` is refused also with headers that name the client `finance`.
4. The key `no-tools` lists nothing, and its `get_about` is refused.
5. The vault route (`VAULT_GATEWAY_URL`): a straight connection from its
   pod to the vault server fails; no key and a random key get a 401; the
   probe key lists exactly the nine tools of the vault rule, and its
   `get_file_info` of `/vault` (the facts of the root of the share, no note
   name and no note text) reaches the server through the NFS mount;
   `write_file` and `move_file` are refused at the gateway; the key
   `no-tools` gets a 401. The container test of the route of finance runs
   with no vault server; the one of main (below) has the vault server.
6. The vault of the other trust domain (`OTHER_VAULT_GATEWAY_URL`,
   `OTHER_VAULT_SERVER`): the probe key gets a 401 on its route, and a
   straight connection to its server fails (R6). Each probe checks the
   other.

**The probe of main** (`agents/main/checks/app/`, daily at 05:47) is the
same probe for the route `main-vault` only, since main has no other route:
items 5 and 6, with its key `main-probe`, and no key with no tool. Its
Secret `mcp-gateway-probe-secret` (`PROBE_KEY`) is read with
`optional: true`: with no Secret, the checks of its key print `skipped: the
key is pending` and pass. A container test
runs it against the gateway of the cluster with this config and the vault
server of main on an empty share
(`observability/tests/test_main_vault.py`).

With the placeholder keys, 3 and 4 print `skipped: the key is pending` and
pass, for as long as the keys are placeholders. The owner's values step
(below) ends that; 1 and 2 always run. The runbook says so at
`AgentPlatformCronJobFailed`. It can not run as the identity of the agent pod (a pod with its
labels would be an agent pod), so the policy of the MCP server, which lets
in the gateway only, is a test in git as well.

## Updates

Renovate opens an update 7 days after a release at the earliest, as for the
MCP server behind it, and a person reviews each one (`impact/high`,
`.github/renovate.json5` and `.github/renovate/autoMerge.json5`). The 7 days
fit: the gateway decides which tool each agent may call, a pulled or bad
release is usually gone in that time, and agentgateway ships a minor about
each month, so an update waits a week at most. The review checks the
release notes for changes of the config format and its defaults (the admin
address, the fields of the log, the API key and authorization policies),
and the container test runs the new image with the config of the repo.

## What is left

- **No tool pin and no argument check** at the gateway (above).
- **Plain HTTP inside the cluster** (above): keys and tool calls cross the
  network between the nodes in clear, as the model calls do.
- **One replica.** While it is down or rolls, the agents have no tools; they
  keep their model and their sandboxes.
- **Stateless MCP.** Each request opens its own session with the MCP
  server, which costs a little time for each call.
- **No rate limit for each client yet.** The simplest version first; a
  limit by client (`localRateLimit`, by `apiKey.client`) is a later step.
- **The probe key** may list the tools of finance and call `get_about`; a
  leak of it gives that much.
- **The vault part of the probe** has no container test until the image
  of the vault server has a release; its first real run is the daily run
  after the merge.
- **A person with exec into the pod** (a cluster admin) can read the config
  through the admin port; it holds no secret.

## The steps of the owner

None of these values goes through an agent or a session.

1. **Make the three keys** on the host, one for each client:
   `openssl rand -hex 32`.
2. **Put each value** with `sops`: the key of `finance` as
   `MCP_GATEWAY_KEY` in `agents/finance/agent/app/secret.sops.yaml` (the
   values step of the cutover of the finance agent), and the two keys of the
   probe as `PROBE_KEY` and `NO_TOOLS_KEY` in
   `agents/finance/checks/app/secret-mcp-gateway-probe.sops.yaml`.
3. **Put each hash** in `app/config/config.yaml`, in place of the
   placeholder of its client: `printf %s "$KEY" | sha256sum` gives the hex
   after `sha256:`. Commit the values and the hashes in one PR.
4. **Check:** the next run of the gateway probe has no `skipped` line, and
   the start of the finance agent prints `values-check result=ok`.

To change a key, do the same with a new key: the old hash goes out with
the old value.
