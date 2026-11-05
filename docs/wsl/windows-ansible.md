# Managing the Windows host with Ansible

The Windows hosts under the WSL clusters (DESKTOP-HKE05KI, and WSL-02 for the
second cluster) are configured from this repo with Ansible over their built-in
OpenSSH server. A host without a Coral, a Z-Stick or a cluster to relay to
skips those roles ([ansible.md](../operations/ansible.md#adding-a-second-windows-host)). Goal: every host
change is declarative, idempotent and reviewable, with a `--check --diff`
run that shows drift.

## What it manages

| Role (tag)                                  | What                                                                                                   |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `windows_coral_detector` (`coral-detector`) | the [Coral detector](coral-detector.md) services, driver, firewall rules                               |
| `windows_monitoring` (`monitoring`)         | windows_exporter (MSI, `config.yaml` collectors, service), smartmontools, smartctl_exporter and its task, firewall rules |
| `windows_udp_relay` (`udp-relay`)           | `Start-TnwksUdpRelay.ps1`, the relay tasks `tnwks-udp-relay` (2055) and `tnwks-udp-relay-syslog` (6001), their firewall rules ([lan-exposure.md](lan-exposure.md)) |
| `windows_lan_bridge` (`lan-bridge`)         | `Sync-TnwksLanBridge.ps1` and its `ports.json` (80, 443, 1883, 8080, 2222, 18789), the task `tnwks-lan-bridge-sync`, the firewall rules `tnwks-lan-bridge-<port>`; the script keeps the portproxy entries pointed at WSL at run time |
| `windows_usb_attach` (`usb-attach`)         | usbipd-win (pinned MSI), the forced bind of the Z-Stick, `Sync-TnwksUsbAttach.ps1` and the task `tnwks-usb-attach` (Z-Stick attach plus worker-2 restart, [usb-passthrough.md](usb-passthrough.md)) |
| `windows_wsl_maintenance` (`wsl-maintenance`) | `Watch-TnwksWsl.ps1` and the task `tnwks-wsl-watchdog` (every 5 min), `Compact-WslVhdx.ps1` and the on-demand task `tnwks-vhdx-compact` |
| `windows_sshd` (`sshd`)                     | check only by default: the OpenSSH server as `Install-TnwksWindowsSshd.ps1` sets it up ([below](#sshd-bootstrap-once-and-after-a-wsl-subnet-change)) |
| `windows_host_settings` (`host-settings`)   | check only: `.wslconfig`, Ethernet power settings, Wi-Fi off, power plan ([below](#check-only-settings)) |

Every role is idempotent: on the live host, the first apply of each role
adopted what was there with only the changes listed in its PR, and
`--check` from `main` reports `changed=0`. A nightly check reports drift to
Prometheus ([below](#nightly-drift-check)).

Run one role with `task wsl-host:windows-check -- --tags <tag>` (or
`windows-apply`).

Why Ansible: Windows over SSH has been official since ansible-core 2.18,
WinRM stays off, the playbooks live next to the existing ones, SOPS works
for host vars, and check mode gives a drift report. PowerShell DSC v3
(Microsoft's direction, with WinGet configure for client setup) is weak on
scheduled tasks, firewall rules and portproxy and has no SOPS story.

## Pieces

|                                                                 |                                                                          |
| --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `infrastructure/ansible/roles/windows_sshd/files/Install-TnwksWindowsSshd.ps1` | sshd bootstrap (`task wsl-host:sshd`) and the role `windows_sshd`'s check |
| `infrastructure/ansible/inventory/hosts.yml`                    | the host, in `windows_hosts` and its `site_<name>` group                 |
| `infrastructure/ansible/inventory/group_vars/windows_hosts.yml` | connection settings shared by every Windows host                         |
| `infrastructure/ansible/inventory/host_vars/<host>/`            | `ansible_host`, `coral_detector_devices`; `ansible_user` in SOPS         |
| `infrastructure/ansible/inventory/known_hosts`                  | the sshd host key, pinned                                                |
| `infrastructure/ansible/playbooks/windows-host.yml`             | controller cache play, then the Windows roles                            |
| `infrastructure/ansible/playbooks/library/`                     | `tnwks_win_scheduled_task`, scheduled tasks as whole task XML            |
| `.taskfiles/WslHostTasks.yml`                                   | `wsl-host:windows-check`, `windows-apply`, one task per role             |

**Controller.** Every playbook runs on the one pinned ansible-core in
`infrastructure/ansible/pyproject.toml` and `uv.lock`, with the collections
in `requirements.yml` (`task ansible:init`); [ansible.md](../operations/ansible.md) has
the layout.

## sshd bootstrap (once, and after a WSL subnet change)

```bash
task wsl-host:sshd        # UAC prompt; generates ~/.ssh/id_ed25519_winhost if missing
task wsl-host:sshd-check  # drift report, exit 2 on drift
```

The script (idempotent, `-Check` for drift) installs the `OpenSSH.Server`
capability and sets:

- `DefaultShell` = Windows PowerShell (Ansible's `powershell` shell type).
- `sshd_config`: `ListenAddress` = the vEthernet (WSL) address, read from the
  adapter at run time (172.26.80.1); `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`,
  `AllowUsers <admin>`, no agent or TCP forwarding.
- `administrators_authorized_keys` = the controllers' Ansible keys
  (`windows_sshd_authorized_keys` in `group_vars/windows_hosts.yml`; the
  bootstrap writes the one in `~/.ssh/id_ed25519_winhost.pub`), ACL
  Administrators and SYSTEM only (sshd ignores the file otherwise).
- Firewall: `tnwks-sshd (WSL)` admits tcp/22 to `sshd.exe` from the WSL subnet
  only. The capability's own `OpenSSH-Server-In-TCP` rule (any address) is
  removed.
- Service `sshd`: Automatic (delayed start), restart on failure after 30s,
  60s, then every 60s, also on non-crash failures.

The vEthernet (WSL) adapter only exists while WSL runs. After a host boot,
sshd can start before its ListenAddress exists, exits with "Cannot bind any
address", and the failure actions restart it until the address is there
(tested 2026-11-03 with an unassigned ListenAddress: the service manager
restarted sshd 30s later). Since Ansible itself runs inside WSL, sshd is
always back by the time anything needs it. The sessions get the admin's full
token (`High Mandatory Level`), so no `become` is needed
(`ansible_become: false` in `group_vars/windows_hosts.yml`).

Verified 2026-11-03: key login works, a password login is refused
(`Permission denied (publickey)`), and `netstat` shows sshd only on
`172.26.80.1:22` (the `127.0.0.1:22` listener is `wslrelay.exe` mirroring
WSL's own sshd).

**Afterwards, the role `windows_sshd`** runs the same script through
`win_powershell` on every playbook run, with `-Check` unless
`-e windows_sshd_apply=true`. Drift fails the run (and so the nightly check)
with the script's `DRIFT` lines. A normal apply never fixes it, because
rewriting `sshd_config` restarts sshd under the Ansible session. Fix it with
`task wsl-host:sshd` (UAC prompt), or with
`task wsl-host:windows-apply -- --tags sshd -e windows_sshd_apply=true` when a
dropped session is acceptable. The `sshd_config` header still names the
script's old path (`infrastructure/wsl-host/`); it is part of the file
content, so it changes the next time sshd has to restart anyway.

## Check mode

`task wsl-host:windows-check` runs `--check --diff`. After an apply it must
report `changed=0`; anything else is drift (a hand edit on the host, or a repo
change not applied yet). The controller play (downloads, checksum checks,
the libusb shim build) also runs in check mode, since it only fills
`~/.cache/tnwks-ops/` on the controller. Steps without a native module are
`win_powershell` scripts with `SupportsShouldProcess`, which compute "would
change" without changing anything. On a host that never had the role, a
check run skips the ACL and service-state tasks that need the directory tree
and the service to exist.

## Scheduled tasks

Roles declare each Scheduled Task as the full task XML, in the form
`Export-ScheduledTask` prints it (a template per task), through the module
`playbooks/library/tnwks_win_scheduled_task.ps1`. It compares the template
with the registered task after dropping registration metadata (date, author,
URI, schema version, comments) and turning account names into SIDs, shows the
difference as an XML diff, and registers the template with
`Register-ScheduledTask -Xml` when they differ. `running: true` also starts a
long-running task that isn't running.

Why not `community.windows.win_scheduled_task`: it models the definition as
options, and fields it doesn't model go unchecked. A template is the exact
definition Task Scheduler stores, so adopting a hand-made task is a copy of
its export, and check mode compares every field.

Write templates in full. Task Scheduler fills in defaults
(`DisallowStartIfOnBatteries`, `IdleSettings`, `MultipleInstancesPolicy`,
`UseUnifiedSchedulingEngine`, ...) when it registers a task, so a template that
leaves them out reports a change on every run. To add a task: register it once
by hand or from a draft template, copy `Export-ScheduledTask -TaskName <name>`
into the template, and check that `--check` reports no change. S4U tasks
(desktop user, no stored password) register fine over the SSH session.

## Check-only settings

The role `windows_host_settings` (last in the play) only reads. Its single
assertion fails the run, in check mode and in apply mode alike, when any of
these differ, and lists each difference:

- **`.wslconfig`** (`C:\Users\<desktop user>\.wslconfig`, the template
  `roles/windows_host_settings/templates/wslconfig.j2`, byte for byte). WSL
  reads it only when the VM starts, and `wsl --shutdown` stops the whole
  cluster, so an apply never writes it on its own. To change it, edit the
  template, run `task wsl-host:windows-apply -- --tags host-settings -e
  wslconfig_write=true`, and plan the `wsl --shutdown` for a quiet moment.
  The only edit so far (2026-11-03) replaced the outdated comment about
  re-attaching the Coral after a restart; the settings are unchanged. A host
  without a cluster yet (WSL-02) sets `wslconfig_write: true` in host_vars,
  so CI writes it there and WSL picks it up at its next restart.
- **Ethernet** (`host_settings_ethernet`, the Realtek 2.5GbE): power
  management "Allow the computer to turn off this device" off, and the
  advanced properties `*EEE`, `AdvancedEEE`, `EnableGreenEthernet` and
  `PowerSavingMode` at 0. Empty on a host on Wi-Fi (WSL-02): not checked.
- **Wi-Fi adapter disabled** (`host_settings_disabled_adapters`), so Windows
  never fails over to Wi-Fi (which resets the WSL vNIC). Empty on WSL-02,
  which stays on Wi-Fi.
- **Power plan** High performance (`8c5e7fda-...`).

Why Ansible doesn't fix these: setting an adapter property restarts the
adapter, which drops every camera stream for several seconds (and once left
the Reolink's RTSP server hung until a power cycle). Fix drift by hand at a
quiet moment, for example
`Set-NetAdapterAdvancedProperty -Name Ethernet -RegistryKeyword EnableGreenEthernet -RegistryValue 0`,
`$pm = Get-NetAdapterPowerManagement -Name Ethernet; $pm.AllowComputerToTurnOffDevice = 'Disabled'; $pm | Set-NetAdapterPowerManagement`,
`Disable-NetAdapter -Name Wi-Fi`, `powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`.

## Nightly drift check

The scheduled run of `.github/workflows/ansible-hosts.yaml` (12:30 UTC, on
each site's `tnwks-ansible-<cluster>` runner, [ansible.md](../operations/ansible.md#ci))
checks `main`:

1. `ansible-playbook windows-host.yml wsl-distro.yml --check --diff
   --limit site_<name>,localhost`, so the WSL distro is covered too. The job
   summary lists the hosts' recap counts and the names of the tasks that
   would change or failed; the full output, with the diffs, is the run's
   `ansible-<site>-log` artifact, age-encrypted to the owner's key.
2. Writes the counts to the site's Windows host:
   `C:\Program Files\windows_exporter\textfile_inputs\tnwks_windows_drift_check.prom`
   (windows_exporter's `textfile` collector):
   `tnwks_windows_drift_check_{timestamp_seconds,exit_code,duration_seconds}` and,
   per host (`host` label), `tnwks_windows_drift_check_{changed,failed,unreachable}`.

`task wsl-host:ci-check` runs the same check on demand (without writing the
metrics). Until 2026-11 a systemd timer in the distro ran it; the role
`wsl_drift_check` now removes that timer.

Prometheus scrapes them with the `windows-exporter` job. Alerts in
`kube-prometheus-stack/addons/alerts/windows.yaml`:

- `WindowsHostConfigDrift` (warning): the last check had changes, failures
  (including the check-only assertions above), was unreachable, or exited
  non-zero. Read the run's summary (and the encrypted log for the diffs),
  then fix the host or the repo.
- `WindowsHostDriftCheckStale` (warning): no result for 30 hours, or never
  while windows_exporter is up. The schedule, the runner, its SSH key login
  or the textfile collector is broken.

The off-site healthchecks.io checks are deliberately limited to the router
and the monitoring stack ([external-monitoring.md](../operations/external-monitoring.md)),
so this uses Prometheus.

## Next migrations

Host config that still lives outside this playbook, roughly in order:

1. Done: monitoring agents (role `windows_monitoring`; it replaced
   `Install-TnwksWindowsMonitoring.ps1`, which is removed).
2. Done: UDP relays (role `windows_udp_relay`, replacing
   `Register-TnwksUdpRelays.ps1` and `Register-TnwksUdpRelayTask.ps1`).
3. Done: LAN bridge Windows half (role `windows_lan_bridge`, replacing
   `Register-TnwksLanBridgeTask.ps1`).
4. Done: USB attach for the Z-Stick (role `windows_usb_attach`). The script
   came from the host into the repo; its Coral handling is off by default.
5. Done: WSL watchdog and vhdx compaction (role `windows_wsl_maintenance`).
   The watchdog no longer tries to attach the Coral.
6. Done (check only): `.wslconfig`.
7. Done (check only): NIC and power settings.

All the host config listed when this started is now in the playbook. Tasks
that call `wsl.exe` run as the desktop user (S4U, `windows_desktop_user`),
not SYSTEM.

## Cleanup

Done on 2026-11-03 (backups of the task XML, rule settings and script in
`C:\ProgramData\tnwks-cleanup-backup\2026-11-03`):

- Removed the duplicate `WSL2 SSH` tcp/2222 rule (one identical rule kept),
  the stale `WSL2 litellm` tcp/4000 rule (nothing listened, no portproxy),
  the `StartWSL` task (SYSTEM's `wsl.exe` failed at every boot with
  0xFFFFFFFF; the `tnwks-wsl-watchdog` boot trigger is what starts the
  distro) and the disabled `AttachUSBToWSL` task with its script.
- The roles removed the old Register scripts, their `.bak-*` copies and
  one-off verification scripts from `C:\ProgramData\tnwks-*`.

Since then, also in the playbook:

- The tcp/2222 (WSL sshd) and tcp/18789 (OpenClaw gateway) forwards are ports
  of `windows_lan_bridge`: the sync script keeps their portproxy entries on
  the WSL address, and `tnwks-lan-bridge-2222` / `-18789` replace the
  hand-made `WSL2 SSH` and `OpenClaw Gateway 18789` rules. They still admit
  any address, as before; scoping them is a `remote:` on their
  `lan_bridge_tcp_ports` entry.
- usbipd-win is pinned (`usb_attach_usbipd_version`, MSI checked by SHA-256)
  and the Z-Stick's `usbipd bind --force` is enforced (`windows_usb_attach`).
