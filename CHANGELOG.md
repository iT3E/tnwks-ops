# Changelog

Notable changes to this repository, newest first. Each PR adds a Changie
fragment under `.changes/unreleased/`; merging to `main` releases them.

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

* The four age-only SOPS files (MikroTik secrets, the Roles Anywhere CA key, the mktxp and raines-things-api Secrets) gain the KMS recipient, so they no longer depend on the age keys alone

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
