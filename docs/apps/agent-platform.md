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

## Trust domains

Each trust domain directory has a `namespace.yaml` and a `kustomization.yaml`
that lists its apps. The namespace of each app comes from `targetNamespace`
in its `ks.yaml`, so the namespace (`agents-<name>`) differs from the
directory name.

- **Pod Security:** each namespace enforces the `restricted` level.
- **Default deny:** a `NetworkPolicy` denies ingress and egress for every pod
  but the sandbox pods (label `openshell.ai/managed-by`). The sandbox gateway
  gives each sandbox its own policies when it makes it: the workload pod gets
  no egress, and its supervisor carries the egress that the sandbox policy
  approves.
- **DNS:** a `CiliumNetworkPolicy` lets the other pods resolve names through
  Cilium's DNS proxy, so a policy here can allow a host name (`toFQDNs`).
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
