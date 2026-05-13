# Local Talos Cluster (Docker Mode)

A 3-node Talos cluster running in Docker containers for local development
and GitOps iteration.

## Prerequisites

- Docker Desktop or Docker Engine running
- `talosctl` CLI installed (`brew install siderolabs/tap/talosctl`)
- At least 8GB RAM available for the cluster

## Bootstrap

```bash
# Create the cluster (1 controlplane + 2 workers)
talosctl cluster create \
  --name homelab-local \
  --controlplanes 1 \
  --workers 2 \
  --cpus 2.0 \
  --memory 2048 \
  --kubernetes-version 1.30.0 \
  --config-patch @talos/local/cluster-config.yaml

# Fetch kubeconfig
talosctl kubeconfig --nodes 10.5.0.2 --force
```

## Teardown

```bash
talosctl cluster destroy --name homelab-local
```

## Notes

- CNI is set to none — Cilium is deployed via Flux after bootstrap
- The cluster uses Docker's default bridge network
- Storage uses local-path-provisioner (no Ceph in Docker mode)
- Port-forward or NodePort for service access (no real LoadBalancer)
