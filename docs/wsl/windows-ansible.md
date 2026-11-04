# Managing the Windows host with Ansible

The Windows host under the WSL cluster (DESKTOP-HKE05KI) is configured from
this repo with Ansible over its built-in OpenSSH server. Goal: every host
change is declarative, idempotent and reviewable, with a `--check --diff`
run that shows drift.

## What it manages

| Role (tag)                                  | What                                                                                                   |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `windows_coral_detector` (`coral-detector`) | the [Coral detector](coral-detector.md) services, driver, firewall rules                               |
| `windows_monitoring` (`monitoring`)         | windows_exporter (MSI, `config.yaml` collectors, service), smartmontools, smartctl_exporter and its task, firewall rules |
| `windows_udp_relay` (`udp-relay`)           | `Start-TnwksUdpRelay.ps1`, the relay tasks `tnwks-udp-relay` (2055) and `tnwks-udp-relay-syslog` (6001), their firewall rules ([lan-exposure.md](lan-exposure.md)) |
| `windows_lan_bridge` (`lan-bridge`)         | `Sync-TnwksLanBridge.ps1` and its `ports.json`, the task `tnwks-lan-bridge-sync`, the firewall rules `tnwks-lan-bridge-<port>`; the script keeps the portproxy entries pointed at WSL at run time |
| `windows_usb_attach` (`usb-attach`)         | `Sync-TnwksUsbAttach.ps1` and the task `tnwks-usb-attach` (Z-Stick attach plus worker-2 restart, [usb-passthrough.md](usb-passthrough.md)) |
| `windows_wsl_maintenance` (`wsl-maintenance`) | `Watch-TnwksWsl.ps1` and the task `tnwks-wsl-watchdog` (every 5 min), `Compact-WslVhdx.ps1` and the on-demand task `tnwks-vhdx-compact` |

Run one role with `task wsl-host:windows-check -- --tags <tag>` (or
`windows-apply`).

Why Ansible: Windows over SSH has been official since ansible-core 2.18,
WinRM stays off, the playbooks live next to the existing ones, SOPS works
for host vars, and check mode gives a drift report. PowerShell DSC v3
(Microsoft's direction, with WinGet configure for client setup) is weak on
scheduled tasks, firewall rules and portproxy and has no SOPS story.

## Pieces

|                                                        |                                                                          |
| ------------------------------------------------------ | ------------------------------------------------------------------------ |
| `infrastructure/wsl-host/Install-TnwksWindowsSshd.ps1` | sshd bootstrap (`task wsl-host:sshd`, drift: `task wsl-host:sshd-check`) |
| `infrastructure/ansible/requirements-windows.txt`      | the pinned ansible-core, run through `uvx`                               |
| `infrastructure/ansible/requirements.yml`              | `ansible.windows`, `community.windows` (pinned)                          |
| `infrastructure/ansible/inventory/windows.yml`         | the host, its connection settings, `coral_detector_devices`              |
| `infrastructure/ansible/playbooks/windows-host.yml`    | controller cache play, then the Windows roles                            |
| `infrastructure/ansible/playbooks/library/`            | `tnwks_win_scheduled_task`, scheduled tasks as whole task XML            |
| `.taskfiles/WslHostTasks.yml`                          | `wsl-host:windows-init`, `windows-check`, `windows-apply`                |

**Controller.** The apt ansible in WSL is 2.16, too old for Windows over
SSH. The Windows tasks run the ansible-core from `requirements-windows.txt`
through `uvx`, with collections in `~/.ansible/collections-windows`
(`ANSIBLE_COLLECTIONS_PATH`), so the apt ansible and its collections that run
the other playbooks are untouched. The tasks also set
`ANSIBLE_STDOUT_CALLBACK=default` with `result_format=yaml`, because
`ansible.cfg`'s `yaml` callback is gone from current community.general.

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
- `administrators_authorized_keys` = the one Ansible key, ACL Administrators
  and SYSTEM only (sshd ignores the file otherwise).
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
token (`High Mandatory Level`), so no `become` is needed; the inventory sets
`ansible_become: false` against `ansible.cfg`'s `become = True`.

Verified 2026-11-03: key login works, a password login is refused
(`Permission denied (publickey)`), and `netstat` shows sshd only on
`172.26.80.1:22` (the `127.0.0.1:22` listener is `wslrelay.exe` mirroring
WSL's own sshd).

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

A scheduled `--check` (a WSL systemd timer, with the result pushed to a
healthchecks.io check or a Prometheus textfile) is the obvious next step for
continuous drift detection.

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
6. **`.wslconfig`** (memory cap, networking).
7. **NIC settings** (Ethernet power management, Wi-Fi radio off).

Tasks that call `wsl.exe` must run as the desktop user (S4U), not SYSTEM;
`ansible.windows.win_scheduled_task` can express that.

## Cleanup candidates (not changed yet)

- Firewall rules open to any address: two `WSL2 SSH` rules (tcp 2222), a
  stale `WSL2 litellm` rule (4000) and the OpenClaw gateway rule (18789).
- netsh portproxy entries for 2222 and 18789.
- Scheduled task `StartWSL` (SYSTEM, last result 0xFFFFFFFF, likely broken)
  and the disabled legacy `AttachUSBToWSL`.
- usbipd-win 4.3.0 is installed by hand; pin it once the USB attach moves here.
