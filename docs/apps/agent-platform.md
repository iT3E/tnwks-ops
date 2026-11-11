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
