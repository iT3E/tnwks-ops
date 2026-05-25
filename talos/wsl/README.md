# WSL Talos Cluster (Docker Mode)

A 3-node Talos cluster running in Docker containers on the WSL2 desktop.
Hosts real workloads — including USB-passthrough apps (Frigate with the
Coral TPU and zwave-js-ui with the Aeotec Z-Stick) when the dongles are
attached to the desktop.

## Prerequisites

- Docker Desktop or Docker Engine running on Windows / WSL2
- `talosctl` CLI installed (`brew install siderolabs/tap/talosctl`)
- At least 8GB RAM available for the cluster
- For USB-attached apps: usbipd-win on the Windows host
  (see [docs/usb-passthrough.md](../../docs/usb-passthrough.md))

## Bootstrap

```bash
# Create the cluster (1 controlplane + 2 workers)
talosctl cluster create \
  --name homelab-wsl \
  --controlplanes 1 \
  --workers 2 \
  --cpus 2.0 \
  --memory 2048 \
  --kubernetes-version 1.30.0 \
  --config-patch @talos/wsl/cluster-config.yaml

# Fetch kubeconfig
talosctl kubeconfig --nodes 10.5.0.2 --force
```

## Teardown

```bash
talosctl cluster destroy --name homelab-wsl
```

## Notes

- CNI is set to none — Cilium is deployed via Flux after bootstrap
- The cluster uses Docker's default bridge network
- Storage uses local-path-provisioner (no in-cluster Ceph here)
- Port-forward or NodePort for service access (no LoadBalancer controller)
- USB-attached pods (Frigate, zwave-js-ui) require USB devices to be
  attached via usbipd-win → WSL → Docker → Talos before they will start
