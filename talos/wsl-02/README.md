# homelab-wsl-02 (Talos in Docker on WSL-02)

The second WSL cluster: 1 control plane + 2 workers in Docker on WSL-02's
distro, **Cilium** as the CNI (kube-proxy replacement, LB IPAM + L2
announcements on the Docker bridge instead of MetalLB). The plan and its
phases are in the owner's project notes; the cluster's GitOps entry point is
`kubernetes/clusters/wsl-02`.

|                   |                                                                                       |
| ----------------- | ------------------------------------------------------------------------------------- |
| Docker network    | `homelab-wsl-02`, `10.6.0.0/24` (control plane `10.6.0.2`)                            |
| Cilium cluster ID | 2 (homelab-wsl is 1; Cluster Mesh, `docs/wsl/multi-site.md`)                          |
| Pods / services   | `10.44.0.0/16` / `10.45.0.0/16`                                                       |
| LoadBalancer IPs  | `10.6.0.200-10.6.0.220` (Cilium LB IPAM, `kubernetes/apps/kube-system/cilium/config`) |
| Storage           | `local-path` (and `-bulk`, `-ext4`) on the distro's ext4 vhdx on C:                   |

## Bootstrap

From WSL-02's distro (the Docker daemon the nodes run on), with the CLIs
from the Ansible role `wsl_cluster_tools` and a clone of this repo:

```bash
task bootstrap:wsl-02   # playbooks 01-04 with -e talos_target=wsl-02
```

1. `01-talos-bootstrap.yml`: `talosctl cluster create docker` with
   `patches/cluster.yaml` (all nodes) and `patches/controlplane.yaml`.
2. `02-cni-bootstrap.yml`: Cilium, with the values Flux will use
   (rendered from `kubernetes/apps/kube-system/cilium/app/helmrelease.yaml`
   and `kubernetes/clusters/wsl-02/cluster-settings.yaml`).
3. `03-sops-keys.yml`: the `sops-age` Secret from
   `~/.config/sops/age/homelab-wsl-02.txt` (a recipient in `.sops.yaml`).
4. `04-flux-install.yml`: applies `kubernetes/clusters/wsl-02/flux-system`
   (no `flux bootstrap`: the repo is public, Flux pulls it over HTTPS, and
   nothing is pushed to `main`).

## Patches need talosctl >= 1.14

`patches/*.yaml` patch the multi-document machine config that talosctl
1.14 generates. The v1alpha1 form used by `talos/wsl/` (for `homelab-wsl`)
is rejected by Talos when the config comes from talosctl 1.14:
"cluster network config is already set in the v1alpha1 config".

## Why Cilium works here

Cilium's defaults ask for `CAP_SYS_MODULE`, which the WSL2 kernel refuses
in nested containers ([cilium#13768](https://github.com/cilium/cilium/issues/13768)).
Talos doesn't let pods load modules either, so the HelmRelease drops it
(Talos' own Cilium guide does the same): `securityContext.capabilities` and
`cgroup.autoMount.enabled: false`. Tested on WSL-02 on 2026-11-05: pod and
service traffic, egress, NetworkPolicy denies (also after a node restart),
LB IPAM + L2 reachable from the WSL host.
