# Changelog

Notable changes to this repository, newest first. Each PR adds a Changie
fragment under `.changes/unreleased/`; merging to `main` releases them.

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
