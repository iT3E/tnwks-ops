# Move the WSL (Ubuntu) disk off C:

The Ubuntu WSL distro that hosts the `homelab-wsl` Talos-in-Docker cluster keeps
all of its data — the distro root **and** Docker's `/var/lib/docker` (the Talos
node containers, images, and every `local-path*` PVC) — inside a single
`ext4.vhdx`. That vhdx grows on whatever Windows drive WSL stored it on.

**Problem:** the vhdx lives on **C:**, which is ~94% full (~32 GiB free). The
distro's filesystem reports 768 GiB free, but that's the *logical* ceiling of a
sparse 1 TB virtual disk — the real limit is C:'s remaining space. When C:
fills, the cluster's storage (postgres on `local-path-ext4`, the sites NFS
export, etc.) starts failing with no obvious in-cluster cause.

**Fix:** relocate the whole distro vhdx to **E:** (387 GiB free) via
export → unregister → import. This is the higher-leverage version of "put the
sites NFS share somewhere roomy": it fixes capacity for *every* workload on the
cluster, not just sites.

> ⚠️ This is a **Windows-host** operation. It cannot be run from inside WSL /
> this repo's tooling. Run the PowerShell/cmd steps from a **Windows terminal**.
> It takes the entire WSL environment (and the cluster) offline for the duration
> of the export+import (tens of minutes; the vhdx is ~188 GiB used).

---

## 0. Pre-flight (do NOT skip)

1. **Free space check.** The export writes a `.tar` roughly the size of the
   *used* space (~188 GiB here). You need that much free on the **destination
   (E:)** for the tar, plus room for the imported vhdx. With 387 GiB free on E:
   that's fine, but if you instead export to a temp drive, verify it first.
   - From inside WSL, current usage: `df -h /` → look at the **Used** column.

2. **Know what you'll lose access to during the move.** While WSL is
   unregistered/re-importing, the Talos cluster is **down** (its containers live
   in `/var/lib/docker` inside this vhdx). Pick a maintenance window. Anything
   pointing at the cluster (Flux, ingress, sites) is unavailable until it's back.

3. **Capture the current default user.** `--import` resets the distro's default
   user to `root`. Note the current user so you can restore it (step 5):
   ```bash
   whoami            # run in WSL  → expected: it3e
   ```

4. **(Recommended) Quiesce gracefully** from inside WSL before shutting down,
   so Docker/Talos stop clean:
   ```bash
   docker ps --format '{{.Names}}'      # confirm the homelab-wsl-* containers
   talosctl cluster destroy --name homelab-wsl   # OPTIONAL — see note below
   ```
   **Note:** you do NOT have to destroy the cluster — the vhdx move preserves
   `/var/lib/docker` byte-for-byte, so the Talos containers and all PVC data come
   back exactly as they were. Destroying + re-bootstrapping is the *clean-slate*
   alternative if you'd rather rebuild (see "Alternative" at the bottom). For a
   straight disk move, skip the destroy.

---

## 1. Find the current vhdx location (Windows)

The path is in the registry. From a **Windows PowerShell**:

```powershell
# List WSL distros and their on-disk vhdx locations
Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss |
  ForEach-Object {
    $p = $_.GetValue("BasePath")
    [PSCustomObject]@{
      Name     = $_.GetValue("DistributionName")
      BasePath = $p
      Vhdx     = Join-Path $p "ext4.vhdx"
    }
  } | Where-Object Name -eq 'Ubuntu' | Format-List
```

Expected `BasePath` is something like
`C:\Users\mh\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu...\LocalState`.
Note the full `Vhdx` path — that's what you're moving off of.

---

## 2. Shut WSL down (Windows)

```powershell
wsl --shutdown
wsl --list --verbose      # confirm Ubuntu shows Stopped
```

---

## 3. Export → unregister → import (Windows)

Pick a destination dir on E: for the *new* home of the distro, and a temp path
for the tar. Adjust paths to taste.

```powershell
# --- variables ---
$Distro   = "Ubuntu"
$Tar      = "E:\wsl-backup\ubuntu-$(Get-Date -Format yyyyMMdd).tar"
$NewHome  = "E:\WSL\Ubuntu"           # where the new ext4.vhdx will live

New-Item -ItemType Directory -Force -Path (Split-Path $Tar), $NewHome | Out-Null

# --- 3a. Export the whole distro to a tar (cluster is offline from here) ---
wsl --export $Distro $Tar
#   ^ large + slow (~188 GiB). Let it finish. Verify the tar size after.

# --- 3b. Remove the registered distro (this deletes the OLD vhdx on C:) ---
#   The tar from 3a is your full backup — do NOT delete it until step 6 passes.
wsl --unregister $Distro

# --- 3c. Re-import from the tar into the new E: location ---
wsl --import $Distro $NewHome $Tar --version 2
```

> If anything fails at 3b/3c, you still have `$Tar`. Re-import from it; you have
> not lost data as long as the tar is intact.

---

## 4. Confirm the new location (Windows)

```powershell
Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss |
  Where-Object { $_.GetValue("DistributionName") -eq "Ubuntu" } |
  ForEach-Object { $_.GetValue("BasePath") }
# → should now be under E:\WSL\Ubuntu
```

---

## 5. Restore default user + WSL config (Windows → WSL)

`--import` makes the distro boot as `root` and drops `/etc/wsl.conf`-driven
defaults. Fix the default user:

```powershell
# Quick way: set default user via the distro launcher
ubuntu config --default-user it3e
```

If `ubuntu config` isn't available, set it inside WSL by ensuring
`/etc/wsl.conf` has:
```ini
[user]
default=it3e
[boot]
systemd=true
```
then `wsl --shutdown` and reopen. (systemd=true matters — this distro relies on
systemd for the LAN-bridge units and snap mounts.)

---

## 6. Verify the cluster came back (WSL)

Open a new WSL terminal and confirm everything reconstituted from the moved
`/var/lib/docker`:

```bash
# distro root is now backed by the E:-resident vhdx
df -hT /                       # /dev/sdX ext4 — Used should match pre-move (~188G)

# Talos containers are back
docker ps --filter label=talos.owned=true --format '{{.Names}}\t{{.Status}}'

# cluster reachable
kubectl get nodes
flux get kustomizations | grep -E 'nfs-server|sites|sites-runner|actions-runner'

# the sites platform specifically
kubectl get pods -n storage -o wide
kubectl get pods -n production -l app.kubernetes.io/name=sites
kubectl get pods -n tools | grep -E 'runner|listener'
```

If the Talos containers don't auto-start, start them:
```bash
docker start $(docker ps -aq --filter label=talos.owned=true)
```
Then re-check `kubectl get nodes` until all three are `Ready`.

> **LAN bridge / port-proxy:** the `tnwks-lan-bridge` systemd units
> (`infrastructure/wsl-host/`) and any Windows `netsh portproxy` are tied to the
> WSL VM, not the vhdx — they should survive, but if LAN access to the cluster
> is broken after the move, re-run the bridge setup (see
> `docs/wsl-lan-exposure.md`).

---

## 7. Reclaim the C: space + delete the backup (Windows)

Once step 6 is fully green and you're confident:

```powershell
# The OLD vhdx was already removed by --unregister in 3b; C: space is freed.
# Delete the export tar once you're satisfied (it's no longer needed):
Remove-Item "E:\wsl-backup\ubuntu-*.tar"
```

Confirm C: free space recovered (from WSL): `df -h /mnt/c`.

---

## Rollback

At any point before step 7, the export tar is a complete backup. To revert:
```powershell
wsl --shutdown
wsl --unregister Ubuntu
wsl --import Ubuntu <ORIGINAL_C_PATH> E:\wsl-backup\ubuntu-YYYYMMDD.tar --version 2
ubuntu config --default-user it3e
```

---

## Alternative: clean-slate rebuild instead of disk move

If you'd rather not move 188 GiB of vhdx (much of it stale Docker layers), the
clean option is to destroy and re-bootstrap the cluster on a fresh, E:-resident
distro. Everything cluster-side is GitOps, so it rebuilds from this repo:

1. Move/re-import the distro to E: with an empty `/var/lib/docker` (or a fresh
   distro), OR just relocate as above but `docker system prune -a` first to
   shrink the tar.
2. Re-bootstrap per `docs/bootstrap-wsl.md`:
   ```bash
   cd infrastructure
   ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/01-talos-bootstrap.yml
   ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/02-cni-bootstrap.yml
   ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/03-sops-keys.yml
   ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/04-flux-install.yml
   ```
   Flux then reconciles the whole platform back from `main`. **Caveat:** PVC data
   (postgres, the sites NFS export contents) is NOT preserved on a clean-slate
   rebuild — only the disk-move path keeps it. Sites content is regenerated by
   re-running the `tnwks-sites` build workflow, so for *sites* specifically a
   rebuild is harmless; postgres-backed apps would need their own restore.
```
