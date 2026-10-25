# RB5009 cutover checklist

The step-by-step for moving from `sce-vyos01` + `sce-er01` to the RB5009, with
who does each step. **You** = something physical or a decision. **Agent** =
typed work an AI coding agent (or you) runs from this repo. Steps marked
**(approve)** change something outside the repo and wait for a go-ahead.

Background, design and the rationale for each stage are in
[`mikrotik-vyos-port.md`](mikrotik-vyos-port.md). Access model and break-glass
are in [`network-access.md`](network-access.md).

Nothing touches the live network until Phase 3, and every step from there is
undone by moving one cable back.

## What you need

- The Ethernet cable that currently runs from your PC's network port to the
  wall/switch. The bench steps borrow it.
- Wi-Fi on the PC, so it stays online while that cable is on the router.
- Optional: an RJ45-to-USB serial console cable (RB5009 "Serial" port,
  115200 8N1), as a last-resort way in. The router's USB-A port is a host port
  for storage, not a console.

## Phase 0: Prep (no network impact)

| # | Who | Step |
| --- | --- | --- |
| 0.1 | Agent (approve) | Squash the PR's commits into one with a clean message, then squash-merge it. Applying from `main` keeps `main` the source of truth |
| 0.2 | Agent (approve) | Create the `tnwks-mikrotik-prod` workspace (execution mode **local**) by applying `environments/bootstrap/aws-init`, which owns `modules/tfc`. The plan must show only the workspace and its settings |
| 0.3 | Agent | Build and encrypt `secrets.sops.yaml`: generated passwords (Terraform, admin, mktxp), `admin_username: it3e`, the WireGuard keys from `vyos-config/secrets.sops.yaml`, and the DDNS host/domain/password from the ERL's current config |
| 0.4 | Agent | Copy the SOPS age key and the admin password to `G:\My Drive\Backup` |
| 0.5 | Agent | Diff live VyOS (`show configuration commands`) against `vyos-config`; anything real goes into `vyos-config` and `task mikrotik:generate` |
| 0.6 | Agent | Generate the PC's `wg01` client config (new keypair if the old one is gone; the peer public key goes into `vyos-config`). The phone keeps its `wg02` config: it is an admin peer by address |
| 0.7 | You | Install WireGuard for Windows (the agent can start `winget install WireGuard.WireGuard`; you accept the UAC prompt) and import the config. One install covers Windows apps and WSL, since WSL egresses through Windows |
| 0.8 | Agent | Download the latest RouterOS 7 `arm64` package |

## Phase 1: Bench (router on your desk, PC on Wi-Fi)

| # | Who | Step |
| --- | --- | --- |
| 1.1 | You | Connect the PC to Wi-Fi. Unplug the PC's Ethernet cable from the wall/switch end and plug it into the RB5009's **ether8**. Power the RB5009 on |
| 1.2 | You | Run the elevated PowerShell command the agent provides (UAC prompt) that sets the PC's Ethernet adapter to `192.168.88.2/24` with no gateway, so Windows keeps using Wi-Fi for the internet. Read out the password on the router's sticker |
| 1.3 | Agent | Upload RouterOS and upgrade it, upgrade the RouterBOARD firmware, reboot |
| 1.4 | Agent | Upload a filled-in `routeros-bootstrap.rsc` and run `/system reset-configuration no-defaults=yes skip-backup=yes run-after-reset=routeros-bootstrap.rsc`. Confirm the REST API answers on `https://192.168.88.1` as `terraform` |
| 1.5 | Agent | `task mikrotik:bootstrap`, `task mikrotik:init`, `task mikrotik:plan`; summarise the plan |
| 1.6 | Agent (approve) | `task mikrotik:apply`, then `task mikrotik:plan` again (must be empty). `/ip traffic-flow set enabled=yes`. Check free memory with the adlists loaded and the firewall rule order |
| 1.7 | You + Agent | **Test `wg01`:** activate a bench copy of the PC tunnel with endpoint `192.168.88.1:51820`. The agent confirms SSH/REST to `10.10.30.1` through it. Deactivate it afterwards |
| 1.8 | Agent, then You | The agent reverts the PC's Ethernet adapter to DHCP (UAC prompt); you plug the cable back into the wall/switch |

While the PC's cable is on the router, services published through the PC
(`*.internal.tnwks.us`, Frigate, Home Assistant) are unreachable from the rest
of the house. The cluster itself keeps running.

## Phase 2: Rack it (no impact)

| # | Who | Step |
| --- | --- | --- |
| 2.1 | You | Mount and power the RB5009. Leave every port empty; `ether8` is the break-glass port for a direct cable later |

## Phase 3: Stage A, replace VyOS (LAN down a few minutes)

The ERL stays the edge, so the internet path, NAT and the WireGuard
port-forwards are unchanged.

| # | Who | Step |
| --- | --- | --- |
| 3.1 | You | Unplug VyOS's trunk from Aruba **GE0/0/23** and plug the RB5009's **ether2** into GE0/0/23. Power VyOS off and leave it as is |
| 3.2 | Agent (approve) | Flush the ERL's ARP table (`sudo ip neigh flush all`); the gateway IPs are the same but the MAC changed |
| 3.3 | You | Make sure the PC's `wg01` tunnel is active |
| 3.4 | Agent | Verify: DHCP leases, DNS (`10.10.91.1`, a `*.internal.tnwks.us` name), internet, Frigate streams, APs, logs; and that 910 cannot reach router management or the cameras |
| 3.5 | You | Phone on cellular, `wg02`: browse, and open the switch UI (`10.98.0.251`) |
| — | You | **Rollback:** trunk cable back to VyOS, power it on; the agent flushes ERL ARP again |

## Phase 4: Stage B, move the WAN (internet down about 5 minutes)

| # | Who | Step |
| --- | --- | --- |
| 4.1 | You | Power the ERL off. Move the modem's cable from the ERL's `eth0` to the RB5009's **ether1**. Power-cycle the modem (unplug 60 s): cable modems often only lease to the first MAC they see until rebooted |
| 4.2 | Agent | Verify the WAN lease, default route, NAT, DDNS update and flow export; watch CPU while you run a speed test |
| 4.3 | You | From cellular: `wg01` (laptop) and `wg02` (phone) connect, and an outside port check shows nothing but WireGuard |
| — | You | **Rollback:** modem cable back into the ERL, power-cycle the modem, power the ERL on |

### Outside check (4.3)

From a machine on cellular or another network, against the WAN address:

```bash
dig @<wan-ip> example.com          # must time out
ntpdate -q <wan-ip>                 # must fail
nmap -sU -p 53,123 <wan-ip>         # must not be open
nmap -Pn -p 22,80,443,8291 <wan-ip> # must not be open
```

## Phase 5: Clean up (after a few stable days)

| # | Who | Step |
| --- | --- | --- |
| 5.1 | Agent (approve) | Remove the ERL fallback route (`UPSTREAM_GATEWAY = None` in the generator), regenerate, PR, apply |
| 5.2 | Agent | Point `routeros_url` in SOPS at `https://10.10.30.1` (wg01) |
| 5.3 | Agent | PRs: `sce-rtr01` DNS records; UniFi controller on k8s with its relays; mktxp with DHCP lease names; Vector on WSL with a tcp/6001 relay |
| 5.4 | You | Factory-reset the ERL and VyOS before shelving them (they hold old credentials and the reused WireGuard key). Power off the old blade servers on VLAN 550 |
