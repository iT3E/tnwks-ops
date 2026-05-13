# Disaster Recovery

> Skeleton — fill in concrete commands as scenarios are exercised.

## What can go wrong

1. **Cluster gone**: all nodes lost, etcd unrecoverable
2. **Storage lost**: Ceph cluster unrecoverable but nodes intact
3. **Flux drift**: cluster diverged from git, can't reconcile
4. **SOPS key lost**: encrypted secrets in repo unreadable
5. **GitHub repo lost**: source of truth gone

## 1. Cluster gone — full rebuild

If the prod cluster is unrecoverable:

1. Re-image MS-01 nodes with Talos
2. Re-run prod bootstrap chain — this is the GitOps recovery path:

   ```bash
   export GITHUB_TOKEN=$(op read 'op://Private/GitHub Flux Token/credential')
   task bootstrap:prod
   ```

3. Restore from backups (TODO: document VolSync/snapscheduler restore once
   those are wired into the prod stack)

## 2. Storage lost

If Rook Ceph is unrecoverable but nodes are healthy:

```bash
# Drain workloads
kubectl scale deploy --all -n production --replicas=0

# Delete CephCluster (cluster only, NOT operator)
kubectl delete cephcluster rook-ceph -n rook-ceph

# Wipe Ceph disks (DESTRUCTIVE — confirm device path)
talosctl --nodes <each-node> dashboard
# manually wipe /dev/nvme1n1 with: dd if=/dev/zero of=/dev/nvme1n1 bs=1M count=100

# Re-apply CephCluster manifest via flux
task flux:reconcile:prod
```

Restore PVC data from VolSync backups (TODO).

## 3. Flux drift

```bash
flux suspend kustomization flux-system
# investigate the divergence
flux reconcile kustomization flux-system --with-source
flux resume kustomization flux-system
```

## 4. SOPS key lost

If the per-cluster age private key is lost AND not backed up to 1Password:

- All `*.sops.yaml` files in the repo become **unrecoverable** for that cluster
- Recovery: re-create the secrets from source (1Password, vendor consoles),
  generate a new age key, re-encrypt the repo's secrets, commit, reconcile

**Mitigation:** the `03-sops-keys.yml` playbook prints a backup reminder.
Store the key in 1Password under "SOPS Age Key — {cluster_name}".

## 5. GitHub repo lost

Recovery options, in order of preference:

1. **Local clone**: `~/git/iT3E/tnwks-ops` — push to a new remote
2. **Flux-side checkout**: the `source-controller` keeps a clone in
   `/data/gitrepository/flux-system/home-kubernetes` inside its pod —
   `kubectl cp` it out before deleting the cluster
3. **Tag recovery**: `git tag legacy-pve` preserves the pre-restructure state
   for reference

After recovery, push to a new remote and re-run `flux bootstrap github` against it.

## Routine backup checklist (TODO)

- [ ] VolSync schedules wired up for stateful PVCs
- [ ] CNPG WAL archive to S3 (Rook RGW or external)
- [ ] etcd snapshots (`talosctl etcd snapshot`)
- [ ] Repo: GitHub is canonical, but mirror to a second remote weekly
- [ ] Age keys: stored in 1Password under "SOPS Age Key — {cluster}"
