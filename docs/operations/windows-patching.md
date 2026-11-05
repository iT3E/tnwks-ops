# Windows patching (wsl-01, wsl-02)

**Status: design.** Nothing here is built yet. It depends on the second WSL
cluster (`homelab-wsl-02` on `WSL-02`) and the multi-site design in
[multi-site.md](../wsl/multi-site.md): Cluster Mesh, the routed network and
`task failover`. See [Dependencies](#dependencies) and
[Build order](#build-order).

Windows Update on the two hosts under the WSL clusters is driven from this
repo. Windows never installs or reboots on its own. Every patch is approved
per host in git, installed by Ansible from the **other** site's runner in a
window the owner armed, and wrapped in a pre and post procedure that fails
the host's key apps over to the other site first and back afterwards.

## Goals

- Windows does nothing by itself: no automatic install, no automatic reboot,
  no driver updates, no surprise feature updates, no Store app updates
  (WSL included).
- Every update is approved explicitly: a KB list per host, merged to `main`.
- A host goes down only in a window the owner armed, one host at a time.
- Key apps keep running: the warm tier fails over to the other site before
  the host is touched and fails back after it passes its checks.
- `WSL-02` patches first. `DESKTOP-HKE05KI` gets the same KBs only after
  `WSL-02` soaked clean.
- Every step is gated by checks. A failed check stops the run, and key
  apps stay on whichever site is healthy.

## Today (2026-11-04)

| Host | Windows | Update policy | What happens |
| --- | --- | --- | --- |
| `DESKTOP-HKE05KI` (wsl-01) | 11 Pro 25H2 (26200) | none | Active hours 14:00 to 08:00, so Windows treats 08:00 to 14:00 as free time. It rebooted at about 08:30 the morning after Patch Tuesday in September and October, taking the whole cluster down mid-morning. |
| `WSL-02` (wsl-02) | 11 Pro 23H2 (22631.6199) | none | 23H2 Home/Pro left servicing on 2025-11-11 and gets no security updates. Active hours 02:00 to 20:00. |

A Store update of the WSL app also took wsl-01's distro down for 38 hours on
2026-07-25 (`Watch-TnwksWsl.ps1` exists because of it).

## How it fits together

```text
nightly          patch-scan (runner on the other site)
                   searches each host, opens or updates a PR per host
                   that lists the new KBs in host_vars/<host>/patches.yml
                      |
owner            reviews, edits, merges            <- approval 1: which KBs
                      |
evening          arm job, GitHub Environment       <- approval 2: go tonight
                 patch-<host> with a required
                 reviewer
                      |
window           windows-patch.yml on the other site's runner
                   0 plan  1 preflight  2 failover  3 drain
                   4 install  5 verify host  6 restore site  7 failback
                      |
soak (wsl-02)    72h clean, then patch-scan opens the promotion PR
                 for DESKTOP-HKE05KI with the same KBs
```

The runner always sits on the other site. A run on the host's own cluster
would kill itself when the host reboots. This is the cross-site `ci_runner`
from [ansible.md](ansible.md): the site that patches `DESKTOP-HKE05KI` is
`site_wsl02`, and the reverse.

## Pieces (proposed)

| Path | What |
| --- | --- |
| `infrastructure/ansible/roles/windows_updates` (tag `updates`) | the brake policy below, the textfile metrics, part of `windows-host.yml` so the nightly drift check covers it |
| `infrastructure/ansible/roles/windows_monitoring` | turns on windows_exporter's `update` collector (pending updates) |
| `infrastructure/ansible/inventory/host_vars/<host>/patches.yml` | the approval record for one host |
| `infrastructure/ansible/playbooks/windows-patch.yml` | the runbook below, one tag per phase |
| `.github/workflows/windows-patch-scan.yaml` | nightly search, approval PRs, promotion PRs |
| `.github/workflows/windows-patch.yaml` | arm, then the window run |
| GitHub Environments `patch-DESKTOP-HKE05KI`, `patch-WSL-02` | required reviewer: the owner |
| `.taskfiles/` `windows-patch:scan`, `windows-patch:run` | the same playbook from the workstation, break-glass only |
| `kubernetes/.../addons/alerts/windows-patching.yaml` | the alerts in [Monitoring](#monitoring-and-alerts) |

## The brakes

Role `windows_updates` writes these keys on both hosts. They are the only
thing that stops Windows from patching itself, so drift on them fails the
nightly check like any other managed setting.

| Key (under `HKLM\SOFTWARE\Policies\Microsoft\...`) | Value | Why |
| --- | --- | --- |
| `Windows\WindowsUpdate\AU\NoAutoUpdate` | `1` | no automatic download, install or reboot; the Windows Update API still works for Ansible |
| `Windows\WindowsUpdate\AU\NoAutoRebootWithLoggedOnUsers` | `1` | belt and braces for the reboot |
| `Windows\WindowsUpdate\ExcludeWUDriversInQualityUpdate` | `1` | drivers never come through Windows Update. A NIC driver update resets the Ethernet advanced properties `windows_host_settings` checks |
| `Windows\WindowsUpdate\TargetReleaseVersion` / `TargetReleaseVersionInfo` / `ProductVersion` | `1` / `25H2` / `Windows 11` | a feature update happens only when this pin changes in a PR |
| `WindowsStore\AutoDownload` | `2` | no Store app updates, which is how WSL updated itself in July |

Exceptions, on purpose: Defender security intelligence (definitions) keeps
updating itself. It needs no reboot and blocking it only weakens the host.

Spike these before trusting them (see [Open questions](#open-questions)):
whether 25H2's update orchestrator honours `NoAutoUpdate`, whether
`AutoDownload` works on Pro, and how WSL 3.x is updated now. The
`WindowsUnplannedReboot` and `WindowsUnapprovedUpdateInstalled` alerts
catch a brake that doesn't hold.

## Approval

### The patch file

One file per host, `host_vars/<host>/patches.yml`:

```yaml
windows_patches:
  # Approval 1: the update IDs Ansible may install. Nothing else installs.
  approved:
    - KB5127648        # 2026-11 cumulative update (25H2)
    - KB5127690        # .NET
  # Never install, even if it shows up again. Add a KB here when it's
  # rolled back.
  blocked: []
  # Feature release this host is pinned to (the brake writes it).
  target_release: 25H2
  # WSL version to install with `wsl --update` in the window, if newer
  # than what's on the host.
  wsl_version: 3.0.3.0
  # Earliest start for the next window, local time. The window run does
  # nothing before it.
  window_not_before: 2026-11-14T03:00:00-08:00
  # Ring: the KBs here must have soaked on these hosts first.
  after: [WSL-02]
```

### Nightly scan

`windows-patch-scan.yaml` runs once a night on each site's runner against
the other site's host. It runs `ansible.windows.win_updates` with
`state: searched` (changes nothing) and, for each host:

- **New updates:** opens or updates one PR, `patch(<host>): approve
  <yyyy-mm> updates`, that adds them to `approved` with their titles as
  comments and sets the next `window_not_before` (the Saturday after Patch
  Tuesday for `WSL-02`, a week later for `DESKTOP-HKE05KI`). Merging the PR
  is approval 1. Remove lines from the PR to approve part of it.
- **Promotion:** when a KB has been installed on every host in `after` for
  72 hours with no `WindowsPatchRunFailed`, no unplanned reboot and no new
  critical alert on that site, it opens the PR that approves the same KB
  for the host. Different feature releases get different KBs, which is one
  reason both hosts must sit on the same `target_release`.
- **Drivers and firmware:** never proposed. A driver update is a pinned
  version in the repo, its own PR, applied in a window like a KB.
- **Feature updates:** never proposed. Moving `target_release` is its own
  PR, patched on `WSL-02` first.

The Actions logs are public. The PR body carries only KB numbers and
titles, which are public anyway.

### Arming the window

Approval 2 is a tap in GitHub, made the evening before:

1. `windows-patch.yaml` has an `arm` job (manual dispatch, or scheduled the
   evening before `window_not_before`) that targets the environment
   `patch-<host>`. The job waits for the owner's approval in the GitHub app
   and then succeeds, which records a successful deployment to that
   environment.
2. The window run at `window_not_before` (a cron in UTC; 03:00 local moves
   an hour with DST, accept 02:30 to 03:30) patches only if the latest
   deployment to `patch-<host>` succeeded in the last 24 hours at the
   commit it's running from. No arm, no patching; a stale arm doesn't count.
3. `workflow_dispatch` with `now: true` skips the wait for a daytime window.
   It still goes through the environment approval.

Both workflows share one `concurrency` group, `windows-patching`, so the two
hosts are never in a window at the same time.

## Runbook

`windows-patch.yml`, one tag per phase. Each phase starts with its gates; a
failed gate stops the run and leaves the state described in
[If a phase fails](#if-a-phase-fails). The run writes a step summary with
pass/fail per phase and KB numbers only.

### 0. Plan (changes nothing)

- Read `patches.yml`. Search the host. Work out `approved` minus installed
  minus `blocked`. If that's empty and `wsl_version` is current, stop with
  "nothing to do": no failover, no reboot.
- Check the arm (above).
- Take the shared lock (the concurrency group) and check the other host
  isn't mid-window.

### 1. Preflight gates (changes nothing)

Both sites:

- The other site, which takes the load, is healthy: Talos nodes Ready, Flux
  Kustomizations and HelmReleases Ready, no critical alert firing, its
  runner up (it's running this job), enough free memory for the apps that
  will fail over onto it.
- The mesh is healthy: `cilium clustermesh status` shows both clusters
  connected, and the other site's BGP session on the router is up, so the
  shared VIPs move when this host goes down.

The host being patched:

- Nothing critical firing that would hide a problem the patch causes.
- `windows-check` reports `changed=0`: start from a known host.
- Free space: at least 20 GB on `C:` for update staging, and the WSL disk's
  drive below its warning.
- Note a reboot that's already pending (`windows_exporter`'s `update`
  collector, `RebootRequired` key) in the summary.

Every key app (see [What happens to each app](#what-happens-to-each-app)):

- Its standby is healthy: the CNPG replica cluster on the other site
  streaming over the mesh with lag under 1 minute, and its last weekly PVC
  restore drill verified less than 8 days ago.
- Last barman base backup succeeded in the last 24 hours.

Snapshot, written as a run artifact (age-encrypted like the Ansible log)
for the post checks to compare against: OS build, installed KBs,
`wsl --version`, driver versions, scheduled task and service states, the
pods that are already not Ready, the Talos node list.

### 2. Fail over key apps

- Silence alerts for the patched site in the surviving site's Alertmanager
  (host instance and that cluster's targets, for the window plus 30
  minutes), and pause that site's healthchecks.io checks
  ([external-monitoring.md](external-monitoring.md)).
- Post the outage notice: Z-Wave is down for the window, everything else
  moves.
- For each app that moves ([below](#what-happens-to-each-app)), one at a
  time, run `task failover APP=<app> TO=<other site>`, the planned
  switchover in [multi-site.md](../wsl/multi-site.md#planned-switchover):
  1. set `<APP>_REPLICAS=0` in the patched site's `site-overrides`
     ConfigMap and reconcile; the app stops;
  2. warm apps: take a final kopiur snapshot of its PVCs and restore it on
     the other site, so the restore is current instead of up to a week old;
  3. warm apps: hand the CNPG primary over (the old side demotes and gives
     its demotion token, the other side promotes with it). With the app
     stopped there are no writes in flight, so nothing is lost;
  4. set the app's replicas and primary in the other site's
     `site-overrides` and reconcile; Flux starts it there;
  5. check it: the app answers on its own name, its pods are Ready, its
     database is primary there. Its global service already sends traffic
     to the other site, and the shared VIP moves on its own when the host
     goes down, so no DNS changes.
- Frigate and mosquitto move the same way without the data steps: each
  site keeps its own Frigate database and recordings, and Frigate on
  `WSL-02` detects on the GPU detector service.

A patch run is a **planned switchover**, which the "failover is manual"
decision allows: both sites are reachable, the old side is stopped before
the new side is promoted, and a human armed it. The split-brain risk that
rules out automatic failover comes from guessing whether a site is dead,
and nothing here guesses.

The switch needs only the two clusters' API servers: no pull request, no
DNS change, nothing on GitHub in the path once the run has started. The
overrides are runtime state, and an alert fires if one outlives the window
by a day.

### 3. Drain the host

- Pause every host task that calls `wsl.exe`: `tnwks-wsl-watchdog`,
  `tnwks-usb-attach`, `tnwks-lan-bridge-sync`. Otherwise the watchdog
  starts WSL again five minutes in.
- Stop what's left on the cluster cleanly: cold-tier apps and zwave-js-ui
  scale to 0, then the Talos nodes shut down (worker-2, worker-1, control
  plane). The distro's BGP sessions drop with them, and the router sends
  the shared VIPs to the other site.
- `wsl --shutdown`, and confirm `wsl --list --running` is empty.
- Write `tnwks_windows_patch_window_open 1` to the host's textfile
  collector.

### 4. Install

- `win_updates` with `accept_list` set to the approved KBs that aren't
  installed, `reject_list` set to `blocked`, `reboot: true`, a
  `reboot_timeout` of an hour. Repeat until nothing approved is pending,
  since a servicing stack update can take a second reboot.
- `wsl --update` to `wsl_version` if it's newer than what's on the host.
- A KB that fails to install: stop. Don't retry in the same window.

### 5. Verify the host (gates)

- SSH answers, the build is what the KBs promise, every approved KB is in
  `Get-HotFix`, no reboot pending, no install failures in the
  WindowsUpdateClient events since the window opened.
- `windows-check` reports `changed=0` again, including the check-only
  NIC, Wi-Fi and power settings, which updates like to reset.
- Services and tasks match the snapshot (windows_exporter,
  smartctl_exporter, the Coral detector services, the UDP relays).

### 6. Restore the site

- Unpause the tasks from phase 3. The watchdog brings WSL and the distro
  back; confirm `systemd` is running and, on `DESKTOP-HKE05KI`, the
  OpenClaw gateway unit is active.
- The distro's WireGuard tunnel to the router is up again and its BGP
  sessions are established. Until the LAN bridge is retired, run its sync
  too: the WSL IP can change on reboot.
- Talos nodes Ready, Flux Ready, no pod Not Ready that wasn't in the
  snapshot.
- Hardware on wsl-01: Z-Stick attached over usbip and zwave-js-ui
  connected; Coral detector service answering with normal inference
  times. On `WSL-02`: the GPU detector service answering.
- Exporters up (`up{job="windows-exporter"}`, `smartctl-exporter`).
- The replica clusters on this site catch up over the mesh: they're the
  standby for the apps that just moved away.
- Write `tnwks_windows_patch_window_open 0` and the success timestamp.

### 7. Fail back

- Wait an hour after phase 6 passed (`failback_hold`, set to `manual` to
  keep the apps on the other site until someone runs `task failover`).
- For each app moved in phase 2, the same switchover in reverse, with the
  same checks, once its replica on the home site has lag under 1 minute.
  Failback ends with both sites' overrides for the app deleted, so it runs
  on its git default again.
- Frigate on wsl-01: every camera reporting FPS above 0 on the Coral.
- Expire the silence, resume healthchecks.io, post the all-clear.

### 8. Soak and promote

- 72 hours for `WSL-02`. The nightly scan promotes its KBs to
  `DESKTOP-HKE05KI` (above).
- `DESKTOP-HKE05KI` has nothing after it; its run ends at phase 7.

### If a phase fails

| Fails in | State it leaves | Then |
| --- | --- | --- |
| 0, 1 | nothing changed | fix the cause; re-arm |
| 2 | some apps moved, host untouched | the failed app goes back to its home site (both sides are up); the others stay or go back, owner's call |
| 3 | apps on the other site, host partly drained | unpause the tasks, start WSL; fail back |
| 4, 5 | apps on the other site, host patched or half patched | apps stay on the other site. Page. Roll back the KB (below) or fix forward, then rerun from phase 5 |
| 6 | host fine, its cluster isn't | apps stay on the other site; fix the cluster, rerun from 6 |
| 7 | some apps back, some not | the run is safe to repeat; it only moves apps not yet home |

The point of failing over first: a bad patch costs the patched site, and
key apps carry on at the other one until it's fixed.

## What happens to each app

Tiers as in [multi-site.md](../wsl/multi-site.md#apps).

| Tier | When its home host is patched |
| --- | --- |
| Warm (Mealie, Home Assistant, Firefly, storefront API) | planned switchover to the other site in phase 2, back in phase 7. Home Assistant runs without Z-Wave meanwhile |
| Moves, state per site (Frigate, mosquitto) | scaled over in phase 2 without the data steps, back in phase 7. Frigate detects on the RTX 3080 on `WSL-02` |
| Stateless (sites, playwright-mcp) | scaled over in phase 2, back in phase 7 |
| Cold (media stack, Immich) | stops with the host in phase 3, starts with it in phase 6 |
| Pinned (zwave-js-ui: Z-Stick) | down for the window, announced in phase 2; accepted |
| Not placed (monitoring, the OpenClaw gateway on wsl-01) | down for the window. The other site's Prometheus watches the patched site |

When `WSL-02` is patched, its own apps (wsl-02-primary, mostly cold) stop
with it, and the replicas it holds for wsl-01 pause and catch up
afterwards. Nothing on wsl-01 moves.

## Monitoring and alerts

Rules live on both clusters' Prometheus (each watches the other), because a
host's own cluster is down while it reboots.

| Series | From |
| --- | --- |
| pending updates | windows_exporter `update` collector (exact metric names to confirm after enabling it) |
| `tnwks_windows_patch_window_open` | textfile, written by phases 3 and 6 |
| `tnwks_windows_patch_last_success_timestamp_seconds` | textfile, phase 6 |
| `tnwks_windows_unapproved_updates` | textfile, the nightly drift check: installed updates in neither `approved` nor the host's history at adoption |

| Alert | Fires when |
| --- | --- |
| `WindowsUnplannedReboot` | `windows_system_boot_time_timestamp` changed while no window was open |
| `WindowsUnapprovedUpdateInstalled` | `tnwks_windows_unapproved_updates > 0` |
| `WindowsPatchRunFailed` | the window run failed (the workflow writes it) |
| `WindowsRebootPendingOutsideWindow` | a reboot is pending and no window is open, for 1 hour |
| `WindowsSecurityUpdatesUnapproved` | a security update has been available and unapproved for 21 days |

## Rollback

- Cumulative update: `DISM /Online /Remove-Package /PackageName:<LCU
  package>`. `wusa /uninstall` doesn't work on the combined servicing
  stack plus cumulative packages. Then add the KB to `blocked`.
- Feature update: `DISM /Online /Initiate-OSUninstall` works for 10 days
  after the upgrade. Move `target_release` back in the same PR.
- WSL: `wsl --update` can't go back. Reinstall the previous MSIX from the
  WSL GitHub release, then pin `wsl_version`.

## Dependencies

Patching with failover needs these from the second-cluster project:

- Cross-site Ansible: sshd on each host's LAN address for the other host,
  `wsl_ci_access` widened, `ci_runner` pointing at the other site.
- Runners on both clusters.
- The multi-site network and mesh ([multi-site.md](../wsl/multi-site.md)):
  WireGuard and BGP to the router, Cluster Mesh, global services, the
  shared VIPs. wsl-01 has to be on Cilium for this.
- Per-app CNPG clusters with replica clusters on the other site, weekly
  kopiur restores, `site-overrides` in both clusters' Flux, and `task
  failover` with its first Mealie drill.
- The GPU detector service on `WSL-02`, before Frigate can move.
- Cross-site monitoring: each cluster's Prometheus watches the other.

Until `task failover` exists, the window run can patch with `failover:
none`, which takes every app down for the window. That's acceptable for
`WSL-02`, which runs nothing critical, and for wsl-01 only when the owner
chooses it.

## Build order

1. `WSL-02` to 25H2 now, before its cluster exists: it's out of servicing,
   and a feature update costs nothing on an empty host. It also puts both
   hosts on the same KBs.
2. Role `windows_updates` (the brakes) and the `update` collector on both
   hosts, plus `WindowsUnplannedReboot` and
   `WindowsUnapprovedUpdateInstalled`. Watch one Patch Tuesday to prove the
   brakes hold.
3. `patches.yml` and the nightly scan opening approval PRs.
4. `windows-patch.yml` phases 0, 1, 3 to 6 with `failover: none`, the arm
   and the lock. First runs on `WSL-02`.
5. Phases 2 and 7 once `task failover` and its drill are done. The first
   wsl-01 window doubles as the second failover drill; record the RTO in
   [disaster-recovery.md](disaster-recovery.md).
6. Promotion PRs.

## Open questions

- Does 25H2's update orchestrator honour `NoAutoUpdate`, or does it still
  install and schedule reboots? Watch one cycle with the alerts on.
- Does `WindowsStore\AutoDownload` work on Pro? How is WSL 3.x updated:
  Store, Windows Update or inbox?
- `win_updates` over SSH: does it need `become`, and how does
  `accept_list` treat combined SSU plus LCU packages?
- Arm check: confirm a GitHub Environment approval leaves a deployment the
  window run can read with `deployments: read`.
- `WSL-02` on wifi: about 100 Mbps of camera streams lands on it while
  Frigate is failed over. Wire it, or give Frigate a lighter profile there
  ([multi-site.md](../wsl/multi-site.md#router-capacity)).
- How long is a window? Set the silence and the outage notice from the
  Mealie drill's RTO times the number of key apps.
- Notifications: the OpenClaw gateway is down while wsl-01 patches, so the
  workflow has to notify on its own (GitHub, or a bot token in the CI
  secrets).
