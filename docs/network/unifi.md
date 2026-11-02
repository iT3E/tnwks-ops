# UniFi access points and controller

Three UniFi APs serve the house's Wi-Fi. Their controller is the UniFi Network
Application on the `homelab-wsl` cluster (`kubernetes/apps/networking/unifi`),
and its configuration (site settings, the WLAN, each AP) is Terraform in
`infrastructure/terraform/environments/prod/unifi`.

| What | Where |
| --- | --- |
| Controller UI | `https://unifi.internal.tnwks.us`: Cognito login (oauth2-proxy), then the controller's local login (`admin_username` in SOPS) |
| Controller API | `https://10.5.0.206:8443` (VIP, desktop only) for local Terraform; `unifi.networking.svc.cluster.local:8443` for CI and unpoller |
| Inform URL the APs use | `http://unifi:8080/inform` (router record `unifi` -> `10.10.91.142`) |
| Configuration | `environments/prod/unifi` + `modules/unifi`: planned on the PR and applied on merge by `unifi-{plan,apply}.yaml` on the self-hosted runner ([`ci.md`](ci.md)). State in TFC workspace `tnwks-unifi-prod`. `task unifi:*` for bootstrap and break-glass |
| Secrets | `environments/prod/unifi/secrets.sops.yaml`: controller logins, device SSH, SSID, passphrase, AP MACs |
| Metrics | `monitoring/unpoller` (local controller user `unifipoller`) |

The SSID and the AP MAC addresses are kept in SOPS, never in this public repo:
UniFi derives each BSSID from the AP's MAC, and an SSID or BSSID is enough to
find the house on a wardriving map. The same goes for anything written about
this setup in PRs and issues.

## The access points (inventory as of 2026-11-01)

The APs' original controller, and its database, are gone (see
[History](#history)). Everything below was read from outside the APs; the notes
under each table say where each value came from.

| AP | Model | Switch port | Mgmt address (old) | 2.4 GHz | 5 GHz |
| --- | --- | --- | --- | --- | --- |
| Kitchen | UAP-AC-Pro-Gen2 | SW03 `GE0/0/0` | `10.10.90.100` | ch 1, 20 MHz | ch 40, 40 MHz |
| Master | UAP-AC-Pro-Gen2 | SW03 `GE0/0/1` | `10.10.90.102` | ch 1, 20 MHz | ch 44 |
| Living Room | UAP-HD | SW03 `GE0/0/12` | `10.10.90.101` | ch 6, 20 MHz | ch 149 |

The rooms were swapped in the pre-2026 notes: the AC-Pro on `GE0/0/1` is in the master bedroom and the UAP-HD
on `GE0/0/12` in the living room, confirmed on site during the rollout.

- **Model and firmware:** LLDP on SW03 (`show lldp neighbor interface ... detail`).
  All three ran **6.6.77** (August 2024) at adoption, so
  a controller still managed them until at least then. (Not the last release:
  the controller offered 6.8.2 from February 2026; auto-upgrade is on, see
  below.) Each AP draws PoE from
  SW03 (the UAP-HD about 7 W, the AC-Pros about 4 W).
- **Addressing:** **static**, not DHCP. The router has no leases on VLAN 900,
  only ARP entries. The APs still point at the VyOS-era services, which no longer
  exist:
  - DNS `10.10.53.4` (the old dnsdist container). The APs send DNS queries
    there and get no answer, so they cannot resolve `unifi`.
  - Remote syslog to `10.10.53.10:5514`, the old controller's syslog-capture
    port. Proof that the controller that adopted them lived at that address,
    on VyOS. With `10.10.53.0/24` unrouted, that traffic currently leaves on
    the default route and is masqueraded out of the WAN.
- **Switch ports:** profile `Unifi AP`, trunk with native VLAN 900 (AP
  management, untagged) and 910/920/930 allowed. Only VLAN 910 carries client
  MACs; nothing has ever been seen on 920 or 930.
- **SSH:** dropbear 2022.83 answers on tcp/22. The default `ubnt`/`ubnt` is
  refused, so the old controller set its own device credentials, and nobody
  knows them.

### The WLAN

Read from the kitchen display's Wi-Fi scan (`cmd wifi list-scan-results` over
ADB) and its connection details:

- **One SSID** on both bands of all three APs, not hidden. The name is
  `wlan_ssid` in SOPS.
- **Security:** WPA2-PSK, AES/CCMP only. No WPA3, no PMF, no 802.11r (no FT
  AKM advertised).
- **VLAN:** clients land on 910 (`unifi-frontend-910`, `10.10.91.0/24`).
- **Clients:** 23 on 2026-11-01 (7 on Kitchen, 9 on Master, 7 on Living Room by
  the SW03 MAC table). They include old IoT radios (LIFX Minis, ESP32s, a Sense
  energy monitor, Sonos), which is why the WLAN stays on WPA2 with PMF off.
- **Not recoverable from outside:** the passphrase (everyone in the house
  knows it; it goes into SOPS as `wlan_passphrase`), transmit power, minimum
  RSSI, band steering, and the AP names the old controller used. The APs
  report their model as their hostname, so they were probably never named.

### What Terraform changes

Terraform reproduces the above, with three deliberate changes:

1. **Static addresses move out of the DHCP pool:** Kitchen `.11`, Master
   `.12`, Living Room `.13`. The router's pool on 900 is `.100-.200`, so the old
   addresses could collide with the DHCP lease a freshly reset AP gets.
2. **Master's 2.4 GHz moves from channel 1 to 11.** It shared channel 1
   with Kitchen.
3. **DNS and gateway are the router** (`10.10.90.1`), so `unifi` resolves.

## How the APs reach the controller

```
AP (VLAN 900)
  -> DNS "unifi" on the router -> 10.10.91.142 (the Windows desktop)
  -> router firewall: unifi-mgmt-900 -> unifi_controller, tcp/udp 8080, 3478, 6789
  -> Windows netsh portproxy 0.0.0.0:8080 -> <wsl-ip>:18080  (firewall: from 10.10.90.0/24 only)
  -> socat tnwks-lan-bridge@unifi-inform -> MetalLB 10.5.0.206:8080
  -> networking/unifi-inform Service -> the controller pod
```

- **Discovery is just DNS.** A factory-reset UniFi device takes a DHCP lease
  and then sends its inform to `http://unifi:8080/inform`. The router's static
  record `unifi` (generated into `environments/prod/mikrotik/locals.tf` from the
  old bind zone) answers with the desktop's address. No DHCP option 43 or
  L2 discovery is needed.
- **Port 18080 on WSL:** Docker on this host already holds `0.0.0.0:8080`
  (Scriberr), so socat listens on 18080 and the Windows portproxy maps LAN 8080
  to it (`MappedTcpPorts` in `Sync-TnwksLanBridge.ps1`). Windows' own
  `wslrelay` holds `127.0.0.1:8080` for that container; a wildcard bind on
  8080 next to it was tested and works.
- **Override Inform Host:** the controller tells adopted devices where to
  inform next. Left alone it would hand out its pod IP, which the APs can't
  reach. The bootstrap below sets it to the hostname `unifi`, so the router
  record stays the one place that says where the controller lives.
- **STUN (udp/3478) is not relayed.** The Windows UDP relay
  (`Start-TnwksUdpRelay.ps1`) forwards one way and STUN needs replies. Without
  it the controller logs "STUN communication failed" and pushes settings on the
  next inform (about every 10 seconds) instead of immediately. Adoption,
  provisioning and statistics all work over 8080.
- **The UI** goes through the internal ingress like any `*.internal.tnwks.us`
  app, behind oauth2-proxy (forward auth, any `tnwks-auth` user). The UniFi
  Network Application has no OIDC or SAML login of its own (its only SSO is a
  ui.com cloud account), so its local login follows the Cognito one. The API
  is also on the `unifi-inform` VIP, which only the desktop can reach: local
  Terraform and `task unifi:*` use that, since they can't pass a browser login.
  Nothing on the LAN needs tcp/8443.
- **When the desktop is off,** the APs keep serving Wi-Fi with their last
  configuration. Only changes, statistics and adoption wait for the controller.

## Rollout (done 2026-11-01)

What was done, in order. To rebuild after losing the controller, repeat 2, 3
and 6-9.

1. **Old backup:** not looked for. The only place one could exist is the VyOS
   disk (`/config/containers/unifi/data/backup/`); copy it off before cutover
   step 5.4 if it's ever wanted. A `.unf` holds the device keys, so restoring
   one would re-adopt the APs without a reset.
2. **Deploy:** Flux deploys `networking/unifi`; the first start creates the
   database in about a minute.
3. **LAN bridge:** `task wsl-host:lan-bridge`, then
   `infrastructure/wsl-host/invoke-elevated.sh infrastructure/wsl-host/Register-TnwksLanBridgeTask.ps1`
   to refresh the copy of `Sync-TnwksLanBridge.ps1` the scheduled task runs.
   Check from the AP subnet, through the real path, on the router:
   `/tool fetch url="http://unifi:8080/inform" src-address=10.10.90.1` fails
   with `Status 400`. That is the controller rejecting an empty inform; a
   timeout means the bridge is broken.
4. **Wi-Fi password:** `wlan_passphrase` in SOPS must be the current password
   (the module refuses `CHANGEME`, because a different value disconnects every
   client). It was read from the desktop's saved Windows Wi-Fi profile for the
   SSID (DPAPI, decrypted as SYSTEM).
5. **Workspace:** `tnwks-unifi-prod` (execution mode local) was created
   through the TFC API, matching `modules/tfc/workspaces.tf`;
   `aws-init/imports.tf` adopts it on the next `aws-init` apply.
6. **Bootstrap the controller:** `task unifi:bootstrap`. It does the
   first-run wizard over the API (owner account `admin_username`, country,
   time zone), sets Override Inform Host (`super_mgmt.override_inform_host`
   with `super_identity.hostname = unifi`), and creates the local accounts
   `terraform` (admin) and `unifipoller` (read-only, password from the
   unpoller Secret). It only works on an uninstalled controller.
7. **Everything except the APs:**
   ```bash
   task unifi:init
   task unifi:apply -- -target=module.unifi.unifi_setting.site -target=module.unifi.unifi_wlan.main
   ```
   The WLAN's first create reports "Provider produced inconsistent result
   after apply" although the WLAN is created correctly (provider 0.65).
   `terraform untaint module.unifi.unifi_wlan.main`, and the next plan shows
   no changes.
8. **Factory-reset each AP (on site):** hold reset about 10 seconds while
   powered, until the LED changes. It takes a DHCP lease, resolves `unifi` and
   shows up as pending (`task unifi:pending`).
9. **Adopt:** per AP, as each one appears,
   `task unifi:apply -- -target='module.unifi.unifi_device.ap["kitchen"]'`
   (or a plain `task unifi:apply` once all three are pending). Adoption takes
   about a minute, then the AP provisions: static address, channels, the SSID.
   Clients rejoin on their own because the SSID and passphrase are unchanged.
9a. **Living Room (UAP-HD):** its reset button broke on 2026-11-01 (a common
    UAP-HD failure). The case is glued; it was pried open along the seam and
    the switch pressed directly on the board while powered. Until then it ran
    standalone on the old config, held out of Terraform with `managed = false`.
10. **Check:** every AP Connected at `.11`/`.12`/`.13` with clients, and
    unpoller's log shows `UAP: 3`.
11. **Hand over to CI:** once `task unifi:plan` shows no changes,
    `gh variable set UNIFI_TERRAFORM_ENABLED --body true`. Until then the
    UniFi workflows skip.

## Day to day

- Change Wi-Fi settings in Terraform through a PR: `unifi-plan.yaml` posts
  the plan summary, merging applies it. A change made in the UI shows up as
  drift on the next plan. `task unifi:plan` / `task unifi:apply` from the
  desktop do the same by hand, against the same state, when CI is down.
- **An AP stops checking in:** SSH to it with `device_ssh_username` /
  `device_ssh_password` from SOPS (or the desktop's key) and run
  `set-inform http://unifi:8080/inform`. No reset needed, as long as the
  controller database survives.
- **The controller database is lost:** redeploy, `task unifi:bootstrap`, and
  `task unifi:apply` rebuilds the configuration. The APs then need either a
  `.unf` restore or the SSH route above (`set-default` over SSH resets an AP
  without touching it), not a ladder.
- **Never resetting an AP again:** every AP has the device SSH credentials
  from SOPS (password and the desktop's key, checked on all three on
  2026-11-01) and informs to the hostname `unifi`. So:
  - Controller moves: change the router's `unifi` record; nothing on the APs.
  - Controller database lost: restore a `.unf` (keeps the APs' keys, nothing
    to re-adopt), or SSH in and `set-default` to factory-reset an AP
    remotely, then let Terraform adopt it.
- Auto-backups: daily at 02:00, 14 kept (`super_mgmt`, set by
  `task unifi:bootstrap`), on the `config` PVC
  (`/config/data/backup/autobackup`), which is local-path on the desktop's
  disk. They are not copied off the desktop yet. A `.unf` holds the Wi-Fi
  passphrase and the device keys, so treat copies as secrets.

## History

- **2023-07 to 2026-10:** `jacobalberty/unifi-docker:v7.4.162` ran as a
  container on VyOS at `10.10.53.10`, data in `/config/containers/unifi`. It
  adopted the APs: their remote syslog still points at that address, and they
  run firmware from August 2024, so it managed them until at least then. The
  VyOS repo has a (commented-out) `custom-config-backup.sh` that was meant to
  copy its autobackups to a USB stick.
- **By 2026-04:** that container was running a fresh database (the vault notes
  date it to January 2024, which the firmware dates contradict). The APs'
  informs got HTTP 400 because the new database did not have their keys, and
  the old one was not backed up anywhere known.
- **2026-10-25:** the RB5009 cutover retired VyOS and its containers. The APs
  kept serving Wi-Fi from their last configuration, orphaned.
- **2026-11:** this controller on `homelab-wsl`, configuration in Terraform.
