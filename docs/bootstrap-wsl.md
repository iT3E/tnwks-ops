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

This runs four Ansible playbooks in sequence:
1. **`01-talos-bootstrap.yml`** — `talosctl cluster create docker`
   (1 controlplane + 2 workers); fetches kubeconfig to `~/.kube/config-homelab-wsl`.
   Talos ships with no CNI, so nodes will register as `NotReady`.
2. **`02-cni-bootstrap.yml`** — `helm install` Flannel on WSL (or Cilium
   on MS-01). The CNI choice is read from
   `kubernetes/clusters/wsl/cluster-settings.yaml` (`CNI: flannel`). After
   this step, nodes go `Ready` and pods can be scheduled.
3. **`03-sops-keys.yml`** — generates an age key at
   `~/.config/sops/age/homelab-wsl.txt` and creates the `sops-age` Secret.
   **Back this key up to 1Password immediately.**
4. **`04-flux-install.yml`** — `flux bootstrap github` against
   `kubernetes/clusters/wsl/`. Requires `GITHUB_TOKEN` with `repo` scope
   (or fine-grained: Contents RW + Administration RW + Metadata R on this
   repo).

After step 4, **everything is GitOps**. Push to the tracked branch and
Flux reconciles.

## Why Flannel on WSL instead of Cilium

MS-01 runs Cilium. WSL runs Flannel. The split exists because Cilium
fundamentally cannot run on Talos-in-Docker on WSL2:

- Cilium's `clean-cilium-state` init container requests `CAP_SYS_MODULE`
  (along with `CAP_SYS_ADMIN`, `CAP_NET_ADMIN`, `CAP_SYS_RESOURCE`).
- The WSL2 kernel rejects `SYS_MODULE` cap requests in nested containers
  with `EPERM`, even when the outer container is privileged and the
  bounding set is widened via Talos `baseRuntimeSpecOverrides`.
- runc fails before Cilium's logic runs, regardless of Cilium version.
  Reproduced empirically with a 5-line busybox pod requesting just
  `SYS_MODULE`. All other caps work fine after widening the bounding
  set; only `SYS_MODULE` is rejected.
- Cilium has acknowledged the desire to drop this requirement
  ([cilium#13768](https://github.com/cilium/cilium/issues/13768),
  open since 2020) but no fix has shipped. The community-suggested
  workaround (custom WSL2 kernel) addresses different runtime issues
  (iptables/conntrack modules), not the cap-apply error at startup.

### Implications of the split

| Capability | WSL (Flannel) | MS-01 (Cilium) |
|---|---|---|
| Pod networking | ✓ | ✓ |
| Service routing | ✓ (via kube-proxy) | ✓ (Cilium replaces kube-proxy) |
| `NetworkPolicy` enforcement | ✗ Flannel doesn't enforce | ✓ |
| `CiliumNetworkPolicy` | ✗ CRD absent | ✓ |
| Hubble flow logs / UI | ✗ | ✓ |
| L2/BGP LB | ✗ (no LB on WSL anyway) | ✓ |

### Apps excluded from WSL because of this

- **`vpn/pod-gateway`** — relies on `CiliumNetworkPolicy` to force
  qbittorrent/*arr egress through the VPN. Without enforcement, traffic
  could leak via the host network (DMCA exposure for qbittorrent).
- **`media/qbittorrent`** and **`media/prowlarr`** — both depend on
  `pod-gateway` for VPN routing.

`sonarr`, `radarr`, and `recyclarr` *are* deployed on WSL — they're
indexers/coordinators that don't push traffic over the VPN.

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
| Ingress hostnames | `*.internal.tnwks.us` | `*.it3e.xyz` (external), `*.internal.tnwks.us` (internal) |

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
