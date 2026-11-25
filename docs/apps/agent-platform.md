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
| The fetch proxy of the main agent ([Web fetch of the main agent](#web-fetch-of-the-main-agent)) | `agents-main` | `main/fetch-proxy/` |
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
the scripts of the agent pod: the kubelet updates a mounted ConfigMap in the
running pod, and each later start of the pod reads it.
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

## NFS volumes and admission

The NFS server of the cluster (`kubernetes/apps/storage/nfs-server/`) checks
only the address of a client, and a kubelet mounts from the address of its
node. So a pod with an NFS volume in its spec, in a namespace where Pod
Security allows one, would reach each export that its node reaches. That
matters once a trust domain has an export of its own for its vault. Two
admission rules
(`kubernetes/apps/storage/nfs-server/admission/nfsvolumes.yaml`, homelab-wsl
only) list what is allowed:

| Rule | What it lets in |
| --- | --- |
| `nfs-pod-volumes` | A pod with an NFS volume only for a listed namespace, server and exact path: the sites (`production`) and the sites runner (`tools`) on `/export`, and the copy of the network backups (`networking`) on `/gdrive-sync`. The pseudo root, the path of a vault, a variant of a path and another name of the server are not listed |
| `nfs-persistent-volumes` | A PersistentVolume with an NFS source only as a listed volume of a share, with its server, path, read-only flag and the one claim it binds to. Two for each share: read and write for its vault server, read only for its agent and its backup mover |

For now the bindings are `Audit` and `Warn`: a pod or a volume off the list
is let in, with an audit annotation and a warning. A later change turns them
to `Deny`, after a read of the audit lines shows that no user of today is off
the list. Admission sees the API only: a privileged container can still mount
with a system call, and a userspace NFS client needs no mount. The list of
clients of each export is the other control.

A new user of an NFS export goes on the list in the same pull request:
`observability/tests/test_nfs_admission.py` fails until it is. The rules run
on a throwaway API server in `admission/tests/run.sh`
(`uv run --project observability kubernetes/apps/storage/nfs-server/admission/tests/run.sh`).

**Where a refusal shows.** The API server writes the annotation
`validation.policy.admission.k8s.io/validation_failure` into the audit line
of the request (each write is kept at `Metadata`, `talos/wsl/patches/audit-policy.yaml`),
and counts it in `apiserver_validating_admission_policy_check_total` with
`enforcement_action="audit"` or `"warn"`. A warning goes back to the client
only, so a pod that a controller makes shows none. In Grafana, Loki of the
security streams:

```logql
{security_stream="k8s-audit", k8s_cluster="homelab-wsl"}
  |= "validation.policy.admission.k8s.io/validation_failure" |= "nfs-"
```

The scrape of the API server keeps no series of these rules, so read the
counter on the API server itself (it starts again at zero when the API
server restarts):
`kubectl get --raw /metrics | grep 'policy="nfs-'`.

To check: `kubectl get validatingadmissionpolicy nfs-pod-volumes
nfs-persistent-volumes -o yaml` shows `status.observedGeneration` equal to
`metadata.generation` and no `status.typeChecking.expressionWarnings`, and
`kubectl get validatingadmissionpolicybinding nfs-pod-volumes
nfs-persistent-volumes` shows both.

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
| Main | Its own repository: ClusterRepository `agents-main`, prefix `kopiur-main/`, password `kopiur-system/kopiur-repository-agents-main`, role `tnwks-rolesanywhere-backups-agents-main` (certificate CN `homelab-wsl-agents-main-backups`), Secret `aws-backup-sts-agents-main`, policy `agent-state` in `main/agent/app/backup.yaml` ([`backups.md`](../operations/backups.md#the-main-repository)) |

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

## The vault shares

Each trust domain with a vault gets a share of its own: one NFSv4 export on
the NFS server of the cluster (`kubernetes/apps/storage/nfs-server/`), on a
claim of its own. Its vault server is the one writer, and its agent mounts it
read only. The parts come in steps; this table says what is there now.

| Part | Now |
| --- | --- |
| Claims of the shares | `storage/nfs-vault-main` and `storage/nfs-vault-finance`, in the form of `nfs-gdrive-sync`, bound on the node of the server |
| Exports | `/vault-main` (`Export_Id` 3) and `/vault-finance` (4): read and write for the three nodes of homelab-wsl only, each client squashed to uid and gid 1000, the owner of the root (mode 0700) |
| Volumes and claims in the trust domains | Two static PersistentVolumes for each share and their two claims, `vault-rw` and `vault-ro`, in `agents-main` and `agents-finance` (`kubernetes/apps/ai/agents/<domain>/vault-share/`). Bound, and mounted by no pod |
| The vault server of finance | `vault-server` in `agents-finance` (`kubernetes/apps/ai/agents/finance/vault-server/`): one pod on `vault-rw`, behind the route `finance-vault` of the central MCP gateway (below) |
| The backup of the finance share | The SnapshotPolicy `vault` in `agents-finance` (`kubernetes/apps/ai/agents/finance/vault-backup/`): each night, the claim `vault-ro` to the repository `agents-finance`, with the retention of the state volume |
| The restore test of the finance share | A template `kubernetes/apps/ai/agents/finance/vault-backup/restore-test/`, listed for one run: the Restore `vault-restore-test` of one `Succeeded` snapshot of the policy `vault` to the scratch claim `vault-restore-test`, then the Job `vault-restore-check`, which compares it with the live share (`vault-ro`, read only) by path and SHA-256 and logs flags and counts only. The second change takes it out of the list, and Flux deletes the Restore, the Job and the scratch claim ([the runbook](agent-platform-runbook.md#the-restore-test-of-the-finance-share)) |
| The backup of the main share | The SnapshotPolicy `vault` in `agents-main` (`kubernetes/apps/ai/agents/main/vault-backup/`), the same as for finance: each night, the claim `vault-ro` to the repository `agents-main`, with the retention of the state volume. The first run (`runOnCreate`) is of the empty share |
| The seed of the finance share | A one-time Job `vault-seed` in `agents-finance` (`kubernetes/apps/ai/agents/finance/vault-seed/`), which ran once on 2026-11-19 with `result=ok` and is not listed now ([The seed of the finance share](#the-seed-of-the-finance-share)) |
| The mount of the finance agent | The claim `vault-ro` at `/vault` in the agent pod (the product, its repair step and the index step), with `readOnly: true` on the claim and on each mount. `/vault` is the path of the vault server too, so a path from a memory search works in a vault tool call. The index reads `/vault` (`memory.search.extraPaths`, Markdown only) and polls (`CHOKIDAR_USEPOLLING`), because a write of the vault server gives no file event on this mount. The agent calls the nine tools as `vault__*` through the route `finance-vault`, with its key. Until the seed, the index also reads the copy on the state volume |
| Active memory of finance | The stock plugin `active-memory` of the product, with the settings of the host: the agent `main`, direct and group chats, query mode `recent`, prompt style `recall-heavy`, a limit of 25 s, summaries of 220 characters, its log on, the default mode `escalate`. Two settings of the host are not here. Its own model (a small model on Bedrock on the host) needs an entry of the model gateway for this trust domain, an open point; until then a recall runs on the model of the session (`litellm/agents-finance`, through LiteLLM) and counts in the budget of that key. Its list of chat ids is not in git; the finance bot takes the direct chat of the owner only. An eligible reply waits for the recall, up to 25 s |
| Active memory of main | The same plugin with the same settings in the config of the main agent, on the model of the session (the decision of the owner for the first run). It takes effect at the start of the agent, which has no pod yet |
| The vault server of main | `vault-server` in `agents-main` (`kubernetes/apps/ai/agents/main/vault-server/`): the pod of finance in the main trust domain, on its own claim `vault-rw`, behind the route `main-vault` of the central MCP gateway, with the key of the main agent, which calls it (the row below) |
| The mount of the main agent | As finance: the claim `vault-ro` of main at `/vault` in the agent pod (the gateway, its repair step and the index step), with `readOnly: true` on the claim and on each mount and no `subPath`. The index reads `/vault` (`memory.search.extraPaths`, Markdown only) and polls (`CHOKIDAR_USEPOLLING`). The vault tools go through the route `main-vault` with `MCP_GATEWAY_KEY`: the six read tools only until the seed of the share, because the seed stops at a share that is not empty, and the step of the seed adds the three write tools (the gateway allows all nine). The values check of the pod stops the start when the SHA-256 of that key is not the hash of the client `main` |

Each share has two volumes, one for each claim:

| Volume | Claim | Mount | For |
| --- | --- | --- | --- |
| `nfs-vault-<domain>-rw` | `vault-rw` | `nfsvers=4.1,hard`, read and write | The vault server of the trust domain, the one writer: a write waits for the NFS server and does not end half done |
| `nfs-vault-<domain>-ro` | `vault-ro` | `nfsvers=4.1,soft,timeo=50,retrans=2,actimeo=3,ro`, read only | The agent pod (the index) and the backup mover: a read fails with an error about 30 seconds after the NFS server stops, and the chat goes on |

The mount options are the values of the rehearsal; the design leaves them
open. Each volume is on the list of the admission rule
`nfs-persistent-volumes`, with its server, path, read-only flag and claim,
and names its claim; each claim names its volume, with no storage class. So
a claim binds at once, with no pod and no provisioner, and the Flux
Kustomization `agents-<domain>-vault-share` (after `nfs-admission`) is
Ready when both claims are `Bound`. The reclaim policy is `Retain`, and the
files of a share are on its claim in `storage`, not on these objects: when
a volume or a claim goes out of git, Flux deletes it and the files stay.
The source and the claim of a volume can not change after it is made; a
change of either one is a new volume. The export lets each node of the site
write: the option `ro` of the volume of `vault-ro` makes each mount of it
read only, and the vault server is the one writer. The flag
`nfs.readOnly` of the volume does not: for a PersistentVolume the kubelet
takes the read-only flag from the pod (its claim or its mount), so a pod
that does not set it would get a mount that writes.

What is still not there: the seed of the main share, and the change of
the two admission rules to `Deny`.

The server checks the address of a client only, and a kubelet mounts from
the address of its node. So a pod address gets no access, and which pod may
mount which path is the work of the admission rules of NFS volumes
(`kubernetes/apps/storage/nfs-server/admission/`).

A change of the exports restarts the server (Reloader, and a new volume
changes the pod), and with `Recreate` the sites have no NFS server until the
new pod is up. The change of this step was rehearsed on a throwaway cluster
first. Merge such a change outside 02:00 to 02:45 local time (the copy of the
network backups at 02:15 and the copy of the distro at 02:30) and when no
build of the sites runs.

To check: `kubectl -n storage get pvc nfs-vault-main nfs-vault-finance` shows
both `Bound`, `kubectl -n storage get pod -l app.kubernetes.io/name=nfs-server`
shows the new pod `Running` with no restart, and
`kubectl -n flux-system get kustomization nfs-server sites sites-runner` shows
each `Ready`. For the volumes and claims: `kubectl get pv nfs-vault-main-rw
nfs-vault-main-ro nfs-vault-finance-rw nfs-vault-finance-ro` shows each
`Bound` to its claim,
`kubectl -n agents-main get pvc vault-rw vault-ro` (and the same in
`agents-finance`) shows each `Bound`, and
`kubectl -n flux-system get kustomization agents-main-vault-share
agents-finance-vault-share` shows each `Ready`.

### The vault server

The vault server of a trust domain is the one writer of its share. It is
the image `ghcr.io/it3e/tnwks-vault-mcp` (the reference file server of the
MCP project, for the one directory `/vault`, over Streamable HTTP), in the
namespace of its trust domain. The owner chose its form at the gate of
Design Doc v3.42 (section 4.2.5 and section 11 of effort 0003).

| Part | Value |
| --- | --- |
| Pod | A Deployment of one replica, `Recreate`, uid and gid 1000 (the uid that the export squashes each client to), the `restricted` level, a read-only root, no service account token, no Secret, no DNS and no egress |
| Share | The claim `vault-rw` at `/vault`, a hard mount: a call waits while the NFS server is away |
| Probes | A readiness probe of `/healthz`, and no liveness probe |
| Memory | A request of 128Mi and a limit of 256Mi. The peak at 5 rounds of 20 calls at one time was 76.5 MiB with small notes and 184 MiB with notes of 1 MiB, with 12 processes |
| In | The central MCP gateway on port 8080, and the kubelet for the probe of `/healthz` |
| Route | `/finance-vault/mcp` on the central MCP gateway, with the key of the finance agent, and a time of 60 s for each call ([mcp-gateway.md](mcp-gateway.md)) |
| Tools | Nine: `read_text_file`, `read_multiple_files`, `list_directory`, `directory_tree`, `search_files`, `get_file_info`, `write_file`, `edit_file`, `create_directory`. The server has no delete tool, and the gateway refuses each other tool |

The server refuses a path outside `/vault`: `..`, an absolute path, and a
symlink that leads out, for a read and for a write. Through the gateway, a
body over 2 MiB gets a 413 and a bad JSON body a 400 from the gateway, and
the gateway drops an `Origin` header. Straight, the server answers an
`Origin` header with a 403 and any path but `/mcp` and `/healthz` with a
404; only the gateway and the kubelet reach it.

**With the NFS server away**, what a call gets depends on where it waits:

- A path that the node does not have in its cache waits in a file call that
  the server bounds: the call ends with the tool error `Filesystem call
  timed out after 15000 ms`, its write does not land, and the server stays
  ready.
- A note that the node has in its cache (a note read or written a short
  time before) waits in a file call that holds the whole process: the
  health path stops too, and the pod is not ready. The gateway answers that
  call with `504 request timeout` after 60 s, and each new call with an
  error at once. When the NFS server is back, the process goes on by itself
  with no restart, and the write lands, after the client got its error.

So a client that gets an error for a write reads the note again before it
tries again, and an edit with the old text fails on a note that changed.

**The costs that the owner accepted at the gate**, in plain words:

- **The stop with the NFS server away.** Each vault call and the health
  path of the server wait until the NFS server is back, for a note in the
  cache. An operator sees it as: the pod `vault-server` in `agents-finance`
  is `Running` and not ready, with no restart; the alert
  `AgentPlatformWorkloadNotReady` names `vault-server`; the gateway log has
  `"route":"default/finance-vault"` lines with status 504 or 500; and the
  NFS server in `storage` is not ready. Bring the NFS server back; do not
  restart the vault server, which drops the write that waits.
- **No liveness probe.** A process that stops for another reason (a fault
  of the server itself) never restarts by itself. Only the readiness alert
  tells, and a person deletes the pod.
- **Two writers for a short time.** A delete or an eviction of the pod is
  not a rollout: the ReplicaSet starts the new pod at once, while the old
  one still runs (`Recreate` keeps them apart in a rollout only). For that
  time two processes write the share: an edit on changed text fails, and a
  write of a whole file wins over the one before it. With the NFS server
  away, the new pod waits in `ContainerCreating`, and the old one stops
  after its grace time and drops the write that waits.
- **A leaked agent key also writes the vault.** The route takes the key of
  the finance agent, the same key as the finance app route. Whoever has
  that key can call the nine tools, writes included, from a pod that the
  network policy of the gateway lets in. A key of its own for the route is
  the stricter option, and only the owner makes it.
- **60 s.** The gateway waits 60 s for the vault server and for the finance
  app, not its default of 10 s. A hung call holds a turn of the finance
  agent up to 60 s.

### The seed of the finance share

The first notes of the finance share come from the copy of the vault on
the state volume of the finance agent (the folder `vault/` of the claim
`agent-state`, which the restore Job of the cutover made). One Job copies
them, one time, with the go of the owner (section 4.2.5 of the design). The
copy on the state volume stays as it is. The steps are in the runbook
([The seed of the finance share](agent-platform-runbook.md#the-seed-of-the-finance-share)).

| Part | Value |
| --- | --- |
| Where | `kubernetes/apps/ai/agents/finance/vault-seed/`: the Flux Kustomization `agents-finance-vault-seed` (after the share and the agent), the Job `vault-seed`, and the ConfigMap of its script `seed.mjs` and of the config of the secret scan |
| Pod | On the node of the agent pod, where the state volume is. uid and gid 1000, the `restricted` level, a read-only root in each container, no service account token, no Secret, no network (out of the DNS rule; only the default deny selects it) |
| From | The folder `vault/` of the claim `agent-state`. Each container mounts the claim read only at its root (`/state`), with no `subPath`: on this cluster a `subPath` of a `local-path` volume is an empty directory that the kubelet makes (below). Each step reads only `/state/vault` (the scan path and `SOURCE`). The volume is `ReadWriteOnce`, and pods of one node can share it: the Job runs beside the running agent, as the backup mover does |
| To | The claim `vault-rw` (the hard mount), which must be empty |
| Scan | The init container `scan`: the secret scanner of CI, the upstream image of betterleaks (MIT license) at the version of `.pre-commit-config.yaml`, by digest, with the config of this repository (`.betterleaks.toml`), on the whole source. With `--redact`, no value is in its report or its log, and it validates nothing against a vendor |
| Findings | The init container `report` gives each finding an id and logs one line for it (below). The id is the start of a SHA-256 of the rule, the line, the column and the whole file: it stays the same while the file does not change, and it says nothing of the file |
| Copy | The container `copy` copies only when each finding has a decision of the owner, and only into an empty share |

**A finding.** The line is `vault-seed finding id=<id> rule=<rule>
line=<n> decision=<decision> file="<relative path>"`, with the decision
`none`, `skip`, `take`, or `left-out-config` (or `-trash`, `-git`) for a
file that never goes to the share. The form is the one the design sets for
the scheduled scan of a share: the file name, never the content. The pod is
gone about 20 seconds after it ends, so the lines are in Loki, for its 14
days: `{namespace="agents-finance", app="vault-seed"} |= "finding"`. That is
the cost for R1: the names of the notes with a finding are in Loki for 14
days. The owner reads them; the decisions go to git as ids only, in the
lists `SKIP` (the file stays out of the share) and `TAKE` (it goes as it
is) of `job.yaml`. A file with one finding in `SKIP` stays out, whatever its
other findings say.

**The copy.** Plain files and folders only: a link and any other kind of
file stay out, and are counted. The config folder of the notes app
(`.obsidian`), the trash (`.trash`) and `.git` stay out at any depth. Files
get mode 0600 and folders 0700, owned by uid 1000 (the export squashes each
client to 1000, and the vault server and the agent read as 1000); each file
keeps its time. The copy goes to `.seed-incoming-<time>/` in the share,
is counted there, and then each top entry is renamed into place; a failure
before the renames removes that folder. A run that finds such a folder of
a stopped run removes it first.

**What the pod can read.** With the claim at its root, each step can read
the whole state volume: the workspace, the sessions and the config of the
agent too, not only the vault. That is the form of the restore Job and of
the backup mover. The steps read only `/state/vault`: the scan has that one
path, the report hashes only the files the scan names under it (and fails
on any other path), and the copy walks only that folder. The pod has no
network, no service account token and no Secret, and its only output is
its log lines and the files in the share, so nothing else of the volume
can leave it.

**One line at the end.** `vault-seed result=ok source-files=<n>
source-notes=<n> files=<n> notes=<n> bytes=<n> left-out-config=<n>
left-out-trash=<n> left-out-git=<n> left-out-findings=<n> taken-findings=<n>
links=<n> other=<n>`, or `vault-seed result=failed reason=<kind>`: `findings
undecided=<n>`, `share-not-empty`, `stale-decision count=<n>` (an id that is
not a finding of this run), `decision-form`, `decision-twice count=<n>`,
`source-missing` (no folder `vault/`), `source-empty` (no file in it),
`nothing-to-copy` (each file stays out), `copy`, `verify` or `rename`.
Counts only; no name. A seed of nothing is never `result=ok`. The
count `source-notes` is the one to compare with `vault-notes` of the restore
Job of the cutover.

**One run only.** A Job can not run again, so each run has its own name
(`vault-seed`, then `vault-seed-2`), as the restore Job does. The Job is in
git for its run: a pull request lists `vault-seed/ks.yaml` in
`kubernetes/apps/ai/agents/finance/kustomization.yaml`, and the next one
takes it out of that list, so Flux deletes the Kustomization, the Job and
its ConfigMap. The files stay in git for the seed of the main share. A run
on a share that has any file or folder (but a folder of a stopped run)
copies nothing and fails with `share-not-empty`, so a second run never
writes over a note.

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

### Plan use

All trust domains share one Claude plan, and the plan has limits: a five
hour window and a weekly window, and at times a weekly window for one model
family. From version 0.3.0 the bridge logs what the vendor tells it about
these limits, and the platform log agent of Vector
(`kubernetes/apps/monitoring/vector/agent-platform/config/vector.yaml`)
turns two kinds of line into series:

| Line of the bridge | Series | Labels |
| --- | --- | --- |
| `plan_limit`, one for each window | `homelab_model_bridge_plan_utilization_ratio` (the share used, 1 is the limit), `homelab_model_bridge_plan_reset_timestamp_seconds`, `homelab_model_bridge_plan_status` (0 `allowed`, 1 `allowed_warning`, 2 `rejected`), `homelab_model_bridge_plan_observed_timestamp_seconds` (the time of the line) | `window` |
| `call` with an answer | `homelab_model_bridge_tokens_total`, a counter | `domain`, `model`, `token` (`input`, `output`, `cache_read`, `cache_write`) |

- **One plan, so no domain on the plan series.** The newest `plan_limit`
  line of any trust domain sets the value of its window. The domain of the
  line stays in Loki.
- **Lists of values.** `window` is `five_hour`, `seven_day`,
  `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included` or
  `overage`; `domain` is `main`, `finance` or `workflows`; `model` is the
  model of the bridge entries of the model gateway. Each other value reads
  `other`. A value that a line does not give, or that is out of range, is
  -1, and the records read it as unknown. So the series are at most 60 on
  the pod on the node of the bridge, and 12 counters at 0 on each other pod. `observability/tests/test_plan_usage.py` keeps the lists equal to
  the entries of the model gateway, and runs the config in the pinned
  Vector, with its exporter as it is, to prove that the newest line wins,
  the counts add up, and the counter is there at 0 before the first call and
  still there with its count after two flush periods with no call.
- **When a line comes.** The bridge reads the limits on each answer, and
  logs a `plan_limit` line at the first answer after its start, when the use
  moves by a whole percent, when the status, the reset time or the warning
  threshold changes, and at most every 30 minutes while calls go on. With
  no call there is no line. A gauge carries the time of its line, so
  Prometheus shows it for 5 minutes after the line (its lookback), and the
  exporter of Vector drops it 1 to 2 minutes after the line (its flush
  period of 60 s).
- **The token counter lives as long as the pod.** A counter that the
  exporter drops starts again at the next call, and `increase()` counts a
  new series from its second sample: the first build lost tokens that way.
  So the source `token_zero` (`static_metrics`) adds 0 to each declared
  series (domain, model and kind) at the start of the pod and every 30
  seconds: the series is there at 0 before the first call, and the exporter
  never drops it. Each sample of the counter has the time it was counted,
  not the time of the line. Each of the 3 pods of the agent serves these 12
  series at 0; the pod on the node of the bridge counts. A value that reads
  `other` has no zero series and still lives only from its line to the
  flush period after it. A restart of the pod starts the counters again
  from 0, which `increase()` reads as a reset.
- **The records keep the reading.** `homelab:ai_plan_utilization_ratio`,
  `homelab:ai_plan_reset_timestamp_seconds`, `homelab:ai_plan_state` and
  `homelab:ai_plan_observed_timestamp_seconds` (`observability/rules.py`)
  read the newest reading of the last 8 days, of any pod of the agent. A
  reading counts until its window resets, or for 6 hours when the line gave
  no reset time. After the reset the use of the window is not known until
  the next call, and the panels say so.
- **Where it shows.** The tab "Limits and security" of the AI board starts
  with the five hour and the week use, the time to each reset, the state of
  each window, the use over time, the age of the last reading, and the
  tokens of each trust domain
  ([the AI board](../operations/observability-implementation.md#the-ai-board)).
  The alert `ModelPlanNearLimit` warns in the chat when a window is near
  its limit ([Alerts](#alerts)).
- **The model gateway counts too.** Its token series count by key. The
  bridge counts by trust domain, with the cache tokens apart, as the vendor
  gives them. The two do not have to be equal.

## The pilot agent

One personal agent in `agents-pilot` (`pilot/agent/`,
`pilot/signal-daemon/`), with no chat channel now: its test Telegram bot
moved to the main agent (below). The product is
OpenClaw, in the agent image
([iT3E/tnwks-agent-image](https://github.com/iT3E/tnwks-agent-image)), whose
README has the contract of the pod.

**Telegram is off since 2026-11-19.** The owner moved the test bot of the
pilot to the main agent, for the test run of main beside the host
([the runbook](agent-platform-runbook.md#the-test-run-beside-the-host)).
Telegram gives the updates of a bot to one poller, so the change that put
the token in the Secret of main also set `channels.telegram.enabled` to
`false` in the pilot's `openclaw.json`. With it off, the gateway does not
load the Telegram plugin and starts no account
(`observability/tests/test_pilot_channel.py` starts the gateway of the
image with this config and checks it). The rest stays, so the way back is
one revert: the block of the channel, `telegram` in `plugins.allow`,
`commands.ownerAllowFrom`, the token in the pilot's Secret, and the rule to
the Telegram API in its network policy.

The pilot runs on for the platform: the daily heartbeat (it sends no
message to a channel), the backups and the nightly snapshot, the sandbox
gateway, the restore drills and the scheduled checks of `agents-pilot`,
none of which uses the channel. What it gives up: nobody can chat with it,
so a test of a chat turn, a tool call from a chat or an answer in a chat
runs on the main agent now, and the pilot answers no chat at a limit of
the Claude plan. A later step can take the token out of the pilot's Secret
and the Telegram rule out of its network policy (with the rule of the
policy test of main), once the main agent has its own bot.

**Signal is off for now.** The Signal daemon stays in the repository but out of `pilot/kustomization.yaml`, and
the config of the agent has no Signal channel. When the test Signal number
exists, one change turns it on: the daemon in the kustomization, the
`dependsOn` of the agent, the channel, its plugin and its id in
`plugins.allow` in `openclaw.json`,
the rule to the daemon in the agent's network policy, and the two Signal
keys in the secret. Then link the number (below).

| Part | What it is |
| --- | --- |
| `agent` (Deployment) | The gateway of the product, on loopback port 18789, `Recreate`, on the second worker, memory request 2 GiB and limit 4 GiB |
| `agent-config` (ConfigMap) | `openclaw.json`, a read-only file in the pod at `/home/node/config/` (`OPENCLAW_CONFIG_PATH`, with `OPENCLAW_CONFIG_READONLY=1`). Its name has a hash of its content (`agent-config-<hash>`), so a change of the file starts the pod again. Applied with no `${VAR}` substitution ([A start of the agent pod](#a-start-of-the-agent-pod)) |
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
  `brave` (web search), `diagnostics-otel` (the traces and the metrics,
  [Metrics](#metrics)), `memory-core`
  (memory search), `openshell` (the sandbox backend) and `telegram` (the
  channel; with the channel off, the plugin does not load). Without the list, the product enables each plugin that it finds
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
    config change starts the pod again (a new pod template), and the step
    rebuilds.
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
| Each day | `backup` container (`agent/config/scripts/daily-backup.sh`): once the gateway is ready, `openclaw backup create --verify` to `archives/daily/`. The command checks that the gateway owns the state, and it can only see the gateway process in the same PID namespace, so the pod shares one (`shareProcessNamespace: true`). The command also captures each file of the config, and it refuses an include file that is a link; in the projected volume of the config each file is a link. So in the main agent, whose config includes the group file, the container runs the script on a copy of the two files in `/tmp/backup-config/` (regular files, made at each start of the container), and the archive holds that copy | The last 7 |
| Each day, after that backup | The same container: the SHA-256 of `AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md` and each file under `skills/`, compared with the list of the day before (`archives/instruction-files/`). One log line for each file that is new, changed or gone, then `instruction-check result=...` | 30 days of lists |
| Each start of the Signal daemon | `archive` init container: a `tar.gz` of `data` in `archives/start/`, with the signal-cli release | 14 days |
| Nightly | kopiur copies each volume, with its archives, to the repository of the agent trust domains (`*/app/backup.yaml`, [Backups of the agent trust domains](#backups-of-the-agent-trust-domains)) | As the other volumes |

A failed archive step stops the start of its pod. A failed daily backup ends
the `backup` container, so the restart shows on the pod. The first archive
with the name of a release holds the state from before that release; restore
that one after a failed update.

### A start of the agent pod

The pod of each agent (pilot and finance) starts again only when its pod
template changes, and one merge gives one rollout:

- **The config and the scripts.** `agent/app/kustomization.yaml` makes the
  ConfigMaps `agent-config` and `agent-scripts` with a hash of their content
  in the name (`agent-config-<hash>`), and Kustomize puts that name in the
  Deployment. A change of `openclaw.json`, of `automations.json` or of a
  script is a new name, so it is a change of the pod template. Flux applies
  the ConfigMaps and the Deployment in one apply, with no `${VAR}`
  substitution in the ConfigMaps (the annotation
  `kustomize.toolkit.fluxcd.io/substitute: disabled`), and prunes the old
  ones. This is the documented way of Kustomize: a new ConfigMap for each
  change of its content
  ([Kubernetes docs, Kustomize, generatorOptions](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/#generatoroptions)).
  The MCP gateway does the same with its config.
- **The Secrets.** Reloader starts the pod again when a Secret that it uses
  changes (`agent-secret`, `agent-search-secret`, and the client
  certificate `openshell-client-tls` at a renewal): the annotation
  `secret.reloader.stakater.com/auto: "true"` watches Secrets only
  ([Reloader README, v1.4.24](https://github.com/stakater/Reloader/blob/v1.4.24/README.md)).
  The Secrets of SOPS have fixed names, so they need it. A merge that
  changes a Secret and the pod template together can still give two
  rollouts, so change a Secret in a pull request of its own.

Before 2026-11-17 the config had a Flux Kustomization of its own, and
Reloader watched the ConfigMaps too. A merge that changed the config and the
pod template then gave two rollouts: Reloader for the ConfigMap, then Flux
for the template about 20 seconds later. Kubernetes stops the first new pod
for the second one, in the middle of its init steps
([Kubernetes docs, Deployments, rollover](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rollover-aka-multiple-updates-in-flight)).
On 2026-11-17 at 09:41 UTC that stopped a pod of finance in its
`official-plugins` step, and the same step of the next pod failed four
times before it passed: the agent was away for about five minutes. The
grafana HelmRelease has the same lesson (Reloader on its Secret only).

**Recreate.** The Deployment has the `Recreate` strategy: Kubernetes stops
the old pod, waits until it is gone, and only then makes the new one
([Kubernetes docs, Recreate](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#recreate-deployment)).
That is right for this pod: one gateway process owns the state on one
`ReadWriteOnce` volume, and the `archive` step reads the state with no
writer. A rolling update would run two gateways on one state. The guarantee
holds for a rollout only: a pod deleted by hand gets its replacement at
once, while the old one still stops (the same page).

**What a rollout costs.** The agent is away from the stop of the old pod
until the new pod is ready: the init steps, then the start of the gateway.
Measured on 2026-11-16 (pilot) and 2026-11-17 (finance), about 2 minutes:

| Step | Time |
| --- | --- |
| `values-check` (finance only), `prepare` | Under 2 seconds |
| `archive` | 2 to 4 seconds |
| `official-plugins` | About 22 seconds |
| `doctor` | About 33 seconds |
| `memory-index` | 7 to 9 seconds for an update of the index; a full rebuild takes longer, up to its limit of 180 seconds |
| The gateway, to ready | About 45 seconds |

No scheduled job of the product runs while the gateway is down. So merge a
change of an agent at a quiet time, and not at the time of one of its
scheduled jobs.

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
  list starts the pod again (the ConfigMap gets a new name, so the pod
  template changes), and the pass at the start applies it.
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
- **Turn the jobs off, and the way back.** The jobs are in the state
  database of the product, not in git or in the pod. So a revert that
  takes out the sidecar, or `automations.json`, in one step leaves each job
  as it is, on: with no file the sync prints `automations-sync result=none`
  and changes nothing, and with no sidecar nothing changes the jobs. The
  way back is two steps:
  1. A pull request sets `"enabled": false` on each entry (each job stays,
     off), or sets the list to `{"jobs": []}` (the sync removes each
     `flux:` job). A list that does not parse is not a way back: it changes
     nothing (`reason=bad-list`).
  2. The merge gives the ConfigMap a new name, so the pod starts again
     ([A start of the agent pod](#a-start-of-the-agent-pod)), and the pass
     at the start applies the list once the gateway answers (about 30
     seconds after the gateway started, on 2026-11-17). With `false` the
     lines are `automations-sync key=<key> result=ok state=off` for each
     job, then `automations-sync result=ok jobs=<n>`. With `{"jobs": []}`
     they are `automations-sync key=<key> result=removed` for each job,
     then `automations-sync result=ok jobs=0`.

  Only after those lines, a second pull request takes out the sidecar or
  the list, if that is the goal. A job that is off stays off in the state;
  a removed job is gone. A job that a chat turns on while git says `false`
  goes off again at the next pass. To stop one job at once, with no merge,
  stop it in a chat: it stays off (`state=stopped`) until git changes from
  `false` to `true`. With `replicas: 0` no job runs, because no gateway
  runs; the jobs stay in the state as they are, and run again when the pod
  starts. The runbook has the steps
  ([Turn off the scheduled jobs](agent-platform-runbook.md#turn-off-the-scheduled-jobs)).
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
- **How a script runs.** A script goes in a file of the workspace and runs
  as one command alone: `python3 <file>.py`, with its arguments if any. The
  exec tool of OpenClaw checks each command before it runs it (the exec
  preflight, `src/agents/bash-tools.exec-script-preflight.ts` at release
  2026.11.3), and it refuses a `.py` or `.js` file that runs inside a
  compound command: after `cd <dir> &&`, before `; <next command>`, on a
  second line, or inside `sh -c` or `bash -c`. It refuses a script that is
  piped into an interpreter (`cat <file>.py | python3`) too. The error says
  "complex interpreter invocation detected". A pipe out of the script
  (`| head`) and a redirect (`> out.txt`) pass. The check also reads the
  file and refuses a shell variable such as `$HOME` in the Python code
  (outside a string): read the environment with `os.environ.get`. An
  inline script (`python3 -c`, or a heredoc to `python3 -`) passes the
  check, but keep each script in a file, so that the owner can read it and
  run it again.

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

## Main agent Control UI

Open [openclaw.internal.tnwks.us](https://openclaw.internal.tnwks.us) from
the LAN. Sign in with a Cognito account in the `admins` group. The internal
ingress uses the existing oauth2-proxy session and passes the verified email
to OpenClaw on HTTP requests and WebSocket upgrades. OpenClaw automatically
pairs the browser for its standard operator scopes, including chat. The
gateway password is for CLI access from inside the pod.

The wildcard DNS record and TLS certificate already cover this hostname.
The gateway Service is a ClusterIP. Cilium only admits connections to its
gateway port from the local `ingress-nginx-internal` controller pods.

## Web fetch of the main agent

The main agent fetches web pages with the `web_fetch` tool of the product,
through a proxy of its own trust domain: the fetch proxy, in `agents-main`
(`kubernetes/apps/ai/agents/main/fetch-proxy/`). The agent pod has no route
to a web site; the proxy has no secret. The owner chose this on 2026-11-19,
over any site from the agent pod, an allow list of sites, or no web fetch
in the first step. While the agent pod is at zero replicas, the proxy runs
and nothing calls it.

**The way of a fetch.**

1. The model calls `web_fetch` with a URL. The gateway process makes the
   call, not a sandbox.
2. The product checks the host name before it sends anything: no IP
   address in a private, loopback, link-local or special range, no
   `localhost`, and no name that ends in `.local` or `.internal`. It checks
   each redirect again, at each hop.
3. `tools.web.fetch.useTrustedEnvProxy: true` (`main/agent/config/openclaw.json`)
   makes the product send the request to the proxy in `HTTPS_PROXY`, and
   leave the DNS lookup to the proxy. The env comes from the ConfigMap
   `agent-fetch-proxy` (`main/agent/app/kustomization.yaml`), on the whole
   `agent` container (below).
4. The product sends `CONNECT <host>:443` to the proxy. The proxy (Envoy,
   `fetch-proxy/app/envoy.yaml`) takes it only for a host name on port 443.
   Any other request gets a 403: an IP address, a name with no dot, another
   port, or a request that is not CONNECT (so a plain `http://` URL fails).
5. The proxy looks up the name itself, with no search domain of the
   cluster, and drops each address in a private, shared (CGNAT), loopback,
   link-local, multicast or special range, and each IPv6 address. With no
   address left, the answer is a 503, and nothing goes out.
6. The proxy opens the TCP connection, and the TLS session goes through it
   from the product to the site. The proxy sees the host and the port, never
   the path, a header or the body.
7. The network policy of the proxy (`fetch-proxy/app/networkpolicy.yaml`)
   lets in the agent pod only (and the kubelet for the probes), and lets
   out port 443 of an address outside the cluster that is not in the same
   ranges. A CIDR rule of Cilium never selects a pod or a node of the
   cluster. That is the second wall, under the proxy.

**What goes through the proxy.** The proxy env is on the whole `agent`
container, and the gateway process makes it the default of its own HTTP
client at its start. So each HTTP call of that container to a host outside
`NO_PROXY` goes through the proxy, not `web_fetch` alone: a plugin, a
dependency and a tool call of the model too. The process with the keys of
main can now reach each public host on port 443 through the proxy; before,
it reached only the API hosts that its network policy names. The proxy
adds the filter of inside addresses and a log line with the host of each
of these calls. The design accepts this reach (threat row 34 of the
design).

**What stays direct.** `NO_PROXY` names the services of the cluster
(`.svc.cluster.local`: the model gateway, the sandbox gateway, the trace
collector), loopback, and the two host names of the agent's network policy
(`api.telegram.org`, `api.search.brave.com`). The chat API is polled about
each 30 seconds, so through the proxy each poll would be a log line, and
the chat would stop when the proxy stops. A miss in `NO_PROXY` fails
closed: the proxy refuses a name that resolves inside. A `web_fetch` of a
name on the list does not use the proxy and is not in its log: a URL on
one of the two API hosts goes direct, and a name in the cluster is refused
by the product (`.local`).

**The log.** One JSON line for each request on the standard output of the
proxy: the time, the method, the host and port, the answer, the Envoy
flags (`DF`: no address left after the lookup), the address of the site,
the bytes in and out, and the time it took. No path, query, header or
body. Read it with `kubectl -n agents-main logs deploy/fetch-proxy`. The
host names show the sites that the household visits, so the log is
household-private (R1). By the owner's decision of 2026-11-19 it stays on
the pod and does not go to Loki: the pod has no label
`logs.home.arpa/ship`, so the platform log agent does not take it, and the
shared log agent of this cluster takes Job pods only ([Logs](#logs)). The
node keeps it in the log file of the container, which the kubelet rotates,
and it is gone when the pod goes. A test fails if the pod gets the label,
if the proxy becomes a Job, or if either log agent changes the selector
that keeps it out.

**What the proxy does not stop.**

- **Data in a URL.** The model can put words of a chat in the path or the
  query of a URL to any public site, and the site gets them. The proxy
  logs the host only, so its log does not show it either. The session
  store of the agent holds the URL, like each tool call.
- **What a site sends back** is text for the model, which a page can use
  to give it instructions. The product marks the answer of `web_fetch` as
  content from the network (`resultContentSource: "network"`), and the
  proxy does not read it.
- **The site sees the public address of the home connection,** as for a
  browser at home.
- **The public address of the site.** A public name that resolves to the
  public address of the site passes both walls, and the router of the site
  answers it from inside. `web_fetch` sends a GET only, so its reach there
  is small. The design accepts this; a later step adds that address to the
  inside ranges of both walls, if it stays the same.

**Limits.** `http://` sites, sites on a port other than 443, and sites with
IPv6 only fail. The address filter of Envoy (`resolved_address_filter`)
uses a type that Envoy marks as work in progress, outside its threat model
(Envoy warns at the start): one more reason for the same ranges in the
network policy. The tests check that both lists are the same.

**The image** is the upstream distroless image of Envoy, pinned by digest,
as user 65532 with a read-only root file system and no capability. It has
no volume that it can write (only its config, read only) and no admin
interface. Renovate merges a patch
or a new digest when the checks pass (`kubernetes/apps/ai/**`), and the
checks run the proxy with the new image in Docker
(`observability/tests/test_main_fetch_proxy.py`, the lab test: each request
inside is refused, and the same config with no address filter connects
inside).

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
| The agent pod of the pilot: the init containers and the `backup` and `sandbox-start` containers | `prepare`: nothing. `archive`: one `archive-state result=ok release=... name=... bytes=...` line, and an `archive-state removed=<file>` line for each old archive. `official-plugins`: the output of the product's installer (the install paths in the state directory), on a failure the reason and the stack of its CLI (`[openclaw] Reason: ...`, from `OPENCLAW_DEBUG=1`; the step has no secret), and `official-plugins id=brave version=... result=installed\|failed`, or `official-plugins result=failed reason=...`. `doctor`: the report of the product's doctor (what it changed in the config copy, the plugins and skills it found, warnings), with file and setting names. `memory-index`: `memory-index result=ok\|failed`. `backup`: the output of the product's backup command (the paths it took or skipped, the archive name, `Archive verification: passed`), `daily-backup result=ok\|failed day=...`, `daily-backup removed=<file>`, `instruction-check file=<name> change=new\|changed\|gone` and `instruction-check result=first\|ok ...`. `sandbox-start`: `sandbox-start name=<name> result=ok\|failed`. `automations-sync` (finance): `automations-sync key=<key> result=ok state=on\|off\|stopped`, `result=removed\|failed`, and one `automations-sync result=ok jobs=<n>\|failed reason=<reason>` line for each pass | Some KB, most of it at a start of the pod |
| The model bridge (`ai`) | One JSON line for each event: `call` (domain, model, status, time), `engine_error` (domain, kind), `engine_init`, `conversation_end`, `rebuild` and `listening`. A conversation is an id; no prompt and no answer | Some KB |

Not in Loki, on purpose:

- **The agent container of an agent pod.** Each agent pod excludes it with
  the annotation `vector.dev/exclude-containers: agent`: its log can hold
  chat content and the name of its bot. The agent's log does not ship; a
  count of fixed error patterns does. For main and finance (the agents with
  Telegram), the platform agent reads that log on the node only to count
  the error lines of the channel and the refuse line of the approval plugin
  (the source `agent_channel` and the
  transform `agent_channel_count`): a line of a known pattern becomes one
  count with two labels, the namespace and the name of the pattern, and
  each other line is dropped. No word of a line leaves the node, and the
  count goes to Prometheus, not to Loki ([Metrics](#metrics)). The file
  source does not log a discarded line: Vector's own log, which can be read
  in `monitoring`, holds no byte of a line of the agent, because the reader
  of the file sources logs nothing (`VECTOR_LOG` in the HelmRelease), and
  containerd splits a line at 16 KiB.
- **Every other pod of the trust domains**: the gateways and supervisors of
  `agents-pilot`, `agents-main` and `agents-finance`, the agent pods of main
  and finance, and the Signal daemon (its log holds phone numbers). Read
  them with `kubectl logs`; a pod's log is gone when the pod goes.
- **The workload pod of a sandbox** (`openshell.ai/boundary-role: workload`):
  the code that an agent runs. The run job logs the result of a run.
- **The other pods of `ai`**: the model gateway and the browser tool.

## Metrics

Each personal agent (pilot, finance, main) sends the metrics of the product
to Alloy, next to its traces (`diagnostics.otel.metrics` in its
`openclaw.json`, every 60 seconds, as cumulative counters). Alloy keeps 8
instruments and their labels, and writes them to the Prometheus of the site
on its OTLP path (`otelcol.processor.filter` and `otelcol.processor.transform`
`agent_metrics` in `kubernetes/apps/monitoring/alloy/app/helmrelease.yaml`).
The AI board shows them on its Messages tab
([the AI board](../operations/observability-implementation.md#the-ai-board)).

| Instrument (series in Prometheus) | Labels kept | What it answers |
| --- | --- | --- |
| `openclaw.message.received` (`openclaw_message_received_total`) | `channel`, `source` | Messages in |
| `openclaw.message.processed` (`openclaw_message_processed_total`) | `channel`, `outcome` | The outcome of each turn |
| `openclaw.message.delivery.started` (`openclaw_message_delivery_started_total`) | `channel`, `delivery.kind` | Replies sent |
| `openclaw.message.delivery.duration_ms` (`openclaw_message_delivery_duration_ms_milliseconds_bucket`, `_count`, `_sum`) | `channel`, `delivery.kind`, `outcome`, `errorCategory` | Replies that failed, and their time |
| `openclaw.tokens` (`openclaw_tokens_total`) | `agent`, `channel`, `provider`, `model`, `token` | Tokens, as the product counts them |
| `openclaw.session.turn.created` (`openclaw_session_turn_created_total`) | `agent`, `channel`, `trigger` | Turns by trigger |
| `openclaw.session.stuck` (`openclaw_session_stuck_total`) | `state` | A turn that does not end |
| `openclaw.model.failover` (`openclaw_model_failover_total`) | `provider`, `model`, `failover.to_provider`, `failover.to_model` | A move to a second model |

Each label has the prefix `openclaw.` (in Prometheus `openclaw_`).

- **Keep lists, at two places.** Alloy drops each other instrument, and each
  other label of a data point. It cuts the resource to `service.name`, so
  Prometheus makes no `target_info`, and `job` is the service name
  (`openclaw-agents-<domain>`). A service name that is not one of the three
  reads `undeclared`. The scope loses its attributes and its version.
- **Declared values.** Each kept label has a list of values in the
  transform, and a value that is not on it reads `other`: a channel, a
  model or provider that no config names, or a value that is not a string.
  The models and providers are the ones of the three configs
  (`observability/tests/test_agent_metrics.py` holds them together). A new
  channel, model or named agent adds its value to the list in the same
  pull request.
- **Never kept.** A session key, a chat, a message, a skill, the hash of an
  actor, a reason of a failover, and the host and process attributes of the
  resource. The test of the keep list sends each one through the real Alloy
  and Prometheus, and fails when one comes out.
- **The write path.** Prometheus takes OTLP on `/api/v1/otlp/v1/metrics`
  (`enableOTLPReceiver`). Its network rule lets Alloy of the same site POST
  there and nothing else, and refuses each other writer; the daily
  `prometheus-rule-check` proves that a reader can not write
  ([the network rule](../operations/observability-implementation.md#the-network-rule-of-prometheus)).
- **The senders.** The network rule of Alloy admits OTLP from the agent
  pods, the sandbox supervisors of `agents-workflows` and the daily test
  span only, so no other pod can add series or false counts
  ([the network rule of Alloy](../operations/observability-implementation.md#the-network-rule-of-alloy)).
  An agent pod can still send false counts of its own.
- **A restart of the agent pod resets the counters.** The series have no
  `instance` label, so `increase` and `rate` read across the restart.
- **One agent at a time.** A change of `openclaw.json` restarts the pod of
  that agent (the ConfigMap has a hash in its name). So the metrics turn on
  in three merges: pilot, then finance, then main, each after the series of
  the one before are right. `METRICS_ON` in
  `observability/tests/test_agent_metrics.py` names the agents that send.
- **The workflow agents** do not send metrics: their runs are short Jobs.
- **The error lines of the chat channel.** One more series does not come
  from the metrics of the product: `homelab_agent_channel_error_lines_total`,
  a counter that the platform agent of Vector serves from the log of the
  agent container of main and finance ([Logs](#logs)). Its labels are
  `pod_namespace` (`agents-main` or `agents-finance`) and `pattern`:
  `send_failed` (a send or an edit of a message that failed after its
  retries or at its timeout, or a final reply, a block of it or its final
  delivery that failed),
  `poll_stall` (no completed poll for 2 minutes), `poll_restart` (the poll
  ended and starts again after a wait) and `channel_restart` (the health
  monitor of the product restarted the channel, or could not), and
  `plugin_refuse` (the host approval plugin logged that it refuses each
  tool call, `host-approval: refusing each tool call:`). The config
  lists the lines of the product and of the plugin for each (OpenClaw
  2026.11.3, agent image 0.5.0). Each pair
  has a series at 0 from the start of the Vector pod, and a restart of that
  pod starts the counter again; `plugin_refuse` has its series at 0 only
  for the agents that load the plugin (main). `AgentChannelFailing` reads
  the four patterns of the channel, and `AgentPluginRefusingTools` reads
  `plugin_refuse`.

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
`warning`. Most read kube-state-metrics; `WorkflowAppDrift`, `MainGithubAppDrift`,
`AgentInstructionFileChanged`, `AgentInstructionCheckStale`,
`AgentMemoryIndexFailed`, `AgentAutomationsSyncFailed`,
`ModelBridgeRateLimited` and `ModelPlanNearLimit` read lines of the logs, as
gauges of Vector: the
aggregator for the `<check> result=` lines (`WorkflowAppDrift`, `MainGithubAppDrift`,
`AgentInstructionCheckStale`, `AgentMemoryIndexFailed`,
`AgentAutomationsSyncFailed`),
the platform agent for the `instruction-check file=` lines and the bridge
(`AgentInstructionFileChanged`, `ModelBridgeRateLimited`, and through the
records of [Plan use](#plan-use) `ModelPlanNearLimit`) ([Logs](#logs)).
`AgentBackupMoverConfigMapDenied` reads a counter of the API server,
`AgentBackupRepositoryNotReady` the phase gauge of the backup operator,
`AgentTurnNotEnded` two counters of the product ([Metrics](#metrics)), and
`AgentChannelFailing` the count of the error lines of the chat channel that
the platform agent makes on the node ([Logs](#logs)), and
`AgentPluginRefusingTools` the refuse line of the approval plugin in the
same count.

[`agent-platform-runbook.md`](agent-platform-runbook.md) has a section for
each alert (what it means, the first checks, what was seen so far, the fix),
and each rule's `runbook_url` links to it. It also has the restore of the
state of an agent, the fast stop of the workflow agent, the move to Bedrock
by hand, and the steps at a limit of the Claude plan.

The model gateway has 2 rules of its own, in
`kubernetes/apps/ai/litellm/app/prometheusrule.yaml`: the certificate alert,
and `ModelGatewayCallsFailing`, which reads the series of the gateway
([`litellm.md`](litellm.md#metrics)).

Each alert, and each alert of the model gateway, opens the AI board in
Grafana (`tnwks-ai`) for the site whose Prometheus raised it. The
board has a state for each part, scheduled check, result line and snapshot
policy that `observability/inventory.py` declares
([the AI board](../operations/observability-implementation.md#the-ai-board)).

| Alert | Fires when | `for` | First step |
| --- | --- | --- | --- |
| `AgentPlatformWorkloadNotReady` | A Deployment or StatefulSet of a trust domain or of the sandbox controller, or the model gateway or the model bridge, has fewer ready pods than wanted | 10m | `kubectl -n <namespace> describe pod`: a failed `archive`, `official-plugins` or `doctor` init container, or a lock on the state volume. Revert the last image if it came with an update |
| `AgentPlatformCronJobFailed` | The last run of a CronJob of a trust domain failed, and no run passed since: the providers job, the sweep, a daily check. It resolves at the next good run | none | Its log in Loki (below). A `result=failed` line of the providers job names the step |
| `WorkflowProvidersStale` | No good run of `workflow-providers` in 35 minutes, or the CronJob is gone. A token of the reviewer lives one hour, so about 25 minutes are left | 5m | `kubectl -n agents-workflows get cronjob,jobs`, then the log of the last run |
| `WorkflowAppDrift` | An `app-drift result=drift` line in the last 20 minutes: the App or its installation has more permissions than the list in git | none | The `detail=` of the line names each one. Set the App back, or change the list by a pull request |
| `MainGithubTokenStale` | No good run of the token job of the App of main (`github-token` in `agents-main`) in 35 minutes, or the CronJob is gone: the last run failed or did not run. Its token lives one hour, so about 25 minutes are left before a call of the code host server gets 401 | 5m | `kubectl -n agents-main get cronjob,jobs -l app.kubernetes.io/name=github-token` |
| `MainGithubAppDrift` | A `github-token result=drift` line in the last 40 minutes: the App of main or its installation has more permissions or repositories than the design | none | The `what=` of the line names it ([github-token.md](github-token.md)) |
| `AgentHeartbeatMissing` | The daily `agent-heartbeat-check` (06:33 UTC) found no completed heartbeat run of an agent in Tempo in the last 26 hours, or could not ask Tempo. It asks 3 times, 60 seconds apart, and a failed pod gets one more try, so one short break of Tempo does not fire. It resolves at the next good run | none | Its log in Loki (`{namespace="monitoring", app="agent-heartbeat-check"}`): `result=missing` names the service, `result=error` is Tempo. Then the agent pod and its traces |
| `AgentHeartbeatCheckStale` | The heartbeat check itself was not scheduled in 26 hours, or its CronJob is gone | 15m | `kubectl -n monitoring get cronjob agent-heartbeat-check` |
| `AgentPlatformCheckStale` | A daily check of a trust domain (`platform-check-*`), or the daily key check of the model gateway (`ai/litellm-key-check`), has not passed in 26 hours, or never passed once a run was due | 15m | The CronJob (suspended?) and its Jobs |
| `AgentRunSandboxTooOld` | A run sandbox (`default--run-*`) in `agents-workflows` is older than 80 minutes: the run job and the sweep both failed to delete it | 10m | The CronJob `workflow-sweep`, then delete the sandbox through the gateway |
| `AgentWorkflowRunFailed` | A run job failed, for 2 hours from its start | none | The annotation `workflow-agents/result` of the Job, and the run log in Loki. A 502 from the model gateway can be the limit of the Claude plan |
| `AgentDailyArchiveFailed` | The `backup` container of an agent pod started again in the last 2 hours after an exit code from 1 to 127: the daily archive failed. A stop of the node (exit 143 from SIGTERM, or 255 with reason `Unknown`) does not count | none | `kubectl -n <namespace> logs <pod> -c backup --previous` |
| `AgentInstructionFileChanged` | An `instruction-check file=<name> change=<change>` line in the last 20 minutes: an instruction file of the agent's workspace (`AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, `skills/`) is new, changed or gone since the day before | none | The lines in Loki name each file. Compare it with the daily archive, and find what made the change before the agent runs on it |
| `AgentInstructionCheckStale` | No `instruction-check result=` line of the pilot's agent pod in 26 hours, once its `backup` container has run for 26 hours | 15m | `kubectl -n agents-pilot logs deploy/agent -c backup`. A backup that waits on a gateway that is not ready logs nothing |
| `AgentMemoryIndexFailed` | The newest `memory-index result=` line of the pilot's agent pod is `result=failed`, and it is not older than the start of the newest agent pod. It clears at a start that ends ok. A new pod that has not run the step yet, or no pod, does not fire | none | `kubectl -n agents-pilot logs deploy/agent -c memory-index`. When the model gateway and the model server of the host answer, start the pod again |
| `AgentAutomationsSyncFailed` | The newest `automations-sync result=` line of an agent pod is `result=failed`, in the last 3 hours: the scheduled jobs of the product are not as `automations.json`. A pass that ends ok clears it | none | `kubectl -n agents-finance logs deploy/agent -c automations-sync`. The `reason=` of the pass says what failed |
| `AgentBackupMoverConfigMapDenied` | The admission rule `agents-backup-mover-configmaps` refused a ConfigMap write of a backup mover in a trust domain ([The backup mover and ConfigMaps](#the-backup-mover-and-configmaps)). It fires for one to two hours after each denial (`keep_firing_for: 1h`) | none | The audit lines of that hour in Loki name the namespace, the ConfigMap and the ServiceAccount, and the events of the namespace name the mover Job. Suspend the snapshots of that namespace |
| `AgentBackupRepositoryNotReady` | The backup repository of the trust domains (`agents`) is `Pending` or `Initializing`: it never got `Ready`, so no snapshot of a trust domain runs. `Failed` and `Degraded` are the chart rule `KopiurRepositoryNotReady` | 1h | `kubectl get clusterrepository agents -o yaml`, then the refresher of its session ([Backups of the agent trust domains](#backups-of-the-agent-trust-domains)) |
| `ModelBridgeRateLimited` | An `engine_error` line of the model bridge with the kind `rate_limit` in the last 20 minutes, for each trust domain: the limit of the Claude plan stopped a call | none | The calls of that domain fail until the limit resets. The `engine_error` lines in Loki (`{namespace="ai", app="model-bridge"}`) |
| `ModelPlanNearLimit` | The newest reading of the five hour window or a week window of the Claude plan is near the limit (`allowed_warning`, the warning of the vendor) or at it (`rejected`), and the window did not reset since ([Plan use](#plan-use)). The overage windows do not fire | 5m | The tab "Limits and security" of the AI board: the use and the reset time of each window. Slow the scheduled work of the agents, or wait for the reset |
| `AgentTurnNotEnded` | A personal agent took a message and has not ended its turn for 30 minutes: its count of messages in (`openclaw_message_received_total`) is higher than its count of messages that ended (`openclaw_message_processed_total`, completed, skipped or error), by channel ([Metrics](#metrics)). It catches a turn that never ends (a model call, a tool or a streamed part that hangs), not a final reply that hangs or fails after the turn ended. A quiet evening has no message and does not fire | 30m | "Messages with no end" on the Messages tab of the AI board, then [its runbook](agent-platform-runbook.md#agentturnnotended). A restart of the agent pod ends it |
| `AgentChannelFailing` | The Telegram channel of main or finance logged error lines in the last 30 minutes, by the count of fixed patterns on the node ([Logs](#logs)): one `send_failed` line (a reply or a part of it did not reach the chat) or one `channel_restart` line, or 3 lines of `poll_stall` or of `poll_restart` (the poll stopped and two restarts did not bring it back). It catches the two cases that `AgentTurnNotEnded` does not: a final reply that hangs or fails after the turn ended, and a poll that stops. It ends 30 minutes after the last line | none | The heads of the lines on the pod, then [its runbook](agent-platform-runbook.md#agentchannelfailing) |
| `AgentPluginRefusingTools` | The host approval plugin of an agent (main) logged that it refuses each tool call, in the last hour, by the count of its fixed line on the node ([Logs](#logs)). The agent chats but has no tool, the vault and the read routes too. The values check stops a fault of the form at the start, so this is a fault at run time. The plugin logs the line once for each fault, so it fires at once and ends one hour after the line | none | The heads of the plugin lines on the pod (a `loaded` line after it: the plugin loaded), then [its runbook](agent-platform-runbook.md#agentpluginrefusingtools) |
| `ModelGatewayCallsFailing` | At least 3 model calls of the key of a trust domain failed in 15 minutes, and more than half of its calls. One turn with its retries does not fire it | 20m | "Failed model calls by status" on the Model calls tab of the AI board: 429 the limit of the plan, 401 or 403 a key or a model list, 5xx the bridge, Bedrock or the vendor |

- **Logs in Loki.** `{namespace="agents-workflows", app="workflow-providers"}`,
  `{namespace="agents-workflows", app="workflow-run"}`, and
  `{namespace="agents-<name>", app="platform-checks"}`. Job pods ship
  logs on this cluster, and the long-running pods of the allowlist
  ([Logs](#logs)).
- **A failed Job.** The chart rule `KubeJobFailed` also fires for each
  failed Job, and keeps firing while the Job is kept: a run job for 7 days,
  the Job of a CronJob for 26 hours after it ended. Each CronJob of a trust
  domain and of `ai` deletes a finished Job after 26 hours
  (`ttlSecondsAfterFinished: 93600`), a failed one too: a day and room for
  the next run to end. So `AgentPlatformCronJobFailed` and the state of the
  schedule on the AI board still see a failure until a newer run ends, and
  `KubeJobFailed` clears by itself when no newer run fails. The one-off Jobs
  that Flux applies (a restore, the seed, a restore check) get no time to
  live: Flux would make a deleted Job again, and it would run again
  (`observability/tests/test_agent_job_ttl.py`). Alertmanager drops it
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
  (comma separated:
  `openclaw-agents-pilot,openclaw-agents-finance,openclaw-agents-main` now).
  Add a service after the first heartbeat of a new agent is in Tempo; a
  domain with no agent is not in the list, so it never alerts. Each service
  gets its own tries and its own log line, and one missing service fails
  the run.
  `KubeJobFailed` also fires for a failed run of the check.
- **Not alerted yet.** The age of the agent image against the upstream
  releases (a check of the agent image's repository), an `engine_error` of
  another kind than `rate_limit`, and a Sandbox record with no pod.
