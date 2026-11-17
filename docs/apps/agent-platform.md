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
| Alert rules of the platform, and the heartbeat check | `monitoring` | `monitoring/` |

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
  egress policy. Nor a pod with the label `agents.home.arpa/dns`: it has a
  rule of its own for the names it needs only
  ([The finance app](#the-finance-app)).
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

## The backup mover and ConfigMaps

The mover roles of kopiur give `get` and `patch` on each ConfigMap of the
namespace where a mover runs. In a trust domain that reaches the config and
the scripts of the agent pod, and Reloader starts the pod again on a change.
At kopiur 0.10.20 a mover writes a ConfigMap only for the result of a
repository bootstrap or discovery run, and those runs go in `kopiur-system`.
A snapshot or a restore writes none.

So the admission rule `agents-backup-mover-configmaps`
(`kubernetes/apps/ai/agents/admission/`) refuses each create, change or
delete of a ConfigMap by a mover ServiceAccount (`kopiur-mover`,
`kopiur-snapshot-replication-mover`, `kopiur-stream-mover`) in a namespace
with the label `agents.home.arpa/trust-domain`. Reads, and the status writes
of the mover, are not affected. A namespaced `Repository` in a trust domain
would bootstrap there and need a change of the rule.

What the rule does not change: the mover still gets the credentials that
kopiur projects before each run. Those are the session and the password of the
repository of the agent trust domains, which reach `kopiur-agents/` only
([Backups of the agent trust domains](#backups-of-the-agent-trust-domains)).

A refusal fires `AgentBackupMoverConfigMapDenied` ([Alerts](#alerts)). The
API server counts the refusals of a policy
(`apiserver_validating_admission_policy_check_total`), and the scrape of the
API server on homelab-wsl keeps the series of this policy with
`enforcement_action="deny"` and no other
(`kubernetes/clusters/wsl/apps/kustomization.yaml`).

To check: `kubectl get validatingadmissionpolicy agents-backup-mover-configmaps`,
and after the next snapshot of a trust domain,
`kubectl -n agents-pilot get snapshots.kopiur.home-operations.com` shows the
newest one `Succeeded`.

## Backups of the agent trust domains

The snapshots of the trust domains go to a Kopia repository of their own, not
to the shared repository of the other apps
(`kubernetes/apps/kopiur-system/agents-repository/`, homelab-wsl only):

| Part | Value |
| --- | --- |
| ClusterRepository | `agents`, prefix `kopiur-agents/` of the backup bucket. `allowedNamespaces`: `kopiur-system` and the trust domains with a policy (`agents-pilot` now) |
| Password | `kopiur-system/kopiur-repository-agents` (SOPS), its own value, on the off-site media |
| S3 session | CronJob `aws-backup-credentials-agents`: certificate CN `homelab-wsl-agent-backups` to the role `tnwks-rolesanywhere-backups-agents`, which lists, reads and writes `kopiur-agents/` and nothing else (`rolesanywhere_backups_prefixed` in `environments/prod/aws/main.tf`). Secret `aws-backup-sts-agents` |
| Policies | `agents-pilot`: `agent-state-v2` (the agent) and `signal-state` (the Signal daemon), in `pilot/*/app/backup.yaml` |
| Finance | Its own repository: ClusterRepository `agents-finance`, prefix `kopiur-finance/`, password `kopiur-system/kopiur-repository-agents-finance`, role `tnwks-rolesanywhere-backups-agents-finance` (certificate CN `homelab-wsl-agents-finance-backups`), Secret `aws-backup-sts-agents-finance`, policy `agent-state` in `finance/agent/app/backup.yaml`. Set up in steps, and not all applied yet ([`backups.md`](../operations/backups.md#the-finance-repository)) |

kopiur copies this session and this password into the namespace of the trust
domain before each mover run. So a mover there, and anything that reads the
Secrets of that namespace, reaches the snapshots of the trust domains and no
other snapshot: not those of the household apps in `kopiur/`, and not the
password that decrypts them. The shared repository does not allow
`agents-pilot` once the move ends ([Move a trust domain to another
repository](#move-a-trust-domain-to-another-repository)).

One repository for each trust domain: a mover of one trust domain reaches
the snapshots of its own trust domain only. The repository `agents` holds the
pilot. Finance has its own (one more entry of `rolesanywhere_backups_prefixed`,
one more directory like `agents-repository/`, `agents-finance-repository/`),
and each later trust domain gets one the same way before its first policy.
No prefix may start with the name of another one (`kopiur-finance/`, not
`kopiur-agents-finance/`), as the list deny of the bucket policy matches the
start of a prefix.

The snapshots of the pilot agent from before the move (policy `agent-state`)
stay in the shared repository. The policy was replaced, not edited: kopiur
deleted its Snapshot objects with it and kept the snapshots
(`deletion.onPolicyDelete: Retain`, the default). The runbook restores one by
its kopia identity ("A snapshot from before the move").

### Move a trust domain to another repository

Two pull requests, in this order:

1. Replace each SnapshotPolicy of the namespace with one of a new name on the
   new repository, and keep the namespace in the `allowedNamespaces` of the
   old repository. Flux deletes the old policy; kopiur keeps its snapshots.
2. When `kubectl -n <namespace> get snapshotpolicies` no longer shows the old
   policy, remove the namespace from the `allowedNamespaces` of the old
   repository.

Not both in one pull request. A policy that names a ClusterRepository can
finish its delete only while its namespace is allowed there: the admission
webhook of kopiur (0.10.20) checks that list on every update of a
SnapshotPolicy, and the removal of the `policy-cleanup` finalizer is an
update. Without the grant the old policy stays `Terminating`, the controller
retries about once a minute, and the namespace gets `BlockedOnGrant` and
`KubeApiError` events. No alert fires and the new backups are not hit. The
pilot moved in one pull request on 2026-11-15, and its old policy stayed
`Terminating` until the grant came back.

Between the two pull requests, the change of the old repository makes it list
its snapshots again, and kopiur makes a `Discovered` Snapshot object in the
namespace for each snapshot of the old policy (forced `Retain`, owned by the
old repository). Step 2 does not remove them: kopiur expires a discovered
object by its age, its count or a snapshot that is gone, not by a lost grant.
They do no work and hold no data; they list the snapshots from before the
move. Deleting one never deletes its snapshot. They go when the old snapshots
are deleted.

The alerts of these backups: a failed run of the refresher is
`KubeJobFailed`; a repository in `Failed` or `Degraded` is
`KopiurRepositoryNotReady` (the chart, critical); a repository that stays
`Pending` or `Initializing` is `AgentBackupRepositoryNotReady` ([Alerts](#alerts));
a failed first snapshot is `KopiurSnapshotFailed` and `KopiurLastBackupFailed`.

To check: `kubectl get clusterrepository agents` is `Ready`, and
`kubectl -n agents-pilot get snapshotpolicies,snapshots.kopiur.home-operations.com`
shows the newest snapshot of `agent-state-v2` `Succeeded`.

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
- **One attempt at the gateway.** The gateway sends a call to the bridge
  one time, with no retry of its own (a decision of the owner, 2026-11-14;
  `docs/apps/litellm.md`, "One attempt to the bridge"). The caller retries:
  the Anthropic SDK 2 times for a 429 or a 5xx, and an agent framework can
  retry on top of it (Strands retries a 429 up to 6 attempts in all, from
  4 s to 64 s apart).
  At the usage limit of the plan one model call of a workflow agent reaches
  the bridge 18 times at most in one run (3 by the SDK times 6 by Strands).
  Before this change it was 54. A caller that makes its 3 attempts for one
  review starts 3 runs, so one review can still reach 54 over the three.
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
  `/health` answers 503, a new call gets a 503 that the
  SDK of the caller retries (the gateway does not), and the calls in work run to their end for up to
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
`dependsOn` of the agent, the channel, its plugin and its id in
`plugins.allow` in `openclaw.json`,
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
  agent's own reads and changes of its config) are denied, and so is
  `github_publish`. The product puts that tool in a sandboxed session by
  default, and it opens a draft pull request from the session's
  repository. It would exist only with a repository workspace in the
  session, a GitHub credential on the gateway and a route to GitHub, and
  the pilot has none of these. The deny keeps the pilot's outward paths
  (R4) on a written rule, not on a missing credential and a network rule.
  The agent has no `message` tool: the product leaves it out of a sandboxed session by
  default (`tools.sandbox.tools`), and every session of the pilot is
  sandboxed. So the agent answers in the chat that wrote to it, and can't
  send to an address that it names. Adding `message` to that list is a
  design change: the exec approvals of the product cover host commands
  only, so a hold of an outward message needs a plugin hook
  (`before_tool_call` with `requireApproval`).
  **Plugins.** `plugins.allow` names the plugins that the agent loads:
  `brave` (web search), `diagnostics-otel` (the traces), `memory-core`
  (memory search), `openshell` (the sandbox backend) and `telegram` (the
  channel). Without the list, the product enables each plugin that it finds
  by default: the bundled ones, and any plugin in the state directory
  (`state/extensions`, or an install record in the state database)
  (`src/plugins/config-activation-shared.ts:138` and `:170` at 2026.11.3). With it, a plugin that is not in the list does not
  load, in `doctor` or in the gateway. The list leaves out 13 bundled
  plugins that loaded before it: the model providers `anthropic`, `openai`,
  `xai` and `ollama` (the model and the embeddings go through the model
  gateway, with the product's own clients), and `browser`, `canvas`,
  `cua-computer`, `device-pair`, `file-transfer`, `geolocation`, `github`,
  `linux-node` and `talk-voice`, which give no tool to a sandboxed session
  or are denied (`file-transfer`). A sandboxed session offers and finds the
  same tools with and without the list. A new plugin needs its id in the
  list, in the same change.
  `autoProviders` of the sandbox plugin is off, so no credential of the pod
  goes to a sandbox as a provider. Each channel takes messages from its one
  allowed sender, and no group. The config can't be changed from a chat
  (`commands.config: false`, `configWrites: false`). The hosted model
  catalog is not fetched (`models.catalogRefresh.enabled: false`), and
  neither are helper binaries (`OPENCLAW_OFFLINE=1`). The gateway still
  asks for the hosted plugin catalog once at start, which has no setting at
  this release; the network policy blocks it, and the product uses its
  bundled catalog.
- **The scheduled turn** is the heartbeat of the product
  (`agents.defaults.heartbeat`): once a day, a turn in its own session with
  one shell command in a sandbox, and no message to a channel. Its traces go
  to the collector (alloy, OTLP). The CronJob `agent-heartbeat-check` in
  `monitoring` asks Tempo each morning for a completed heartbeat in the last
  26 hours ([Alerts](#alerts)).
- **Memory search.** The agent searches its memory files (`MEMORY.md` and
  `memory/*.md` in its workspace) with `memory_search`, and reads a part of
  one with `memory_get` (`memory.search` in the config). The embeddings come
  from the model gateway: provider `openai-compatible`, base URL
  `http://litellm-app.ai.svc.cluster.local:4000/v1`, the key `agents-pilot`,
  and the entry `agents-pilot-embeddings`, which runs on the Ollama of the
  host ([litellm.md](litellm.md#the-entries)). So the text of the memory
  goes to no vendor.
  - The gateway process of the agent pod makes these calls, not a sandbox.
    The network policy already lets the pod reach the model gateway on port
    4000, so the search needs no new rule.
  - The text sent is the memory files, in chunks, when they change, and the
    query of each `memory_search`. Past conversations are not sent:
    `sources` is `["memory"]` and `rememberAcrossConversations` is false.
  - The index is in `state/agents/main/agent/openclaw-agent.sqlite` on the
    volume `agent-state`, so the archives and the nightly copy hold it
    ([State, archives and backups](#state-archives-and-backups)). It holds
    the text of the chunks as well as their vectors.
  - The product leaves the two tools out of a sandboxed session by default,
    so `tools.sandbox.tools.alsoAllow` names them. It names the two tools
    and not `group:memory`, because at this release that group also gives
    `personal_instructions`.
  - The traces hold no tool input or output: the config doesn't set
    `diagnostics.otel.captureContent`.
  - If the model gateway is down, a search finds nothing and the chat goes
    on. The start of the pod doesn't need the model gateway.
  - **The index at each start.** At this release the product does not
    rebuild the index by itself when the embedding provider or model
    changes. It records the provider and model in the index, and a
    mismatch with the config only marks the index stale: each search
    returns `stale` and no result ("index was built for model ...,
    expected ..."). It does this by design, so that no change of config
    starts a cost that nobody asked for. Its rebuild command
    (`openclaw memory index`) refuses to run while the gateway owns the
    state, so it can't run in the `agent` container.
  - So the `memory-index` init container
    (`agent/config/scripts/memory-index.sh`) runs `memory index --agent
    main` at each start of the pod, after `doctor` and before the gateway.
    A stale index gets a full rebuild. A valid one gets an incremental
    pass, which embeds only the changed files. The product keeps a cache
    of embeddings, so a rebuild embeds only text that it has not seen.
    The step has a limit of 180 seconds. Its only secret is
    `LITELLM_API_KEY`. It prints one line, `memory-index result=ok` or
    `memory-index result=failed`, and nothing of the CLI's own output.
  - A later change of the embedding model needs no step by hand: the
    config change restarts the pod (Reloader), and the step rebuilds.
  - When the step fails (the model gateway or the host's Ollama is down,
    or the limit fires), the pod still starts. The index stays as it was,
    so a stale index stays stale until the next start that ends with
    `memory-index result=ok`. To check:
    `kubectl -n agents-pilot logs deploy/agent -c memory-index`. A
    `result=failed` line fires `AgentMemoryIndexFailed` until that start.
  - To check a search with no chunk text, in the `agent` container (the
    gateway answers it): `openclaw memory search "<word>" --agent main
    --json`, keeping only `path`, `score` and `vectorScore` of each
    result. A ready index gives results with a `vectorScore`; a stale one
    gives `stale: true` and a warning.
- **Web search.** The agent searches the web with `web_search`, through the
  Brave Search API (the plugin `@openclaw/brave-plugin` of the agent image,
  `tools.web.search` in the config). The gateway process of the agent pod
  makes the call, not a sandbox.
  - **A verified official install.** A sandboxed session uses a search
    provider only when it is bundled with the product or is a verified
    official install, and each session of the pilot is sandboxed. A plugin
    from `plugins.load.paths` is neither, and the Brave plugin is not
    bundled. So the image holds the plugin in an npm cache, and the
    `official-plugins` init container installs it from there with the
    product's own installer, with no network, before `doctor`. The install
    record in the state database makes it a verified official install, and
    its code is in `state/npm/`. The config enables it in
    `plugins.entries` (the image README, "The official plugins"), and
    `plugins.load.paths` names no path for it.
  - **The code on the state volume is replaced at each start.** The
    gateway runs as the same user and can write the state volume, and the
    product checks no hash of installed code at a start: the trust comes
    from the install record alone, which the gateway can write too. So the
    step removes `state/npm/` and installs the plugin again from the image
    at each start (some 8 seconds), and the record with it. A restored
    archive does not bring back old code either. The step also stops the
    start when the state holds an install record of another plugin or a
    file in `state/extensions` (the runbook, "The official plugins step
    fails").
  - What goes to the vendor: the query that the model writes for the tool,
    and its filters (count, country, language, dates), with the key. The
    conversation does not go. The answer is a list of titles, URLs and
    snippets.
  - The vendor keeps the query logs for up to 90 days on its self-serve
    plans, and its terms allow only transient storage of the results.
  - What the pod keeps: the gateway caches a result in memory for 15
    minutes. The query and the result are also in the session store
    (`state/agents/main/agent/openclaw-agent.sqlite`), like each tool call,
    so the archives, the daily backup and the nightly copy hold them
    ([State, archives and backups](#state-archives-and-backups)). The
    owner accepted this for the pilot, which runs on test accounts. A
    trust domain with real accounts needs that decision again.
  - The traces hold no query: the config doesn't set
    `diagnostics.otel.captureContent`.
  - The product leaves `web_search` out of a sandboxed session by default,
    so `tools.sandbox.tools.alsoAllow` names it. `web_fetch` stays out.
  - **The key** is `BRAVE_API_KEY` in the Secret `agent-search-secret`
    (`kubernetes/apps/ai/agents/pilot/agent/app/secret-search.sops.yaml`).
    Only the `agent` container gets it, as an env var: not the init
    containers (`memory-index` included), not the backup container, and no
    sandbox. It is one key, shared with an OpenClaw deployment outside the
    cluster. So a leak or a rotation touches both, and the usage counts of
    the vendor are for both.
  - **Rotate:** make a new key at the vendor, put it in the file with
    `sops <file>` (the file above) in a PR, merge, and change the other
    deployment too. Reloader starts the pod again when the Secret
    changes. Then revoke the old key.
  - **Turn off:** set `tools.web.search.enabled` to `false` in
    `openclaw.json` and take `web_search` out of `alsoAllow`. To remove it,
    also take out the plugin entry, the network rule and
    the Secret. With a wrong key, the gateway starts and each search fails
    with an error from the vendor (401).
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
  gateway of this namespace, alloy, `api.telegram.org` and
  `api.search.brave.com`, each on its one port. The Signal daemon takes the
  agent pod only (and the kubelet's probes), and reaches `*.signal.org`
  only.

### State, archives and backups

| When | What | Kept |
| --- | --- | --- |
| Each start of the agent pod | `archive` init container (`agent/config/scripts/archive-state.sh`): a `tar.gz` of `state` in `archives/start/`, named with the release of the product and the time, read back against its checksum. Then `official-plugins` installs the Brave plugin again from the image (it stops the start on a plugin in the state that the image did not put there), `doctor` migrates the state, on a copy of the config, and `memory-index` brings the memory index up to date (see "The index at each start" above) | 14 days |
| Each day | `backup` container (`agent/config/scripts/daily-backup.sh`): once the gateway is ready, `openclaw backup create --verify` to `archives/daily/`. The command checks that the gateway owns the state, and it can only see the gateway process in the same PID namespace, so the pod shares one (`shareProcessNamespace: true`) | The last 7 |
| Each day, after that backup | The same container: the SHA-256 of `AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md` and each file under `skills/`, compared with the list of the day before (`archives/instruction-files/`). One log line for each file that is new, changed or gone, then `instruction-check result=...` | 30 days of lists |
| Each start of the Signal daemon | `archive` init container: a `tar.gz` of `data` in `archives/start/`, with the signal-cli release | 14 days |
| Nightly | kopiur copies each volume, with its archives, to the repository of the agent trust domains (`*/app/backup.yaml`, [Backups of the agent trust domains](#backups-of-the-agent-trust-domains)) | As the other volumes |

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

Then start the agent pod again by a delete of the pod
(`kubectl -n agents-pilot delete pod -l app.kubernetes.io/name=agent`), so
the Signal channel finds the account. Not `rollout restart`: Flux removes
its annotation at the next reconcile, which starts the pod one more time.

## The finance agent

The finance agent runs as the pilot does, in `agents-finance`
(`kubernetes/apps/ai/agents/finance/`), with its own key at the model
gateway, its own sandbox gateway and its own backup repository. It is in git
and not applied yet: the Deployment, the volume and the backup policy wait
for the values of the owner (R4) and for the cutover, whose steps are in the
runbook ([The cutover of the finance agent](agent-platform-runbook.md#the-cutover-of-the-finance-agent)). The tools that differ
from the pilot:

- **Web search,** as the pilot's ([The pilot agent](#the-pilot-agent)): the
  Brave plugin, the official plugins step, `tools.web.search` and the rule
  to `api.search.brave.com`, with a key of its own: `BRAVE_API_KEY` in the
  Secret `agent-search-secret` of `agents-finance`
  (`agent/app/secret-search.sops.yaml`), not the pilot's key. The gateway
  process makes the call. The query goes to the vendor, and the query and
  the result stay in the session store of the agent, so in its archives
  and snapshots: the model can put words of a finance conversation in a
  query.
- **The notes vault,** read only. The owner decided on 2026-11-16: no sync
  job. The owner copies the vault once into the state volume, at
  `/home/node/data/vault`, and the backup of the volume holds it (the
  policy `agent-state` copies the whole volume to the repository of
  finance; the restore test counts its notes and fails with `no-vault`).
  - The agent reads it with `memory_search` and `memory_get`: the config
    names it in `memory.search.extraPaths`, Markdown files only
    (`**/*.md`). The gateway process indexes it with the embeddings of
    finance (`agents-finance-embeddings`, the Ollama of the host, so no
    vendor gets the text of a note) and reads it from the volume. It is
    outside the workspace, so no sandbox gets a copy, and no tool of the
    agent writes it.
  - **The copy is apart from the vault on the owner's devices.** After the
    seed, a change on a device does not reach the agent, and the agent
    changes nothing on a device. To bring the agent a newer vault, seed it
    again (below).
- **The finance app,** read and write, never delete, through the MCP
  gateway, the MCP server of the app and its proxy
  ([The finance app](#the-finance-app)).
- **The memory file is over the limit of one bootstrap file.** The doctor
  step of the first start: `MEMORY.md` has 34,376 characters, and the
  product puts 20,000 of them in the prompt (`bootstrapMaxChars`, default
  20000; 42 % left out). All bootstrap files have 46,585 characters, under
  the limit of all files (`bootstrapTotalMaxChars`, 60000). The host had
  the same limits, so nothing changed with the move. Left as it is, for
  now:
  - The product's way is a short `MEMORY.md`: the detail goes in daily
    files `memory/YYYY-MM-DD.md`, which `memory_search` finds on demand
    (the product docs, "User model"). The agent can do that in a chat.
  - The other way is `agents.defaults.bootstrapMaxChars: 40000` in
    `openclaw.json`: all of the file in the prompt, with room to grow. The
    cost: about 14,400 characters more in each model request, about 3,600
    to 4,100 tokens (3.5 to 4 characters a token), most of it read from
    the prompt cache. The files would then be 78 % of the limit of all
    files.
  - A scheduled job gets no `MEMORY.md` in either case: the product
    leaves the memory file out of a scheduled session (a lab with the
    image: a scheduled turn got `AGENTS.md` and no `MEMORY.md`, a chat turn
    got both).

### Scheduled jobs

The scheduled jobs of the agent are in git:
`finance/agent/config/automations.json`, one entry for each job (a key, a
cron expression and a message). The `automations-sync` container of the
pod makes the jobs of the product equal to that list.

- **Why a container.** At the release of the image (OpenClaw 2026.11.3)
  the product keeps its jobs in its state database, and only a running
  gateway changes them, through its CLI (`openclaw automations`) or its
  API. Its config has no key for jobs (`cron` holds settings only), and its
  docs say to change jobs with the CLI, not in a file. A job can carry a
  declaration key: an add with a key that is there already updates that
  job, with the same id. The product's own declared jobs use it (the
  heartbeat `heartbeat:main`, the job of `memory-core`). An init step
  can't do it (no gateway runs yet), so the container does it once the
  gateway is up, with the keys `flux:<key>`.
- **What it does.** At the start of the pod and then each hour: an add for
  each entry (it makes the job, or sets it back to git after a change in a
  chat), and a remove for each `flux:` job that is not in the list. A job
  with another key, or with none (made in a chat), stays. A change of the
  list starts the pod again (Reloader).
- **A broken list changes nothing.** With no file, the sync declares
  nothing (`result=none`). A file that does not parse, with no `jobs`
  list, or with one bad entry stops the pass with
  `automations-sync result=failed reason=bad-list`, and no job is made,
  changed or removed. Only `{"jobs": []}` on purpose removes each `flux:`
  job.
- **On and off: git decides, in one way** (`enabled` of each entry):
  - `false`: the job is off; the sync turns it off at each pass.
  - `true`: the sync turns the job on when it makes it, and when the value
    in git changes from `false` to `true`. Between two such changes, a job
    that is off (stopped in a chat, or by the scheduler after errors)
    stays off, and its line says `state=stopped`. To start it again: false
    and then true in git (two pull requests), or
    `openclaw automations enable <id>` by hand.

  The value of git of the last pass is in the description of the job
  (`git enabled=true|false`). A chat can edit that field, so a job that is
  off and has no value the sync can read is a stop of the owner: the sync
  never turns it on, and its line says `state=stopped note=no-git-value`
  once. A turn-on writes `true` first and then enables the job; when the
  enable fails, the sync writes `false` again so the next pass tries again,
  and the job never comes on by mistake. Both jobs of the host were `false` in git
  until the owner wrote their task files; they are `true` since 2026-11-17.
- **The lines,** in Loki: one for each job,
  `automations-sync key=<key> result=ok state=on|off|stopped`, `result=removed`
  or `result=failed`, and one for each pass,
  `automations-sync result=ok jobs=<n>` or
  `automations-sync result=failed reason=<reason>`. With no `TZ` or no chat
  id, the sync changes nothing and prints the failed line again each hour,
  so the alert holds until the pod starts with them. The pass line fires
  `AgentAutomationsSyncFailed` ([Alerts](#alerts)).
- **Each job:** an agent turn of the agent `main` in a new session
  (`isolated`), at the time of its cron expression in the zone of the
  cluster (the cluster variable `TIMEZONE`, the `TZ` of the container, set
  on each job). The other containers have no `TZ`, so the product runs in
  UTC, and a job with no zone would run in UTC. The answer goes to the
  owner's chat, the one id of `TELEGRAM_ALLOW_FROM`.
- **The task text is not in git.** The message of a job is neutral: it
  names a file of the workspace, `automations/<key>.md`, and the owner
  writes the task in that file (the workspace is in the backups). A job
  whose file is missing answers that it is missing. The agent can change
  that file, as it can change its other workspace files.
- **A run gets no `MEMORY.md`.** The product leaves the memory file out of
  a scheduled session: in a lab with the image, a scheduled turn got
  `AGENTS.md` and no `MEMORY.md`, and a chat turn got both. So a task file
  names what the job needs, or tells the agent to search its memory
  (`memory_search`).
- **The tools of a run.** A job made with no tool list stores `*`: each
  run uses the tool policy of the agent as it is at that time, with its
  sandbox, its deny list and the filter of the MCP server. The test
  `observability/tests/test_agent_automations.py` runs a job and a chat
  turn against a fake model and a fake MCP server: both get the same
  tools, and a search finds the same finance tools (no delete).

### The steps of the owner

None of these values goes through an agent or a session. The search key is
done: `agent/app/secret-search.sops.yaml` is in git with its value, and is
listed with the Deployment.

1. **The seed of the vault,** in the state copy of the cutover: an archive
   through the inbox pod, which the restore Job checks and unpacks before
   the agent starts
   ([The copy of the state](agent-platform-runbook.md#the-copy-of-the-state),
   step 6). The notes can hold a secret: the owner scans the vault folder
   first. The `memory-index` step of the first start indexes the notes,
   and the next nightly snapshot holds the vault. To seed a newer vault
   later, while the pod runs, from the host, with the vault folder in `V`:
   empty the folder first, so that a note deleted on a device goes here
   too, then copy and count. No Job checks this copy, so first check the
   folder on the host: plain files and directories only, no setuid or
   setgid bit, and no secret in a note (each command prints nothing):

   ```bash
   find "$V" ! -type f ! -type d
   find "$V" -perm /6000
   betterleaks dir --redact --no-banner "$V" > /dev/null || echo "read the findings first"
   kubectl -n agents-finance exec deploy/agent -c agent -- find /home/node/data/vault -mindepth 1 -delete
   tar -C "$V" --exclude=.obsidian --exclude=.trash --exclude=.git -cf - . \
     | kubectl -n agents-finance exec -i deploy/agent -c agent -- tar -C /home/node/data/vault --no-same-owner -xf -
   kubectl -n agents-finance exec deploy/agent -c agent -- sh -c 'find /home/node/data/vault -type f -name "*.md" | wc -l'
   ```

   The count is the number of notes. The product watches the folder and
   indexes the notes; the next start of the pod indexes them in any case.

## The finance app

The finance agent reads and writes the finance app (Firefly III,
[firefly.md](firefly.md)) through the MCP gateway of the platform and two
pods of its own in `agents-finance`
(`kubernetes/apps/ai/agents/finance/firefly-mcp/`), and its sandboxes reach
the app through one of them. The agent may create and update data. It may
never delete.

```text
agent pod (MCP client) --> mcp-gateway :3000 /finance/mcp --> firefly-mcp :3000 --> firefly-proxy :8080 (reads, writes) --+
                                                                                                                          +--> the app
sandbox --> its supervisor (by the sandbox policy) -------------------------------> firefly-proxy :8081 (reads only) -----+
```

- **`mcp-gateway`** (`kubernetes/apps/ai/mcp-gateway`, in `ai`,
  [mcp-gateway.md](mcp-gateway.md)): the MCP gateway of every trust domain.
  It checks the key of the agent (`MCP_GATEWAY_KEY` of `agent-secret`, a
  `Bearer` header of the `mcp.servers.firefly` entry), lets it see and call
  only the 73 kept tools of the server, by name, and sends each call on to
  `firefly-mcp` with the bearer `none`. It holds no credential of the app.

- **`firefly-proxy`** (`app/proxy.yaml`): nginx, and the only holder of the
  token of the agent at the app. The token is the env var `FIREFLY_TOKEN`
  of its one container, from the Secret `firefly-proxy-secret`; the image
  renders it into the config in an emptyDir in memory at the start. The
  proxy sets `Authorization` on each request that it lets through, in place
  of the client's header, and drops the client's cookie and forwarding
  headers. It has two ports, one for each client
  (`app/proxy/default.conf.template`). On 8080, for the MCP server, it lets
  through only the calls of its allow list: `GET`, `POST` and `PUT` on the
  API paths that the kept tools of the MCP server call, taken from the
  source of the server at v1.0.1. On 8081, for the sandboxes, only the
  `GET` calls of the same list. Every other call gets a 403 with the header
  `X-Firefly-Proxy: refused` and never reaches the app: each `DELETE`, each
  other method, the preferences, webhooks, cron, configuration, users and
  tokens, the bulk update, the rule and recurrence triggers, the exports
  and the attachments, and the writes of rules. A raw path with a dot
  segment, an encoded dot, slash or backslash, a double slash or a `;` is
  refused before the match, since nginx matches the path it normalizes and
  the app reads the raw one. A method override is refused or dropped: the
  framework of the app (Laravel 12.55 on Symfony http-foundation 7.4,
  `Request::getMethod`) routes a POST as the method of the header
  `X-HTTP-Method-Override`, or of a `_method` argument in the query, a form
  body or a JSON body. The proxy drops that header and its variants
  `X-HTTP-Method` and `X-Method-Override`, refuses a write with any query
  string (no kept write of the server sends one; PHP decodes the names of
  the arguments, so `_%6dethod` is `_method` too), and lets through a
  write only with the content type `application/json`, so no form body.
  Each answer carries the header `X-Firefly-Proxy-Port`. One log line for
  each request: `firefly-proxy port=<n> method=<m> path=<path> status=<n>
  refused=0|1`, with no query string, header or body.
- **`firefly-mcp`** (`app/mcp.yaml`): the MCP server
  [fireflyiii-mcp](https://github.com/daften/fireflyiii-mcp) 1.0.1, in HTTP
  mode with no OAuth client. In that mode it takes the bearer of each
  request from its client and sends it on to `FIREFLY_URL`, which is the
  proxy; it checks only that a bearer is there. The MCP gateway, its one
  client, sends the fixed value `none` (the `backendAuth` of the finance
  route of its config). It is no secret, and nothing checks it: the proxy
  replaces it. So the server never holds the token. It loads the
  tool groups `accounts`, `transactions`, `categories`, `budgets`, `bills`,
  `reports` (with the tags, the summary and the insights) and `rules`; not
  recurring, attachments, currencies, exports, object groups, piggy banks
  or transaction links.
- **The sandboxes** of the agent reach the proxy with no credential: the
  sandbox policy of the agent (`agent/config/sandbox-policy.yaml`, the
  `policy` of the `openshell` entry) has one network rule, to the read port
  `firefly-proxy.agents-finance.svc.cluster.local:8081`, for `python3`
  only, `GET` under `/api/v1`: the scripts of the agent only read, and its
  writes go through the MCP server. The read port is the wall: a sandbox
  policy can be changed (the client certificate of the agent container can
  make a sandbox with another policy), and the network policy still lets a
  supervisor reach no port of the proxy but 8081. There is no provider and
  no providers job: nothing of the provider store of the sandbox gateway is
  used, and the gateway holds no credential of the app. A script in a
  sandbox needs only the base URL. The workload pod of a sandbox has no
  egress; its supervisor opens each connection that the policy approves,
  from its own pod, so the supervisor is the client that the proxy lets in.
  OpenShell applies a policy when it makes a sandbox: a sandbox made before
  this policy keeps its old one until it is made again.

**Network policy.** `firefly-proxy`, by the labels of the client and the
port: in from `firefly-mcp` on 8080 only, from the supervisors of the
sandboxes of `agents-finance` (label `openshell.ai/boundary-role:
supervisor`) on 8081 only, and from the probe of the app on both; out to
the app's pods on port 8080. `firefly-mcp`: in from the MCP gateway only,
out to the proxy only. The agent pod gets one rule to the MCP gateway and
none to `firefly-mcp`; the gateway lets in the agent pod and its probe, and
reaches `firefly-mcp` only ([mcp-gateway.md](mcp-gateway.md), "Network
policy").

**Who can carry the labels of a client of 8080.** The rule for 8080 trusts
the labels of the MCP pod (`app.kubernetes.io/name: firefly-mcp`) and of the
probe (`app.kubernetes.io/name: platform-checks` with
`app.kubernetes.io/component: firefly-probe`). A sandbox can not carry them:
at OpenShell 0.1.8 the Kubernetes driver puts a label of the caller on the
pod only when the gateway's `allowed_pod_labels` names its key, the default
is empty, and the create is refused otherwise
(`crates/openshell-driver-kubernetes/src/config.rs`, `allowed_pod_labels`;
`driver.rs`, `validate_pod_metadata`); an update of a sandbox changes no pod
label (`proto/openshell.proto`, `UpdateSandboxRequest`). The finance gateway
sets no `allowed_pod_labels`, and a test keeps it so
(`observability/tests/test_firefly_proxy.py`). What is left: a pod with
those labels in `agents-finance` can be made by Flux (from git), by a
cluster admin, by the service account of the sandbox gateway (its Role
`openshell-sandbox` can create pods there; the gateway's code sets only its
own labels) and by the agent sandbox controller (from the Sandbox records,
whose pod labels the gateway sets). The probe's Role reads pods only. A new
release of OpenShell can change this: check `allowed_pod_labels` and the
label code of the driver when the gateway moves to a new release.

**DNS.** DNS is a way out too, so each of the two pods resolves one name:
the proxy the app's Service, the MCP server the proxy's Service. Cilium
adds up the allows of every policy that selects a pod, the name patterns of
DNS rules too, so a narrow rule next to the namespace's `allow-dns` (any
name) would change nothing. The two pods carry the label
`agents.home.arpa/dns: own-rule`, `allow-dns` selects no pod with that
label, and each pod's own policy has a DNS rule for its one name. The pods
set `ndots` to 1, so the resolver asks for the full name as it is and
never for a name with a search domain, which the rule would refuse (and
musl, the resolver of both images, stops at the first refused name).
Both pods meet the `restricted` level, run as non-root with a read-only
root file system, and mount no service account token.

**What the agent may write.** Create and update of accounts, transactions
(split ones too), categories, budgets and their limits, bills and tags.
Never a delete, a trigger, a bulk update, an export, an attachment or a
setting. Four walls, each on its own:

1. The MCP gateway: the rule of the client `finance` names the 73 kept
   tools one by one, so the agent sees no other tool, and a call of
   another tool is refused before it reaches the server.
2. The proxy, at the HTTP level, whatever the server or a sandbox sends: a
   sandbox reaches only its read port, which refuses each write.
3. The agent: `toolFilter.include` of the MCP entry lets through the reads
   (`get_*`, `search_*`, `test_rule`, `test_rule_group`) and the fifteen
   writes by name; `tools.deny` removes each `firefly__delete_*`,
   `firefly__bulk_*`, `firefly__trigger_*`, export, attachment and rule
   write tool. `tools.sandbox.tools.alsoAllow` has `firefly__*`, so a
   sandboxed session has the tools.
4. The sandbox policy, for a script: reads only, in front of the read port.

The limits of the walls:

- **No rule writes,** though the agent's instructions name them. A rule of
  the app can have the action `delete_transaction` and fire when a
  transaction is stored or updated, so a rule write is a delete by another
  path. The agent can read and test rules. The owner decided that rule
  writes stay refused.
- **`bulk_update_transactions`** of the server sends one `PUT
  /transactions/{id}` for each transaction, the same call as
  `update_transaction`. The proxy can not tell them apart; the rule of the
  gateway and the deny of the agent stop the bulk tool. What it can do is what many single updates
  can do.
- **An update can remove a split.** At the app's release 6.5.9, an update
  of a transaction group that has two or more splits deletes each split
  that the update does not list (`GroupUpdateService::update`, the
  `array_diff` of the stored and the updated ids). The proxy can not see
  the stored transaction, so it can not refuse such a `PUT` without
  reading and comparing bodies. The `update_transaction` tool of the
  server reads the group first and sends every split with its id, so the
  agent's tool does not do it; a script could, and so a sandbox reaches
  only the read port. What is left: a bad release of the server, and
  the restore below.
- **A `_method` in a JSON body** still reaches the app, and the framework
  applies it to a POST. At 6.5.9 the app has only `GET` and `POST` routes
  on the paths where the allow list lets through a POST (`routes/api.php`),
  so such a POST gets a 405 or a read. A new release of the app with
  another route there opens a way: check the routes of those paths when the
  app moves to a new minor release.

`observability/tests/test_firefly_proxy.py` checks the allow list against
each API call of the kept groups of the server at v1.0.1 (each delete,
trigger and rule write refused, each other call let through), against the
calls of secrets and settings, and checks the filter and the deny against
the tool names of v1.0.1. With the product's own client (`openclaw mcp
probe`, agent image 0.4.1) against the server, the agent keeps 73 of its 89
tools, and the writes among them are the fifteen above.

**The risk the owner should know.** One session of the agent has three
things: web search, so text from outside; write access to the app; and the
finance data. A page that the agent reads can steer it to a wrong create or
update, or to put finance data into a search query, which goes to the search
vendor. The controls: no delete (the four walls above), the allow list of
the proxy, no egress of the agent but the listed ones and none of a sandbox
but the read port of the proxy, DNS for one name in the pods of the app, and the backup of
the app's database. What is left: a wrong
create or update can happen, and data can leave in a search query.

**A wrong write, and what brings data back.** The app's database is a
CloudNativePG cluster with continuous WAL archiving and a base backup each
night at 01:00, kept 30 days, so it can come back to any point in time,
minutes old ([backups.md](../operations/backups.md)). For one wrong write,
fix the data in the app by hand, or make a test restore (a second Cluster
from the backup at a time before the write, as in "Restoring" of
backups.md), read the old values there, and set them again in the app. A
recovery of the whole database to that time also takes back every later
write, of the owner and of the finance sync.

**The supply chain.** The server is a young project of one author, so it
runs with as little as it can: no token, no service account token, egress
to the proxy only, and the writes of the allow list only. The image is
pinned by digest. Renovate opens an update 7 days after its release at the
earliest (`.github/renovate.json5`), and a person reviews each one
(`impact/high`, `.github/renovate/autoMerge.json5`). What is left: a bad
release can make any call of the allow list with the token, so create or
change data (and remove a split, above), and read all the data the reads
give; it can not delete through the proxy, reach another path, resolve
another name, or send data anywhere but the proxy. The proxy is the
official unprivileged nginx image, pinned by digest.

**The probes.** `platform-check-mcp-gateway-probe` (`finance/checks/app/`,
05:37) tests the path of the agent's tools through the MCP gateway, with two
keys of its own ([mcp-gateway.md](mcp-gateway.md), "The probe"): the tools
of the rule, a read that reaches the app, refusals at the gateway, and no
way to the MCP server but the gateway.
`platform-check-firefly-probe` (05:17) tests both ports of the proxy with
the token it holds, so it works before the real token and after it. From a
sandbox made with the image of the agent's sandboxes and the agent's
sandbox policy, widened for the probe: each method on the read port, so
that the proxy and not the policy must refuse a write, and a rule for 8080.
`GET /api/v1/about` reaches the app on the read port; each write and each
delete there is refused by the proxy; each other call of the list above
is refused, by the proxy or by the policy; 8080 through the supervisor is
blocked by the network policy; and the proxy's address with no proxy in
between is blocked. That the agent's own policy lets through `GET` only is
a test in git. Straight to the proxy, the calls that neither client sends:
on 8080 the override header is dropped (the app routes a POST, not a PUT,
so no 405), and a `_method` in the query (also with encoded letters), any
query on a write, a form body, a `DELETE`, a `PATCH` and a path trick are
refused; on 8081 an allowed read reaches the app and a `POST` and a `PUT`
are refused. No call of the probe can change data: each write or delete
names an id that does not exist or sends an empty body.
`platform-check-firefly-dns` (05:27) is set up as the two pods are (the
label, `ndots` 1, a rule for the name of the proxy): that name resolves,
and an outside name, a cluster name off its rule and the API server's name
do not. It can not run as the identity of the MCP pod (a pod with its
labels would be an endpoint of its Service), so it proves the mechanism,
and the DNS rules of the two pods are in git next to it.

**After the cutover,** the lead proves one tool call in a sandboxed session
with one real turn: the session lists the `firefly__` tools, one call (for
example `firefly__get_summary`) answers, the MCP gateway logs it with
`"client":"finance"` and status 200, and the proxy logs the call with
`refused=0` and a status of 200.

### The steps of the owner for the finance app

1. **The token.** Make a personal access token in the app for the agent
   (Profile, OAuth, Personal access tokens). A token of the app has all the
   rights of its user; the proxy is what limits it. Put it in place of
   `pending` in `firefly-mcp/app/secret-firefly-proxy.sops.yaml` with
   `sops`, and commit. With the placeholder the app answers each call with
   a 401, and nothing else changes. After Flux applies it, Reloader starts
   the proxy again. The check prints only `ok` or `not ok`:

   ```bash
   kubectl -n agents-finance exec deploy/firefly-proxy -- \
     sh -c 'wget -q -O /dev/null http://127.0.0.1:8080/api/v1/about 2>/dev/null' \
     && echo ok || echo "not ok"
   ```

2. **The scripts of the workspace.** Only the owner edits the copied
   workspace. In each script that calls the app, set the base URL to
   `http://firefly-proxy.agents-finance.svc.cluster.local:8081`, the read
   port (the paths stay `/api/v1/...`), and drop the read of the token
   file: the proxy sets the token, and the sandbox has none. The scripts
   must run with `python3` and only read; the sandbox policy lets no other
   program reach the proxy, and the read port lets no write through.
   When the scripts no longer use the token of the host, revoke it in the
   app.
3. **Decided: which writes the agent needs.** The owner said "it needs to
   write as well i believe" on 2026-11-17, and then that rule writes stay
   refused. So the agent keeps the fifteen writes and no rule write; it can
   propose a rule in the chat, and the owner adds it. If rule writes are
   wanted later, the rule of the gateway, the proxy and the deny list
   change together, and a rule that deletes becomes possible.
4. **Open: writes from a sandbox.** Off for now, as the scripts only read.
   On, the sandbox policy, the read port and the network policy change
   together, and a script could remove the splits of a transaction by
   mistake (an update that lists fewer splits). The recommendation: keep them off, and
   let the agent write through its tools only.
5. **Open: the split removal of an update.** The controls are the tool of
   the server (it sends every split), the deny of the bulk tool, and the
   restore. The recommendation: accept it with these, and add to the
   instructions of the agent that an update of a split transaction goes
   through `update_transaction` with its `transaction_journal_id`, one
   split at a time.

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
| Token | `RUN_RECEIVER_TOKEN` in the Secret `run-receiver-secret` (`app/secret.sops.yaml`). The workflow that calls the receiver holds the same value, in the secret of its environment `run-receiver` |
| Caller | The platform job of `.github/workflows/renovate-review.yaml`, when the variable `RENOVATE_REVIEW_ROUTE` is `platform` (`docs/operations/renovate.md`, "The route of a review") |

- **The list of agents** (`app/agents.yaml`, the ConfigMap
  `run-receiver-agents`) has the Job template of each agent: the image, the
  sandbox policy, the providers and the variables of the agent. The caller
  sets the name of an agent, the input and the key, and nothing else of the
  Job. The Job name comes from the key, so a second call with the same key
  gets the same run. A Job stays 7 days. The receiver refuses to start when
  a template breaks a rule of the image.
- **The retry rule of a caller.** The same key always gets the same answer,
  a failure too, for the 7 days that its Job stays. So a retry is a new key:
  the caller puts an attempt number at the end, for example
  `renovate-review-<pr>-<sha7>-<n>` with `n` from 1.
  - It tries again only after a `failed` (`502`) or `timed_out` (`504`)
    answer, with `n + 1`, and at most 3 attempts in all. It never tries
    again after a `2xx` answer.
  - A call with no answer goes again with the same key, and gets the same
    run.
  - The receiver does not change for this. Each attempt is its own Job and
    stays as a record. The agent must be safe to run twice for the same
    work: the Renovate reviewer posts one comment for each head.
- **The answer of a run stays with its Job.** The answer is the last line
  of the log of the run pod. The pod GC of this cluster
  (`terminated-pod-gc-threshold: 1`, `talos/wsl/cluster-config.yaml`) checks
  every 20 s and deletes each terminated pod of the whole cluster but the one
  created last (Kubernetes `pkg/controller/podgc`). A run pod can go seconds
  after it ends, for example when another pod started at the same second (the
  providers job at :07) or a CronJob of another namespace ends.
  - So the receiver follows the log of each running run as it is written
    (`follow=true`), from the call that makes or finds the run, and from its
    loop every 10 s (`RUN_RECEIVER_RECORD_SECONDS`) for a run that no call
    started. The run job writes the result line last, and the pod GC deletes
    only a pod that has ended, so the line is in hand before the pod can go.
    This needs no right beyond `pods`: `list` and `pods/log`: `get`.
  - The receiver keeps the answer in the annotation `workflow-agents/result`
    of the Job. A later call with the same key reads the annotation.
  - An answer of more than 64 KiB is kept, and answered, without its
    `output`.
  - If the receiver was down for the whole end of a run and the pod went,
    the answer is the state of the Job (`succeeded`, `failed` or
    `timed_out`), with `"output_kept": false`.
  - For the annotation, the Role of the receiver has `jobs`: `list` and
    `patch`. The patch changes only the annotations, and the admission
    policy checks it as it checks a new Job (it matches `UPDATE` too).
- **The Renovate reviewer** is the one agent. It reviews pull requests of
  `iT3E/tnwks-ops` only (`REVIEWER_REPOSITORIES`), and posts one comment for
  each head: it never merges, approves, pushes or changes a label. It calls
  the Messages API of the model gateway (`POST /v1/messages`) with the name
  `agents-workflows` and the effort `high` (`AGENT_ENV_REVIEW_EFFORT` in
  `agents.yaml`), through the provider `model-gateway`, and GitHub through
  the provider `github-reviewer`.
- **The providers** are in the store of the sandbox gateway. The CronJob
  `workflow-providers` (`app/providers.yaml`, every 15 minutes at :07, :22,
  :37 and :52) runs the `providers` command of the image of the workflow
  agents (`src/providers/` in iT3E/tnwks-workflow-agents, tested in its CI
  with a fake CLI and on its kind cluster). Each run:
  1. checks that the gateway answers;
  2. imports each provider profile of `app/providers/` that is not in the
     store, and updates each one whose file changed in git. The profile keeps
     the sha256 of its file in the annotation
     `workflow-agents/source-sha256`, so an unchanged profile is not touched.
     An update sends the `resource_version` of the stored profile (from
     `openshell profile export`), as the gateway requires;
  3. updates `model-gateway` with `MODEL_GATEWAY_KEY`;
  4. gets a new installation token of the GitHub App of the reviewer
     (`src/providers/github_app.py` of the image);
  5. updates `github-reviewer` with that token. (A provider that is not
     there yet is created.)

  The command runs no `openshell profile lint`. Import and update check the
  profile themselves, and lint refuses a profile that is already in the
  store, so the first version (a script in this repo) failed on each run
  after its first. A failure that a new token can't fix (the gateway, a
  profile, the model provider) stops the run before the token call. Each
  failure logs one line for an alert, `workflow-providers result=failed
  step=<step> name=<name>`, with step `gateway`, `profile-export`,
  `profile-import`, `profile-update`, `provider-create`, `provider-update`
  or `github-token`, and exits 1 (`step=config` and exit 2 when the gateway
  URL is not set); a good run logs `workflow-providers result=ok`. To run it now,
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
  list pods and read pod logs in this namespace, and list Sandbox records
  (`agents.x-k8s.io`), and nothing more. The run jobs run as `workflow-run`,
  with no role and no token: they call the sandbox gateway with its client
  certificate. The sweep runs as `workflow-sweep`, which can list and delete
  Sandbox records. RBAC can't limit a list or a delete by label, so each of
  the two Roles covers every Sandbox record of the namespace; the code
  touches only run records, and the admission policy
  `workflow-sweep-deletes` refuses a delete by the sweep of any record that
  OpenShell did not make for a run name. A record holds no credential.
- **The admission policy** (`run-receiver/admission/`, the
  ValidatingAdmissionPolicy `workflow-run-jobs` and its binding to this
  namespace). A right to make a Job is a right to read each secret of the
  namespace, because the Job sets its pod. So each Job that the receiver
  sends must match the rules of a template: an agent of the list with its
  own providers, one container from the image of the workflow agents pinned
  by digest (and a sandbox of the same image), run with the one argument
  `run-job` and no command, lifecycle hook or probe, the service account
  `workflow-run` with no token, no secret but the client certificate, one
  try, and a time limit of one hour at most. A second policy,
  `workflow-run-job-updates`, lets an update of a Job by the receiver change
  only its two annotations `workflow-agents/result` and
  `workflow-agents/sandbox-record`: nothing in the spec, the labels, the
  owner references, the finalizers or the other annotations. Both bind the
  account of the receiver only, so the Job, TTL and garbage collector
  controllers are not bound. `admission/tests/run.sh` tests them on a
  throwaway API server in containers. The objects are cluster-scoped, so
  they have a Flux Kustomization of their own with no `targetNamespace`
  (`agents-workflows-run-admission`), and the receiver depends on it. A new agent adds its providers to the policy in the same
  pull request as its entry in `agents.yaml`.
- **The sweep** (CronJob `workflow-sweep`, every 15 minutes) deletes each
  run sandbox that is older than one hour, by its name and label, through
  the sandbox gateway, and then checks that its Sandbox record went. It also
  removes, through the API, each run record older than one hour that the
  gateway does not list. A run job deletes its own sandbox; the sweep is for
  a run job that stopped before it could, and for a record that the gateway
  left.
- **A record that the gateway leaves.** OpenShell 0.1.8 can answer a delete
  with success, keep the Sandbox record and forget the sandbox (seen on
  2026-11-14 on the probe of a trust domain). The run job can't see that:
  it has no token, and its result says `sandbox_deleted: true`. So the run
  receiver checks the record of each run 60 s after its end, and writes
  `gone` or `left: <sandbox>` in the Job annotation
  `workflow-agents/sandbox-record`. A record that stays is one ERROR line
  in its log; the answer of the run does not change. No alert reads that
  line: the sweep removes the record after one hour and
  fails once (AgentPlatformCronJobFailed), with an `ERROR sweep:` line that
  names the sandbox.
- **Network policy.** In: the runner pods of the `openclaw-hooks` scale set
  in `tools`, a pod `app.kubernetes.io/name: workflow-schedule` of this
  namespace (a schedule, later), and the kubelet's probes. Out: the API
  server only. The run jobs and the sweep reach the sandbox gateway of this
  namespace only, which also relays the SSH path to a sandbox; the sweep
  also reaches the API server, for the Sandbox records (and the providers
  job `api.github.com`, for the token of the App). The agent runs in the
  sandbox, whose egress is its sandbox policy and its providers.
- **A test call by hand** needs no rule: a `kubectl port-forward` reaches
  the pod through the kubelet, not from another pod, and the receiver
  listens on each address of the pod.

```sh
kubectl -n agents-workflows port-forward svc/run-receiver 8080:8080 &
curl -s localhost:8080/healthz
# With no token, a run fails (401):
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8080/runs -d '{}'
```

## Logs

Loki has the logs of each Job pod of the platform (the run jobs, the
providers job, the sweep and the checks), and of the long-running pods on an
allowlist. A pod ships only when its pod template has the label
`logs.home.arpa/ship: "true"`; a new pod in the same namespace stays out
until its manifest has the label too. The sandbox supervisors are made by the
gateway, so their namespace opts in instead
(`logs.home.arpa/ship-supervisors: "true"`, `agents-workflows` only), and
only pods with the labels `openshell.ai/component: supervisor` and
`openshell.ai/boundary-role: supervisor` ship. A pod with
`openshell.ai/boundary-role: workload` never ships, even with the allowlist
label.

A Vector agent of its own reads them: the platform agent, a DaemonSet that
only homelab-wsl runs (`kubernetes/apps/monitoring/vector/agent-platform/`,
the sources `allowlist` and `supervisors` of its `config/vector.yaml`). The
shared Vector agent and the aggregator stay the same on each cluster. Query
the lines by namespace and app, for example
`{namespace="agents-workflows", app="run-receiver"} |= "result kept"`.

Before a line leaves the node, the platform agent changes it as follows (its
tests are in `observability/vector-tests/agent-platform-logs.yaml`):

- **The sandbox gateway** (`openshell-gateway`). The colour codes go. From
  the first field that can hold a command, or its input or output, to the
  end of the line, the line becomes `(dropped)`: a `[cmd:` context, or a key
  that ends in `preview`, `stdin`, `stdout`, `stderr`, `input`, `output` or
  `body`, or `cmd`, `cmd_line`, `cmdline`, `command`, `args`, `argv` or
  `env`, before `=` or `:`, in any case. An `error=` or `detail=` field
  ships: the gateway writes its own error text there. The gateway logs the first 120 or
  so characters of each command it relays to a sandbox (`command_preview=`),
  and Loki gets `command_preview=(dropped)`. The query string of each URL,
  path or host becomes `?(dropped)`. A JSON OCSF record (it holds
  `class_uid`) becomes `(dropped: OCSF JSON record)`: it is off by default,
  and can hold the command line of a process.
- **A sandbox supervisor** (`supervisor`). The same, and then the line
  stays only when it is a known line type of OpenShell 0.1.8 (SSH, NET,
  HTTP, CONFIG, an SSH relay EVENT, LIFECYCLE, PROC, or a plain log line of
  the supervisor) and holds only the characters and fields that type allows:
  an SSH auth field is one of the labels of the format, a CONFIG field is
  one the format writes (version, hash, source and the like, never
  `rejection_reason`), and a LIFECYCLE line names an `openshell` program.
  Any other line becomes `(dropped: unknown line type)`. A new release that
  logs a new kind of line shows the marker, and a test of the new line
  moves it to the list.

  What still ships by design: the host and port of each connection out of
  the sandbox; the method, host and path of each HTTP request (a token
  inside a path ships; the query string does not); and the short text (up
  to 120 characters) of a CONFIG line or a plain log line.
- **Every other container** (the run receiver, the model bridge and the
  pilot's pod) ships its line as it is. Their lines are the program's own
  (below), and the bridge's JSON lines stay whole.

| Pod | What its log holds | About a day |
| --- | --- | --- |
| The run receiver (`agents-workflows`) | One line for each run made or found, and for each answer kept (`result kept (succeeded)`, `result kept (failed)`), and the errors of the cluster API. Never the token or the input of a run | Some KB |
| The sandbox gateway of `agents-workflows` (`openshell-0`) | Each call to its API (method, request id, status), each sign-in (`OCSF AUTH:LOGON`, allowed or denied, with the address of the caller), the sandboxes it makes and removes, and each command it relays to a sandbox, by length only once the preview is dropped. The text of an `error=` or `detail=` field ships. No credential | 2 to 7 MB |
| A sandbox supervisor of `agents-workflows` (`os-supervisor-*`) | The policy it loaded, each SSH session into the sandbox, and each connection out of it, by host and port (`OCSF NET:OPEN`), or by method, host and path for HTTP. A token inside a path ships; a query string does not. The short text of a CONFIG or plain line ships. Any other line is the marker | Some KB for a run |
| The agent pod of the pilot: the init containers and the `backup` and `sandbox-start` containers | `prepare`: nothing. `archive`: one `archive-state result=ok release=... name=... bytes=...` line, and an `archive-state removed=<file>` line for each old archive. `official-plugins`: the output of the product's installer (the install paths in the state directory) and `official-plugins id=brave version=... result=installed\|failed`, or `official-plugins result=failed reason=...`. `doctor`: the report of the product's doctor (what it changed in the config copy, the plugins and skills it found, warnings), with file and setting names. `memory-index`: `memory-index result=ok\|failed`. `backup`: the output of the product's backup command (the paths it took or skipped, the archive name, `Archive verification: passed`), `daily-backup result=ok\|failed day=...`, `daily-backup removed=<file>`, `instruction-check file=<name> change=new\|changed\|gone` and `instruction-check result=first\|ok ...`. `sandbox-start`: `sandbox-start name=<name> result=ok\|failed`. `automations-sync` (finance): `automations-sync key=<key> result=ok state=on\|off\|stopped`, `result=removed\|failed`, and one `automations-sync result=ok jobs=<n>\|failed reason=<reason>` line for each pass | Some KB, most of it at a start of the pod |
| The model bridge (`ai`) | One JSON line for each event: `call` (domain, model, status, time), `engine_error` (domain, kind), `engine_init`, `conversation_end`, `rebuild` and `listening`. A conversation is an id; no prompt and no answer | Some KB |

Not in Loki, on purpose:

- **The agent container of an agent pod.** The pilot's pod excludes it with
  the annotation `vector.dev/exclude-containers: agent`: its log can hold
  chat content and the name of its bot.
- **Every other pod of the trust domains**: the gateways and supervisors of
  `agents-pilot`, `agents-main` and `agents-finance`, the agent pods of main
  and finance, and the Signal daemon (its log holds phone numbers). Read
  them with `kubectl logs`; a pod's log is gone when the pod goes.
- **The workload pod of a sandbox** (`openshell.ai/boundary-role: workload`):
  the code that an agent runs. The run job logs the result of a run.
- **The other pods of `ai`**: the model gateway and the browser tool.

## Checks

Two CronJobs in each trust domain test the boundaries every day
(`checks/app/`, shared by the trust domains; each `<name>/checks/ks.yaml`
applies it to its namespace), and `agents-finance` has two more, the probe
and the DNS check of the finance app (`finance/checks/app/`, [The finance app](#the-finance-app)). A failed check fails its Job, and the Job
pod's log goes to Loki. `AgentPlatformCronJobFailed` and
`AgentPlatformCheckStale` alert on them ([Alerts](#alerts)).

| CronJob | Schedule | What must hold |
| --- | --- | --- |
| `platform-check-isolation` | 04:17 | The check pod connects to its own sandbox gateway and to no other trust domain's gateway, and its client certificate works there (`sandbox list`) |
| `platform-check-probe` | 04:47 | A new sandbox of the domain reaches nothing that no sandbox policy names: the API server, the internet, its own gateway, the model gateway and the metadata address, direct or through the sandbox proxy, and the cluster DNS Service directly over UDP and TCP (any answer fails). It holds no service account token. While the sandbox runs, its two pods (workload and supervisor) must be found running; the probe logs each pod with its node, then deletes the sandbox. Before the create it removes a sandbox of its own name that an earlier run left (a `leftover sandbox` line), and at its end it fails when its Sandbox record is still there (a `sandbox removed` line), after it removes it |
| `platform-check-firefly-probe` | 05:17 | `agents-finance` only. Straight to the write port of the proxy (as the MCP server calls it) and from a sandbox with the agent's sandbox policy (widened to each method on the read port), an allowed read reaches the app, and each delete and each call off the allow list of the proxy is refused; a sandbox can write nothing through the read port and can not reach the write port. It removes its sandbox `firefly-probe` as the probe does |
| `platform-check-mcp-gateway-probe` | 05:37 | `agents-finance` only. Through the route of finance at the MCP gateway: no key and an unknown key get a 401; the probe key lists exactly the tools of the rule of finance, its `get_about` reaches the app, and a delete, a rule trigger and a write are refused at the gateway, the write also with headers that name the client `finance`; the key with no tool lists nothing; a straight connection to the MCP server fails. While its keys are placeholders, the checks with a key print `skipped: the key is pending` and pass, until the owner's values step ([mcp-gateway.md](mcp-gateway.md), "The probe") |
| `platform-check-firefly-dns` | 05:27 | `agents-finance` only. A pod set up as the pods of the finance app (out of the namespace's DNS rule, its own rule for one name, `ndots` 1) resolves that name and no other |

- The check pods may try every trust domain's gateway on port 8080, so the
  isolation check tests each gateway's own policy, not the check pod's.
- The OpenShell CLI is a release binary, fetched by an init container at a
  pinned version and checked against its SHA-256. Move `CLI_VERSION` and
  `CLI_SHA256` (`checks/app/cronjobs.yaml`, both CronJobs, and
  `finance/checks/app/firefly-probe.yaml`) with the chart
  version of the sandbox gateways; Renovate doesn't track them.
- The probe finds the pods of its sandbox through the `Sandbox` record
  (label `openshell.ai/sandbox-name`), whose `openshell.ai/sandbox-id` the
  two pods carry in lower case as `openshell.ai/boundary-pair`. A Role lets
  it get and list `Sandbox` records and pods in its own namespace, and
  delete its own record `default--platform-probe` (the name that OpenShell
  0.1.8 gives the sandbox `platform-probe`), and in `agents-finance` also
  `default--firefly-probe`, and nothing more. It deletes
  that record only when it has the labels of OpenShell for this probe.
- Nothing places the two pods of a sandbox: the scheduler picks each node
  on its own, so they can share a node. The probe logs `nodes` and
  `different_nodes` and doesn't fail on one node.
- To run one now: `kubectl -n agents-<name> create job --from=cronjob/platform-check-isolation check-now`.

## Alerts

The rules are in `monitoring/app/prometheusrule.yaml` and
`monitoring/app/heartbeat-prometheusrule.yaml` (the Flux Kustomization
`agents-monitoring`, homelab-wsl only). Each alert goes to the
War Room, the default Telegram route of Alertmanager, with severity
`warning`. Most read kube-state-metrics; `WorkflowAppDrift`,
`AgentInstructionFileChanged`, `AgentInstructionCheckStale`,
`AgentMemoryIndexFailed`, `AgentAutomationsSyncFailed` and
`ModelBridgeRateLimited` read lines of the logs, as gauges of Vector: the
aggregator for the `<check> result=` lines (`WorkflowAppDrift`,
`AgentInstructionCheckStale`, `AgentMemoryIndexFailed`,
`AgentAutomationsSyncFailed`),
the platform agent for the `instruction-check file=` lines and the bridge
(`AgentInstructionFileChanged`, `ModelBridgeRateLimited`) ([Logs](#logs)).
`AgentBackupMoverConfigMapDenied` reads a counter of the API server, and
`AgentBackupRepositoryNotReady` the phase gauge of the backup operator.

[`agent-platform-runbook.md`](agent-platform-runbook.md) has a section for
each alert (what it means, the first checks, what was seen so far, the fix),
and each rule's `runbook_url` links to it. It also has the restore of the
state of an agent, the fast stop of the workflow agent, the move to Bedrock
by hand, and the steps at a limit of the Claude plan.

| Alert | Fires when | `for` | First step |
| --- | --- | --- | --- |
| `AgentPlatformWorkloadNotReady` | A Deployment or StatefulSet of a trust domain or of the sandbox controller, or the model gateway or the model bridge, has fewer ready pods than wanted | 10m | `kubectl -n <namespace> describe pod`: a failed `archive`, `official-plugins` or `doctor` init container, or a lock on the state volume. Revert the last image if it came with an update |
| `AgentPlatformCronJobFailed` | The last run of a CronJob of a trust domain failed, and no run passed since: the providers job, the sweep, a daily check. It resolves at the next good run | none | Its log in Loki (below). A `result=failed` line of the providers job names the step |
| `WorkflowProvidersStale` | No good run of `workflow-providers` in 35 minutes, or the CronJob is gone. A token of the reviewer lives one hour, so about 25 minutes are left | 5m | `kubectl -n agents-workflows get cronjob,jobs`, then the log of the last run |
| `WorkflowAppDrift` | An `app-drift result=drift` line in the last 20 minutes: the App or its installation has more permissions than the list in git | none | The `detail=` of the line names each one. Set the App back, or change the list by a pull request |
| `AgentHeartbeatMissing` | The daily `agent-heartbeat-check` (06:33 UTC) found no completed heartbeat run of an agent in Tempo in the last 26 hours, or could not ask Tempo. It asks 3 times, 60 seconds apart, and a failed pod gets one more try, so one short break of Tempo does not fire. It resolves at the next good run | none | Its log in Loki (`{namespace="monitoring", app="agent-heartbeat-check"}`): `result=missing` names the service, `result=error` is Tempo. Then the agent pod and its traces |
| `AgentHeartbeatCheckStale` | The heartbeat check itself was not scheduled in 26 hours, or its CronJob is gone | 15m | `kubectl -n monitoring get cronjob agent-heartbeat-check` |
| `AgentPlatformCheckStale` | A daily check (`platform-check-isolation` or `platform-check-probe`) of a trust domain has not passed in 26 hours, or never passed once a run was due | 15m | The CronJob (suspended?) and its Jobs |
| `AgentRunSandboxTooOld` | A run sandbox (`default--run-*`) in `agents-workflows` is older than 80 minutes: the run job and the sweep both failed to delete it | 10m | The CronJob `workflow-sweep`, then delete the sandbox through the gateway |
| `AgentWorkflowRunFailed` | A run job failed, for 2 hours from its start | none | The annotation `workflow-agents/result` of the Job, and the run log in Loki. A 502 from the model gateway can be the limit of the Claude plan |
| `AgentDailyArchiveFailed` | The `backup` container of an agent pod started again in the last 2 hours after an exit with an error: the daily archive failed. A restart of the node (reason `Unknown`) does not count | none | `kubectl -n <namespace> logs <pod> -c backup --previous` |
| `AgentInstructionFileChanged` | An `instruction-check file=<name> change=<change>` line in the last 20 minutes: an instruction file of the agent's workspace (`AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, `skills/`) is new, changed or gone since the day before | none | The lines in Loki name each file. Compare it with the daily archive, and find what made the change before the agent runs on it |
| `AgentInstructionCheckStale` | No `instruction-check result=` line of the pilot's agent pod in 26 hours, once its `backup` container has run for 26 hours | 15m | `kubectl -n agents-pilot logs deploy/agent -c backup`. A backup that waits on a gateway that is not ready logs nothing |
| `AgentMemoryIndexFailed` | The newest `memory-index result=` line of the pilot's agent pod is `result=failed`, and it is not older than the start of the newest agent pod. It clears at a start that ends ok. A new pod that has not run the step yet, or no pod, does not fire | none | `kubectl -n agents-pilot logs deploy/agent -c memory-index`. When the model gateway and the model server of the host answer, start the pod again |
| `AgentAutomationsSyncFailed` | The newest `automations-sync result=` line of an agent pod is `result=failed`, in the last 3 hours: the scheduled jobs of the product are not as `automations.json`. A pass that ends ok clears it | none | `kubectl -n agents-finance logs deploy/agent -c automations-sync`. The `reason=` of the pass says what failed |
| `AgentBackupMoverConfigMapDenied` | The admission rule `agents-backup-mover-configmaps` refused a ConfigMap write of a backup mover in a trust domain ([The backup mover and ConfigMaps](#the-backup-mover-and-configmaps)). It fires for one to two hours after each denial (`keep_firing_for: 1h`) | none | The audit lines of that hour in Loki name the namespace, the ConfigMap and the ServiceAccount, and the events of the namespace name the mover Job. Suspend the snapshots of that namespace |
| `AgentBackupRepositoryNotReady` | The backup repository of the trust domains (`agents`) is `Pending` or `Initializing`: it never got `Ready`, so no snapshot of a trust domain runs. `Failed` and `Degraded` are the chart rule `KopiurRepositoryNotReady` | 1h | `kubectl get clusterrepository agents -o yaml`, then the refresher of its session ([Backups of the agent trust domains](#backups-of-the-agent-trust-domains)) |
| `ModelBridgeRateLimited` | An `engine_error` line of the model bridge with the kind `rate_limit` in the last 20 minutes, for each trust domain: the limit of the Claude plan stopped a call | none | The calls of that domain fail until the limit resets. The `engine_error` lines in Loki (`{namespace="ai", app="model-bridge"}`) |

- **Logs in Loki.** `{namespace="agents-workflows", app="workflow-providers"}`,
  `{namespace="agents-workflows", app="workflow-run"}`, and
  `{namespace="agents-<name>", app="platform-checks"}`. Job pods ship
  logs on this cluster, and the long-running pods of the allowlist
  ([Logs](#logs)).
- **A failed Job.** The chart rule `KubeJobFailed` also fires for each
  failed Job, and keeps firing while the Job is kept: a run job for 7 days,
  a failed CronJob run until three newer runs fail. Alertmanager drops it
  for the run jobs and the Jobs of the CronJobs `platform-check-isolation`,
  `platform-check-probe`, `workflow-providers` and `workflow-sweep`, which
  the alerts above cover (the route in
  `kubernetes/apps/monitoring/alertmanager/app/config/alertmanager.yml`, and
  its cases in `observability/tests/test_alertmanager_routes.py`). It still
  reaches the channel for any other Job of a trust domain: a backup mover, a
  Job made by hand, or a new CronJob. It stays in Prometheus and in the
  Alertmanager UI for all of them.
- **The drift line.** The Vector aggregator turns each line of a pod in a
  namespace `agents-*` that starts with `<check> result=<result>` into the
  gauge `homelab_agent_platform_result_timestamp_seconds{pod_namespace, app,
  check, result}`: the time of the newest such line. No other word of the
  line becomes a label. Each sample carries the time of its line, so
  Prometheus holds it for 5 minutes only: a rule reads it with
  `max_over_time` over a window longer than the time between two lines
  (20 minutes for the drift line, which comes every 15). A line
  `instruction-check file=<name> change=<change>` becomes the check
  `instruction-file` with the change as its result; the file name stays in
  Loki. A kind or domain of the bridge outside a short pattern becomes
  `other`. The bridge's `engine_error` lines become
  `homelab_model_bridge_engine_error_timestamp_seconds{domain, kind}` the
  same way. A line read again from the start of its file keeps its old time,
  so it does not fire an alert again. The aggregator makes the gauge of
  each `<check> result=` line; the platform agent makes the gauge of the
  `instruction-check file=` lines and of the bridge, on its own port
  (`homelab-wsl` only).
- **The snapshots** of a state volume have the kopiur chart's rules,
  which match each SnapshotPolicy, `agent-state-v2` too: `KopiurLastBackupFailed`,
  `KopiurBackupStale` (48 hours), `KopiurSnapshotFailed`,
  `KopiurBackupConsecutiveFailures`, and `KopiurRestoreFailed` for a restore.
  They have no runbook link; the runbook has a section for each.
- **The heartbeat check** reads one span of each agent's heartbeat trace:
  `openclaw.run` with `openclaw.trigger=heartbeat` and
  `openclaw.outcome=completed`, for each service in its env `SERVICES`
  (`openclaw-agents-pilot` now). Add a service when a trust domain gets its
  agent; a domain with no agent is not in the list, so it never alerts.
  `KubeJobFailed` also fires for a failed run of the check.
- **Not alerted yet.** The age of the agent image against the upstream
  releases (a check of the agent image's repository), an `engine_error` of
  another kind than `rate_limit`, and a Sandbox record with no pod.
