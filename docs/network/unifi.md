# UniFi access points and controller

Three UniFi APs serve the house's Wi-Fi. Their controller is the UniFi Network
Application on the `homelab-wsl` cluster (`kubernetes/apps/networking/unifi`),
and its configuration (site settings, the WLAN, each AP) is Terraform in
`infrastructure/terraform/environments/prod/unifi`.

| What | Where |
| --- | --- |
| Controller UI and API | `https://unifi.internal.tnwks.us` (internal ingress) |
| Inform URL the APs use | `http://unifi:8080/inform` (router record `unifi` -> `10.10.91.142`) |
| Configuration | `environments/prod/unifi` (TFC workspace `tnwks-unifi-prod`, local execution), `task unifi:{plan,apply}` |
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
| Living Room | UAP-AC-Pro-Gen2 | SW03 `GE0/0/1` | `10.10.90.102` | ch 1, 20 MHz | ch 44 |
| Master | UAP-HD | SW03 `GE0/0/12` | `10.10.90.101` | ch 6, 20 MHz | ch 149 |

- **Model and firmware:** LLDP on SW03 (`show lldp neighbor interface ... detail`).
  All three run **6.6.77** (August 2024), the last release for these models, so
  a controller still managed them until at least then. Each AP draws PoE from
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
- **Clients:** 23 on 2026-11-01 (7 on Kitchen, 9 on Living Room, 7 on Master by
  the SW03 MAC table). They include old IoT radios (LIFX Minis, ESP32s, a Sense
  energy monitor, Sonos), which is why the WLAN stays on WPA2 with PMF off.
- **Not recoverable from outside:** the passphrase (everyone in the house
  knows it; it goes into SOPS as `wlan_passphrase`), transmit power, minimum
  RSSI, band steering, and the AP names the old controller used. The APs
  report their model as their hostname, so they were probably never named.

### What Terraform changes

Terraform reproduces the above, with three deliberate changes:

1. **Static addresses move out of the DHCP pool:** Kitchen `.11`, Living Room
   `.12`, Master `.13`. The router's pool on 900 is `.100-.200`, so the old
   addresses could collide with the DHCP lease a freshly reset AP gets.
2. **Living Room's 2.4 GHz moves from channel 1 to 11.** It shared channel 1
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
  app. Nothing on the LAN needs tcp/8443.
- **When the desktop is off,** the APs keep serving Wi-Fi with their last
  configuration. Only changes, statistics and adoption wait for the controller.

## Rollout (one time)

Steps marked **owner** need hands on site, a UAC prompt or a password only the owner has.

1. **Look for an old backup first (owner).** The only place the old
   configuration can still exist is the VyOS box's disk:
   `/config/containers/unifi/data/backup/autobackup/*.unf` (and
   `/config/containers/unifi/data/backup/`). Do this **before** cutover step
   5.4 factory-resets VyOS. Boot it with its LAN cables unplugged (it would
   otherwise fight the RB5009 for the gateway addresses) and copy anything
   there to a USB stick. A `.unf` from the controller that adopted the APs
   restores into the new one (Settings -> System -> Backups -> Restore, newer
   versions read older backups) and brings the APs back without a reset,
   because the backup holds their device keys. If none is found, carry on.
2. **Merge the PR.** Flux deploys `networking/unifi`. First start creates the
   database and takes a few minutes:
   `kubectl -n networking get pods -l app.kubernetes.io/name=unifi -w`.
3. **LAN bridge (owner, UAC prompt):**
   ```bash
   task wsl-host:lan-bridge
   infrastructure/wsl-host/invoke-elevated.sh infrastructure/wsl-host/Register-TnwksLanBridgeTask.ps1
   ```
   The second line refreshes the copy of `Sync-TnwksLanBridge.ps1` that the
   scheduled task runs. Check: `curl -s -o /dev/null -w '%{http_code}\n' http://10.10.91.142:8080/inform`
   from WSL prints an HTTP status (the controller answering), not `000` (a
   timeout or refused connection).
4. **Bootstrap the controller (owner, about 5 minutes).** Open
   `https://unifi.internal.tnwks.us`. Logins are in
   `environments/prod/unifi/secrets.sops.yaml` (`sops -d` it).
   1. First-run wizard: any site name, skip the ui.com sign-in (advanced setup
      -> local account), owner account `admin_username` / `admin_password`.
      Leave local auto-backup on.
   2. Settings -> System -> Advanced: **Inform Host override** on, `unifi`.
   3. Admins: add local `terraform` (`unifi_username`/`unifi_password`, Super
      Admin) and local `unifipoller` (View Only), whose password is
      `unifipoller_password` in
      `kubernetes/apps/monitoring/unpoller/app/secrets.sops.yaml`.
5. **Wi-Fi password (owner):** `sops infrastructure/terraform/environments/prod/unifi/secrets.sops.yaml`,
   set `wlan_passphrase` to the current password. Terraform refuses to apply
   while it is `CHANGEME`, because a different value disconnects every client.
6. **Workspace:** `tnwks-unifi-prod` (execution mode local) is in
   `modules/tfc/workspaces.tf`; apply `environments/bootstrap/aws-init`, or
   create it through the TFC API and add import blocks the way
   `tnwks-mikrotik-prod` was (`aws-init/imports.tf`).
7. **Apply everything except the APs:**
   ```bash
   task unifi:init
   task unifi:apply -- -target=unifi_setting.site -target=unifi_wlan.main
   ```
8. **Factory-reset the APs (owner, on site).** One at a time keeps two of three
   up. Hold the reset button about 10 seconds until the LED changes, release,
   and give it a couple of minutes to boot. It comes up on DHCP in the
   `.100-.200` pool, resolves `unifi` and shows up as pending:
   ```bash
   task unifi:pending   # mac, model, name, adopted, state
   ```
9. **Adopt and configure:** `task unifi:apply`. `allow_adoption` adopts each
   pending AP, then Terraform names it, gives it its static address and pins
   its channels. The AP reboots its radios once; clients reconnect on their own
   because the SSID and passphrase are unchanged.
10. **Check:** each AP shows Connected in the UI at its `.11`/`.12`/`.13`
    address, the SW03 MAC table has clients on 910 behind all three ports, and
    unpoller logs no errors:
    `kubectl -n monitoring logs deploy/unpoller --tail=5`.

## Day to day

- Change Wi-Fi settings in Terraform, then `task unifi:plan` / `task unifi:apply`
  over the LAN or `wg01`. A change made in the UI shows up as drift on the next
  plan.
- **An AP stops checking in:** SSH to it with `device_ssh_username` /
  `device_ssh_password` from SOPS (or the desktop's key) and run
  `set-inform http://unifi:8080/inform`. No reset needed, as long as the
  controller database survives.
- **The controller database is lost:** redeploy, repeat bootstrap step 4, and
  `task unifi:apply` rebuilds the configuration. The APs then need either a
  `.unf` restore or the SSH route above (`set-default` over SSH resets an AP
  without touching it), not a ladder.
- Local auto-backups land on the `config` PVC
  (`/config/data/backup/autobackup`), which is local-path on the desktop's
  disk. They are not copied anywhere else yet.

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
