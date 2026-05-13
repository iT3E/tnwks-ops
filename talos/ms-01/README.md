# MS-01 Talos Cluster (Bare Metal)

3x Minisforum MS-01 (i9-13900H) running Talos Linux on bare metal.

## Hardware

| Node | Role | RAM | NVMe (OS) | NVMe (Ceph) |
|------|------|-----|-----------|-------------|
| ms01-cp1 | controlplane + worker | 64GB | 256GB | 1TB |
| ms01-cp2 | controlplane + worker | 64GB | 256GB | 1TB |
| ms01-cp3 | controlplane + worker | 64GB | 256GB | 1TB |

## Network

- Management/Node VLAN: 910 (10.10.91.0/24)
- Pod CIDR: 10.42.0.0/16
- Service CIDR: 10.43.0.0/16
- Cilium L2/BGP for LoadBalancer IPs

## Bootstrap

```bash
# PXE-boot or USB-boot all 3 nodes with Talos ISO
# Then apply configs:
talosctl apply-config --nodes <ms01-cp1-ip> --file talos/ms-01/controlplane.yaml
talosctl apply-config --nodes <ms01-cp2-ip> --file talos/ms-01/controlplane.yaml
talosctl apply-config --nodes <ms01-cp3-ip> --file talos/ms-01/controlplane.yaml

# Bootstrap etcd on first node
talosctl bootstrap --nodes <ms01-cp1-ip>

# Fetch kubeconfig
talosctl kubeconfig --nodes <ms01-cp1-ip> --force
```

## TODO

- [ ] Fill in actual IPs when hardware arrives
- [ ] Generate secrets with `talosctl gen secrets`
- [ ] Apply machine-specific patches (hostname, install disk)

## USB-attached apps

USB passthrough for Frigate (Coral TPU) and zwave-js-ui (Aeotec Z-Stick) on
this cluster is straightforward — just plug the dongles into one of the
MS-01s. Talos exposes them as `/dev/bus/usb/...`, and the pods claim them
via the existing `securityContext` / hostPath patterns. See
[docs/usb-passthrough.md](../../docs/usb-passthrough.md).
