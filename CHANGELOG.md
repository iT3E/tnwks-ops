# Changelog

Notable changes to this repository, newest first. Each PR adds a Changie
fragment under `.changes/unreleased/`; merging to `main` releases them.

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
