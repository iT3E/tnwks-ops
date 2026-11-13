# Agent platform

The parts that run AI agents on homelab-wsl, each in its own trust domain.
A trust domain is one namespace with its own sandbox gateway, secrets and
network policies; code that an agent runs goes into a sandbox of its own
domain. Manifests: `kubernetes/apps/ai/agents/`. Only homelab-wsl lists them
(`kubernetes/clusters/wsl/apps/kustomization.yaml`); no other cluster gets
these parts.

| Part | Namespace | Manifests |
| --- | --- | --- |
| Sandbox controller (Agent Sandbox) | `agent-sandbox-system` | `sandbox-controller/` |
| Trust domain of the main personal agent | `agents-main` | `main/` |
| Trust domain of the finance agent | `agents-finance` | `finance/` |
| Trust domain of the workflow agents, and their run receiver | `agents-workflows` | `workflows/` |
| Pilot of a personal agent, on test accounts | `agents-pilot` | `pilot/` |
| Model bridge, next to the model gateway | `ai` | `../model-bridge/` |

## Trust domains

Each trust domain directory has a `namespace.yaml` and a `kustomization.yaml`
that lists its apps. The namespace of each app comes from `targetNamespace`
in its `ks.yaml`, so the namespace (`agents-<name>`) differs from the
directory name.

- **Pod Security:** each namespace enforces the `restricted` level.
- **Default deny:** a `NetworkPolicy` denies ingress and egress for every pod
  but the supervisor pods of sandboxes (label `openshell.ai/managed-by`). The
  sandbox gateway gives each sandbox its own policies when it makes it: the
  workload pod (label `openshell.ai/boundary-role: workload`) gets no egress,
  DNS included, and its supervisor carries the egress that the sandbox policy
  approves.
- **DNS:** a `CiliumNetworkPolicy` lets the pods of the trust domain (the
  gateway, the agent, the checks) resolve names through Cilium's DNS proxy,
  so a policy here can allow a host name (`toFQDNs`). It selects no pod of a
  sandbox (`openshell.ai/managed-by` or `openshell.ai/boundary-role`): a
  sandbox resolves names through its own resolver, which its supervisor
  answers by the sandbox policy, and the supervisor resolves through its own
  egress policy.
- Each app adds its own allow rules next to its manifests.
- The label `agents.home.arpa/trust-domain` names the trust domain of a
  namespace.

## Sandbox gateways

Each trust domain runs its own [OpenShell](https://github.com/NVIDIA/OpenShell)
gateway (`<name>/sandbox-gateway/`), so no trust domain can list, reach or
use the sandboxes and providers of another. It makes the sandboxes of its
domain in its own namespace, through the sandbox controller.

| What a caller in the trust domain uses | Value |
| --- | --- |
| Gateway address | `https://openshell.agents-<name>.svc.cluster.local:8080` |
| Client certificate | Secret `openshell-client-tls` in the same namespace: `ca.crt`, `tls.crt`, `tls.key` |
| Server name in the certificate | `openshell.agents-<name>.svc.cluster.local` (also the short forms) |

- **Chart:** `oci://ghcr.io/nvidia/openshell/helm-chart`, from an
  `OCIRepository` in each namespace. The gateway, supervisor and sandbox
  images follow the chart version. The four copies move in one Renovate PR.
- **Certificates:** cert-manager (`certManager.enabled`) makes a certificate
  authority for each gateway (a self-signed `Issuer`, then a CA `Issuer`),
  the server certificate and the client certificate, valid for a year and
  renewed 30 days before they end. The chart's hook makes only the signing
  key of the sandbox tokens. The key that encrypts provider credentials is a
  Secret the chart makes once and keeps (`helm.sh/resource-policy: keep`).
- **Telemetry:** off (`server.telemetryEnabled: false`); the chart's default
  sends it from the gateway and the sandbox supervisors.
- **Storage:** the gateway's SQLite database (1 GiB) and each sandbox
  workspace (2 GiB) are on `${STORAGE_CLASS_DEFAULT}`.
- **Network policy:** `sandbox-gateway` lets only pods of its own namespace
  reach port 8080: the callers of the trust domain and the supervisors of its
  sandboxes. The listener accepts a connection with no client certificate and
  checks each call, and a service that a sandbox exposes through the gateway
  needs no certificate, so this policy is what keeps other trust domains out.
  The gateway reaches the API server and the pods of its own namespace.

## Sandbox controller

[Agent Sandbox](https://github.com/kubernetes-sigs/agent-sandbox) makes the
pods of a `Sandbox` record (`agents.x-k8s.io/v1beta1`). One controller serves
the cluster.

- Upstream publishes its chart in the repository only (`helm/`), so a Flux
  `GitRepository` reads it at the release tag, and the HelmRelease builds the
  chart again for each new tag (`reconcileStrategy: Revision`, since the
  chart version doesn't move with the release).
- The CRDs come from the chart's `crds/`. Helm leaves those alone on an
  upgrade, so the HelmRelease sets `upgrade.crds: CreateReplace`.
- The controller image is pinned by tag and digest. The tag must match the
  `GitRepository` tag: Renovate opens one PR for each, and both are patch
  updates that merge when CI is green.
- The chart puts its version and `image.tag` in two labels (`helm.sh/chart`,
  `app.kubernetes.io/version`). Neither value is a valid label value here:
  Flux gives the chart the version `0.1.4+<commit>` for each tag, and the tag
  holds the digest. A `postRenderers` patch in the HelmRelease removes both
  labels; no selector uses them.
- The namespace has the `restricted` Pod Security level and a policy that
  denies by default. The controller reaches the API server and DNS only.

## Network policy and the Cluster Mesh

The two WSL clusters are one Cilium Cluster Mesh, and a policy selector with
no cluster label can match pods of the other cluster
([`docs/wsl/multi-site.md`](../wsl/multi-site.md#global-services)). Every
policy here that names a peer also names this cluster
(`io.cilium.k8s.policy.cluster: ${CLUSTER_NAME}`). The policies that the
charts make have no cluster label; they stay local because Cilium 1.19 sets
`clustermesh.policyDefaultLocalCluster: true` by default, and the Cilium
HelmRelease (`kubernetes/apps/kube-system/cilium/app/helmrelease.yaml`) does
not change it.

## The model path

Every model call of an agent goes to the model gateway (LiteLLM,
`http://litellm-app.ai.svc.cluster.local:4000`), with the key of its trust
domain, to the name of its trust domain. Nothing else in a trust domain
reaches a model.

```text
agent (key agents-<name>) -> model gateway, name agents-<name>
  -> claude-opus-bridge-<domain> -> model bridge (bridge key of the domain) -> Claude plan
  or, after a move by a person:
  -> claude-opus-bedrock -> Bedrock
```

| Trust domain | Name and key alias | Entry today | Bridge domain |
| --- | --- | --- | --- |
| `agents-main` | `agents-main` | `claude-opus-bridge-main` | `main` |
| `agents-finance` | `agents-finance` | `claude-opus-bridge-finance` | `finance` |
| `agents-workflows` | `agents-workflows` | `claude-opus-bridge-workflows` | `workflows` |
| `agents-pilot` | `agents-pilot` | `claude-opus-bridge-main` | `main` (the pilot has no bridge key of its own) |

- **One name, one line.** Each name points at one entry, on one line of the
  gateway config. A move between the plan and Bedrock is a change of that
  line, by a person, in a pull request, and a revert is the way back. The
  finance agent follows the main agent in the same pull request.
  `docs/apps/litellm.md` ("The agent platform") has the steps.
- **No fallback.** The gateway config has none. Each key lists its one name,
  so a call to any other name, or a fallback in a request, gets 403. The
  config of each agent names its one model, with no fallback list.
- **Model:** Claude Opus 5.5 on both: `claude-opus-5-5` through the bridge,
  and `us.anthropic.claude-opus-5-5` on Bedrock (Invoke API).
- **Keys:** the agent's copy of its key is `LITELLM_API_KEY` in the Secret
  `agent-secret` of its namespace, which the agent config reads by an env
  SecretRef (`{"source": "env", "provider": "default", "id": "LITELLM_API_KEY"}`); the gateway's copy is in `keys.json`
  (`docs/apps/litellm.md`, "Keys of the agent platform"). No spend limit
  until the pilot has measured the use.

## The model bridge

The model bridge (`kubernetes/apps/ai/model-bridge/`, image from
[iT3E/tnwks-model-bridge](https://github.com/iT3E/tnwks-model-bridge)) takes
a Messages API call from the model gateway and runs it through the Claude
Agent SDK, with the sign-in of the plan. It belongs to no trust domain: it
runs in the namespace of the model gateway, which is its one caller, and
homelab-wsl lists it next to `agents/`.

- **Service:** `http://model-bridge.ai.svc.cluster.local:8080`, with
  `POST /v1/messages` and `GET /health`. The bridge listens on the address
  of its pod.
- **Callers:** each trust domain has its own bridge key, in the `x-api-key`
  header. The entry of the model gateway for a trust domain points at the
  bridge with that key, and the bridge keeps the conversations of each key
  apart.
- **Network policy:** in from the model gateway pods only (and the probes of
  the kubelet), out to `api.anthropic.com` only, with DNS through Cilium's
  DNS proxy.
- **Pod:** one replica, the `restricted` settings, a read-only root file
  system with an `emptyDir` on `/tmp`, a memory request of 512 MiB, and a
  memory limit of 2 GiB for at most 8 live conversations
  (`BRIDGE_MAX_CONVERSATIONS`).
- **Secret** `model-bridge-secret` (`secret.sops.yaml`):
  `CLAUDE_CODE_OAUTH_TOKEN` (the token of `claude setup-token`; it lives one
  year), and `BRIDGE_KEY_MAIN`, `BRIDGE_KEY_FINANCE` and
  `BRIDGE_KEY_WORKFLOWS` (32 characters or more each, one for each trust
  domain, not starting with `sk-ant-`). The model gateway gets the same
  three keys for its entries, in `litellm-secret`.
- **Stop:** a `preStop` sleep of 5 s, then SIGTERM. The bridge drains:
  `/health` answers 503, a new call gets a 503 that a
  caller retries, and the calls in work run to their end for up to
  `BRIDGE_DRAIN_SECONDS` (75 s). The grace period is 90 s. A rollout starts
  the new pod before it stops the old one (`maxSurge: 1`,
  `maxUnavailable: 0`).
- A restart ends each live conversation; the next call of a conversation
  goes in with its history as one text. A `rebuild` line in the log tells
  why a call matched no live conversation. The repository README has the
  contract and the log format.

## The pilot agent

One personal agent in `agents-pilot`, on a test Telegram bot and a test
Signal number (`pilot/agent/`, `pilot/signal-daemon/`). The product is
OpenClaw, in the agent image
([iT3E/tnwks-agent-image](https://github.com/iT3E/tnwks-agent-image)), whose
README has the contract of the pod.

**Signal is off for now.** The pilot starts on Telegram only. The Signal
daemon stays in the repository but out of `pilot/kustomization.yaml`, and
the config of the agent has no Signal channel. When the test Signal number
exists, one change turns it on: the daemon in the kustomization, the
`dependsOn` of the agent, the channel and its plugin in `openclaw.json`,
the rule to the daemon in the agent's network policy, and the two Signal
keys in the secret. Then link the number (below).

| Part | What it is |
| --- | --- |
| `agent` (Deployment) | The gateway of the product, on loopback port 18789, `Recreate`, on the second worker, memory request 2 GiB and limit 4 GiB |
| `agent-config` (ConfigMap) | `openclaw.json`, a read-only file in the pod at `/home/node/config/` (`OPENCLAW_CONFIG_PATH`, with `OPENCLAW_CONFIG_READONLY=1`). Applied with no `${VAR}` substitution (`agents-pilot-agent-config`) |
| `agent-state` (PVC, 10 GiB) | Mounted at `/home/node/data`: `state/` (the state of the product, `OPENCLAW_STATE_DIR`, with the workspace in `state/workspace/`) and `archives/` |
| `signal-daemon` (Deployment and Service) | signal-cli, native build, as an HTTP daemon on port 8080, in multi-account mode |
| `signal-state` (PVC, 1 GiB) | Mounted at `/var/lib/signal-cli`: `data/` (the account state, `--config`) and `archives/` |

Each container mounts a volume at its root, with no `subPath`: on this
cluster a `subPath` of a `local-path` volume comes out owned by root
(`talos/wsl/README.md`). The `prepare` init container makes the two
directories as the pod user, with mode 0700. The archives are outside the
state directory, so they are in no backup of the product and in no sandbox:
the sandbox plugin copies the workspace only.

- **Config.** The product's sandbox backend is OpenShell, for every session
  (`sandbox.mode: all`), at the gateway of this trust domain. One model,
  `litellm/agents-pilot`, with no fallback list, and the provider list
  replaced by the model gateway alone. The model entry names its Claude
  model (`params.canonicalModelId: claude-opus-5-5`), so the product uses
  the rules of that model: it keeps each turn's context in the history,
  which lets the model bridge match the conversation at the next turn and
  keeps the prompt cache, and it sends the effort as set. Change it in the
  same PR as a move of the name to a different model. The tools `code_execution`,
  `file_fetch`, `file_write`, `dir_list`, `dir_fetch` and `gateway` (the
  agent's own reads and changes of its config) are denied.
  `autoProviders` of the sandbox plugin is off, so no credential of the pod
  goes to a sandbox as a provider. Each channel takes messages from its one
  allowed sender, and no group. The config can't be changed from a chat
  (`commands.config: false`, `configWrites: false`). The hosted model
  catalog is not fetched (`models.catalogRefresh.enabled: false`), and
  neither are helper binaries (`OPENCLAW_OFFLINE=1`). The gateway still
  asks for the hosted plugin catalog once at start, which has no setting at
  this release; the network policy blocks it, and the product uses its
  bundled catalog.
- **The hold of a message.** A plugin file in the config
  (`agent/config/plugins/tnwks-message-hold.ts`, loaded from
  `plugins.load.paths`) asks the owner in the chat before the message tool
  sends to an address that the agent names, or broadcasts: allow once, or
  deny, with a deny after 10 minutes. A normal reply names no address and
  is not held. The exec approvals of the product cover host commands only,
  and the pilot runs each command in a sandbox, so no command is held. Each
  decision logs `message-hold decision=<decision>` in the `agent` container.
- **The scheduled turn** is the heartbeat of the product
  (`agents.defaults.heartbeat`): once a day, a turn in its own session with
  one shell command in a sandbox, and no message to a channel. Its traces go
  to the collector (alloy, OTLP).
- **Secret** `agent-secret` (env vars of the pod):

  | Key | Value |
  | --- | --- |
  | `OPENCLAW_GATEWAY_TOKEN` | A random token for the gateway of the product |
  | `LITELLM_API_KEY` | The key `agents-pilot` of the model gateway (the same value as in `keys.json`) |
  | `TELEGRAM_BOT_TOKEN` | The token of the test bot |
  | `TELEGRAM_ALLOW_FROM` | The numeric Telegram user id of the one sender the bot takes |
  | `SIGNAL_ACCOUNT` | Not set while Signal is off. The test Signal number, in E.164 form |
  | `SIGNAL_ALLOW_FROM` | Not set while Signal is off. The one Signal number the agent takes messages from, in E.164 form |

  The config names each one as `${NAME}`; the product fails to start when
  one is missing.
- **Client certificate** of the sandbox gateway: Secret
  `openshell-client-tls`, copied by the `prepare` init container into the
  directory the CLI reads.
- **Stopped sandboxes.** When the sandbox gateway starts again (a node
  restart, a new release), it stops the sandboxes it had, and the product
  doesn't start a stopped one. The `sandbox-start` container
  (`agent/config/scripts/sandbox-start.sh`) waits at most 5 minutes for the
  gateway when the pod starts, then each minute starts every sandbox of the
  product (names `oc-`) in phase `Stopped`, with the CLI and the client
  certificate above. It logs one line for each start,
  `sandbox-start name=<name> result=ok|failed`, and nothing else. To check:
  `kubectl -n agents-pilot logs deploy/agent -c sandbox-start`, and
  `kubectl -n agents-pilot get sandboxes.agents.x-k8s.io` shows each one
  `Ready`. A sandbox stopped by hand starts again within a minute; delete it
  to keep it down.
- **Network policy.** No pod comes in. Out: the model gateway, the sandbox
  gateway of this namespace, alloy, and
  `api.telegram.org`. The Signal daemon takes the agent pod only (and the
  kubelet's probes), and reaches `*.signal.org` only.

### State, archives and backups

| When | What | Kept |
| --- | --- | --- |
| Each start of the agent pod | `archive` init container (`agent/config/scripts/archive-state.sh`): a `tar.gz` of `state` in `archives/start/`, named with the release of the product and the time, read back against its checksum. Then `doctor` migrates the state, on a copy of the config | 14 days |
| Each day | `backup` container (`agent/config/scripts/daily-backup.sh`): once the gateway is ready, `openclaw backup create --verify` to `archives/daily/`. The command checks that the gateway owns the state, and it can only see the gateway process in the same PID namespace, so the pod shares one (`shareProcessNamespace: true`) | The last 7 |
| Each day, after that backup | The same container: the SHA-256 of `AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md` and each file under `skills/`, compared with the list of the day before (`archives/instruction-files/`). One log line for each file that is new, changed or gone, then `instruction-check result=...` | 30 days of lists |
| Each start of the Signal daemon | `archive` init container: a `tar.gz` of `data` in `archives/start/`, with the signal-cli release | 14 days |
| Nightly | kopiur copies each volume, with its archives (`*/app/backup.yaml`, `docs/operations/backups.md`) | As the other volumes |

A failed archive step stops the start of its pod. A failed daily backup ends
the `backup` container, so the restart shows on the pod. The first archive
with the name of a release holds the state from before that release; restore
that one after a failed update.

### Link the test Signal number

Once, after the first start of the daemon (Signal is off for now, see
above). The daemon is in multi-account mode, so it runs with no account. With the test phone at hand:

```sh
kubectl -n agents-pilot port-forward svc/signal-daemon 8080:8080 &
curl -s localhost:8080/api/v1/rpc -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"startLink","params":{}}'
# Show the deviceLinkUri of the answer as a QR code (for example
# `qrencode -t ansiutf8 '<uri>'`), and scan it in Signal on the test phone:
# Settings, Linked devices.
curl -s localhost:8080/api/v1/rpc -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"finishLink","params":{"deviceLinkUri":"<uri>","deviceName":"agents-pilot"}}'
```

Then start the agent pod again
(`kubectl -n agents-pilot rollout restart deploy/agent`), so the Signal
channel finds the account.

## The run receiver

The one entry for a run of a workflow agent, in `agents-workflows`
(`workflows/run-receiver/`). A caller sends a token, the name of an agent,
the input and a key. The receiver makes a run job from the template of that
agent, and gives back the answer of the run. The run job starts the agent in
a sandbox of this trust domain that runs one time, sends it the input, and
deletes the sandbox. The image is `ghcr.io/it3e/tnwks-workflow-agents`
(public); the API and the image contract are in the README of
[iT3E/tnwks-workflow-agents](https://github.com/iT3E/tnwks-workflow-agents).

| What a caller uses | Value |
| --- | --- |
| Address | `http://run-receiver.agents-workflows.svc.cluster.local:8080` |
| A run | `POST /runs` with `Authorization: Bearer <token>` and `{"agent": "<name>", "input": {...}, "key": "<key>", "wait": true}` |
| The state of a run | `GET /runs/<key>`, with the token |
| Token | `RUN_RECEIVER_TOKEN` in the Secret `run-receiver-secret` (`app/secret.sops.yaml`). The workflow that calls the receiver holds the same value |

- **The list of agents** (`app/agents.yaml`, the ConfigMap
  `run-receiver-agents`) has the Job template of each agent: the image, the
  sandbox policy, the providers and the variables of the agent. The caller
  sets the name of an agent, the input and the key, and nothing else of the
  Job. The Job name comes from the key, so a second call with the same key
  gets the same run. A Job stays 7 days. The receiver refuses to start when
  a template breaks a rule of the image.
- **The Renovate reviewer** is the one agent. It reviews pull requests of
  `iT3E/tnwks-ops` only (`REVIEWER_REPOSITORIES`), and posts one comment for
  each head: it never merges, approves, pushes or changes a label. It calls
  the Messages API of the model gateway (`POST /v1/messages`) with the name
  `agents-workflows` and the effort `high` (`AGENT_ENV_REVIEW_EFFORT` in
  `agents.yaml`), through the provider `model-gateway`, and GitHub through
  the provider `github-reviewer`.
- **The providers** are in the store of the sandbox gateway. The CronJob
  `workflow-providers` (`app/providers.yaml`, every 15 minutes at :07, :22,
  :37 and :52) imports or updates each provider profile of `app/providers/`,
  gets a new installation token of the GitHub App of the reviewer
  (`app/scripts/github_app_token.py`), and creates or updates each provider.
  To run it now,
  `kubectl -n agents-workflows create job --from=cronjob/workflow-providers providers-now`.

  | Provider (profile) | Credential | What the proxy of a sandbox allows with it |
  | --- | --- | --- |
  | `model-gateway` (`workflow-model-gateway`) | `MODEL_GATEWAY_KEY` of the Secret `workflow-providers-secret`: the key `agents-workflows` of the model gateway ([litellm.md](litellm.md#keys-of-the-agent-platform)) | `POST /v1/messages` at `litellm-app.ai.svc.cluster.local:4000` |
  | `github-reviewer` (`workflow-github-reviewer`) | `GITHUB_TOKEN`: an installation token of the GitHub App of the reviewer, for `iT3E/tnwks-ops` only, with contents read, pull requests write (the comment) and checks read | `api.github.com`: `GET` on `pulls/*`, `pulls/*/files`, `contents/**`, `issues/*/comments` and `commits/*/check-runs`, and `POST issues/*/comments`, under `/repos/iT3E/tnwks-ops` |

  The sandbox holds a placeholder for each credential, and the proxy puts
  the value in the `Authorization` header of those calls only. The paths
  have the case of the repository name, `iT3E/tnwks-ops`, so a caller sends
  `repo` in that case.
- **The GitHub App** of the reviewer is its own identity at GitHub. GitHub
  has no API that registers an App or changes its permissions, so the owner
  does that in the browser, and git declares the rest.
  - **Registration.** The owner opens this link, which fills in the form
    (GitHub docs, "Registering a GitHub App using URL parameters"): a private
    App with no webhook, contents read, pull requests read and write, and
    checks read:
    <https://github.com/settings/apps/new?name=tnwks-renovate-reviewer&url=https://github.com/iT3E/tnwks-ops&public=false&webhook_active=false&contents=read&pull_requests=write&checks=read>.
    Then the owner makes a private key, and installs the App on
    `iT3E/tnwks-ops` only ("Only select repositories").
  - **The list in git.** What the reviewer may do at GitHub is in one place:
    `GITHUB_REPOSITORY` and `GITHUB_APP_PERMISSIONS` in `app/providers.yaml`
    (`contents:read,pull_requests:write,checks:read`). Each token is for
    that one repository with exactly those permissions, whatever the App was
    granted; the job fails if GitHub's answer grants more.
  - **The drift check.** Each run of the job reads the permissions of the
    App (`GET /app`) and the permissions and the repository selection of its
    installation, compares them with the list, and logs one line:
    `app-drift result=ok|drift detail=<names>` (for example
    `extra:app:administration` or `extra:installation:repository_selection`).
    More than the list is drift: the line is for an alert, and the token is
    still scoped. Less than the list (`missing:...`) fails the job before any
    token, with a line that says what to grant.
  - **A change of permissions** is two steps: change the list in git, in a
    pull request, and the owner's click in the App settings (and the
    acceptance on the installation). Until both are done, the drift line
    says which side is ahead.
  - The App ID is `GITHUB_APP_ID` in `app/providers.yaml`. The private key
    is `GITHUB_APP_PRIVATE_KEY` in `workflow-providers-secret`, a file in the
    providers job that only `openssl` reads. Each run signs a JWT of the App
    (RS256, 9 minutes). No key or token is on a command line or in a log.
- **The token life.** An installation token lives one hour. A sandbox keeps
  the credential of its start for its whole life: after a provider update,
  the gateway still resolves the placeholder of a running process to the
  value of its start, while that revision is in its history of 8 (OpenShell
  0.1.8, `crates/openshell-core/src/provider_credentials.rs:16` and `:776`;
  docs, "Runtime Limitations" of the provider profiles). A run job lives 40
  minutes at most, so with a new token every 15 minutes a token is at most
  15 minutes old when a run starts and 55 minutes old when it ends: a run
  always has a valid token to its end. A revision that leaves the history
  resolves to the current value, which is newer. A new run gets the newest
  token.
- **Roles.** The service account `run-receiver` can create and get Jobs,
  list pods and read pod logs in this namespace, and nothing more. The run
  jobs and the sweep run as `workflow-run`, with no role and no token: they
  call the sandbox gateway with its client certificate.
- **The admission policy** (`run-receiver/admission/`, the
  ValidatingAdmissionPolicy `workflow-run-jobs` and its binding to this
  namespace). A right to make a Job is a right to read each secret of the
  namespace, because the Job sets its pod. So each Job that the receiver
  sends must match the rules of a template: an agent of the list with its
  own providers, one container from the image of the workflow agents pinned
  by digest (and a sandbox of the same image), the service account
  `workflow-run` with no token, no secret but the client certificate, one
  try, and a time limit of one hour at most. The two objects are
  cluster-scoped, so they have a Flux Kustomization of their own with no
  `targetNamespace` (`agents-workflows-run-admission`), and the receiver
  depends on it. A new agent adds its providers to the policy in the same
  pull request as its entry in `agents.yaml`.
- **The sweep** (CronJob `workflow-sweep`, every 15 minutes) deletes each
  run sandbox that is older than one hour, by its name and label, through
  the sandbox gateway. A run job deletes its own sandbox; the sweep is for
  a run job that stopped before it could.
- **Network policy.** In: the runner pods of the `openclaw-hooks` scale set
  in `tools`, a pod `app.kubernetes.io/name: workflow-schedule` of this
  namespace (a schedule, later), and the kubelet's probes. Out: the API
  server only. The run jobs and the sweep reach the sandbox gateway of this
  namespace only (the providers job also reaches `api.github.com`, for the
  token of the App), which also relays the SSH path to a sandbox. The agent
  runs in the sandbox, whose egress is its sandbox policy and its providers.
- **A test call by hand** needs no rule: a `kubectl port-forward` reaches
  the pod through the kubelet, not from another pod, and the receiver
  listens on each address of the pod.

```sh
kubectl -n agents-workflows port-forward svc/run-receiver 8080:8080 &
curl -s localhost:8080/healthz
# With no token, a run fails (401):
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8080/runs -d '{}'
```

## Checks

Two CronJobs in each trust domain test the boundaries every day
(`checks/app/`, shared by the trust domains; each `<name>/checks/ks.yaml`
applies it to its namespace). A failed check fails its Job, and the Job
pod's log goes to Loki.

| CronJob | Schedule | What must hold |
| --- | --- | --- |
| `platform-check-isolation` | 04:17 | The check pod connects to its own sandbox gateway and to no other trust domain's gateway, and its client certificate works there (`sandbox list`) |
| `platform-check-probe` | 04:47 | A new sandbox of the domain reaches nothing that no sandbox policy names: the API server, the internet, its own gateway, the model gateway and the metadata address, direct or through the sandbox proxy, and the cluster DNS Service directly over UDP and TCP (any answer fails). It holds no service account token. While the sandbox runs, its two pods (workload and supervisor) must be found running; the probe logs each pod with its node, then deletes the sandbox |

- The check pods may try every trust domain's gateway on port 8080, so the
  isolation check tests each gateway's own policy, not the check pod's.
- The OpenShell CLI is a release binary, fetched by an init container at a
  pinned version and checked against its SHA-256. Move `CLI_VERSION` and
  `CLI_SHA256` (`checks/app/cronjobs.yaml`, both CronJobs) with the chart
  version of the sandbox gateways; Renovate doesn't track them.
- The probe finds the pods of its sandbox through the `Sandbox` record
  (label `openshell.ai/sandbox-name`), whose `openshell.ai/sandbox-id` the
  two pods carry in lower case as `openshell.ai/boundary-pair`. A Role lets
  it get and list `Sandbox` records and pods in its own namespace, and
  nothing more.
- Nothing places the two pods of a sandbox: the scheduler picks each node
  on its own, so they can share a node. The probe logs `nodes` and
  `different_nodes` and doesn't fail on one node.
- To run one now: `kubectl -n agents-<name> create job --from=cronjob/platform-check-isolation check-now`.
