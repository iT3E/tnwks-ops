# Bootstrap: MS-01 Talos Cluster

> **Status: SKELETON — fill in once MS-01 hardware is on the bench.**

End-to-end procedure for spinning up the MS-01 Talos cluster on the
3x Minisforum MS-01 (i9-13900H) bare-metal nodes.

## Hardware assumptions

| Node | Role | OS Disk | Ceph Disk | RAM |
|------|------|---------|-----------|-----|
| ms01-cp1 | controlplane + worker | 256GB NVMe (`/dev/nvme0n1`) | 1TB NVMe (`/dev/nvme1n1`) | 64GB |
| ms01-cp2 | controlplane + worker | 256GB NVMe | 1TB NVMe | 64GB |
| ms01-cp3 | controlplane + worker | 256GB NVMe | 1TB NVMe | 64GB |

Network: VLAN 910 (10.10.91.0/24).

## Pre-flight (TODO)

- [ ] Rack and cable all 3 MS-01 nodes
- [ ] Configure switch trunk on VLAN 910
- [ ] Assign static IPs (10.10.91.11–13) and update
      `infrastructure/ansible/inventory/ms-01.yml`
- [ ] Decide and document the cluster VIP (probably 10.10.91.10) and update
      `KUBE_VIP_ADDR` in `kubernetes/clusters/ms-01/cluster-settings.yaml`
- [ ] Reserve LB range in DHCP and update `LB_RANGE`/`METALLB_*_ADDR` in
      cluster-settings
- [ ] Decide actual Ceph device path on MS-01 — confirm `/dev/nvme1n1`
      assumption in `kubernetes/apps/storage/rook-ceph/cluster/app/ceph-cluster.yaml`

## Bootstrap

```bash
# 1. Generate Talos secrets bundle (one-time)
talosctl gen secrets -o talos/ms-01/secrets.yaml
# Encrypt and commit secrets.yaml — DO NOT commit plaintext
sops --encrypt --in-place talos/ms-01/secrets.yaml

# 2. PXE-boot or USB-boot all 3 MS-01s with Talos ISO

# 3. Run the bootstrap chain
export GITHUB_TOKEN=$(op read 'op://Private/GitHub Flux Token/credential')
task bootstrap:ms-01
```

## Verify

```bash
export KUBECONFIG=~/.kube/config-homelab-ms-01

kubectl get nodes -o wide
kubectl get cephcluster -A          # should show HEALTH_OK after ~10 min
flux get all -A
```

## Storage validation

Once the CephCluster reports HEALTH_OK:

```bash
# Test RBD storage class
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata: { name: test, namespace: default }
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ceph-block
  resources: { requests: { storage: 1Gi } }
EOF
kubectl get pvc test
```

## USB-attached apps

Coral TPU (Frigate) and Aeotec Z-Stick (zwave-js-ui) are deployed on this
cluster too. USB passthrough is native — plug each dongle into one of the
MS-01s and Talos exposes it. See [usb-passthrough.md](./usb-passthrough.md).

## Disaster recovery

See [disaster-recovery.md](./disaster-recovery.md).
