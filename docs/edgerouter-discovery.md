# EdgeRouter Lite discovery (sce-er01)

Live discovery of the internet edge router, run 2026-09-19 against `172.16.1.1`.
This is the second half of the consolidation: the RB5009 replaces **both**
`sce-vyos01` (internal L3/firewall) and `sce-er01` (internet edge).

Read alongside `mikrotik-vyos-port.md`, which covers the VyOS side and the
cutover. This document is the evidence for what the port takes over from the ERL
and what it deliberately leaves behind.

## Device

| | |
| --- | --- |
| Hostname | `sce-er01` (DNS name) |
| Model | `UBNT_E100` (EdgeRouter Lite, 3-port, MIPS64) |
| Firmware | EdgeOS 2.x |
| RAM | 480MB total, 107MB used |
| Uptime | 15 days |
| Load | 0.09 |
| Conntrack | 300 active of 32768 |

Config is 180 `set` lines. Discovery artifacts in `/tmp/erl-discovery/`.

## Topology as actually wired

```
ISP ──▶ eth0  <wan-ip>/20  (DHCP, default via <isp-gateway>)
        │
        │ masquerade NAT (service nat rule 5000, outbound eth0)
        │
        └─▶ eth2  172.16.1.1/24  "LAN2"  ── transit ──▶ 172.16.1.250  sce-vyos01
                                                        172.16.1.252  DEAD
                                                        172.16.1.254  DEAD

            eth1  10.98.0.1/24  "LAN1"  ── DISABLED, 0 bytes ever
```

`eth0` has moved 560GB in / 170GB out. `eth2` mirrors it. `eth1` has never passed
a single packet.

## The ERL owns two things VyOS does not

This is the whole reason the port has to change:

1. **The default route.** `default via <isp-gateway> dev eth0`, learned by DHCP.
   VyOS points `0.0.0.0/0` at `172.16.1.1`.
2. **Source NAT.** `service nat rule 5000 type masquerade outbound-interface eth0`.
   VyOS had its masquerade rule commented out precisely because this box does it.

Everything else on the ERL is either dead, duplicated on VyOS, or a security
problem.

## What the replacement must not inherit

The ERL is being retired, not hardened, so these are requirements on the RB5009
rather than fixes to the ERL:

- An explicit input chain ending in a drop. From the WAN, only WireGuard.
- DNS, NTP and DHCP answered on internal VLANs only.
- No routing-protocol or vendor-discovery listeners.
- Reverse-path filtering on, ICMP redirects off.
- No credentials carried over from the ERL config; the DDNS password is rotated.
- No default SNMP community names.

## Dead configuration

Roughly a third of the ERL config refers to things that no longer exist. None of
it should be ported.

### AWS site-to-site VPN: dead

- `protocols bgp 65000`, neighbors `169.254.28.13` and `169.254.225.73`
  (AS 64512 = AWS VGW).
- **`show ip bgp summary`: both `State/PfxRcd = Connect`, `MsgRcv 0`, `MsgSen 0`,
  `Up/Down never`. Zero established sessions.**
- `interfaces vti vti0` / `vti1` are both `disable`, and `ip link show` reports
  **"Device does not exist"**.
- `set vpn` is empty; `ipsec status` returns nothing.
- `firewall options mss-clamp interface-type vti mss 1379` exists only for those
  dead tunnels.
- `policy prefix-list BGP` denies `<old-wan-ip>/32` as "localgw", but the current
  WAN address is `<wan-ip>` — the prefix-list is stale on top of dead.

The tunnel advertised `0.0.0.0/0` and `10.10.0.0/16` out and expected
`10.20.0.0/22` back. If AWS connectivity is wanted again it should be rebuilt
deliberately, not transplanted.

### 172.16.1.252: dead next-hop for 13 routes

`ip neigh` = `FAILED`, ping = 100% loss. Still referenced by:

`10.10.12.0/24`, `10.10.13.0/24`, `10.10.80.0/24`, `10.10.92.0/24`,
`10.10.93.0/24`, `10.10.160.0/24`, `10.10.200.0/24`, `10.60.10.0/24`,
`10.97.0.0/24`, `10.98.0.0/24`, `192.168.1.0/24`

Note `10.60.10.0/24` is the airMAX subnet, which VyOS correctly routes via
`10.10.140.140` instead. The ERL entry is simply wrong.

### 172.16.1.254: dead, and it takes live config with it

`ip neigh` = `FAILED`, ping = 100% loss. Referenced by:

- `port-forward rule 1` — an `isitup` host, tcp 8888
- `port-forward rule 2` — **Helium Miner**, tcp 44158
- `traffic-policy shaper client-up-s` classes 2 and 3, both matching
  `source address 172.16.1.254/32`, plus `eth2 redirect ifb1`

VyOS's `transit-10` NTP dstnat also targets `.254`, so NTP for that zone is
black-holed today. Not ported.

VyOS also routes `10.98.0.0/24` and `10.10.93.0/24` via `172.16.1.254`. For
`10.98.0.0/24` that static is shadowed by VyOS's connected `eth1` address, so
switch management still works; `10.10.93.0/24` goes nowhere.

### eth1: disabled, never used

`10.98.0.1/24`, `disable`, 0 bytes RX and TX. The same address is live on
VyOS's bare `eth1` as switch management.

## What must be ported

| ERL config | RouterOS equivalent |
| --- | --- |
| `eth0 address dhcp` + default route | `ip_dhcp_client` on `ether1` with `add_default_route = "yes"` |
| `service nat rule 5000` masquerade | `ip_firewall_nat` srcnat masquerade, `out_interface = ether1` |
| `port-forward rule 3/4` (wg01/wg02 → .250) | **disappears** — the RB5009 terminates WireGuard itself |
| `port-forward hairpin-nat enable` | RouterOS needs an explicit hairpin srcnat rule if any dstnat survives |
| `service dns forwarding` (cache 150) | already covered by native `ip_dns` from the VyOS port |
| `system ntp server *.ubnt.pool.ntp.org` | merge with VyOS `us.pool.ntp.org`, **LAN-only, never WAN** |
| `service ssh listen-address 172.16.1.1` | `ip_service` ssh + input-chain accept from mgmt only |
| `service snmp` | mktxp over the read-only API instead |
| `system conntrack` tuning | `ip_firewall_connection_tracking` |
| `system offload` | RouterOS fasttrack, already in the port |
| `service dns dynamic` (namecheap) | dropped: consolidated on Cloudflare, see below |
| `system syslog` | already ported from VyOS |
| `system time-zone`, `host-name` | already ported |

### DDNS is duplicated across two providers and two domains

- ERL: Namecheap DDNS for the WireGuard endpoint hostname.
- VyOS: a `cloudflare-ddns` container for `tnwks.us`.

The WireGuard clients use the Cloudflare record as their endpoint, so the RB5009
keeps that one current with a scheduler script (`modules/mikrotik/ddns.tf`). The
Namecheap DDNS is not carried over.

### Not portable

- `system traffic-analysis dpi enable` / `export enable` — DPI feeding UISP.
  RouterOS has no DPI engine. No equivalent.
- `traffic-policy shaper client-up-s` — target is dead, so there is nothing to
  shape. If upload shaping is wanted, RouterOS `queue_tree` / CAKE is the tool,
  but it should be configured against a live host.
- `service unms connection` — UISP device adoption is Ubiquiti-specific. The
  RB5009 will not appear in UISP.

## Consolidation effects

### The transit VLAN loses its purpose

`172.16.1.0/24` exists only to carry ERL ↔ VyOS, plus two dead hosts. With one
router, there is no transit hop. But it cannot simply be deleted:

- bind has `sce-er01 A 172.16.1.1` and `sce-vyos01 A 172.16.1.250`.
- VyOS `transit-10` is a real firewall zone with rules
  (`seccam-610-transit-10`, `transit-10-containers`, `transit-10-k8s-120`,
  `transit-10-unifi-mgmt-900`).

Recommendation: keep `transit-10` as a VLAN so those zone rules and DNS records
stay meaningful, and let the RB5009 hold `172.16.1.1`. Retire `.250`, `.252`,
`.254` and the routes pointing at them.

### Static routes mostly evaporate

25 of the ERL's 28 routes are `10.10.x.0/24 via 172.16.1.250`, which is "send it
to VyOS." When the same box owns those SVIs they become **connected routes**. Of
the rest: 11 point at dead `.252`, and `10.60.10.0/24` is already handled
correctly by VyOS via `10.10.140.140`.

Net: the consolidated router needs roughly **one** static route
(`10.60.10.0/24 → 10.10.140.140`) instead of 29 across two boxes.

### The WAN input chain becomes load-bearing

With the ERL folded in, the RB5009's input chain is the only filter between the
internet and the router, so it is load-bearing rather than optional.

Requirements it must meet:

- DNS (`53`) and NTP (`123`) accepted from LAN interface-lists **only**, never
  from `ether1`.
- No BGP listener, no UBNT discovery ports.
- WireGuard `51820`/`51821` accepted on `ether1` (this is the one WAN-facing
  service, and it replaces ERL port-forward rules 3 and 4).
- Established/related and ICMP as already configured.
- Reverse-path filtering on.

## Reproducing this discovery

EdgeOS 2.0.9 predates modern OpenSSH defaults, so legacy overrides are
mandatory:

```bash
sshpass -p "$ERL_PASS" ssh \
  -o KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 \
  -o HostKeyAlgorithms=+ssh-rsa,ssh-dss \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  -o Ciphers=+aes128-cbc,aes256-cbc,3des-cbc \
  "$ERL_USER"@172.16.1.1 \
  "/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands"
```

Operational commands need the `/opt/vyatta/bin/vyatta-op-cmd-wrapper` prefix
over SSH, and `arp` does not exist (use `ip neigh`).
