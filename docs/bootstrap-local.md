# Bootstrap: Local Talos Cluster

End-to-end procedure for spinning up the local Talos sim cluster from scratch.

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
- Docker Desktop or Docker Engine running
- A GitHub Personal Access Token with `repo` scope, exported as `GITHUB_TOKEN`
- 1Password CLI configured (or skip and provide tokens manually)

## Bootstrap

```bash
# 1. Provide your GitHub PAT
export GITHUB_TOKEN=$(op read 'op://Private/GitHub Flux Token/credential')

# 2. Run the full bootstrap chain
task bootstrap:local
```

This runs three Ansible playbooks in sequence:
1. **`01-talos-bootstrap.yml`** — `talosctl cluster create` against Docker
   (1 controlplane + 2 workers); fetches kubeconfig to `~/.kube/config-homelab-local`
2. **`03-sops-keys.yml`** — generates an age key at
   `~/.config/sops/age/homelab-local.txt` and creates the `sops-age` Secret
3. **`02-flux-install.yml`** — `flux bootstrap github` against
   `kubernetes/clusters/local/`

## Verify

```bash
export KUBECONFIG=~/.kube/config-homelab-local

kubectl get nodes
flux get all -A
task cluster:resources
```

## Iterate

After bootstrap, **everything is GitOps**. Push a commit to the
`main` branch (or whichever branch flux is tracking) and Flux will reconcile.

To force a reconcile:

```bash
task flux:reconcile:local
```

## Teardown

```bash
task talos:destroy:local
```

To destroy and recreate from scratch:

```bash
task talos:reset:local
```

## Apps disabled in local

The local overlay (`kubernetes/clusters/local/`) deliberately omits:

| App | Why |
|---|---|
| `production/frigate` | Needs Coral USB device |
| `home-automation/zwave-js-ui` | Needs Z-Wave USB stick |
| `networking/cloudflared` | No real Cloudflare tunnel for sim |
| `vpn/pod-gateway` | Pointless without real VPN gateway |
| `storage/rook-ceph` | No real disks; uses local-path instead |
| `production/uisp` | Heavy resource footprint, prod-only |

Storage classes resolve to `local-path` in local; `ceph-block`/`nfs-bulk` in prod.

## Troubleshooting

### `talosctl cluster create` hangs
- Make sure Docker has at least 8GB of memory allocated
- Check that ports 50000–50099 aren't in use

### Flux can't decrypt SOPS secrets
- Verify the `sops-age` Secret exists: `kubectl -n flux-system get secret sops-age`
- Check the public key in `.sops.yaml` matches the one in
  `~/.config/sops/age/homelab-local.txt`. If you regenerated the key, you'll
  need to re-encrypt every `*.sops.yaml` file with the new pubkey, or add the
  new key as an additional recipient.

### LoadBalancer services pending
- Expected in local — no LB controller. Use `kubectl port-forward` or NodePort.
