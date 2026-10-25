# VyOS to MikroTik RouterOS port

Collapsing **two** routers into one MikroTik RB5009UG+S+IN on RouterOS 7:

- `sce-vyos01` — VyOS 1.4-rolling on an Intel N5105 box. Internal L3, VLANs,
  zone firewall, DHCP, DNS containers, WireGuard.
- `THOMAS-ER01` — EdgeRouter Lite. Internet edge: WAN DHCP, the default route,
  and source NAT.

The ERL half is documented separately in
[`edgerouter-discovery.md`](edgerouter-discovery.md), including the security
findings that set the migration timeline. Read that before cutover; several of
its findings change what this document ports.

- **Source of truth:** [`iT3E/vyos-config`](https://github.com/iT3E/vyos-config)
  `config-parts/*.sh`, HEAD `34b952a` (2026-07-13). Chosen over a live
  `show configuration commands` dump because the repo also carries the container
  configs and bootstrap scripts, which the live dump does not express.
- **Terraform:** `infrastructure/terraform/modules/mikrotik` (child) and
  `infrastructure/terraform/environments/prod/mikrotik` (root).
- **Bootstrap:** `infrastructure/mikrotik/bootstrap/`.
- **Generator:** `infrastructure/mikrotik/tools/vyos-to-locals.py`.

## Why a generator instead of hand-written HCL

The VyOS firewall is 140 rulesets across 14 zones. Hand-transcribing that is how
you ship a subtly wrong firewall. `task mikrotik:generate` regenerates
`locals.tf` from the VyOS repo, so the port is reproducible and can be
re-run if the VyOS config changes before cutover.

The generated file is committed. Review its diff, do not edit it.

## Translation summary

Counts are in the header of the generated `locals.tf`; they change whenever the
VyOS source or the retirement lists in the generator do.

| Concern | VyOS | RouterOS |
| --- | --- | --- |
| VLANs | `eth1` + `vif` subinterfaces | VLAN-filtering bridge + `interface_vlan` |
| Switch management | bare `eth1` (untagged) | `native_vlan` on the bridge, uplink only |
| Addressing | `vif <n> address` | `ip_address` per SVI |
| DHCP | `shared-network-name` | `ip_pool` + `ip_dhcp_server` + `_network` |
| Zones | per-pair rulesets | `interface_list` + forward-chain segments |
| Address / port groups | `firewall group` | `ip_firewall_addr_list` / `dst_port` strings |
| NAT | `nat destination` | `ip_firewall_nat` dstnat (tcp_udp split) |
| Routing | `protocols static route` | `ip_route` |
| WireGuard | `wg01`, `wg02` | `interface_wireguard` + peers |
| DNS | 3 Podman containers | native RouterOS DNS |
| Containers | 9 Podman containers | Kubernetes / native / dropped |

### Retired, not ported

| What | Effect |
| --- | --- |
| VLANs `ad-110`, `app-720`, `bastion-410`, `iLO-550`, `pve-11`, `k8s-120` | No SVI, DHCP, NAT, DNS records into the subnet, or zone pairs to/from them |
| `udmpro` address group | Named for a UDM Pro but pointing at the EdgeRouter; its rule into VLAN 900 is dropped |
| Blue Iris (`blue_iris`) | Every rule naming it dropped; the camera VLAN initiates nothing beyond the router |
| haproxy (3 containers) | Its rules and `*.tnwks.local` names dropped |

Address and port lists that no remaining rule references are not emitted.

## What consolidation changes

Folding the ERL in is not additive, it deletes work:

| | Before (two boxes) | After (RB5009) |
| --- | --- | --- |
| Default route | ERL learns from cable modem, VyOS statics `0.0.0.0/0` at it | WAN DHCP lease, `add_default_route = "yes"` |
| Source NAT | ERL `service nat rule 5000` masquerade on `eth0` | `masquerade_out_interface = "ether1"` |
| Static routes | 25 on the ERL + 4 on VyOS = 29 | **1** (`10.60.10.0/24 → 10.10.140.140`) |
| WireGuard | ERL port-forwards 51820/51821 to VyOS | terminated on the router, input-chain accept on `ether1` |
| Transit VLAN | real hop, ERL ↔ VyOS | vestigial; kept only for its zone rules and DNS records |
| Input filtering | **none on either box** | explicit input chain with terminal drop |

25 of the ERL's routes were "send it to VyOS." When one box owns those SVIs they
become connected routes and simply disappear from config.

### Dead config found on the ERL, not ported

Evidence in `edgerouter-discovery.md`.

- **`172.16.1.252` and `172.16.1.254` are both dead** (`ip neigh` FAILED, 100%
  ping loss) yet 13 and 3 routes respectively still point at them, across both
  routers. The generator drops any route whose next-hop is in `DEAD_NEXT_HOPS`
  and records each one.
- **The AWS site-to-site VPN is dead.** BGP to `169.254.28.13` /
  `169.254.225.73` has `MsgRcv 0`, `Up/Down never`, zero established sessions;
  both `vti` interfaces are `disable` and `ip link` reports they do not exist.
  Not ported. Rebuild deliberately if AWS connectivity is wanted.
- **`traffic-policy shaper client-up-s`** shapes `172.16.1.254/32`, which is
  dead. Nothing to shape.
- **ERL `eth1` (`10.98.0.1/24`) is disabled with 0 bytes ever.** The same
  address is live on VyOS's bare `eth1` as switch management, which the RB5009
  takes over (`native_vlan`).

### The input chain stops being optional

With the ERL folded in, the input chain is the only thing between the internet
and the router, and it is what closes the ERL's two worst findings: DNS and NTP
were bound to `0.0.0.0`, making the box a **public open resolver and an open NTP
reflector** usable for amplification attacks against third parties.

So the module gained an aggregate `zone-lan` interface list. Router services
match on it and therefore cannot be answered from the WAN:

| Service | Scope |
| --- | --- |
| DNS 53 | originating zone list only |
| NTP 123, DHCP 67/68 | `zone-lan` |
| SSH 22, REST/Winbox 443/8291 | the admin VPN (`wg01`) and out-of-band `ether8` only |
| RouterOS API-SSL 8729 | the `monitoring` list (WSL host), for mktxp; read-only `monitoring` group |
| WireGuard 51820/51821 | `zone-transit-10` (ERL port-forward in stage A, `ether1` in stage B) and `zone-lan` (joining the admin VPN from home) |

People reach sensitive systems (router, switch, UniFi APs, UISP radios) only
over `wg01`. The full access model and the break-glass procedures are in
[`network-access.md`](network-access.md).
| everything else | terminal drop |

## The four translations that are not one-to-one

### 1. Zones become interface-lists plus explicit per-pair drops

VyOS gave every directed zone pair its own ruleset with its own
`default-action drop`. RouterOS has one flat ordered `forward` chain.

The port creates a `routeros_interface_list` per zone, emits each pair's accept
rules matching `in_interface_list` / `out_interface_list`, then emits **that
pair's own terminating default**: a drop, or an accept for the 21 pairs VyOS
defaulted open (every `X -> transit-10` internet-egress pair and every `vpn -> X`
pair). After all pairs comes a **terminal forward drop**.

That trailing per-pair default is load-bearing. Without it, a packet that matches no
accept in its own pair falls through into a *later* pair's accept rules and the
policy silently loosens. This is the single most dangerous place to "simplify"
this config.

Rule order is therefore semantic, and Terraform does not guarantee resource
creation order. `ip_firewall_filter` has no `place_before`, so
`routeros_move_items` re-sequences the chain after apply using the sorted
`zone_policies` keys. That is why keys are `NNN-from-to`.

Most VyOS rulesets contain no rules at all. The empty default-drop ones produce
no chain: the terminal forward drop expresses the same policy, and also covers
every zone pair VyOS never bound (VyOS zones default to drop). The empty
default-*accept* ones still produce a pair with no rules and an accept default,
because they are what let each VLAN reach the internet.

The terminal forward drop is the second load-bearing rule. RouterOS `forward`
defaults to accept, so without it every unbound pair, and WAN to LAN, is open.

**Internet egress keeps VyOS's semantics.** VyOS reached the internet through
`transit-10` (the ERL hop), so its `X -> transit-10` rulesets are the
internet-egress policy: cameras (`seccam-610`) get no internet access at all. `ether1` joins `zone-transit-10`
(`outside_zone`), so those same rules apply before and after the modem moves.

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
  VLAN's SVI, and the forced-DNS dstnat rules (910, 610) become `redirect` to
  the router instead of a dst-nat to the dnsdist container.
- Blocking and split-horizon now apply to **every** VLAN. dnsdist only sent
  `10.10.91.0/24` and `10.10.31.0/24` through blocky; RouterOS adlists are
  global. The lists are malware/phishing/ad lists, so the wider scope is mostly
  a gain, but a false positive now hits every VLAN.

What the move to RouterOS DNS keeps and loses, relative to what the
containers actually did:

| Feature | Today | RouterOS |
| --- | --- | --- |
| Ad/malware blocking (45 lists) | blocky, 910 + `wg02` only | `ip_dns_adlist`, every VLAN |
| Allowlists (2 anudeep lists) | blocky `whiteLists` | **lost**: no allowlist URLs; unblock single names with static `FWD` records |
| `*.internal.tnwks.us` and other overrides | blocky `customDNS` | static records, `match_subdomain` for wildcards |
| `tnwks.local`, `unifi` zones | bind (authoritative) | static records (no SOA/NS, not authoritative) |
| `.zip` dropped | dnsdist `DropAction` | static `NXDOMAIN` record for `zip` and everything under it |
| Encrypted upstream | DoT to Cloudflare | DoH to Cloudflare |
| Query and block metrics | blocky Prometheus endpoint, dnsdist query log | **lost**: RouterOS exposes cache stats, not per-query or per-block metrics |
| ControlD pools | defined in dnsdist, no rule routes to them | nothing to port |

RAM is the constraint to watch: adlists live in memory on a 1 GB router. Check
`/ip dns adlist print` and `/system resource print` after the bench apply, and
trim to fewer, larger curated lists if usage is high.
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

## Camera VLAN policy

`seccam-610` is untrusted:

- Cameras can reach only the router's DHCP, DNS, NTP and ICMP. No internet, no
  management access to the router, nothing on any other VLAN.
- Nothing on 910 reaches a camera except Frigate's RTSP pulls (tcp/udp 554 from
  the `frigate` address list to `security_cameras`). Everything else on 910
  talks to Frigate, not to cameras.
- Frigate runs on the WSL cluster, so to the router its traffic comes from the
  Windows host (`10.10.91.142`). Anything else on that host can therefore also
  pull RTSP; tightening that needs Frigate on its own address.
- The three Wyze cameras live on 910 itself (`wyze_cameras`), so they are not
  segregated by this policy.

## Container disposition

| Container | Disposition |
| --- | --- |
| `blocky`, `dnsdist`, `bind` | replaced by native RouterOS DNS (above) |
| `haproxy` ×3 (`haproxy.cfg`, `-frontend`, `-authenticated`) | **retired, not replaced.** Every backend (the old `10.10.120.x` cluster, its ingress `.52`) is gone; the WSL cluster serves those apps on `*.internal.tnwks.us` via its own ingress. `haproxy-k8s-api` fronted that old cluster's API; WSL Talos has no LB in front of its API. All haproxy rules and `*.tnwks.local` haproxy names are dropped |
| `unifi` | **moves to Kubernetes on WSL**, reached through the Windows host (`10.10.91.142`). The `unifi` DNS record (used by unpoller) and the AP-inform / controller-to-AP rules point there. Needs LAN-bridge relays for tcp/8080 (inform), udp/3478 (STUN) and tcp/8443 (UI). Budget ~1–2 GB RAM (Java + MongoDB) |
| `uisp` | already migrated to Kubernetes 2026-05-12 (`kubernetes/apps/production/uisp`) |
| `speedtest-exporter` | move to Kubernetes |
| `node-exporter` | replaced by RouterOS SNMP + snmp-exporter; Grafana dashboard needs swapping |
| `cloudflare-ddns` | RouterOS `ip_cloud` DDNS, or a `system_scheduler` script |

The `containers` zone itself does not survive, since its interface was the Podman
pod bridge on the router. 22 of the 45 VyOS accepts referenced it, so the
generator resolves each one rather than dropping it:

- DNS rules move to the input chain.
- Client -> UniFi controller rules retarget to `unifi-frontend-910` with the
  destination scoped to the `unifi_controller` list (`10.10.91.142`).
- Client -> haproxy rules are dropped with haproxy.
- Controller -> AP rules (`containers -> unifi-mgmt-900`) keep the source scoped
  to `unifi_controller`, not the whole VLAN.
- haproxy -> backend rules (`containers -> k8s-120`, `containers -> seccam-610`)
  and the haproxy stats rule are dropped with haproxy.
- Rules whose source is already in `unifi-frontend-910` are dropped as router
  rules: that traffic is switched locally and never reaches the forward chain.

The generator records every one of these in the locals.tf footer. The
`*.tnwks.local` names that pointed at haproxy (`hass`, `jellyfin`,
`obsidian-couchdb`, `blueiris`) are dropped. Blue Iris itself is retired: its
address group and every rule naming it are dropped, so the camera VLAN
initiates nothing beyond the router's DHCP/DNS/NTP.

## Gaps with no native RouterOS equivalent

These need a decision, they are not solved by this port:

1. **`udp-broadcast-relay`** for Sonos/SSDP (1900) and mDNS (5353). Both
   containers were configured with a **single** interface (`CFG_DEV=eth1.910`),
   and a relay with one interface has nowhere to relay to, so they did nothing.
   Not ported, nothing lost. If cross-VLAN discovery is ever wanted, RouterOS
   `mdns_repeat_ifaces` covers mDNS natively; SSDP would need a relay host.
2. **Router metrics and DHCP client names: mktxp.** vyos-config `a7fb814` added
   a task-scheduler job exporting `dhcp_lease_info` via node-exporter, which
   feeds flow-collector's `wan:client_name`. RouterOS has no equivalent, so the
   module creates a read-only `mktxp` API account (api-ssl 8729, LAN only).
   Still to do on the cluster: deploy mktxp, and relabel its DHCP lease metric
   to `dhcp_lease_info{ip,hostname}` (or point `wan:client_name` at it). Until
   then WAN alerts fall back to bare IPs.
3. **Syslog target.** VyOS shipped to `10.10.120.56:6001`, a Vector instance on
   the old cluster that no longer answers. The router now sends to the WSL
   host's LAN bridge, `10.10.91.142:6001` (tcp). Still to do on the cluster:
   vector-aggregator is MS-01-only today, so it needs enabling on WSL plus a
   tcp/6001 `wsl_lan_bridge` instance. Its `vyos_syslog` parsing expects VyOS
   kernel-log firewall lines and will need a RouterOS variant.

## Known VyOS drift found while porting

Worth fixing in `vyos-config` regardless of the migration:

- **`wg01` exists in git but not on the live router**, while the `vpn` zone still
  references it. The port provisions it from git as the admin VPN, so its peers
  need working client configs before cutover.
- **`910 -> 10.10.140.140:443` (the EP-R6 UI) is open live** but has no rule in
  git. In the port it is reachable over `wg01` (`vpn -> UISP-140` defaults to
  accept) and not from 910.
- **NTP dstnat for `transit-10` targets `172.16.1.254`.** `.254` is dead, so the
  rule black-holes NTP for
  that zone today. Not ported; the generator records it.
- **`eth0` WAN DHCP is no longer vestigial.** With the ERL gone this is the only
  internet uplink, so `add_default_route = "yes"`. The VyOS
  `0.0.0.0/0 → 172.16.1.1` static becomes `upstream_gateway` at distance 10, so
  the router can sit behind the ERL first (stage A) and the WAN lease's
  distance-1 default wins as soon as the modem is on `ether1` (stage B).
- **Both routers route `10.98.0.0/24` and `10.10.93.0/24` at dead
  `172.16.1.254`.** Dropped. `10.98.0.0/24` is reachable anyway because live
  VyOS has `10.98.0.1/24` on bare `eth1` (the trunk's untagged native VLAN),
  which `vyos-config` lacks. That is where the Aruba's management address
  (`10.98.0.251`) lives, and it is in no VyOS zone, so every VLAN can reach it.
  The port routes it as zone `switch-mgmt` (`native_vlan`), untagged on the
  uplink only, reachable from 910, 900 and `wg01`.

- **The live router has drifted from git.** Live VyOS has
  `unifi-frontend-910-seccam-610 rule 1` ("Allow all from 910 to cameras"),
  which `vyos-config` does not. That rule is what lets Frigate reach the cameras
  today; the port replaces it with the Frigate-only rule above. Before cutover,
  diff `show configuration commands` against `config-parts/*.sh` for any other
  drift, since the generator only sees git.

## Hardware constraints

- RB5009: ARM64, 1GB RAM, 1GB NAND, no M.2, USB 3.0 only for external storage.
- Enabling the `container` package requires **physical access** (device-mode
  change needs a reset-button press or power cycle). The port does not use
  containers, so this is only relevant if a decision above reverses.
- IPv6 forwarding was disabled in the source config
  (`set system ipv6 disable-forwarding`); the port does not enable it.
- RouterOS caps `dst-port` at **15 entries per rule**. The generator splits any
  longer port list across multiple rules and annotates the comments.
- VyOS ran `ethtool --set-eee <iface> eee off` on every interface from its
  post-config bootup script. RouterOS has no EEE toggle. If link flaps appear
  after cutover, this is the first suspect and it must be handled on the Aruba
  side.
- RouterOS remote logging is plain syslog. VyOS shipped octet-counted TCP to the
  Kubernetes Vector aggregator on `10.10.120.56:6001`, so the Vector source for
  this router must accept the RouterOS framing.

## Cutover runbook

Staged so that each step changes one thing and has a one-cable rollback. The
stages can run back to back in one session; nothing here needs a soak period.
The RB5009 reuses VyOS's exact gateway addresses and DHCP scopes, so it can never
share the trunk with VyOS: stage A is a swap, not a parallel run.

### Before the window

- `wg01` client configs working (the admin VPN is the only management path
  after cutover; see `network-access.md`), and a copy of the SOPS age key and
  admin password stored off the desktop for break-glass.
- Live VyOS config diffed against `vyos-config` (see "Known VyOS drift"), and
  anything real committed to `vyos-config` and regenerated.
- `secrets.sops.yaml` created and encrypted (see `secrets.sops.yaml.example`;
  `admin_password` and `ddns_credential` are required).
- UniFi controller running on the WSL cluster with its relays, or accept that
  APs run unmanaged for a while (they keep passing traffic).
- Namecheap DDNS password rotated.

### Stage 0: bench (router not connected to anything but the laptop)

1. **Reset and bootstrap.** `/system reset-configuration no-defaults=yes
   skip-backup=yes`, then paste
   `infrastructure/mikrotik/bootstrap/routeros-bootstrap.rsc` over serial or
   Winbox-by-MAC on `ether8`. Laptop on `ether8` at `192.168.88.2/24`.
2. **Verify API reachability:**
   `curl -k -u terraform:<pw> https://192.168.88.1/rest/system/resource`
3. **Snapshot and verify:** `task mikrotik:bootstrap` (Ansible: asserts RouterOS
   7.x, exports a pre-Terraform config snapshot, checks `www-ssl`).
4. **Plan:** `task mikrotik:init && task mikrotik:plan`. `ether8` must not appear
   as a bridge port, and nothing may touch `192.168.88.1/24`.
5. **Apply**, then **plan again**: the second plan must be empty. Anything it
   wants to change is drift between the module and what RouterOS stored, and is
   cheaper to find now.
6. **Enable flow export once:** `/ip traffic-flow set enabled=yes` (provider
   v1.99.1 cannot set `enabled`).
7. **Check the chain order on the box:** `/ip firewall filter print` shows input
   rules ending in `zzz input: default drop` and forward rules ending in
   `zzz forward: default drop`.

### Stage A: replace VyOS, ERL stays the edge

The RB5009 takes VyOS's addresses, including `172.16.1.250` on transit-10, so
the ERL's routes, NAT, and WireGuard port-forwards keep working untouched. The
default route is `upstream_gateway` (ERL, distance 10). Internet egress goes out
transit-10, where VyOS's own `X -> transit-10` rules apply.

8. **Swing the Aruba trunk** from VyOS to the RB5009 trunk port. Power VyOS off,
   unmodified.
9. **Expect a few minutes of stale ARP** on clients and the ERL for the gateway
   addresses (same IPs, new MAC). `clear arp` on the ERL speeds it up.
10. **Verify:** DHCP lease on a test client in 910 and 900, DNS through the
    router (`dig @10.10.91.1 example.com`, and a `*.internal.tnwks.us` name),
    internet from a client, one known-good accept (e.g. `wg01` -> router
    REST), one known-good denial (e.g. 910 -> router REST, or a camera in 610
    reaching the internet), and a WireGuard client connecting through the ERL
    port-forward.
11. **Rollback:** trunk back to VyOS, power it on.

### Stage B: move the WAN

12. **Modem into `ether1`.** The WAN lease installs a distance-1 default, which
    beats `upstream_gateway` without a Terraform change. Masquerade on `ether1`
    starts doing work, and the DDNS script starts updating Namecheap from the
    lease within 5 minutes (`/log print where message~"ddns"`).
13. **Verify** internet and NAT from a client, WireGuard from outside (now direct,
    not port-forwarded), and that the router is not an open resolver or NTP
    server. From outside the network, against the WAN address:

    ```bash
    dig @<wan-ip> example.com          # must time out
    ntpdate -q <wan-ip>                 # must fail
    nmap -sU -p 123,53,10001 <wan-ip>   # must not be open
    ```

    This is the single most important post-cutover check. The ERL failed all
    three.
14. **Flow export:** `wan_client_receive_bytes_total` grows in Prometheus and
    FlowCollectorNoFlows clears. FastTrack stays off (`var.fasttrack = false`)
    because FastTracked packets bypass traffic-flow; run a speed test and watch
    `/system resource monitor`, and only enable it if the CPU cannot keep up.
15. **Rollback:** modem back into the ERL. Stage A state is restored.

### Stage C: retire

16. Power the ERL off, unmodified, as a second rollback path.
17. Once stable: set `upstream_gateway = null` in the generator, regenerate,
    apply; replace the `sce-er01` / `sce-vyos01` DNS records with a `sce-rtr01`
    record (`10.10.91.1`); swap the node-exporter Grafana dashboard for mktxp;
    bring up Vector on WSL with its tcp/6001 relay so router syslog lands;
    point flow-collector's client names at mktxp's DHCP lease metric.

## Deliberately not ported

Recorded in the module's `unported_vyos_subsystems` output as well:

- All 9 Podman containers (see disposition table).
- `udp-broadcast-relay` (Sonos 1900, mDNS 5353).
- Conntrack helper modules (ftp, h323, nfs, pptp, sip, sqlnet, tftp). RouterOS
  enables a smaller default set; add back only what is needed.
- `commit-revisions` config management. RouterOS has its own backup/rollback.
- `kernel.pty.max` sysctl and reboot-on-panic. No RouterOS equivalent.
