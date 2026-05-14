# Bootstrap: WSL Talos Cluster

End-to-end procedure for spinning up the WSL Talos cluster from scratch.
This cluster runs real workloads on the desktop workstation, including
USB-attached apps (Frigate / Coral, zwave-js-ui / Aeotec Z-Stick).

## Prerequisites

Install the CLI tools:

```bash
brew install \
  age \
  ansible \
  fluxcd/tap/flux \
  go-task/tap/go-task \
  kubernetes-cli \
  siderolabs/tap/talosctl \
  sops
```

You also need:
- Docker Desktop or Docker Engine running on Windows / WSL2
- A GitHub Personal Access Token with `repo` scope, exported as `GITHUB_TOKEN`
- 1Password CLI configured (or supply tokens manually)
- For USB-attached apps (Frigate, zwave-js-ui): `usbipd-win` on the Windows
  host with the dongles bound and attached. See
  [usb-passthrough.md](./usb-passthrough.md).
- Working VPN credentials for `pod-gateway`. The VPN secret
  (`kubernetes/apps/vpn/pod-gateway/downloads/secrets.sops.yaml`) is
  encrypted to the existing repo age recipient — to use it on this cluster,
  you'll either need to add the WSL cluster's age public key as an
  additional recipient OR re-encrypt the file with the WSL key after
  `03-sops-keys.yml` has provisioned it. Until that's done, qbittorrent
  and the *arr stack will fail to reconcile (they `dependsOn` pod-gateway).

## Bootstrap

```bash
# 1. Provide your GitHub PAT
export GITHUB_TOKEN=$(op read 'op://Private/GitHub Flux Token/credential')

# 2. Run the full bootstrap chain
task bootstrap:wsl
```

This runs three Ansible playbooks in sequence:
1. **`01-talos-bootstrap.yml`** — `talosctl cluster create` against Docker
   (1 controlplane + 2 workers); fetches kubeconfig to `~/.kube/config-homelab-wsl`
2. **`03-sops-keys.yml`** — generates an age key at
   `~/.config/sops/age/homelab-wsl.txt` and creates the `sops-age` Secret.
   **Back this key up to 1Password immediately.**
3. **`02-flux-install.yml`** — `flux bootstrap github` against
   `kubernetes/clusters/wsl/`

After step 3, **everything is GitOps**. Push to the tracked branch and
Flux reconciles.

## Verify

```bash
export KUBECONFIG=~/.kube/config-homelab-wsl

kubectl get nodes
flux get all -A
task cluster:resources
```

## Iterate

```bash
task cluster:reconcile:wsl
```

## Teardown / reset

```bash
task talos:destroy:wsl
task talos:reset:wsl    # destroy + recreate
```

## What's different from the MS-01 cluster

This cluster shares all manifests under `kubernetes/apps/` with the MS-01
cluster. Per-cluster differences live in
`kubernetes/clusters/wsl/cluster-settings.yaml`:

| Setting | WSL | MS-01 |
|---|---|---|
| `STORAGE_CLASS_DEFAULT` | `local-path` | `ceph-block` |
| `STORAGE_CLASS_BULK` | `local-path` | `nfs-bulk` |
| `BULK_PVC_SIZE` | `10Gi` | `10Ti` |
| LB allocation | (none — Services pend) | Cilium L2/BGP on `10.10.91.0/24` |
| Ingress hostnames | `*.tnwks.local` | `*.it3e.xyz` (external), `*.tnwks.local` (internal) |

Apps not deployed on the WSL cluster:
- `storage/rook-ceph` — no real disks (`local-path` is the default class)
- `networking/cloudflared` — public tunnel terminates on MS-01
- `production/uisp` — lives on MS-01

Apps that are deployed but require USB passthrough to function:
- `production/frigate` (Coral TPU)
- `home-automation/zwave-js-ui` (Aeotec Z-Stick)

See [usb-passthrough.md](./usb-passthrough.md).

## Troubleshooting

### `talosctl cluster create` hangs
- Docker should have at least 8GB of memory allocated
- Ports 50000–50099 must be free

### Flux can't decrypt SOPS secrets
- Verify the `sops-age` Secret exists: `kubectl -n flux-system get secret sops-age`
- Check the public key in `.sops.yaml` matches the one in
  `~/.config/sops/age/homelab-wsl.txt`. If you regenerated the key, you'll
  need to re-encrypt every `*.sops.yaml` file with the new pubkey, or add
  the new key as an additional recipient.

### LoadBalancer services pending
- Expected — no LB controller on this cluster. Use `kubectl port-forward`
  or NodePort.

### qbittorrent / *arr pods stuck Pending
- They `dependsOn: cluster-apps-pod-gateway-downloads`. Check
  `flux get kustomizations -A | grep pod-gateway`. If pod-gateway can't
  start, the *arr stack won't either. Most common cause: VPN secret not
  decryptable on this cluster (see Prerequisites).

### Frigate / zwave-js-ui pods crash on startup
- USB device probably isn't reaching the Talos container. Walk down the
  chain in [usb-passthrough.md](./usb-passthrough.md#verification-commands).
