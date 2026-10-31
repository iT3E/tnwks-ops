# Changelog

Notable changes to this repository, newest first. Each PR adds a Changie
fragment under `.changes/unreleased/`; merging to `main` releases them.

## v1.40.0 - 2026-10-31

### Added

* Mealie: log in with tnwks-auth (Cognito) through Mealie's built-in OIDC; admins become Mealie admins, viewers users, anyone else is refused. Password login stays for the break-glass admin

## v1.39.0 - 2026-10-31

### Added

* Cognito: a mealie app client (confidential, authorization code) for Mealie's built-in OIDC login, with its client ID and secret as outputs

## v1.38.0 - 2026-10-31

### Added

* Kitchen display: closer to the Apolosign app. Calendar tab with Family, Week, Month and List views, day/week/month paging and a person filter; a Meals tab (week grid from the Mealie calendars); a Lists tab with several lists, an add box, tap to check off and a Completed section; sticky notes (todo.notes) on Home and Lists; a greeting line with chores left, events and groceries; labelled routine periods and stars earned today

### Fixed

* Kitchen display: the cards reload the page when HA serves a new version of them, so a wall display or phone that kept the old code after a restart no longer crashes on a new dashboard config. A card that hits an error shows it in place instead of blanking

## v1.37.0 - 2026-10-31

### Added

* Privacy check covers commit messages (commit-msg hook) and your git identity at commit time, re-checks PR titles and bodies on edit, and deletes GitHub comments or replaces review bodies that contain a denylisted term (privacy-text.yaml)

## v1.36.0 - 2026-10-31

### Changed

* Privacy check now blocks: required in the main ruleset and run by a pre-commit hook. Exceptions live in .github/privacy-allow (name-free globs) or a privacy:allow line comment; the storefront app is the first

## v1.35.0 - 2026-10-31

### Changed

* Kitchen display: members use neutral keys (adult1, adult2, kid1, kid2) in entity IDs, and display names come from the home-assistant Secret, so no family names are in the repo. Stars carry over through the counters' initial values

## v1.34.0 - 2026-10-31

### Changed

* Renovate approval label is now owner/approved (was named after a person); docs and the review workflow say "the owner". Drops a commented-out Frigate camera

## v1.33.0 - 2026-10-31

### Added

* Privacy check: bin/privacy-check flags family names and personal emails from a SOPS-encrypted denylist on every PR (CI reads it from the PRIVACY_TERMS secret); report-only until the existing hits are cleaned up

## v1.32.0 - 2026-10-31

### Changed

* litellm: v1.86.2 -> v1.106.6. Runs new Prisma migrations on start; the master key already meets v1.104's refuse-to-start check

## v1.31.1 - 2026-10-31

### Fixed

* litellm: pin the image to v1.86.2 with its digest instead of the rolling main-stable tag, so Renovate can track it; Renovate follows only plain vX.Y.Z releases and LiteLLM minors are reviewed, not automerged

## v1.31.0 - 2026-10-31

### Added

* home-assistant: Kitchen dashboard for the Apolosign wall display, with chores and stars (routines package), our own frontend cards served from a ConfigMap, and kiosk-mode

## v1.30.3 - 2026-10-31

### Fixed

* Flux manages its own install on WSL: kubernetes/clusters/wsl includes ./flux-system, so a merged Flux upgrade (gotk-components.yaml) rolls out on reconcile instead of needing a manual apply

## v1.30.2 - 2026-10-31

### Fixed

* k8s-gateway moves to the k8s-gateway project chart 4.0.1 on Codeberg (image codeberg.org/k8s-gateway/k8s_gateway 1.9.2). The old quay.io/oriedge/k8s_gateway image is gone, so the pod only ran from worker-1's image cache

## v1.30.1 - 2026-10-31

### Fixed

* WSL kubelets delete images no container has used for a week (imageMaximumGCAge 168h). Disk-based image GC never ran because the node filesystem is the 1 TB WSL disk, so old images piled up (124G across the workers) and filled E:

## v1.30.0 - 2026-10-31

### Changed

* Flux Alert and Provider for the War Room move from notification.toolkit.fluxcd.io/v1beta3 to v1 (deprecated in Flux 2.10)

## v1.29.0 - 2026-10-31

### Changed

* WSL cluster on Talos 1.13.12 (from 1.13.2), rolled node by node with `task talos:upgrade:wsl`

## v1.28.0 - 2026-10-31

### Changed

* WSL cluster on Kubernetes 1.35.10 (from 1.34.0), rolled out by tuppr; Flux 2.10 needs 1.35.2 or newer

## v1.27.0 - 2026-10-31

### Added

* Cluster upgrades through Renovate: tuppr (system-upgrade) rolls out Kubernetes versions from a KubernetesUpgrade on merge, MS-01 also gets a TalosUpgrade, and the WSL Talos version is pinned in talos/wsl/versions.yaml and rolled with `task talos:upgrade:wsl`, which swaps each node container onto the new image with its volumes. Renovate groups each version across files with one PR per minor (docs/operations/cluster-upgrades.md)

### Fixed

* The worker-2 augmentation recreate in 01-talos-bootstrap.yml keeps the container's memory and CPU limits instead of resetting memory to 8G

## v1.26.0 - 2026-10-31

### Changed

* playwright-mcp runs Microsoft's official image (mcr.microsoft.com/playwright/mcp v0.0.83) instead of the Acuvity rebuild, which lagged upstream by ~27 releases and published its Helm chart as image tag 1.0.0

## v1.25.1 - 2026-10-31

### Fixed

* CI: the image diff renders each cluster from its own entrypoint, so PRs that touch frigate no longer fail on an unsubstituted ${SERVICE_MONITOR_ENABLED}; the live WSL cluster also gets its own render test. Image exists rejects tags that are not container images (Helm charts pushed into an image repo), and a registry rate limit on a Renovate PR warns instead of failing

## v1.25.0 - 2026-10-29

### Changed

* Renovate: updates that only touch MS-01 manifests or Talos config (Cilium, Rook Ceph, Thanos, Vector agent, UISP, node-feature-discovery, CNPG backups) automerge behind Build Success while MS-01 is not live, instead of going to review

## v1.24.0 - 2026-10-29

### Changed

* Renovate review: Ori now migrates and lands impact/high PRs instead of leaving majors open, and a new impact/critical tier (Flux, cluster version, CNI, data store majors) merges only after the owner adds owner/approved; ARC controller and scale sets are grouped, and image-only bumps of the generated Flux manifest are off

## v1.23.0 - 2026-10-28

### Added

* Renovate review workflow: impact/high Renovate PRs go straight to Ori's reviewer agent through an OpenClaw hook on a new openclaw-hooks runner, with ori/blocked tracking what was left open

## v1.22.1 - 2026-10-28

### Fixed

* Flux failure alerts cover HelmReleases in every app namespace (an Alert event source only matches one namespace); CI fails if a namespace with a HelmRelease is left out

## v1.22.0 - 2026-10-28

### Added

* Renovate tiers: app patches (and non-0.x minors, weekly) automerge once the new Kubernetes CI passes (flate render + image exists); platform, Terraform, majors, 0.x minors and stateful-app minors get impact/high and are never automerged. Flux error events now page the War Room through Alertmanager

## v1.21.0 - 2026-10-28

### Changed

* Cloudflare Terraform moved to provider v5 (cloudflare_dns_record, per-setting cloudflare_zone_setting, list-style ruleset rules); state migrated with moved/import blocks, Cloudflare itself unchanged

## v1.20.0 - 2026-10-27

### Added

* "WAN Traffic" Grafana dashboard: live and historical WAN usage per device, by app, category and destination network, from the router's NetFlow; flow-collector now labels each flow with service, app and category from its port and remote network

### Fixed

* Per-client WAN rates (the "Top users" line in WAN alerts) are no longer inflated ~80000x; since the mktxp switch they were multiplied by each DHCP lease's seconds-to-expiry

## v1.19.1 - 2026-10-27

### Fixed

* WAN download usage per client is counted again; since the RB5009 cutover every download flow carried the router's public address and was dropped, so the "Top users" download line in WAN alerts was empty

## v1.19.0 - 2026-10-27

### Added

* Prometheus scrapes Frigate, and the War Room gets an alert when Frigate's object detection stalls, every camera feed is down, or Frigate stops answering; a hung Coral detector left person alerts off for 16 minutes on 2026-10-26 with every pod Running

## v1.18.4 - 2026-10-27

### Fixed

* The LAN bridge's Scheduled Task re-syncs the portproxy rules after a WSL vNIC reset; its VmSwitch trigger listened on a log that never gets EventID 232, and it ran as SYSTEM, which can't see the WSL distro

## v1.18.3 - 2026-10-26

### Security

* cloudflared runs 2026.10.4; versions before 2026.10.2 still trust the eight eMudhra (emSign) intermediates with weak private keys, so anyone holding those keys could impersonate the Cloudflare edge to the tunnel

## v1.18.2 - 2026-10-26

### Security

* Frigate's LAN UI requires an SSO login (oauth2-proxy) and redirects plain http to https; it used to serve its unauthenticated API to the whole LAN, camera credentials included

## v1.18.1 - 2026-10-26

### Fixed

* Home Assistant's login works over a port-forward (it no longer forces https, which sent http://localhost:8123 to https), and its welcome screen offers both SSO and the local login

## v1.18.0 - 2026-10-26

### Added

* Windows host monitoring agents as code: task wsl-host:monitoring installs windows_exporter, smartmontools and smartctl_exporter at pinned, checksummed versions and limits both exporters to the WSL subnet

## v1.17.2 - 2026-10-26

### Fixed

* Public apps see real visitor IPs; the public ingress-nginx trusts cloudflared's X-Forwarded-For from the pod network instead of reporting every visitor as the cloudflared pod

## v1.17.1 - 2026-10-26

### Fixed

* Frigate runs object detection on the Coral again; detect.enabled defaults to false, so no camera was sending it frames and no person alerts or alert recordings were being produced

## v1.17.0 - 2026-10-26

### Added

* Home Assistant runs on homelab-wsl (2026.10, was pinned at 2025.3) at hass.tnwks.us and hass.internal.tnwks.us with Cognito SSO, the Frigate integration, a Security camera dashboard and night-time person alerts as Companion app pushes that break through Do Not Disturb; its config lives in the repo and CI runs check_config on it

## v1.16.0 - 2026-10-26

### Added

* SMART disk-health alerts for the Windows host (smartctl_exporter + Win32_DiskDrive), and the Windows memory alerts work again

## v1.15.0 - 2026-10-26

### Added

* Cognito app client for Home Assistant SSO (public, PKCE) with managed login branding

## v1.14.1 - 2026-10-26

### Fixed

* Mosquitto persists its database again; the PVC was mounted at /data while the broker saved to the read-only /mosquitto/data, so retained messages were lost on restart

## v1.14.0 - 2026-10-26

### Added

* WSL Talos break-glass: a script that mints an admin talosconfig from the cluster's CA, and a SOPS-encrypted copy in the repo

## v1.13.3 - 2026-10-26

### Fixed

* WSL Talos: finished pods are garbage-collected (terminated-pod-gc-threshold 1), so leftovers from node restarts can no longer stall Recreate rollouts

## v1.13.2 - 2026-10-26

### Fixed

* Grafana restarts once per config change (Reloader no longer races the chart's own restart), which had left it with no pod

## v1.13.1 - 2026-10-26

### Fixed

* Grafana shows one Home dashboard (a stale provisioned duplicate is gone)

## v1.13.0 - 2026-10-26

### Removed

* Grafana: retired dashboards (Ceph, node-exporter, speedtest); the MikroTik dashboard moves to a Network folder

## v1.12.1 - 2026-10-26

### Security

* Router no longer routes the retired EdgeRouter transit VLAN 10 (172.16.1.0/24), so no VLAN can reach a device left there; the ERL fallback route is gone

## v1.12.0 - 2026-10-26

### Added

* EdgeOS devices plan on PRs and apply on merge from the self-hosted runner, like the MikroTik root

## v1.11.0 - 2026-10-26

### Changed

* The CI runner's tunnel also forwards the EP-R6's SSH, and the runner carries an SSH key for EdgeOS devices

## v1.10.0 - 2026-10-26

### Added

* The CI runner's wg03 tunnel can reach the EP-R6's SSH (and nothing else on VLAN 140)

## v1.9.0 - 2026-10-26

### Changed

* EP-R6: hostname sce-ep01, its drop-invalid firewall rules drop, NTP from the router, the airCube gets public DNS for the clients behind it, and the CI runner's SSH key

## v1.8.2 - 2026-10-26

### Fixed

* edgeos-sync applies over SSH: commit-confirm without EdgeOS's interactive prompt, and the confirm step checks the rollback job is really cancelled

## v1.8.1 - 2026-10-26

### Fixed

* EP-R6 DHCP clients, DNS forwarder and resolver use the router (10.10.140.1) instead of the retired VyOS DNS container

## v1.8.0 - 2026-10-26

### Added

* EP-R6 configuration as code: EdgeOS set commands in infrastructure/edgeos with a plan/apply tool (device-validated plans, commit-confirm applies)

## v1.7.0 - 2026-10-26

### Added

* MikroTik changes plan on PRs and apply on merge from a self-hosted runner (tnwks-ops-runner) that reaches only the router's REST API over wg03

## v1.6.0 - 2026-10-26

### Added

* Router wg03 listener for the CI runner: its own zone, REST API only, joined from the LAN; the MikroTik secrets gain a CI-only age recipient

## v1.5.3 - 2026-10-26

### Security

* The four age-only SOPS files (MikroTik secrets, the Roles Anywhere CA key, the mktxp and storefront API Secrets) gain the KMS recipient, so they no longer depend on the age keys alone

## v1.5.2 - 2026-10-26

### Fixed

* Roles Anywhere SOPS sessions can pass their session tags to iam-role-sops (sts:TagSession)

## v1.5.1 - 2026-10-26

### Fixed

* Roles Anywhere SOPS sessions can assume iam-role-sops (sts:SetSourceIdentity), and tasks find the SOPS age key

## v1.5.0 - 2026-10-26

### Added

* IAM Roles Anywhere for workstation SOPS KMS access: a self-managed CA, a prod-account role that can only assume iam-role-sops, and aws:ra-* tasks

## v1.4.1 - 2026-10-26

### Fixed

* Router syslog reaches Loki: sent over UDP, since RouterOS TCP syslog has no message framing; the Windows and WSL relay setup is now task wsl-host:lan-bridge and task wsl-host:udp-relays

## v1.4.0 - 2026-10-26

### Added

* Router syslog to Loki: vector-aggregator on the WSL cluster, a tcp/6001 LAN bridge, and RouterOS BSD-syslog output

## v1.3.0 - 2026-10-26

### Added

* Router monitoring via mktxp: RouterOS metrics, router alerts, a MikroTik dashboard, and WAN throughput and client names from the router

### Removed

* VyOS and EdgeRouter scrape jobs, alerts and dashboards, and the EdgeRouter smokeping target

## v1.2.7 - 2026-10-26

### Fixed

* Router DNS drops records for retired hosts and the absent UISP server, and adds sce-rtr01

## v1.2.6 - 2026-10-26

### Fixed

* Router DDNS updates the Cloudflare record the WireGuard clients use as their endpoint; Terraform reaches the router over wg01

## v1.2.5 - 2026-10-26

### Fixed

* Flow export uses NetFlow v9: the routeros provider only accepts IPFIX in a spelling RouterOS 7.25 rejects

## v1.2.4 - 2026-10-26

### Fixed

* Flow export target uses RouterOS's lowercase ipfix version value

## v1.2.3 - 2026-10-26

### Fixed

* MikroTik config matches how RouterOS stores values (durations, single-host addresses, booleans, DHCP lease identifiers), so plans after an apply are empty

## v1.2.2 - 2026-10-26

### Fixed

* MikroTik module applies cleanly on RouterOS 7.25: NAT and flow export wait for VLAN interfaces, log prefixes fit 31 characters, the logging action name is alphanumeric, and the removed SSH password option is no longer set

## v1.2.1 - 2026-10-26

### Fixed

* RouterOS bootstrap waits for the API certificate key instead of a fixed 5 s pause

## v1.2.0 - 2026-10-26

### Changed

* Terraform CI: least-privilege tokens, applies gated on the prod environment, PR gating on author and source repo

### Fixed

* Release workflow no longer fails when another merge lands on main while it runs

## v1.1.0 - 2026-10-26

### Added

* Terraform port of the VyOS router and the EdgeRouter onto a MikroTik RB5009: zone firewall, admin-VPN-only management, staged cutover checklist and break-glass docs

## v1.0.0 - 2026-10-26

### Added

* Changie-driven changelog and releases: each PR adds a fragment, and merging to main cuts the version, tag and GitHub Release
