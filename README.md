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

Two peer production clusters, one source of truth:

| Cluster | Hardware | Storage | Notes |
|---------|----------|---------|-------|
| **`wsl`** | Talos in Docker on the WSL2 desktop (1 CP + 2 workers) | local-path | Hosts USB-attached apps (Frigate / Coral, zwave-js-ui / Z-Stick) via [usbipd-win](./docs/usb-passthrough.md) |
| **`ms-01`** | 3x Minisforum MS-01 i9-13900H, bare-metal Talos | Rook Ceph (3x 1TB NVMe) + democratic-csi NFS | Full stack including the public Cloudflare tunnel |

Both share the manifests under `kubernetes/apps/` and the Flux machinery
under `kubernetes/flux/meta/`. Per-cluster differences (storage class,
IP ranges, DNS) live in `kubernetes/clusters/{wsl,ms-01}/cluster-settings.yaml`
and are resolved via Flux variable substitution. `flux bootstrap github`
is run with `--path=kubernetes/clusters/<name>` so each cluster gets its
own entry point.

```
tnwks-ops/
├── README.md
├── Taskfile.yml
├── .sops.yaml
├── talos/                       # Machine configs
│   ├── wsl/                     # talosctl cluster create config
│   └── ms-01/                   # MS-01 controlplane + worker + patches
├── infrastructure/
│   ├── ansible/                 # Bootstrap playbooks (talos, flux, sops)
│   ├── terraform/aws/           # Active
│   ├── terraform/cloudflare/    # Active
│   └── _archive/                # Legacy PVE/Proxmox terraform + packer
├── kubernetes/
│   ├── apps/                    # Shared app manifests (namespaced)
│   ├── bootstrap/               # Optional manual Flux install (legacy)
│   ├── flux/meta/               # Shared HelmRepositories / GitRepositories
│   └── clusters/
│       ├── wsl/                 # Flux entry point — trims to local-path
│       │   ├── flux-system/     # Created by `flux bootstrap`
│       │   ├── cluster-settings.yaml
│       │   ├── cluster-secrets.sops.yaml
│       │   ├── meta.yaml        # Flux Kustomization → ../../flux/meta
│       │   ├── apps.yaml        # Flux Kustomization → ./apps
│       │   └── apps/            # Trimmed namespace overlays
│       └── ms-01/               # Flux entry point — full stack + Rook Ceph
│           └── (same shape; apps/ pulls shared apps + storage)
└── docs/
    ├── bootstrap-wsl.md
    ├── bootstrap-ms-01.md
    ├── usb-passthrough.md
    └── disaster-recovery.md
```

---

## Getting started

### WSL cluster

```bash
export GITHUB_TOKEN=$(op read 'op://Private/GitHub Flux Token/credential')
task bootstrap:wsl
```

Detailed steps in [docs/bootstrap-wsl.md](./docs/bootstrap-wsl.md).

### MS-01 cluster

See [docs/bootstrap-ms-01.md](./docs/bootstrap-ms-01.md) — currently a
skeleton, will be filled in once MS-01 hardware is racked.

### USB passthrough (Coral, Z-Wave)

See [docs/usb-passthrough.md](./docs/usb-passthrough.md). The MS-01 cluster
gets it natively; the WSL cluster needs `usbipd-win` on the host.

### Disaster recovery

See [docs/disaster-recovery.md](./docs/disaster-recovery.md).

---

## Networking (MS-01 cluster)

| Name | CIDR |
|------|------|
| Node VLAN (910) | `10.10.91.0/24` |
| Pod CIDR | `10.42.0.0/16` |
| Service CIDR | `10.43.0.0/16` |
| LoadBalancer pool | `10.10.91.50–60` (Cilium L2/BGP) |

The WSL cluster has no LoadBalancer controller — Services are reached via
`kubectl port-forward` or NodePort.

CNI: [Cilium](https://cilium.io) on MS-01 (also providing L2/BGP for
LoadBalancer IPs — MetalLB has been removed). On WSL, [Flannel](https://github.com/flannel-io/flannel)
because Cilium can't run on Talos-in-Docker on WSL2 — see
[bootstrap-wsl.md](./docs/bootstrap-wsl.md#why-flannel-on-wsl-instead-of-cilium).
[cloudflared](https://github.com/cloudflare/cloudflared) provides the public
ingress tunnel into the MS-01 cluster.

## DNS

- **Internal:** `internal.tnwks.us` (resolved via [k8s-gateway](https://github.com/ori-edge/k8s_gateway)
  and [blocky](https://github.com/0xERR0R/blocky))
- **External:** `it3e.xyz` (managed by [external-dns](https://github.com/kubernetes-sigs/external-dns)
  against Cloudflare; only ingresses tagged with the
  `external-dns.alpha.kubernetes.io/target` annotation are exported —
  this only happens on the MS-01 cluster)

---

## Core Components

| Component | Purpose |
|-----------|---------|
| [cilium](https://cilium.io) | CNI + L2/BGP LB |
| [cert-manager](https://cert-manager.io/docs/) | TLS via Let's Encrypt |
| [external-dns](https://github.com/kubernetes-sigs/external-dns) | Cloudflare DNS sync |
| [external-secrets](https://github.com/external-secrets/external-secrets/) | 1Password Connect → K8s secrets |
| [ingress-nginx](https://github.com/kubernetes/ingress-nginx/) | HTTP ingress |
| [rook](https://github.com/rook/rook) | In-cluster Ceph (block, FS, S3) — MS-01 only |
| [sops](https://toolkit.fluxcd.io/guides/mozilla-sops/) | In-repo secret encryption |
| [volsync](https://github.com/backube/volsync) + [snapscheduler](https://github.com/backube/snapscheduler) | PVC backup |

## License

See [LICENSE](./LICENSE).
