# Cluster upgrades: Kubernetes and Talos

Renovate tracks both versions and opens the PRs. Merging is the go signal.
What happens after the merge depends on the cluster:

| | Kubernetes | Talos |
| --- | --- | --- |
| Pinned in | `kubernetes/apps/system-upgrade/tuppr/upgrades/kubernetes.yaml` (+ `talos/wsl/versions.yaml` for new clusters) | `talos/wsl/versions.yaml` (WSL), `kubernetes/apps/system-upgrade/tuppr/talos/talos.yaml` (MS-01) |
| Renovate group | `Kubernetes` (`ghcr.io/siderolabs/kubelet`) | `Talos` (`ghcr.io/siderolabs/talos`) |
| WSL (Talos in Docker) | Automatic: Flux applies the `KubernetesUpgrade`, [tuppr](https://tuppr.home-operations.com/) runs `talosctl upgrade-k8s` from a Job | Manual after merge: `task talos:upgrade:wsl` |
| MS-01 (bare metal) | Same as WSL | Automatic: tuppr's `TalosUpgrade` |

Both groups get one PR per minor (`separateMultipleMinor`) and a separate PR
for patches. Minors carry `impact/critical`, so Ivan approves them; patches
are `impact/high` (`autoMerge.json5`).

## Rules for a version bump

- **One minor at a time.** Kubernetes 1.34 -> 1.35 -> 1.36, never 1.34 ->
  1.36. Talos 1.13 -> 1.14, likewise.
- **Kubernetes has to be supported by the Talos that runs it.** Check the
  [support matrix](https://docs.siderolabs.com/talos/v1.14/getting-started/support-matrix)
  before approving a Kubernetes minor. Talos 1.13 runs Kubernetes 1.31-1.36,
  Talos 1.14 runs up to 1.37. When Kubernetes outruns Talos, upgrade Talos
  first and leave the Kubernetes PR blocked (`ori/blocked`) until then.
- **Read the Kubernetes changelog for removed APIs** before a minor, and
  check that the charts in the cluster support the new version.

## Kubernetes (both clusters): tuppr

`tuppr` (`kubernetes/apps/system-upgrade/tuppr`) is a controller that owns
two resources. The `KubernetesUpgrade` named `kubernetes` holds the target
version. When Flux applies a new version, tuppr starts a Job that runs
`talosctl upgrade-k8s --to <version>` against the control plane: API server,
controller manager and scheduler, then kube-proxy and every node's kubelet.
Running containers keep running while the kubelet restarts. The API is
unavailable for a minute or two while the single WSL control plane restarts
its API server.

Watch it:

```bash
kubectl get kubernetesupgrade kubernetes -w
kubectl -n system-upgrade logs -f job/<status.jobName>
```

`status.phase` goes `Upgrading` -> `Completed`; `status.history` keeps the
last three runs. A failed Job shows up as `Failed` with `status.lastError`,
and tuppr's PrometheusRule alerts.

tuppr reaches the Talos API from inside the cluster through a `talos.dev`
ServiceAccount, which needs `machine.features.kubernetesTalosAPIAccess`
(`os:admin` for the `system-upgrade` namespace) on the control plane nodes
(Talos rejects it on workers). On WSL that is
`talos/wsl/patches/talos-api-access.yaml`, included at cluster creation and
applied to the running control plane with:

```bash
talosctl --talosconfig ~/.talos/config-homelab-wsl \
  -n 10.5.0.2 patch mc --mode=no-reboot \
  --patch @talos/wsl/patches/talos-api-access.yaml
```

`talos/wsl/versions.yaml` also pins `kubernetes`, but only
`01-talos-bootstrap.yml` reads it, when it creates a cluster. Renovate bumps
it in the same PR so a rebuilt cluster starts on the current version.

## Talos on WSL: roll the node containers

Talos refuses in-place upgrades in container mode, so neither `talosctl
upgrade` nor tuppr's `TalosUpgrade` works on WSL. A node's state lives in
Docker volumes (`/system/state` holds the machine config and node identity,
`/var` holds etcd, containerd and the kubelet), so the upgrade swaps the
container: stop the node, start a new container from the new
`ghcr.io/siderolabs/talos` image with the same volumes, env, address, ports,
limits and bind mounts (`talos/wsl/roll-node.sh`). The node boots the new
Talos with its existing config, including the Kubernetes version tuppr set.

After the Talos PR merges:

```bash
task talos:upgrade:wsl:dry-run   # the docker run each node would get
task talos:upgrade:wsl           # infrastructure/ansible/playbooks/05-talos-upgrade.yml
```

The playbook goes control plane first, then the workers, one node at a time:
every node Ready, drain, swap, wait until the node reports the new Talos
version and Ready, uncordon. Nodes already on the target are skipped, so a
re-run continues after a failure. Single-instance CloudNativePG clusters
would block the drain (their PDB allows no disruption), so the playbook
opens a CNPG maintenance window (`nodeMaintenanceWindow`, PVC reused) for
the run and closes it at the end, even on failure. Pods on local-path
volumes wait Pending until their node is back. Expect a few minutes per
node.

If a swapped container fails to start, `roll-node.sh` puts the old one back.
To go back after a node has booted the new version, run `roll-node.sh` with
the old image. Within a minor that is low risk; across a minor, read the
Talos release notes first.

This was proven on a throwaway Talos-in-Docker cluster on 2026-10-30:
1.13.2 -> 1.13.12 -> 1.14.5 with etcd data and the Kubernetes version kept,
and tuppr's `KubernetesUpgrade` 1.34.0 -> 1.35.10.

## MS-01

Same tuppr install, plus the `TalosUpgrade` named `cluster`
(`kubernetes/clusters/ms-01/apps/tuppr-talos-ks.yaml`). tuppr upgrades one
node at a time from a healthy peer: drain, `talosctl upgrade`, reboot, wait
Ready. The MS-01 control plane config (`talos/ms-01/controlplane.yaml`)
carries the same `kubernetesTalosAPIAccess` feature.
