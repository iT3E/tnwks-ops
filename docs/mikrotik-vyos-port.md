# VyOS to MikroTik RouterOS port

Porting `sce-vyos01` (VyOS 1.4-rolling on an Intel N5105 box) to a MikroTik
RB5009UG+S+IN running RouterOS 7.

- **Source of truth:** [`iT3E/vyos-config`](https://github.com/iT3E/vyos-config)
  `config-parts/*.sh`, HEAD `34b952a` (2026-07-13). Chosen over a live
  `show configuration commands` dump because the repo also carries the container
  configs and bootstrap scripts, which the live dump does not express.
- **Terraform:** `infrastructure/terraform/modules/mikrotik` (child) and
  `infrastructure/terraform/environments/prod/mikrotik` (root).
- **Bootstrap:** `infrastructure/mikrotik/bootstrap/`.
- **Generator:** `infrastructure/mikrotik/tools/vyos-to-locals.py`.

## Why a generator instead of hand-written HCL

The VyOS firewall is 140 rulesets / 195 rule lines / 45 accept rules, plus 20
DHCP static mappings and 37 address-group members. Hand-transcribing that is how
you ship a subtly wrong firewall. `task mikrotik:generate` regenerates
`locals.tf` from the VyOS repo, so the port is reproducible and can be
re-run if the VyOS config changes before cutover.

The generated file is committed. Review its diff, do not edit it.

## Translation summary

| Concern | VyOS | RouterOS | Result |
| --- | --- | --- | --- |
| VLANs | `eth1` + `vif` subinterfaces | VLAN-filtering bridge + `interface_vlan` | 11 VLANs |
| Addressing | `vif <n> address` | `ip_address` per SVI | 11 addresses |
| DHCP | 9 `shared-network-name` | `ip_pool` + `ip_dhcp_server` + `_network` | 9 servers, 21 leases |
| Zones | 14 zones, per-pair rulesets | `interface_list` + forward-chain segments | 22 chains |
| Firewall | 45 accepts across 30 rulesets | 44 forward accepts + 28 input accepts | see below |
| Address groups | `firewall group address-group` | `ip_firewall_addr_list` | 21 lists, 37 members |
| Port groups | `firewall group port-group` | rendered into `dst_port` strings | 3 |
| NAT | 8 `nat destination` rules | `ip_firewall_nat` dstnat | 12 (tcp_udp split) |
| Routing | 4 `protocols static route` | `ip_route` | 4 |
| WireGuard | `wg01`, `wg02` | `interface_wireguard` + peers | 2 listeners, 5 peers |
| DNS | 3 Podman containers | native RouterOS DNS | see below |
| Containers | 9 Podman containers | Kubernetes / native / dropped | see below |

## The four translations that are not one-to-one

### 1. Zones become interface-lists plus explicit per-pair drops

VyOS gave every directed zone pair its own ruleset with its own
`default-action drop`. RouterOS has one flat ordered `forward` chain.

The port creates a `routeros_interface_list` per zone, emits each pair's accept
rules matching `in_interface_list` / `out_interface_list`, then emits **that
pair's own terminating drop**.

That trailing per-pair drop is load-bearing. Without it, a packet that matches no
accept in its own pair falls through into a *later* pair's accept rules and the
policy silently loosens. This is the single most dangerous place to "simplify"
this config.

Rule order is therefore semantic, and Terraform does not guarantee resource
creation order. `ip_firewall_filter` has no `place_before`, so
`routeros_move_items` re-sequences the chain after apply using the sorted
`zone_policies` keys. That is why keys are `NNN-from-to`.

110 of the 140 VyOS rulesets contain no rules at all: the pair was declared but
never given an accept, so it was pure default-drop. Those produce no chain. The
absence of an accept plus the terminal drop already expresses the same policy.

### 2. VLANs move onto a VLAN-filtering bridge

VyOS terminated VLANs directly on `eth1` with no L2 bridge. The RB5009 is a
router *and* an 8-port switch, so VLANs go on a bridge to let the switch chip
hardware-offload the trunk.

Consequences worth knowing:

- Every VLAN needs a `routeros_interface_bridge_vlan` tagged entry as well as an
  `interface_vlan`. Missing the tagged entry is the classic "everything died the
  moment I enabled VLAN filtering" failure.
- Bridge `pvid` is pinned to 4094 (unused). Nothing on the Aruba trunk is
  untagged, and leaving pvid at the default 1 would dump untagged frames into
  VLAN 1.
- `ether1` is WAN and stays out of the bridge.

This is a genuine improvement over the VyOS router-on-a-stick topology, since
inter-VLAN traffic no longer hairpins through a single physical link.

### 3. The DNS container stack is replaced, not ported

VyOS ran three DNS containers on the router:

| Container | Address | Role |
| --- | --- | --- |
| `dnsdist` | `10.10.53.4` | the resolver every DHCP scope advertised; split queries into pools by client subnet |
| `blocky` | `10.10.53.7` | ad/tracker blocklists + split-horizon `customDNS` |
| `bind` | `10.10.53.3` | authoritative for `tnwks.local` and the `unifi` zone |

This stack was a significant contributor to VyOS sitting at roughly 187% CPU.
The RB5009 has 1GB RAM, 1GB NAND, no M.2, and needs external USB storage for
container layers. Rebuilding a three-container DNS chain on it would be strictly
worse than what it replaced.

Native RouterOS takes over:

| Was | Now |
| --- | --- |
| dnsdist upstream pools | `ip_dns.servers` (DoH/DoT), `ip_dns_forwarders` where per-subnet answers are genuinely needed |
| blocky `blackLists` | `ip_dns_adlist` |
| blocky `customDNS` + bind zones | `ip_dns_record` static entries, `match_subdomain` for the wildcards |

Two knock-on effects:

- The resolver address advertised by DHCP changes from `10.10.53.4` to each
  VLAN's SVI, so the forced-DNS dstnat rules retarget to the SVI too.
- Client DNS stops being *forwarded* traffic and becomes traffic *to the router*.
  Every VyOS `<zone> -> containers` `accept_dns` rule therefore moves from the
  forward chain to the **input** chain. The generator does this automatically and
  records it in the locals.tf footer.

Deliberately not ported: dnsdist's `zip` `DropAction` and its per-subnet ControlD
DoH pools. If those still matter they come back as `ip_dns_forwarders` plus a
mangle rule, not as a container.

### 4. An explicit input chain, which VyOS never had

VyOS had no local zone, so traffic *to* the router was unfiltered. RouterOS
defconf is also accept-all. The port writes an explicit `input` chain: state
accepts, the ported client-DNS accepts, ICMP, DHCP, NTP, SSH and API/Winbox
restricted to the mgmt VLAN, WireGuard listeners, then a terminal drop.

This is an intentional hardening improvement, not a port. It is called out here
because it is the one place the RouterOS config is deliberately *stricter* than
the VyOS config it replaces, and because a mistake here locks you out of the
router.

## Container disposition

| Container | Disposition |
| --- | --- |
| `blocky`, `dnsdist`, `bind` | replaced by native RouterOS DNS (above) |
| `haproxy` ×3 (`haproxy.cfg`, `-frontend`, `-authenticated`) | move to Kubernetes ingress; firewall rules retarget from the `containers` zone to `k8s-120` |
| `unifi` | move to Kubernetes |
| `uisp` | already migrated to Kubernetes 2026-05-12 (`kubernetes/apps/production/uisp`) |
| `speedtest-exporter` | move to Kubernetes |
| `node-exporter` | replaced by RouterOS SNMP + snmp-exporter; Grafana dashboard needs swapping |
| `cloudflare-ddns` | RouterOS `ip_cloud` DDNS, or a `system_scheduler` script |

The `containers` zone itself does not survive, since its interface was the Podman
pod bridge on the router. 22 of the 45 VyOS accepts referenced it, so the
generator resolves each one rather than dropping it:

- DNS rules move to the input chain.
- haproxy / unifi-controller rules retarget to `k8s-120`, with container address
  groups remapped (`haproxy_frontend` → `k8s_ingress`, `haproxy_all` →
  `k8s_ingress_internal`, and so on).
- Rules where **both** ends end up inside `k8s-120` after the move are dropped
  from the router config, because intra-VLAN traffic is switched locally and
  never reaches the router's forward chain. Those belong in a Kubernetes
  NetworkPolicy. The generator flags each one instead of emitting dead config.

## Gaps with no native RouterOS equivalent

These need a decision, they are not solved by this port:

1. **`udp-broadcast-relay`** for Sonos/SSDP (1900) and mDNS (5353) on VLAN 910.
   RouterOS `mdns_repeat_ifaces` may partially cover mDNS. **There is no SSDP
   relay.** Either accept the loss, keep a relay elsewhere on the LAN, or run a
   container (which the hardware argues against).
2. **`haproxy-k8s-api`** L4 load balancer for the Kubernetes API. Needs a
   replacement target: k8s-side (kube-vip / MetalLB) is the natural answer.

## Known VyOS drift found while porting

Worth fixing in `vyos-config` regardless of the migration:

- **`app-720-containers` is an orphan.** The ruleset has an accept rule but no
  `firewall zone containers from app-720` binding, so it never applied. The
  generator records it and emits nothing.
- **`wg01` exists in git but not on the live router**, while the `vpn` zone still
  references it. The port provisions both `wg01` and `wg02` from git. Decide
  whether `wg01` should exist before cutover.
- **NTP dstnat for `transit-10` targets `172.16.1.254`**, which is not a VyOS
  address (it is a transit VIP). Ported verbatim; verify it is intentional.
- **`eth0` WAN DHCP is vestigial.** The default route goes via transit to the
  EdgeRouter, not via the cable modem. Ported with `add_default_route = "no"` so
  it cannot install a competing route.
- 🔴 **Exposed credential:** the `cloudflare-ddns` container env holds a
  plaintext `CF_API_TOKEN` in the VyOS repo. **Rotate it.** This adds to the
  network credential rotation debt outstanding since April.

## Hardware constraints

- RB5009: ARM64, 1GB RAM, 1GB NAND, no M.2, USB 3.0 only for external storage.
- Enabling the `container` package requires **physical access** (device-mode
  change needs a reset-button press or power cycle). The port does not use
  containers, so this is only relevant if a decision above reverses.
- IPv6 forwarding was disabled in the source config
  (`set system ipv6 disable-forwarding`); the port does not enable it.
- RouterOS caps `dst-port` at **15 entries per rule**. `ad_auth_ports` has 18, so
  the generator splits it across multiple rules and annotates the comments.
- VyOS ran `ethtool --set-eee <iface> eee off` on every interface from its
  post-config bootup script. RouterOS has no EEE toggle. If link flaps appear
  after cutover, this is the first suspect and it must be handled on the Aruba
  side.
- RouterOS remote logging is plain syslog. VyOS shipped octet-counted TCP to the
  Kubernetes Vector aggregator on `10.10.120.56:6001`, so the Vector source for
  this router must accept the RouterOS framing.

## Cutover runbook

1. **Bootstrap the router.** Paste
   `infrastructure/mikrotik/bootstrap/routeros-bootstrap.rsc` into a console on a
   factory-reset RB5009. Creates identity, the `terraform` service account, mgmt
   IP, REST API + certificate, disables insecure services, removes `admin`.
2. **Verify API reachability:**
   `curl -k -u terraform:<pw> https://10.98.0.1/rest/system/resource`
3. **Snapshot and verify:** `task mikrotik:bootstrap` (Ansible: asserts RouterOS
   7.x, exports a pre-Terraform config snapshot, checks `www-ssl`).
4. **Regenerate and review locals.tf:** `task mikrotik:generate`, then read the diff
   and the translation-notes footer.
5. **Plan:** `task mikrotik:init && task mikrotik:plan`. Confirm it does not
   propose destroying the bootstrap mgmt address out from under itself.
6. **Apply with physical access to the router.** An input-chain or bridge-VLAN
   mistake locks you out; you want console access when it happens, not after.
7. **Verify before swinging the trunk:** per-VLAN ping from the router, DHCP lease
   on a test client, DNS resolution through the router, one known-good firewall
   accept, and one known-good firewall *denial*.
8. **Swing the Aruba trunk** from the VyOS box to the RB5009. Keep VyOS powered
   off but unmodified as the rollback path.
9. **Post-cutover:** remove the bootstrap mgmt address, migrate the remaining
   container workloads to Kubernetes, swap the node-exporter Grafana dashboard
   for SNMP, and rotate the Cloudflare token.

## Deliberately not ported

Recorded in the module's `unported_vyos_subsystems` output as well:

- All 9 Podman containers (see disposition table).
- `udp-broadcast-relay` (Sonos 1900, mDNS 5353).
- Conntrack helper modules (ftp, h323, nfs, pptp, sip, sqlnet, tftp). RouterOS
  enables a smaller default set; add back only what is needed.
- `commit-revisions` config management. RouterOS has its own backup/rollback.
- `kernel.pty.max` sysctl and reboot-on-panic. No RouterOS equivalent.
