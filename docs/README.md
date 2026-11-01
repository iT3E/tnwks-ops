# Docs

Runbooks and design notes. The repo-wide orientation (clusters, change
workflow, conventions) is [`../AGENTS.md`](../AGENTS.md).

## Network

| | |
| --- | --- |
| [`network/architecture.md`](network/architecture.md) | Topology, VLANs and zones, router services, observability flows |
| [`network/access.md`](network/access.md) | Who reaches what (admin VPN only), and break-glass |
| [`network/ci.md`](network/ci.md) | MikroTik and EdgeOS changes via the self-hosted runner |
| [`network/migration/`](network/migration/) | The finished RB5009 cutover: [checklist](network/migration/cutover-checklist.md), [VyOS port](network/migration/vyos-port.md), [EdgeRouter discovery](network/migration/edgerouter-discovery.md) |

Device config lives with its tooling: `infrastructure/terraform/modules/mikrotik`
(RouterOS) and `infrastructure/edgeos` (EP-R6).

## WSL cluster (`homelab-wsl`)

| | |
| --- | --- |
| [`wsl/bootstrap.md`](wsl/bootstrap.md) | Building the Talos-in-Docker cluster |
| [`wsl/lan-exposure.md`](wsl/lan-exposure.md) | How the LAN reaches cluster services (and the Windows host's exporters) |
| [`wsl/usb-passthrough.md`](wsl/usb-passthrough.md) | Coral TPU and Z-Wave stick into the cluster |
| [`wsl/move-disk-off-c.md`](wsl/move-disk-off-c.md) | Moving the WSL disk off `C:` |

Talos access and break-glass: [`../talos/wsl/README.md`](../talos/wsl/README.md).

## MS-01 cluster (`homelab-ms-01`)

| | |
| --- | --- |
| [`ms-01/bootstrap.md`](ms-01/bootstrap.md) | Bare-metal Talos bootstrap |
| [`ms-01/ceph-notes.md`](ms-01/ceph-notes.md) | Rook Ceph troubleshooting |

## Operations

| | |
| --- | --- |
| [`operations/disaster-recovery.md`](operations/disaster-recovery.md) | Rebuilding from the repo |
| [`operations/sops.md`](operations/sops.md) | Secrets: recipients, editing, re-keying |
| [`operations/renew-kubeconfig-cert.md`](operations/renew-kubeconfig-cert.md) | Renewing the kubeconfig client certificate |
| [`operations/renovate.md`](operations/renovate.md) | Renovate tiers, and how impact/high PRs reach Ori's reviewer agent |

## Apps

| | |
| --- | --- |
| [`apps/home-assistant.md`](apps/home-assistant.md) | Home Assistant |
| [`apps/immich.md`](apps/immich.md) | Immich: photo library for the kitchen screensaver, its storage, login and break-glass |
| [`apps/kitchen-screensaver.md`](apps/kitchen-screensaver.md) | Kitchen screensaver: the page the kitchen display's screensaver shows (photos, time, weather, events, dinner, stars), its API, accounts and how to switch back |
| [`apps/immichframe.md`](apps/immichframe.md) | ImmichFrame: the kitchen display's screensaver (photos, clock, today's events), its settings and the tablet setup |
| [`apps/peloton-to-garmin.md`](apps/peloton-to-garmin.md) | peloton-to-garmin: the dead-auth v3.6.1 pin and why Renovate can't see it |

## Other

- [`terraform/tfcloud-flow.drawio`](terraform/tfcloud-flow.drawio): how
  Terraform Cloud reaches AWS (`environments/bootstrap/aws-init`).
- [`archive/`](archive/): the README of the Flux cluster template this repo
  started from; reference only.
