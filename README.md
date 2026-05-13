<div align="center">

## My Home Operations repository

_... managed by Flux, Renovate, GitHub Actions, Terraform, Ansible_ :robot:

[![Talos](https://img.shields.io/badge/Talos%20Linux-blue?style=for-the-badge&logo=kubernetes&logoColor=white)](https://www.talos.dev)
[![Renovate](https://img.shields.io/github/actions/workflow/status/iT3E/tnwks-ops/release.yaml?branch=main&label=&logo=renovatebot&style=for-the-badge&color=blue)](https://github.com/iT3E/tnwks-ops/actions/workflows/release.yaml)

</div>
<br>

Welcome to **tnwks** /tee-networks/. This monorepo is the source of truth for
my home infrastructure. IaC + GitOps via [Talos](https://www.talos.dev),
[Kubernetes](https://kubernetes.io/), [Flux](https://github.com/fluxcd/flux2),
[Ansible](https://www.ansible.com/), [Terraform](https://www.terraform.io/),
[Renovate](https://github.com/renovatebot/renovate), and
[GitHub Actions](https://github.com/features/actions).

---

## Multi-Cluster Architecture

This repo supports two clusters from a single source of truth:

| Cluster | Purpose | Hardware | Storage |
|---------|---------|----------|---------|
| **`local`** | WSL2 sim for GitOps iteration | Talos in Docker (1 CP + 2 workers) | local-path |
| **`prod`** | Real homelab | 3x Minisforum MS-01 i9-13900H, bare-metal Talos | Rook Ceph (3x 1TB NVMe) + democratic-csi NFS |

Both share the same `kubernetes/apps/` and `kubernetes/flux/` configs.
Per-cluster differences (storage class, IP ranges, DNS) live in
`kubernetes/clusters/{local,prod}/cluster-settings.yaml` and are resolved
via Flux variable substitution.

```
tnwks-ops/
├── README.md
├── Taskfile.yml
├── .sops.yaml
├── talos/                       # Machine configs
│   ├── local/                   # talosctl cluster create config
│   └── prod/                    # MS-01 controlplane + worker + patches
├── infrastructure/
│   ├── ansible/                 # Bootstrap playbooks (talos, flux, sops)
│   ├── terraform/aws/           # Active
│   ├── terraform/cloudflare/    # Active
│   └── _archive/                # Legacy PVE/Proxmox terraform + packer
├── kubernetes/
│   ├── apps/                    # Shared app manifests (cross-cluster)
│   ├── bootstrap/               # Initial Flux + helm install
│   ├── flux/                    # Flux config (GitRepository, vars)
│   └── clusters/
│       ├── local/               # Local overlay (excludes hardware apps)
│       └── prod/                # Full prod stack
└── docs/
    ├── bootstrap-local.md
    ├── bootstrap-prod.md
    └── disaster-recovery.md
```

---

## Getting started

### Local sim cluster

```bash
export GITHUB_TOKEN=$(op read 'op://Private/GitHub Flux Token/credential')
task bootstrap:local
```

Detailed steps in [docs/bootstrap-local.md](./docs/bootstrap-local.md).

### Production cluster

See [docs/bootstrap-prod.md](./docs/bootstrap-prod.md) — currently a skeleton,
will be filled in once MS-01 hardware is racked.

### Disaster recovery

See [docs/disaster-recovery.md](./docs/disaster-recovery.md).

---

## Networking

| Name | CIDR |
|------|------|
| Node VLAN (910) | `10.10.91.0/24` |
| Pod CIDR | `10.42.0.0/16` |
| Service CIDR | `10.43.0.0/16` |
| LoadBalancer pool | `10.10.91.50–60` (Cilium L2/BGP) |

[Cilium](https://cilium.io) provides the CNI and L2/BGP for LoadBalancer IPs
(MetalLB has been removed). [cloudflared](https://github.com/cloudflare/cloudflared)
provides the public ingress tunnel into the prod cluster.

## DNS

- **Internal:** `tnwks.local` (resolved via [k8s-gateway](https://github.com/ori-edge/k8s_gateway)
  and [blocky](https://github.com/0xERR0R/blocky))
- **External:** `it3e.xyz` (managed by [external-dns](https://github.com/kubernetes-sigs/external-dns)
  against Cloudflare; only ingresses tagged with the
  `external-dns.alpha.kubernetes.io/target` annotation are exported)

---

## Core Components

| Component | Purpose |
|-----------|---------|
| [cilium](https://cilium.io) | CNI + L2/BGP LB |
| [cert-manager](https://cert-manager.io/docs/) | TLS via Let's Encrypt |
| [external-dns](https://github.com/kubernetes-sigs/external-dns) | Cloudflare DNS sync |
| [external-secrets](https://github.com/external-secrets/external-secrets/) | 1Password Connect → K8s secrets |
| [ingress-nginx](https://github.com/kubernetes/ingress-nginx/) | HTTP ingress |
| [rook](https://github.com/rook/rook) | In-cluster Ceph (block, FS, S3) |
| [sops](https://toolkit.fluxcd.io/guides/mozilla-sops/) | In-repo secret encryption |
| [volsync](https://github.com/backube/volsync) + [snapscheduler](https://github.com/backube/snapscheduler) | PVC backup |

## License

See [LICENSE](./LICENSE).
