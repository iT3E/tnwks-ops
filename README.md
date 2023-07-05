<div align="center">

## My Home Operations repository

_... managed by Flux, Renovate, GitHub Actions, Terraform, Ansible, and Powershell_ :robot:

</div>

<div align="center">

[![Kubernetes](https://img.shields.io/badge/v1.27.3-blue?style=for-the-badge&logo=kubernetes&logoColor=white)](https://k8s.io/)
[![Renovate](https://img.shields.io/github/actions/workflow/status/iT3E/tnwks-ops/release.yaml?branch=master&label=&logo=renovatebot&style=for-the-badge&color=blue)](https://github.com/iT3E/tnwks-ops/actions/workflows/release.yaml)

[![Home-Internet](https://img.shields.io/uptimerobot/status/m794729136-a0c7c0a6dabf661ccbf287ee?color=brightgreeen&label=Home%20Internet&style=for-the-badge&logo=v&logoColor=white)](https://status.devbu.io)&nbsp;&nbsp;&nbsp;
[![Status-Page](https://img.shields.io/uptimerobot/status/m794729136-a0c7c0a6dabf661ccbf287ee?color=brightgreeen&label=Status%20Page&style=for-the-badge&logo=statuspage&logoColor=white)](https://status.it3e.xyz)&nbsp;&nbsp;&nbsp;



</div>
<br><br>

👋 Welcome to my Home Operations repository, **tnwks** /tee-networks/. This is a mono repository that serves as the foundation for my home infrastructure.

 I try to adhere to Infrastructure as Code (IaC) and GitOps practices using the tools like [Ansible](https://www.ansible.com/), [Terraform](https://www.terraform.io/), [Kubernetes](https://kubernetes.io/), [Flux](https://github.com/fluxcd/flux2), [Renovate](https://github.com/renovatebot/renovate) and [GitHub Actions](https://github.com/features/actions).

---

## ⛵ Kubernetes

### Installation

The cluster is running on [Proxmox Virtual Environment](https://www.proxmox.com/en/proxmox-ve), an open-source hypervisor built on top of Debian. [Kubernetes](https://k8s.io) is running on virtual machines within Proxmox. A [PVE Ceph Cluster](https://pve.proxmox.com/wiki/Deploy_Hyper-Converged_Ceph_Cluster) is handling my VM storage, along with an [externally-connected](https://rook.io/docs/rook/v1.11/CRDs/Cluster/external-cluster/) implementation of [Rook Ceph](https://rook.io) that is providing my K8s workloads with persistent block, object, and file storage.


### Core Components

- [actions-runner-controller](https://github.com/actions/actions-runner-controller): Self-hosted Github runners.
- [cilium](https://cilium.io): Internal Kubernetes networking plugin.
- [cert-manager](https://cert-manager.io/docs/): Creates SSL certificates for services in my Kubernetes cluster.
- [external-dns](https://github.com/kubernetes-sigs/external-dns): Automatically manages DNS records from my cluster in a cloud DNS provider.
- [external-secrets](https://github.com/external-secrets/external-secrets/): Managed Kubernetes secrets using [1Password Connect](https://github.com/1Password/connect).
- [ingress-nginx](https://github.com/kubernetes/ingress-nginx/): Ingress controller to expose HTTP traffic to pods over DNS.
- [rook](https://github.com/rook/rook): Distributed block storage for peristent storage.
- [sops](https://toolkit.fluxcd.io/guides/mozilla-sops/): Managed secrets for Kubernetes, Ansible and Terraform which are commited to Git.
- [volsync](https://github.com/backube/volsync) and [snapscheduler](https://github.com/backube/snapscheduler): Backup and recovery of persistent volume claims.

### GitOps

[Flux](https://github.com/fluxcd/flux2) watches my [kubernetes](./kubernetes/) folder (see Directories below) and makes the changes to my cluster based on the YAML manifests.

The way Flux works for me here is it will recursively search the [kubernetes/apps](./kubernetes/apps) folder until it finds the most top level `kustomization.yaml` per directory and then apply all the resources listed in it. That aforementioned `kustomization.yaml` will generally only have a namespace resource and one or many Flux kustomizations. Those Flux kustomizations will generally have a `HelmRelease` or other resources related to the application underneath it which will be applied.

[Renovate](https://github.com/renovatebot/renovate) watches my **entire** repository looking for dependency updates, when they are found a PR is automatically created. When some PRs are merged [Flux](https://github.com/fluxcd/flux2) applies the changes to my cluster.

### Directories

This Git repository contains the following directories under [kubernetes](./kubernetes/).

```sh
📁 kubernetes      # Kubernetes cluster defined as code
├─📁 bootstrap     # Flux installation
├─📁 flux          # Main Flux configuration of repository
└─📁 apps          # Apps deployed into my cluster grouped by namespace (see below)
```

### Cluster layout

Below is a a high level look at the layout of how my directory structure with Flux works. In this brief example you are able to see that `authelia` will not be able to run until `glauth` and `cloudnative-pg` are running. It also shows that the `Cluster` custom resource depends on the `cloudnative-pg` Helm chart. This is needed because `cloudnative-pg` installs the `Cluster` custom resource definition in the Helm chart.

```python
# Key: <kind> :: <metadata.name>
GitRepository :: k8s-gitops
    Kustomization :: cluster
        Kustomization :: cluster-apps
            Kustomization :: cluster-apps-authelia
                DependsOn:
                    Kustomization :: cluster-apps-glauth
                    Kustomization :: cluster-apps-cloudnative-pg-cluster
                HelmRelease :: authelia
            Kustomization :: cluster-apps-glauth
                HelmRelease :: glauth
            Kustomization :: cluster-apps-cloudnative-pg
                HelmRelease :: cloudnative-pg
            Kustomization :: cluster-apps-cloudnative-pg-cluster
                DependsOn:
                    Kustomization :: cluster-apps-cloudnative-pg
                Cluster :: postgres
```

### Networking

| Name                                         | CIDR            |
| -------------------------------------------- | --------------- |
| Kubernetes Nodes VLAN                        | `10.0.0.0/24`   |
| Kubernetes external services (Cilium w/ BGP) | `7.0.0.0/8`     |
| Kubernetes pods                              | `10.244.0.0/16` |
| Kubernetes services                          | `10.245.0.0/16` |

- [Cilium](https://cilium.io) is configured with the `io.cilium/lb-ipam-ips` annotation to expose Kubernetes services with their own IP over BGP which is configured on my router.
- [cloudflared](https://github.com/cloudflare/cloudflared) provides a [secure tunnel](https://www.cloudflare.com/products/tunnel/) for [Cloudflare](https://www.cloudflare.com/) to ingress traffic from the Internet into my Kubernetes cluster.

---

## 🌐 DNS

### Internal DNS

[blocky](https://github.com/0xERR0R/blocky) provides the first hop of DNS resolution inside my network. DNS requests to my public domain are forwarded to [k8s-gateway](https://github.com/ori-edge/k8s_gateway) which checks to see if it's present in my cluster; if not, it talks out to [1.1.1.1](https://1.1.1.1) which is configured as my primary DNS provider.

### External DNS

[external-dns](https://github.com/kubernetes-sigs/external-dns) is deployed in my cluster and configured to sync DNS records to [Cloudflare](https://www.cloudflare.com/). The only ingresses this `external-dns` instance looks at to gather DNS records to put in `Cloudflare` are ones that have an annotation of `external-dns.alpha.kubernetes.io/target`.

---

### 📖 Docs

The documentation that goes along with this repo can be found [over here](https://it3e.github.io/tnwks-ops/).

---

## 🔧 Hardware

| Device                      | Count | OS Disk Size         | Data Disk Size                         | Ram   | Operating System | Purpose            |
| --------------------------- | ----- | ------------         | ------------------------               | ----- | ---------------- | ------------------ |
| Unifi UDM Pro               | 1     | -                    | -                                      | -     | -                | Router             |
| HP 1810g                    | 1     | -                    | -                                      | -     | -                | Switch             |
| Aruba S2500-24P             | 1     | -                    | -                                      | -     | -                | Switch             |
| HP DL360p G8                | 3     | 3x16GB JetFlash 780  | 3x 1.2TB HDD, 3x 960GB Samsung PM1633a | 192GB | PVE              | Hypervisor         |
| Synology NAS RS820+         | 1     | -                    | 4x16TB w/ 2TB NVMe cache               | 16GB  | -                | NFS                |
| CyberPower ATS PDU          | 1     | -                    | -                                      | -     | -                | PDU                |
| CyberPower UPS              | 1     | -                    | -                                      | -     | -                | PSU                |

---

## 🤝 Gratitude and Thanks

Thanks to all the people who donate their time to the [Kubernetes @Home](https://discord.gg/k8s-at-home) Discord community. A lot of inspiration for my cluster comes from the people that have shared their clusters using the [k8s-at-home](https://github.com/topics/k8s-at-home) GitHub topic. Be sure to check out the [Kubernetes @Home search](https://nanne.dev/k8s-at-home-search/) for ideas on how to deploy applications or get ideas on what you can deploy.

---

### 🔏 License

See [LICENSE](https://github.com/bjw-s/home-ops/blob/main/LICENSE)
