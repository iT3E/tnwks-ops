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

Driven by ansible — don't invoke `talosctl cluster create` directly:

```bash
cd infrastructure
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/01-talos-bootstrap.yml
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/02-cni-bootstrap.yml
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/03-sops-keys.yml
ansible-playbook -i ansible/inventory/wsl.yml ansible/playbooks/04-flux-install.yml
```

`01-talos-bootstrap.yml` runs `talosctl cluster create docker` and then
augments `homelab-wsl-worker-2` with the USB devices and `/mnt/e`
bind-mount needed by Frigate, zwave-js-ui, and the `local-path-bulk`
StorageClass. Idempotent — re-runs are a no-op if the container is
already in the desired state.

## Teardown

```bash
talosctl cluster destroy --name homelab-wsl
```

## Notes

- CNI is set to none — installed by `02-cni-bootstrap.yml` (Flannel on WSL,
  Cilium on MS-01)
- The cluster runs on a dedicated Docker bridge network (`homelab-wsl`)
- Two StorageClasses:
  - `local-path` (default) — Talos node container's overlay (C:)
  - `local-path-bulk` — pinned to worker-2 → Windows E: at
    `/mnt/e/k8s-storage` for bulk video / archive PVCs
- Port-forward or MetalLB for service access; the LB range
  (`10.5.0.200-220`) is reachable from the WSL host directly and from
  the LAN via the socat bridge (`wsl-lan-bridge.yml`)
- USB-attached pods (Frigate, zwave-js-ui) require USB devices to be
  attached via usbipd-win → WSL before `01-talos-bootstrap.yml` runs;
  see [docs/usb-passthrough.md](../../docs/usb-passthrough.md)

## Talos access (talosconfig) and break-glass

The cluster's admin talosconfig lives at `~/.talos/config-homelab-wsl` (mode
600), with a SOPS-encrypted copy in `talosconfig.sops.yaml`. Use it with
`talosctl --talosconfig ~/.talos/config-homelab-wsl ...`; the context already
points at the control plane's Docker-mapped API port and node `10.5.0.2`.

If it is lost, expired, or the containers were recreated (new port, new CA),
mint a fresh one from the cluster's own CA:

```bash
bash talos/wsl/mint-talosconfig.sh      # optional: number of days, default 365
```

It reads the CA from the control-plane container into RAM only, issues an
`os:admin` client certificate, writes both files above, and wipes the rest.
Nothing on the nodes changes. Talos has no certificate revocation: a minted
config is valid until it expires, and revoking one early means
`talosctl rotate-ca`. Commit the regenerated `talosconfig.sops.yaml`.

To restore from the repo copy instead: `sops -d talos/wsl/talosconfig.sops.yaml
> ~/.talos/config-homelab-wsl && chmod 600 ~/.talos/config-homelab-wsl`
(the endpoint port in it is only valid for the containers it was minted for).

### Applied machine-config patches

`cluster-config.yaml` is used at cluster creation. Changes to it reach the
running cluster only when patched in, e.g.:

```bash
talosctl --talosconfig ~/.talos/config-homelab-wsl -n 10.5.0.2 patch machineconfig \
  --patch '{"cluster":{"controllerManager":{"extraArgs":{"terminated-pod-gc-threshold":"1"}}}}'
```
