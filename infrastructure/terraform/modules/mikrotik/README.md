# mikrotik

Terraform module that reproduces the `sce-vyos01` VyOS router on a MikroTik
RB5009UG+S+IN running RouterOS 7.

Source of truth for the thing being ported: [`iT3E/vyos-config`](https://github.com/iT3E/vyos-config)
(`config-parts/*.sh`). Reasoning, gaps and the cutover runbook live in
[`docs/mikrotik-vyos-port.md`](../../../../docs/mikrotik-vyos-port.md).

## File layout

One file per piece of infrastructure, matching the repo convention:

| File             | Ports from VyOS                                          |
| ---------------- | -------------------------------------------------------- |
| `interfaces.tf`  | `interfaces ethernet` + `vif`, plus zone interface-lists  |
| `dhcp.tf`        | `service dhcp-server shared-network-name`                 |
| `firewall.tf`    | `firewall zone`, `firewall ipv4 name`, `firewall group`   |
| `nat.tf`         | `nat destination` (forced DNS/NTP), `nat source`          |
| `routing.tf`     | `protocols static route`                                  |
| `wireguard.tf`   | `interfaces wireguard wg01/wg02`                          |
| `dns.tf`         | the blocky/dnsdist/bind container stack + `service ntp`   |
| `system.tf`      | `system host-name/time-zone/syslog/login`, `service ssh`  |

## Three things that do not translate one-to-one

**1. Zones become interface-lists plus explicit per-pair drops.**
VyOS gave every directed zone pair its own ruleset with its own
`default-action drop`. RouterOS has one flat ordered `forward` chain. The port
creates an interface-list per zone, emits each pair's accept rules, then emits
that pair's terminating drop. The trailing drop is not optional; without it an
unmatched packet falls through into a later pair's accepts and the policy
silently loosens.

Rule order is therefore semantic, and Terraform does not order resource
creation. `routeros_move_items` re-sequences the chain after apply using the
sorted `zone_policies` keys, which is why those keys are `NNN-from-to`.

**2. VLANs move onto a bridge.**
VyOS terminated VLANs directly on `eth1` with no L2 bridge. RouterOS wants a
VLAN-filtering bridge so the RB5009 switch chip can offload the trunk. Each
`eth1.<vif>` becomes `routeros_interface_vlan` on `bridge-lan`, and the bridge
needs a matching `routeros_interface_bridge_vlan` tagged entry per VLAN. Missing
that entry is the standard "everything died when I enabled VLAN filtering"
failure.

**3. The DNS container stack is replaced, not ported.**
`dnsdist` + `blocky` + `bind` ran in Podman on the router and were a large part
of why VyOS sat at ~187% CPU. The RB5009 has 1GB RAM and no sensible storage for
container layers. Native RouterOS DNS takes over: `ip_dns` for upstreams,
`ip_dns_adlist` for blocking, `ip_dns_record` for the split-horizon entries. The
resolver address advertised by DHCP changes from `10.10.53.4` to the VLAN SVI,
and the forced-DNS dstnat rules follow it.

## Conventions

- `vlans` entries carry `enabled`. VyOS shipped SVIs for VLANs 11/110/120/410/
  550/720 with no live devices; they stay in tfvars as `enabled = false` so the
  intent survives without provisioning dead interfaces.
- `port_lists` exists because RouterOS has no port-group object. Entries are
  rendered into comma-joined `dst_port` strings at plan time.
- WireGuard private keys arrive through SOPS-decrypted tfvars. Never a literal.
- Every resource carries a `(terraform)` comment suffix so hand-made config is
  distinguishable on the device.
