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
| Trust domain of the workflow agents | `agents-workflows` | `workflows/` |
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
  system with an `emptyDir` on `/tmp`, and a memory limit of 2 GiB for at
  most 8 live conversations (`BRIDGE_MAX_CONVERSATIONS`).
- **Secret** `model-bridge-secret` (`secret.sops.yaml`):
  `CLAUDE_CODE_OAUTH_TOKEN` (the token of `claude setup-token`; it lives one
  year), and `BRIDGE_KEY_MAIN`, `BRIDGE_KEY_FINANCE` and
  `BRIDGE_KEY_WORKFLOWS` (32 characters or more each, one for each trust
  domain, not starting with `sk-ant-`). The model gateway gets the same
  three keys for its entries.
- A restart ends each live conversation; the next call of a conversation
  goes in with its history as one text. The repository README has the
  contract and the log format.

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
