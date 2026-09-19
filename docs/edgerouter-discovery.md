# EdgeRouter Lite discovery (THOMAS-ER01)

Live discovery of the internet edge router, run 2026-09-19 against `172.16.1.1`.
This is the second half of the consolidation: the RB5009 replaces **both**
`sce-vyos01` (internal L3/firewall) and `THOMAS-ER01` (internet edge).

Read alongside `mikrotik-vyos-port.md`. That document ports the VyOS side and
assumes the ERL keeps doing NAT and holding the default route. **That assumption
is now wrong**, and the deltas are listed at the bottom.

## Device

| | |
| --- | --- |
| Hostname | `THOMAS-ER01` |
| Model | `UBNT_E100` (EdgeRouter Lite, 3-port, MIPS64) |
| Firmware | `v2.0.9-hotfix.2` built **2021-05-11** (over 5 years old) |
| Kernel | `4.9.79-UBNT` |
| RAM | 480MB total, 107MB used |
| Uptime | 15 days |
| Load | 0.09 |
| Conntrack | 300 active of 32768 |

Config is 180 `set` lines. Discovery artifacts in `/tmp/erl-discovery/`.

## Topology as actually wired

```
ISP ──▶ eth0  35.135.56.103/20  (DHCP, default via 35.135.48.1)
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

1. **The default route.** `default via 35.135.48.1 dev eth0`, learned by DHCP.
   VyOS points `0.0.0.0/0` at `172.16.1.1`.
2. **Source NAT.** `service nat rule 5000 type masquerade outbound-interface eth0`.
   VyOS had its masquerade rule commented out precisely because this box does it.

Everything else on the ERL is either dead, duplicated on VyOS, or a security
problem.

## 🔴 Security findings

These are live, on the box carrying all internet traffic, right now.

### 1. There is no input firewall at all

```
Chain INPUT (policy ACCEPT)     <- zero rules
Chain FORWARD (policy ACCEPT)
Chain anyany                    <- 0 references
```

`firewall name anyany default-action accept` is defined and attached to nothing.
No `firewall in`/`local` applied to `eth0`. The router accepts everything
addressed to itself from the internet, filtered only by what happens to be
listening.

### 2. Open DNS resolver and open NTP server on the public internet

Listening on `0.0.0.0` / `:::`, which includes the WAN address:

| Port | Service | Exposure |
| --- | --- | --- |
| 53/tcp+udp | dnsmasq | **open resolver** despite `listen-on eth2` |
| 123/udp | ntpd | **open NTP**, explicitly bound to `35.135.56.103:123` |
| 179/tcp | BGP | open listener for dead sessions |
| 10001/udp+tcp | UBNT discovery | open |
| 10002/udp | UBNT discovery | open |

IPv6 is bound too (`:::53`, `:::123`, `:::179`, `:::10001`, `:::10002`).

Open resolvers and open NTP are the two classic **reflection/amplification DDoS
sources**. This box is currently usable by strangers to attack third parties, and
the ISP link is the amplifier. `config` says DNS listens on `eth2` only; the
process disagrees, which is exactly the kind of gap a real input drop would have
caught.

This alone justifies the migration timeline.

### 3. Secrets in plaintext in the config

- **Namecheap DDNS password** for `thomasnetworks.us`:
  `3095a90b589d47d79410b676b4a2c54b`. **Rotate.**
- **UISP/UNMS connection token** in the `service unms connection` string.
- `system login user ivan.thomas` SHA-512 hash (expected, but it is in the dump).

Adds to the credential-rotation debt already open since April.

### 4. Weaker settings worth fixing on the way over

- `service gui older-ciphers enable` — deliberately weakened TLS on the web UI.
- `service snmp community public authorization ro` — the default community name,
  plus a second community `thomas`. LAN-bound, but `public` should not exist.
- `firewall source-validation disable` — no anti-spoof / reverse-path check.
- `firewall send-redirects enable` — should be off on a border router.
- Firmware is 5 years stale.

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
- `policy prefix-list BGP` denies `71.84.190.92/32` as "localgw", but the current
  WAN address is `35.135.56.103` — the prefix-list is stale on top of dead.

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

- `port-forward rule 1` — `isitup.thomasnetworks.us`, tcp 8888
- `port-forward rule 2` — **Helium Miner**, tcp 44158
- `traffic-policy shaper client-up-s` classes 2 and 3, both matching
  `source address 172.16.1.254/32`, plus `eth2 redirect ifb1`

**This also resolves an open question from the VyOS port.** I had flagged
"transit-10 NTP dstnat targets `172.16.1.254`, verify it is intentional." It is
not intentional: `.254` is dead, so that NTP redirect is black-holing NTP for
`unifi-mgmt-900` today. A live bug, pre-existing, fixed by not porting it.

Also note VyOS routes `10.98.0.0/24` and `10.10.93.0/24` via `172.16.1.254`, so
**both routers currently send switch-management traffic to a dead host.**

### eth1: disabled, never used

`10.98.0.1/24`, `disable`, 0 bytes RX and TX. That address is what the RB5009
bootstrap script already claims for management, which is consistent: the new box
takes over the role this interface was supposed to play.

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
| `service snmp` | RouterOS SNMP, drop the `public` community |
| `system conntrack` tuning | `ip_firewall_connection_tracking` |
| `system offload` | RouterOS fasttrack, already in the port |
| `service dns dynamic` (namecheap) | ⚠️ no RouterOS namecheap provider — see below |
| `system syslog` | already ported from VyOS |
| `system time-zone`, `host-name` | already ported |

### DDNS is duplicated across two providers and two domains

- ERL: Namecheap DDNS for `ddns.thomasnetworks.us`.
- VyOS: a `cloudflare-ddns` container for `tnwks.us`.

RouterOS has `ip_cloud` (MikroTik's own DDNS) and Cloudflare via a scheduler
script, but **no Namecheap provider**. Options: consolidate on Cloudflare, use
`ip_cloud`, or run a `system_scheduler` script hitting the Namecheap API. Needs a
decision, and the Namecheap credential needs rotating either way.

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

The VyOS port already adds an explicit input chain with a terminal drop, which I
described as "intentional hardening." With the ERL folded in, that input chain is
now **the only thing standing between the internet and the router**, and it is
what closes the open-resolver and open-NTP holes. It stops being a nice-to-have.

Requirements it must meet:

- DNS (`53`) and NTP (`123`) accepted from LAN interface-lists **only**, never
  from `ether1`.
- No BGP listener, no UBNT discovery ports.
- WireGuard `51820`/`51821` accepted on `ether1` (this is the one WAN-facing
  service, and it replaces ERL port-forward rules 3 and 4).
- Established/related and ICMP as already configured.
- `source-validation` on, unlike the ERL.

## Deltas to the existing port

Concrete changes needed in `modules/mikrotik` and the generated `locals.tf`:

1. `add_default_route` on the WAN DHCP client: `"no"` → **`"yes"`**.
2. `masquerade_out_interface`: `null` → **`"ether1"`**.
3. Drop the VyOS `0.0.0.0/0 → 172.16.1.1` static route; the default now comes
   from the WAN lease.
4. Drop `10.98.0.0/24` and `10.10.93.0/24` static routes (dead `.254` next-hop).
5. Keep `10.60.10.0/24 → 10.10.140.140`.
6. Drop the `transit-10` NTP dstnat entirely (dead target, confirmed above).
7. Add WAN-facing input rules: WireGuard accept on `ether1`; make sure DNS/NTP
   accepts are scoped to LAN lists.
8. Add conntrack tuning to match the ERL's table sizes.
9. Add a DDNS decision (see above).
10. `sce-rtr01` should get bind A records for **both** `172.16.1.1` and its
    management address, since it inherits `sce-er01`'s identity as well.

## Reproducing this discovery

Credentials live in `~/.openclaw/credentials/net-devices.env` (`ERL_USER`,
`ERL_PASS`). EdgeOS 2.0.9 predates modern OpenSSH defaults, so legacy overrides
are mandatory:

```bash
sshpass -p "$ERL_PASS" ssh \
  -o KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1 \
  -o HostKeyAlgorithms=+ssh-rsa,ssh-dss \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  -o Ciphers=+aes128-cbc,aes256-cbc,3des-cbc \
  "$ERL_USER"@172.16.1.1 \
  "/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands"
```

Notes for next time: operational commands need the
`/opt/vyatta/bin/vyatta-op-cmd-wrapper` prefix over SSH, `arp` does not exist
(use `ip neigh`), and `sudo` works without a password prompt.
