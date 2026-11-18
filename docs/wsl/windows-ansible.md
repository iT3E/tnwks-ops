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
| `windows_observability_storage` (`observability-storage`) | The 20Gi Prometheus disk `C:\WSL\observability.vhdx` and the task `tnwks-observability-mount` (site 1, [observability-implementation.md](../operations/observability-implementation.md#site-1-prometheus-volume)) |
| `windows_wsl_rehearsal` (`wsl-rehearsal`) | hosts with `wsl_rehearsal` (WSL-02): a temporary standard account `tnwks-rehearsal` with a distro of its own and five tasks, for the rehearsal of the account change ([below](#the-rehearsal-of-the-account-change)) |
| `windows_wsl_move_rehearsal` (`wsl-move-rehearsal`) | hosts with `wsl_move_rehearsal` (none until the next rehearsal, which turns it on for WSL-02 only): two temporary standard accounts, a small distro, and the scripts of the move window, run phase by phase by hand ([below](#the-rehearsal-of-the-move)) |
| `windows_wsl_move` (`wsl-move`) | hosts with `wsl_move` (none until the owner approves it for a host): step 3 of the account change, the account that owns the distro, its folder, and the tasks of the move window ([below](#the-move-of-the-distro)) |
| `windows_power` (`power`)                   | hosts with `power_no_sleep` (WSL-02): standby (S1-S3) not allowed on the active scheme, plugged in or on battery, and hibernation off, so neither a program nor the Start menu can sleep the host. Skipped elsewhere; how to undo it is in the role's defaults |
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

## sshd bootstrap (once, after a WSL subnet change, and after a feature update)

```bash
task wsl-host:sshd        # UAC prompt; generates ~/.ssh/id_ed25519_winhost if missing
task wsl-host:sshd-check  # drift report, exit 2 on drift
```

A Windows feature update (WSL-02, 23H2 -> 25H2, 2026-11-05) uninstalls the
`OpenSSH.Server` capability and with it the `sshd` service; the config, the
keys file and the firewall rule stay. Re-run the bootstrap (it reinstalls the
capability and changes nothing else). From another host, the script and
`invoke-elevated.sh` have to be copied into that host's distro first: the UAC
prompt shows on its own desktop.

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
(`ansible_become: false` in `group_vars/windows_hosts.yml`). The one
exception is the tasks of the rehearsal that act on a task of its account
([below](#the-rehearsal-of-the-account-change)).

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
and the service to exist. A check run of the rehearsal's removal stops no
task, so the profile of the account can stay loaded: the profile step
reports the change and does not wait for the unload.

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
(desktop user, no stored password) register fine over the SSH session when
their principal is the account of the session. For a different account, see
the option `s4u_user` ([below](#the-rehearsal-of-the-account-change)).

The option `keep_task_enabled_state` (off by default) is for a task that
another step disables and enables, such as a start task of the move window.
With it on, the comparison leaves out `Settings/Enabled`, and a registration
of a task that exists keeps the state that the task has. A new task takes the
state of its template. No role but the rehearsal of the move sets it, so the
tasks of the other roles compare and register as before.

## Start WSL with no elevation

Per host, `wsl_start_unelevated` (`group_vars/windows_hosts.yml`, on in
`host_vars/WSL-02` first) makes each task that starts a process in the
distro run with no elevation, and moves each step that needs admin rights to
a task that runs no `wsl.exe` (Design Doc 0004, section 5.9).

**The rule: no elevated client ever creates the WSL VM or starts a process
in a distro.** The `wsl.exe` commands that do neither are `--shutdown`,
`--terminate`, `--list`, `--status`, `--version` and `--update`
(`--update` goes to the upgrade of the package, with no session and no
`_CreateVm`; no task calls it, so the test does not list it). A start of a distro,
`--mount`, and `--manage` (resize, compact, set-version) create the VM when
none runs (the callers of `_CreateVm` in
`src/windows/service/exe/LxssUserSession.cpp`, microsoft/WSL). A test
(`security/tests/test_wsl_start_unelevated.py`) checks each elevated task of
the user, and the scripts that it runs, against that list.

| Task | With the switch on |
| --- | --- |
| `tnwks-wsl-keepalive`, `tnwks-wsl-watchdog`, the UDP relays | The desktop user, no elevation. They log to a `log` directory where the account may make and change files, apart from the scripts |
| `tnwks-wsl-service` (new) | SYSTEM, each 5 minutes and at boot: the part of the watchdog that needs admin rights (the WSL service, the keepalive task). It runs no `wsl.exe`, and logs to `service-log`, which only SYSTEM and the Administrators may change |
| `tnwks-lan-bridge-ip` (new) | The desktop user, no elevation: writes the address of the WSL VM to `C:\ProgramData\tnwks-lan-bridge\wsl-ip\address.json` |
| `tnwks-lan-bridge-sync` | SYSTEM, with that file and no `wsl.exe`. It opens the file once, refuses a link on the way, and takes only an address in the subnet of the vEthernet (WSL) adapter |
| `tnwks-vhdx-compact` | Still elevated for diskpart, with `-NoDistroStart`: it runs only `wsl --shutdown` and `wsl --list`, and the keepalive starts WSL first after it |

Each directory where an account with no elevation writes is made by the
module `tnwks_win_dir_acl` (`playbooks/library`): no inherited rule, the
Administrators own it, and the account may make and change files but not
delete the directory or make one in it. The module stops when the path is a
link or a junction. SYSTEM's `wsl.exe` can not see the distro of the user,
so a SYSTEM task can not start a process in it. The USB attach and the
monitoring volume have no part with no elevation yet, and the play refuses
the switch on a host that runs them (the first task of the play, with each
tag).

**The folders of the roles.** On each host, with the switch on or off, each
folder of a role under `C:\ProgramData` gets its rules from the same module,
with no writer: no inherited rule, the Administrators own it, SYSTEM and the
Administrators have full control, and Users may read. That is the folder of
the LAN bridge, the watchdog, the compaction, the UDP relays, the USB attach
and the monitoring volume. At each run, each file and folder below takes the
owner Administrators and only the rules that it inherits, unless it has them
already (an owner of SYSTEM stays). The log and address folders above keep
their own rules, and the module does not read them. A link or a junction
below stops the module before it changes an item. The folders of the host
check and of the detector set the same rules in their own roles. A folder
that no role owns gets the rules for SYSTEM and the Administrators only from
`windows_locked_dirs` (per host, `readers: none`: Users get no rule).
A test (`security/tests/test_windows_role_folders.py`) reads the rules of
each folder, and which task writes where.

**The services take the change at the next start of WSL**, the start step
below. The merge and the apply of the switch leave the plays green: the last
task of `wsl_host_check` (the write test) can fail the distro play only when
`wsl_start_unelevated_done` is on too. The security result of the check
shows the write test from the first run, with or without it.

### Before the merge of the switch for a host

Read these on the host, as the desktop user. Each is a condition of the
change; if one is not met, stop and say which.

- User Account Control is on: `(Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System).EnableLUA` is `1`.
- The account is not the built-in Administrator: `[Security.Principal.WindowsIdentity]::GetCurrent().User.Value` does not end in `-500`.
- A PowerShell window with no elevation runs at medium integrity: `whoami /groups | Select-String 'Mandatory Label'` shows `Medium Mandatory Level`.
- One distro only, and no Docker Desktop: `wsl --list --verbose` lists only `Ubuntu`, and `Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue` gives nothing. Another distro or Docker Desktop could start WSL first.
- Developer mode is off: `(Get-ItemProperty HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock -ErrorAction SilentlyContinue).AllowDevelopmentWithoutDevLicense` is `0` or empty. With it on, an account may make a symbolic link with no privilege.
- The inherited rules of the three directories of the tasks: `(Get-Acl C:\ProgramData\tnwks-watchdog).Access`, the same for `C:\ProgramData\tnwks-lan-bridge` and `C:\ProgramData\tnwks-udp-relay`. Note who may make files there.
- The `wsl.exe` processes and their start times, to compare after the apply: `Get-Process wsl -ErrorAction SilentlyContinue | Format-Table Id, StartTime`.
- None of the four new directories exists yet. The module sets the rules of a directory that is already there, but does not look at what it holds. This prints `False` four times:

  ```powershell
  'C:\ProgramData\tnwks-watchdog\log', 'C:\ProgramData\tnwks-watchdog\service-log', 'C:\ProgramData\tnwks-udp-relay\log', 'C:\ProgramData\tnwks-lan-bridge\wsl-ip' | ForEach-Object { Test-Path -LiteralPath $_ }
  ```

  If one prints `True`, read its owner (`(Get-Acl <path>).Owner`) and its files (`Get-ChildItem -Force <path>`) first, and remove what the role did not make.
- No other elevated task or startup command starts WSL. In an elevated window, each command gives nothing. The first leaves out the tasks of the roles (`tnwks-*`, which the switch changes) and SYSTEM, whose `wsl.exe` can not see the distro of the user:

  ```powershell
  Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and $_.Principal.RunLevel -eq 'Highest' -and $_.TaskName -notlike 'tnwks-*' -and $_.Principal.UserId -notin 'SYSTEM', 'S-1-5-18' -and (($_.Actions.Execute + ' ' + $_.Actions.Arguments) -match 'wsl|bash\.exe|ubuntu') } | Format-Table TaskName, TaskPath
  Get-CimInstance Win32_StartupCommand | Where-Object Command -match 'wsl|bash|ubuntu|docker' | Format-Table Name, Command, User
  ```

  These read only the action line of a task and the command of a startup entry: a script that calls `wsl.exe` inside does not show. For the scripts of the roles, the test in the repository reads them.

### The start step (site 2 first)

**Who runs it:** the owner, at the console of the host or over remote
desktop, as the desktop user. No play can do it: Ansible reaches `WSL-02`
only through its distro (the jump in `host_vars/WSL-02`), so a play loses
its way to the host when WSL stops. The dispatch of `ansible-hosts` takes
only a mode (check, drift or apply), with no tag, and no workflow or play is
added for this step.

**When:** at a time when the apps of the cluster on the host may be down.
This step moves no app. The cluster is down from step 1 to the end of
step 5: about 10 minutes, at most about 25 (an estimate from the parts
below, not a measured time). The whole runbook takes about 30 minutes with
its checks. Merge nothing under `infrastructure/ansible` during the window:
an apply registers the paused tasks again, so enables them, and starts the
keepalive. That includes the merges of Renovate: it merges its own pull
requests for `infrastructure/ansible/requirements.yml` when they are green,
at any hour. To hold each apply, disable the workflow before step 1:

```sh
gh workflow disable ansible-hosts.yaml -R iT3E/tnwks-ops
gh run list -R iT3E/tnwks-ops --workflow ansible-hosts.yaml --json status,databaseId -q '.[] | select(.status != "completed")'
```

The second command must print nothing. A disable does not cancel a run
that already exists: an apply job for site 2 that was queued before it waits
for the runner, and applies when the cluster comes back at step 5. If a run
shows, wait for it to end before step 1. After step 6, enable the workflow:

```sh
gh workflow enable ansible-hosts.yaml -R iT3E/tnwks-ops
```

While the workflow is off, GitHub drops its events; it does not queue
them:

- **A merge under `infrastructure/ansible`** gets no apply. Dispatch
  `ansible-hosts` with the mode `apply` after the enable. The dispatch has
  no site input: it applies each site.
- **A pull request under `infrastructure/ansible`** pushed in the window
  gets no check run until its next push, and the merge helper does not see
  the check that is missing. Push to it again before a merge.
- **The nightly drift run** (12:30 UTC) is skipped, and the alert for a
  stale drift result fires after 30 hours with no run. If the window
  crossed 12:30 UTC, dispatch the mode `drift` after the enable. Better,
  keep the window away from 12:30 UTC: with the workflow on, that run fails
  red with WSL down.

**Never leave the host in the state after step 2 across a restart of
Windows**: with the tasks disabled, nothing starts WSL at boot. To stop
there, enable the tasks and start the keepalive (step 5).

**Alerts during the window**, on site 1: `PeerSiteUnreachable` after 10
minutes, `SecurityHostUnreachable` after 5, maybe `WindowsHostDown` (if
site 1 reaches the host of site 2 through the tunnel in its distro), and
`SecurityResultLate` later if the window runs long. Before step 1, add a
silence of one hour in the Alertmanager of site 1 with
`alertname=~"PeerSiteUnreachable|SecurityHostUnreachable|WindowsHostDown"`,
and expire it after step 6.

The commands are for Windows PowerShell 5.1, the PowerShell of "Run as
administrator". It does not escape a double quote inside an argument of a
native program, so no argument for `wsl` here holds one: a command for `sh
-c` with quotes inside is a PowerShell string in double quotes with single
quotes inside, never the other way round.

Open two windows on the host, and keep to them:

- **Window A**, PowerShell with "Run as administrator": the
  `ScheduledTask` and `Get-Process` commands below. It runs no `wsl`
  command that creates the VM or starts a process (only `--shutdown` and
  `--list`, as a fallback).
- **Window B**, PowerShell with no elevation: each `wsl` command. Check it
  first; this must print `False`:

  ```powershell
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  ```

The distro on `WSL-02` is `Ubuntu` (`wsl_keepalive_distro`). On another
host, use the name from its host_vars.

0. **Before.** The pull request that turns the switch on for the host is
   merged and its apply is green. In window A:

   ```powershell
   Get-ScheduledTask -TaskName tnwks-wsl-keepalive, tnwks-wsl-watchdog, tnwks-wsl-service, tnwks-lan-bridge-ip |
     Format-Table TaskName, State, @{ n = 'RunLevel'; e = { $_.Principal.RunLevel } }, @{ n = 'User'; e = { $_.Principal.UserId } }
   Get-Process wsl -ErrorAction SilentlyContinue | Format-Table Id, StartTime
   ```

   Check: the keepalive, the watchdog and `tnwks-lan-bridge-ip` show
   `Limited`, and `tnwks-wsl-service` shows `SYSTEM`. Compare the `wsl`
   processes with the list from before the merge. If the tasks are not as
   above, stop here: the apply did not reach the host. Nothing is changed
   yet.

   Then the four directories and the log of `tnwks-wsl-service`:

   ```powershell
   'C:\ProgramData\tnwks-watchdog\log', 'C:\ProgramData\tnwks-watchdog\service-log', 'C:\ProgramData\tnwks-udp-relay\log', 'C:\ProgramData\tnwks-lan-bridge\wsl-ip' |
     ForEach-Object { $acl = Get-Acl -LiteralPath $_; '{0} protected={1} owner={2}' -f $_, $acl.AreAccessRulesProtected, $acl.Owner }
   Get-Item 'C:\ProgramData\tnwks-watchdog\service-log\watchdog.log' | Format-Table Length, LastWriteTime
   ```

   Check: each directory shows `protected=True owner=BUILTIN\Administrators`,
   and `watchdog.log` in `service-log` grows: run the last line again after
   5 minutes, and its time is newer.

   Then, in window B, the reads as root of what the distro uses under
   `/mnt` (open files, Docker mounts, units, fstab and cron):

   ```powershell
   wsl -d Ubuntu -u root -e sh -c "find /proc/[0-9]*/fd -lname '/mnt/*' -printf '%l\n' 2>/dev/null | sort -u"
   wsl -d Ubuntu -u root -e sh -c "docker ps -q | xargs -r docker inspect -f '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' | grep /mnt/ || true"
   wsl -d Ubuntu -u root -e sh -c 'grep -rls /mnt/ /etc/systemd/system /etc/fstab /etc/cron* /var/spool/cron 2>/dev/null'
   ```

   Check: each path under `/mnt` is one that the desktop user may use with
   no elevation. If one is not, stop here and say which.

1. **Stop the cluster cleanly** (window B, about 2 minutes). The
   keepalive still holds the distro, so WSL does not stop it under the
   nodes. Workers first, then the control plane, each with the 120 seconds
   that Talos may take to shut down (about 30 s, as in
   `talos/wsl/recreate-network.sh`):

   ```powershell
   wsl -d Ubuntu -u root -e docker ps --filter label=talos.owned=true --format '{{.Names}} {{.Status}}'
   wsl -d Ubuntu -u root -e sh -c 'docker stop -t 120 $(docker ps -q --filter label=talos.owned=true --filter name=worker)'
   wsl -d Ubuntu -u root -e sh -c 'docker stop -t 120 $(docker ps -q --filter label=talos.owned=true --filter name=controlplane)'
   wsl -d Ubuntu -u root -e docker ps --filter label=talos.owned=true --format '{{.Names}} {{.Status}}'
   ```

   Check: the first list names each node `Up`, and the last list is empty.
   Keep the names: step 5 starts them again. The distro's BGP sessions
   drop with the nodes. The way back: `docker start` the nodes as in
   step 5.

2. **Pause each task that runs `wsl.exe`**, and the SYSTEM task that
   starts the keepalive (window A, 1 minute):

   ```powershell
   $tasks = @('tnwks-wsl-service', 'tnwks-wsl-watchdog', 'tnwks-wsl-keepalive', 'tnwks-lan-bridge-ip') +
     @((Get-ScheduledTask -TaskName 'tnwks-udp-relay*' -ErrorAction SilentlyContinue).TaskName)
   foreach ($t in $tasks) { Stop-ScheduledTask -TaskName $t; Disable-ScheduledTask -TaskName $t | Out-Null }
   Get-ScheduledTask -TaskName $tasks | Format-Table TaskName, State
   ```

   Check: each task shows `Disabled`. Keep window A open: step 5 uses
   `$tasks`. When the keepalive stops, WSL may stop the distro on its own a
   few seconds later; that is expected. A paused task takes its new
   principal when it runs again. The way back: step 5.

3. **Stop WSL** (window B, about 1 minute). `wsl --shutdown` stops the VM,
   so the next start of a distro creates it again:

   ```powershell
   wsl --shutdown
   wsl --list --running
   Get-Process vmmemWSL, vmmem -ErrorAction SilentlyContinue
   ```

   Check: the list says that no distro runs, and `Get-Process` gives no
   output. A `wsl --terminate` is not enough: it stops the distro and
   leaves the VM. If window B is refused, run the same three commands in
   window A: `--shutdown` and `--list` create no VM and start no process.
   The way back: step 5.

4. **No `wsl.exe` is left** (window A, 1 minute). Close each terminal on
   the host other than windows A and B, elevated or not, and each `wsl`
   session. Then:

   ```powershell
   Get-Process -Name wsl, wslhost, vmmemWSL, vmmem -ErrorAction SilentlyContinue
   ```

   Check: no output. A `wsl.exe` that is left can start WSL with its own
   elevation. Find the window that holds it and close it, or end it with
   `Stop-Process -Id <id>`, and run the check again. The way back: step 5.

5. **Start WSL with no elevation, then the nodes** (windows A and B, about
   5 minutes). The keepalive starts WSL first, as the desktop user with no
   elevation:

   ```powershell
   # Window A
   foreach ($t in $tasks) { Enable-ScheduledTask -TaskName $t | Out-Null }
   Start-ScheduledTask -TaskName tnwks-wsl-keepalive
   ```

   ```powershell
   # Window B, about 30 s later
   wsl --list --running
   wsl -d Ubuntu -u root -e readlink /proc/1/ns/mnt /proc/self/ns/mnt
   wsl -d Ubuntu -u root -e systemctl is-system-running
   wsl -d Ubuntu -u root -e sh -c 'docker start $(docker ps -aq --filter label=talos.owned=true --filter name=controlplane)'
   wsl -d Ubuntu -u root -e sh -c 'docker start $(docker ps -aq --filter label=talos.owned=true --filter name=worker)'
   wsl -d Ubuntu -u root -e docker ps --filter label=talos.owned=true --format '{{.Names}} {{.Status}}'
   wsl -d Ubuntu -u root -e systemctl is-active docker tnwks-frr tnwks-site-routing
   wsl -d Ubuntu -u root -e docker exec tnwks-frr vtysh -c 'show bgp summary'
   ```

   ```powershell
   # Window A: the other tasks, the address of the LAN bridge before its sync
   Start-ScheduledTask -TaskName tnwks-wsl-service
   Start-ScheduledTask -TaskName tnwks-wsl-watchdog
   Start-ScheduledTask -TaskName tnwks-lan-bridge-ip
   Start-Sleep -Seconds 20
   Start-ScheduledTask -TaskName tnwks-lan-bridge-sync
   foreach ($t in $tasks -like 'tnwks-udp-relay*') { Start-ScheduledTask -TaskName $t }
   Get-Content 'C:\ProgramData\tnwks-lan-bridge\wsl-ip\address.json'
   netsh interface portproxy show v4tov4
   ```

   Check:
   - the list names `Ubuntu`, and `readlink` prints two equal lines;
   - systemd says `running` (or `degraded`: then `systemctl --failed`
     names the unit to read);
   - each node is `Up`, and docker and the units of the site routing are
     `active`;
   - each BGP session in `show bgp summary` is established (a number of
     prefixes, not `Active` or `Connect`);
   - the address in the file is the one in each portproxy entry;
   - from the workstation, the nodes of the cluster are `Ready`
     (`kubectl get nodes` with the kubeconfig of that cluster), and its
     Flux Kustomizations are ready;
   - from a LAN machine, the ingress VIP of the cluster answers:
     `curl -sk -o /dev/null -w '%{http_code}\n' https://10.6.0.200/`
     prints an HTTP code, not a time-out.

   If `readlink` prints two lines that differ, the services did not start
   with the keepalive: do steps 2 to 5 again, and look harder at step 4.
   If a node does not come back, `docker logs <name>` in the distro.

6. **Read** (window B, then A, 2 minutes). The service of the check runs
   its write test as root:

   ```powershell
   # Window B
   wsl -d Ubuntu -u root -e systemctl start tnwks-host-check.service
   wsl -d Ubuntu -u root -e journalctl -u tnwks-host-check.service -n 5 --no-pager --output cat
   ```

   ```powershell
   # Window A: the relay brings the result to windows_exporter at once
   Start-ScheduledTask -TaskName tnwks-host-check-relay
   Start-Sleep -Seconds 20
   Select-String -Path 'C:\Program Files\windows_exporter\textfile_inputs\tnwks_security_host_check*.prom' -Pattern 'write_test'
   ```

   Check: the journal says `uid 0: Windows refused the write (errno ...)`;
   `homelab_security_host_write_test_result` is 0 for `uid="0"` and for the
   distro user; `homelab_security_host_write_test_errno` names the
   refusal; `homelab_security_host_write_test_files` is 0. Then expire the
   silence. Only then open the pull request of one line that sets
   `wsl_start_unelevated_done: true` in the host's host_vars. From its
   apply on, the distro play fails when the write test is not refused. If
   the read does not pass, do not open that pull request: do steps 1 to 5
   again, and look harder at step 4.

The done switch also turns on the check `wsl-disk-rules` of the host check
(role windows_host_check, security/README.md). From the apply of its pull
request, the host check reads the owner and the rules of the disk file of
the distro each 6 hours as SYSTEM, and a file that does not have the owner
and the rules of Design Doc 0004 (section 7, row 27), a missing file and a
file that it can not read each fail the check. The file must then have the
rules that the move to the account sets: the account that owns the distro
as owner, and protected rules for SYSTEM, the Administrators and that
account, besides the entries that WSL adds for its VM.

What the done switch does not catch: between step 6 and the merge of its
pull request, the play does not fail on the write test (the security
result still shows it). And the play trusts the switch: a switch set to
on with no start step makes the play fail at the next apply, which is the
signal to do the step.

**The fastest way back at the console**, if WSL misbehaves after step 5:
in window A, `wsl --shutdown`, then `wsl -d Ubuntu -e true`, then
`docker start` the nodes as in step 5 (from window A). That is the state
from before the start step. Then turn the switch off as below.

**The way back.** One pull request sets `wsl_start_unelevated` and
`wsl_start_unelevated_done` off for the host (the play refuses the done
switch on with the start switch off). Its apply makes the tasks elevated
again and removes the new tasks. Then do steps 1 to 5 again; with the
switch off, the keepalive and the watchdog start WSL elevated, as before.

## The rehearsal of the account change

Per host, `wsl_rehearsal` (`group_vars/windows_hosts.yml`, on in
`host_vars/WSL-02` only) adds the role `windows_wsl_rehearsal`: step 0 of
the account change (Design Doc 0004, section 9). It does not touch the
distro, the tasks or the account of today.

| Part | What it is, and who gets what |
| --- | --- |
| The account `tnwks-rehearsal` | A local account, a member of Users only. Its password is random, made on the host, and kept nowhere. The play stops if the account is a member of Administrators |
| The right to log on as a batch job | For `tnwks-rehearsal` only (an S4U task needs it) |
| No other logon, no tile | `tnwks-rehearsal` may not log on at the keyboard, by Remote Desktop or from the network (`SeDenyInteractiveLogonRight`, `SeDenyRemoteInteractiveLogonRight`, `SeDenyNetworkLogonRight`), and the `SpecialAccounts\UserList` value keeps it off the sign-in screen |
| `C:\ProgramData\tnwks-rehearsal` | The scripts and the pinned Alpine root file system. Only SYSTEM and the Administrators may change them |
| `...\log`, `...\disk` | `tnwks-rehearsal` may make and change files: the results of its tasks, and the empty test disk file `mount-test.vhdx` |
| Five tasks `tnwks-rehearsal-<step>` | As `tnwks-rehearsal`, S4U, no elevation, no trigger: `probe`, `import`, `start` (holds the distro with nobody signed in), `write`, `mount` |
| The profile of `tnwks-rehearsal` | Its `.wslconfig` (a memory limit, one processor, no swap, no networking key) and its distro `tnwks-rehearsal` |

The tasks of the account run as `tnwks-rehearsal`, by S4U, not as the
account of the Ansible session. Microsoft's rule for registering tasks
([Security Contexts for Tasks](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks),
"Security for Registering Tasks") asks an administrator for the password
of a different account to register an S4U task for it, and SYSTEM counts as
an administrator there. The account has a random password that nothing
keeps. So `tnwks_win_scheduled_task` with `s4u_user` makes a new random
password on the host, sets it on the account, registers the task with it,
and drops it, once for each task that it must register (none in check mode
and none when the task compares equal). The password never leaves the
host. An S4U task stores no password, so a later reset does not touch the
tasks before it; if one of them ever failed to log on at its start, the
probe and the import say so. Use `s4u_user` only for an account whose
password nobody needs. Each registration after the profile exists resets
the password again, so nothing that needs the account's DPAPI secrets may
live in that profile. The module refuses, and changes nothing, when the
account is a member of Administrators, when its Description is not the
required `s4u_description` (the role passes
`wsl_rehearsal_account_description`), when it is the account of the
module's process, or when it has an interactive, network or remote logon
session open (the account of the Ansible session, or a person signed in).
Each task that
registers, starts or stops a task of the account runs as SYSTEM, in its
own `vars`: `ansible_become: true`,
`ansible_become_method: ansible.builtin.runas` and
`ansible_become_user: SYSTEM`. These are the role's tasks "Tasks of the
temporary account", "The probe makes the profile of the account", "The
import of the distro" and "The start of the distro, with nobody signed
in", and "Run the checks" in the check play. runas takes the
SYSTEM token with the debug privilege of the session and needs no password.
The first use of become starts the Secondary Logon service (`seclogon`)
when it is stopped, a change of the host that the role does not declare.

The role is the last of the play, so a failed step of it stops no other
role. A check run on a host where the account does not exist yet does not
check the tasks that name the account (its rights, the rules of its two
folders, its tasks), and says so.

Run the checks after the apply, as the comment of
`playbooks/windows-rehearsal-check.yml` says. The play reads, and changes
no setting.

**The removal.** `wsl_rehearsal_state: absent` (the default of the role
now) removes each part, in this order:

1. When the account has a profile and something of it is left to stop
   (the start task runs, a process of the account runs, or the list of
   distros in its hive names one), the play stops the start task. A task
   of the account then runs `wsl --terminate`, `wsl --unregister` and
   `wsl --shutdown`. The shutdown stops the VM of that account only: WSL
   keeps one session for each account. No task of the play runs `wsl.exe`.
   With no profile, no task of the account ever ran (the first one makes
   the profile), so it has no hive, no distro and no VM, and the play skips
   this step and the next. With nothing left to stop, an earlier run did
   this step, and the play skips it too: each task of the account loads its
   profile, and the profile must unload before it goes.
   The task of the removal registers as the tasks of the state present do
   (`s4u_user`, as SYSTEM, with a new password of the account). When it
   does not register, the removal stops there: the account and each part
   of the rehearsal stay, and the message says so.
2. The play ends any process of the account that is left.
3. It removes the tasks, the profile (with `.wslconfig` and the hive), the
   batch right and the three deny rights (before the account, so no SID
   with no account stays in the policy), the value that hides the tile, the
   account, the folder (the scripts, the root file system, the test disk
   file and the results), and the file of the write test. The batch right
   goes after the profile: the service checks the new password with a
   batch logon, so while a profile is there, a second run can still do
   step 1. The play waits up to 2 minutes for the profile to unload. When
   it stays loaded, the removal stops there, and the message says what
   stays: run the removal again once the profile unloads, at the latest
   after the next restart of the host. Each run of an S4U task adds a
   reference to the profile of its account that the system does not
   release, so a profile that such a task loaded stays loaded until the
   next restart of the host.

The same check play then reads the exit check, with the SID of the
account read before the removal (`-e wsl_rehearsal_removed_sid=<SID>`): no
account; no holder of the batch right or of a deny right that is the
account or its SID; no value for the tile; no task; no profile, and no
folder of the account in the users folder; and no folder of the rehearsal. It shows the local accounts, the members of Administrators and the
holders of the rights, and a SID of another origin that names no account
as a note.

## The rehearsal of the move

Per host, `wsl_move_rehearsal` (`group_vars/windows_hosts.yml`) adds the
role `windows_wsl_move_rehearsal`. The rehearsal ended, and the switch is
off on every host until the next one, which turns it on in
`host_vars/WSL-02` only. The role is step 0 of the move of a distro to
another account (Design Doc 0004, section 9). It runs the scripts of the window on a small distro of its own, with two
temporary accounts: A in the place of the account that owns the distro
today, and B in the place of the new account. It does not touch the live
distro, its registration, its VM or the account of the host's person.

With the switch on, a run does nothing but check its guards. Each phase
runs only when the lead gives it, in this order, and reads its result:

```sh
cd infrastructure/ansible
uv run ansible-playbook playbooks/windows-host.yml --limit 'localhost,WSL-02' --tags wsl-move-rehearsal \
  -e wsl_move_rehearsal_phase=<phase>
uv run ansible-playbook playbooks/windows-move-rehearsal-check.yml --limit WSL-02
```

The check play checks only a host with the switch on. With the switch off
for every host of the run it fails, as nothing was checked. After a
rehearsal, give the switch for that run only: `-e wsl_move_rehearsal=true`.

Each script that runs in a distro goes to `wsl.exe` as base64 in one fixed
command, and the distro decodes and runs it. Windows PowerShell 5.1 does not
escape a double quote inside an argument to a native program, so shell text
passed as an argument breaks at its first quote. The check step reads each
value by its key, not by its line, and a key that comes twice reads as no
value.

The file of the script is safe as it is. sh decodes the script to a new
file from `mktemp`, which makes it with mode 0600 whatever the umask, in
`/tmp`, and the script removes the file first (`rm -f "$0"`). What is left
for the real window:

- **The size.** A command line holds at most 32767 characters. A file that a
  script writes is base64 inside the script, and the script is base64 again,
  so the command is about 1.8 times the size of the file: a file of about
  18 KB is the limit. The script refuses a longer command with no change. A
  larger file needs another way, for example a copy through the Windows file
  system.
- **The first line, if the script moves to stdin.** On the stdin of `sh`,
  `$0` is `/bin/sh`, and `rm -f "$0"` must go.
- **The copies on the host.** Before each run of the procedure, compare the
  SHA-256 of each script and config on the host with the release's, and do
  not start on a difference. Change the copies only when no window is
  started, never under a started window (as the rehearsal does, below).

The limit of the phase run names `localhost` too: the first play of
`windows-host.yml` runs on the controller and downloads the pinned root file
system to its cache, and a limit of `WSL-02` alone skips that play. The setup
then stops at its first step with a message that names the file and this
limit. The check play runs on the Windows hosts only, so `WSL-02` is enough.

A check run (`--check`) of any phase reads only: it starts no task, waits
for nothing and moves no file. The registrations go through
`tnwks_win_scheduled_task`, which changes nothing in a check run.

| Part | What it is |
| --- | --- |
| The accounts `tnwks-moverh-a` and `tnwks-moverh-b` | Local accounts, members of Users only, with a random password that nothing keeps. They may log on as a batch job (S4U needs it), and not at the keyboard, by Remote Desktop or from the network. No tile |
| `C:\ProgramData\tnwks-moverh` | The scripts, their configs, the pinned Alpine root file system, the state of the procedure (`state\`). Only SYSTEM and the Administrators may change them |
| `...\a`, `...\b` | The folders of the disk file. Each account may make files there, in that folder only, and an entry for CREATOR OWNER on files lets it replace or delete a file that it made. A also reads the folder and its rules (`0x1200ab`, read and execute with add file), as a present account does on the folder of its own disk file; B has add file only (`0x100002`), as its moved file is protected (`wsl_move_rehearsal_folder_rights`). No inheritance |
| The marker file `tnwks-move.marker` | In the folder of B (setup) and of A (the window). SYSTEM and the Administrators full, the account read only. It keeps the folder through an unregister |
| `...\log\a`, `...\log\b` | The result of each task of the account, one JSON file for each step |
| The tasks `tnwks-moverh-<a\|b>-<step>` | As the account, S4U, no elevation, no trigger. `stop`, `unregister` and `import` are the window tasks, always enabled. `start` holds the distro, as the keepalive does: A's is enabled, B's is registered disabled |
| The tasks `tnwks-moverh-window` and `tnwks-moverh-boot` | As SYSTEM: the procedure (`Invoke-TnwksMoveWindow.ps1`, no trigger, started by the play with a run request), and the boot task of the way back (a boot trigger) |
| The profiles of the accounts | Their `.wslconfig` (1 GB, one processor, no swap), written only after the probe made the profile, and their registrations of the distro `tnwks-moverh` |

**The guards.** Each name of the rehearsal starts with `tnwks-moverh`, and
each path is under its folder. The play checks this before any phase, and
also that no account of the rehearsal is the account of the host's person
and that the disk of the live distro is not under the folder. Each script
checks it again before any action: the account script refuses a distro name
outside its pattern and the names of `wsl_move_rehearsal_refused_names` (the
live distro), whatever the pattern, and a path outside the folder; the
procedure refuses an account or a task outside its pattern, a path outside
the folder, and a config with the session check on. Each phase that runs the
procedure first reads the last marker in the state file, and does not start
on another one than its order gives (a phase run twice, or out of order).
The phase `window` runs after the setup, or again after a window that went
back (the last marker `wayback-done`), as a second real window would. The
phase `wayback` runs after a done window, and also ends a started window that
did not end (the last marker `window-started` or `boot-run`, as after a boot
run that did not end the way back), as the way back mode of the procedure
does. Before any change, it reads that no run of the window task or of the
boot task goes on, and stops with nothing changed when one does: the
procedure has no lock, and a second way back must not run beside the first.
The procedure itself does not go forward after a started window with no end.
The tests in `security/tests/test_wsl_move_rehearsal.py` hold this.

**The copies on the host.** The scripts, their configs and the tasks are
written by one task file, `tasks/host_files.yml`, which setup and each later
phase run before their first script on the host. It reads the last marker
first. Setup, `window` and `interrupt` refresh: each copies the scripts and
writes the configs that differ, and registers each task whose definition
differs, only when no window is started (the last marker is empty or
`wayback-done`); otherwise it stops with nothing changed. `wayback`,
`restart` and `after-boot` change nothing and stop when a copy differs from
the role's, as the window that they end started with the copies that are
there: end that window with the release that started it, then run `window`
or setup again. The read of the live parts (`Get-TnwksMoveLiveState.ps1`)
is one of the scripts, but it only reads: `tasks/live.yml` copies it before
each read, at any marker, so its copy is the role's in every phase.
`remove-before-restart` prints each copy that differs and goes on;
`remove` runs no script. Then the play reads the SHA-256 of each copy on the
host and compares it with the role's: the bytes of each script, and the text
of each config as the play writes it (`wsl_move_rehearsal_file_hashes`).
Each run of the procedure (`tasks/run_procedure.yml`) compares them again
before it writes the run request, and does not start on a difference. A
registration of a task of an account sets a new random password on that
account (the module's `s4u_user`), as setup does; the window reads the time
of B's last password after the refresh, so its check of no new password for
B stays its own. A task that a release adds to an account (as `strip-rules`
of B) is registered by the refresh too; the phase `wayback` still registers
`strip-rules` itself.

**The live parts.** At the start and the end of each phase, the play reads
the port proxy, the NAT networks, the VM processes, and the registrations
of the distros of the account of the host's person (from its loaded hive,
as a count and a SHA-256, so the output names no path). The first read is
the baseline, in the state folder: the setup makes the folder before it
(the read copies its script into the folder), and stops before its first account unless the baseline
is there and it read the registrations (with the hive not loaded, the read
says "not read"). A later read that differs stops the
phase: run the removal. A VM process does not show its account, so the
check of the VM processes holds until the first restart only. After it, the
lead's read of the nodes (Ready, and the same address) covers the live VM.

| Phase | What it does, and what it shows |
| --- | --- |
| `setup` | Only while the state file holds no marker. The accounts, rights, folders and rules, the marker in the folder of B, the tasks, the state file (only when absent) and the saved version of WSL. The probe of each account (a medium token, no Administrators, its profile), then `.wslconfig`. A imports the distro (a user of UID 1000, a data token, a shortcut with an icon), and the check reads back each file by its SHA-256. A second setup over A's distro at its path imports nothing and writes the files again. A disabled task does not run. The procedure stops with no change on another version, and the boot task on a disk file at both paths or at neither (a test config whose paths, the boot request among them, are all in a test folder and none in the state folder; no WSL) |
| `window` | First a read of the state that the window starts from, before any task starts: the last marker, A's disk file at its path and none at B's path or in the side folder, no list of disabled tasks, A's batch right, A's start task enabled and B's disabled; and after a way back the file had the owner, the protection and the explicit rules that step 4 saved (the play prints whether its inherited rules are those that its folder gives, and does not stop on it). Only after these checks do A's disk file and folder get the rules of an older WSL, as in setup; then A reads its distro and B lists none. The procedure, forward, steps 1 to 9, with the group test: A joins Administrators before step 1 and leaves at step 7, and the log has both moments. Then A loses its own batch right, as the present account does. B reads the distro: the data, UID 0 right after the import and 1000 at the next start (the value that the import wrote), the default distribution, `OOBEComplete`, a VM of 1 GiB at most. The rules of the moved file (B the owner, as below), both markers, the icon in the folder of B, the window tasks enabled with no trigger, B's start enabled and A's disabled, and no new password for B. B makes, replaces and deletes a file of its own and can not remove the marker. B's unregister refuses while the file is at its path; B's import refuses another path. An apply of the start tasks changes nothing. With B's start task started, the file has the rule of step 4 and a rule of B's VM; after B's stop task (a clean stop), no rule of a VM. Then B imports, unregisters and imports again into the same folder, and the file keeps its rule |
| `wayback` | First a read that no run of the window task or the boot task goes on: the phase stops with nothing changed when one does. Then, after a done window, A's import back task can not start: A has no batch right and is not in Administrators (the expected refusal). The check takes a refusal at the start, a last result that is not 0, or no new run in its wait, each with no result of this run: a task that ran well in an earlier window keeps the last result 0 of that run. Then B removes SYSTEM and the Administrators from the rules of its disk file (the stated case; `-e wsl_move_rehearsal_wayback_strip_case=false` leaves it out for one run, and the way back and each other check stay as they are). Then the way back mode: its step 1 puts A into Administrators, the same task imports back, then A's stop task ends the VM that the import started (its shutdown result of this run passes, not an earlier exit code), no WSL process of A is left, and A leaves, both moments logged. The play then gives A its batch right again, for the next phases, reads that the VM processes are those of the baseline by process ID and start time, starts A's start task, and reads the token of each WSL process of A: none has the Administrators group enabled. A owns the distro, the file has the saved owner, protection and explicit rules (the folder gives the inherited ones), and B lists none. After A left the group, the play removes an icon of the shortcut in A's folder that A does not own (see the icon below). `-e wsl_move_rehearsal_wayback_group_steps=false` runs the way back with no group step and no group test, as a standard A, as the boot run does: the check that the task can not start is left out, the play gives A its batch right (A holds it after `interrupt`) and removes an icon in A's folder that A does not own, both before the run; the log has no group moment, and the procedure starts A's start task itself |
| `interrupt` | First a read of the icon of the shortcut in A's folder: the phase stops with nothing changed when A does not own it. The play removes a boot request of an earlier run. Then the window with no group step, stopped after step 5, in both cases below: the file in the folder of B, no account lists the distro, and the last marker is that of a started window. With `-e wsl_move_rehearsal_boot_group_steps=true` (the boot run of the group test, one run for each word of the owner), the play then writes the boot request: two booleans, read back |
| `restart` | With the owner's go and time only (`-e wsl_move_rehearsal_restart_go=true`). The play restarts the host and waits for the run of the boot task. Before it, the play prints the boot request (or none), and the groups check reads each account in Users only: the phase stops before the restart otherwise |
| `after-boot` | The boot task ran the way back once: its marker first, no group step, no restart. The play checks this right after its read of the log, so a boot run that did not end the way back stops the phase before any task of A or B starts. A owns the distro, B lists none. Started again by hand, the boot task does nothing and writes no marker. With `-e wsl_move_rehearsal_boot_group_steps=true` (as given to `interrupt`): the request of the boot run has the group steps and the group test, its log has A's join, the stop of A's VM and the leave, in that order, the start of A's start task is left to the play, and the boot request is spent. A holds its own batch right; the play removes an icon in A's folder that A does not own, reads that the count of the VM processes is that of the baseline (after a restart only the count compares), starts A's start task and reads the token of each WSL process of A: none has the Administrators group enabled. The groups at the end read A in Users only |
| `remove-before-restart` | See the removal |
| `restart` | The second restart, with its own go and time. The boot task finds no started window and does nothing |
| `remove` | See the removal. Then the exit check |

The check play also reads, at any time between the phases, that each
account is in Users only, and that each moment in Administrators in the log
of the procedure is a stated one: a join only in a run of the group test
(mode forward or way back), with its leave in the same run.

**The rule of the moved disk file.** At each attach of the disk (the import
in place and every start), WSL grants the new VM access to the file as the
account that owns the distro: it adds a rule for the virtual machine SID
(`S-1-5-83-1-` and four numbers, the form of MS-DTYP's well-known SIDs)
to the rules of the file, so the account needs the right to change them,
which an owner has. While the VM runs, the file may also hold a rule for
the capability SID that Windows derives from the capability name
`vmWorkerProcess` (the SHA-256 of the name in upper case, as UTF-16, as
eight numbers): `wsl_move_rehearsal_vm_capability_sid`. A clean stop of
the VM removes both rules again. So window step 4 makes the new account the owner of the moved file,
with full control for SYSTEM, the Administrators and the account, and no
inheritance; SYSTEM sets that owner with SeRestorePrivilege, as the way back
does for the saved owner. The role holds the rule as two values,
`wsl_move_rehearsal_disk_owner` and `wsl_move_rehearsal_disk_new_mask`, and
the procedure, its checks and the reads of the play take it from them. Each
check of the rules of a disk file compares the owner, the protection, every
explicit rule and the inherited rules, and allows only the explicit rules
of WSL: any number of a virtual machine SID, and at most one of that
capability SID, each as an allow rule with full control and no flag. Any
other capability SID, a rule of WSL in another form, and any deny rule are
not a pass. Right after the procedure writes rules, they must be the ones
written, with no rule of WSL; a write of the rules leaves out the rules of
WSL that a saved set holds. The procedure logs the rules before the move and after
the import, and the play prints them after setup, before and after the
window, while B's VM runs and after its stop, each account SID masked.

As the owner, the account may also remove SYSTEM and the Administrators from
the rules of its file. So each run of the procedure, in every mode, first
turns on the privileges of SYSTEM (backup, restore, take ownership), before
it reads the state, and stops when one of them is not on, with no marker
and nothing changed. The move of the file works by these privileges, not
by the right of SYSTEM on the folders: the page of `MoveFileEx` names
delete on the file or delete child on the folder, but the rename also opens
the file, and on a file that grants SYSTEM nothing that open is refused
unless the privileges are on. The read of the rules asks for `READ_CONTROL`
only, and either the backup or the restore privilege grants it: the restore
privilege grants `FILE_GENERIC_WRITE`, which holds `STANDARD_RIGHTS_WRITE`,
that is `READ_CONTROL` (Microsoft's pages Privilege Constants, File
Security and Access Rights, and Standard Access Rights). A write of the
rules takes ownership for the Administrators, then writes
the owner, the explicit rules and the protection in one write by name.
Windows gives the inherited rules from the folder in that write, so a
CREATOR OWNER rule of the folder names the owner that the write sets (a
write of the rules before the owner would name the Administrators). A group
(`G:`) in a saved set is not written. Way back
step 3 moves the file back first, then restores the saved owner and rules
at the old path, where the folder gives the inherited rules that were
saved. The check after it compares the saved owner, protection and
explicit rules exactly. The inherited rules are not part of "as saved":
Windows gives them from the old folder at the write, and a difference from
the saved inherited rules is a note in the log, not a failed check. A file
at the old path with other rules (a power loss between the two) is restored
only, and one that passes the same compare is left as it is (the state
read's `old_as_saved` and the play's read, its `rules_ok`, use it too).
Whether the inherited rules of a file are those that its folder gives is a
fact of its own: the play's read gives it as `inherits_ok`, and the guard
of window step 1 acts on it.

What a folder gives a file comes from Windows' own inheritance function,
`CreatePrivateObjectSecurityEx`: the folder's rules as the parent, the
owner and the group of the file as the creator with an empty DACL, the
generic mapping of files, and an object that is not a container. So each
rule of the folder that reaches a file (OI, with or without IO and NP; CI
alone does not) comes with the flag ID, CREATOR OWNER names the owner of
the file, CREATOR GROUP names its group, and each generic right becomes the
rights of a file. The unit tests hold that rule
in a stand-in, on three forms of a folder; the hand test runs the function
of Windows on the same forms against a real write. Another way, a probe
file in the folder, is a write, and the file gets the owner of the writer.
Before any change, window step 1 reads the disk file: a file with no
protection must have the inherited rules that its folder gives. It must
also have no rule that the check of the way back refuses: no deny rule,
explicit or inherited (the way back writes the explicit ones back, and the
folder gives the inherited ones again, so its check would fail on each
run), at most one rule of the capability SID, and no explicit rule of a
virtual machine SID or a capability SID in another form than WSL's. A
file that has other inherited rules or such a rule stops the window with
nothing changed (exit 3), and the log names each reason, each account
masked; the boot run never reads it. Step
2 removes the saved rules of an earlier window before the marker of a
started window, so a way back restores only rules that step 4 of its own
window saved. In setup, and again at the start of each window, A's disk file
is made like one of an older WSL: no protection, the rules of its folder
(which gives files a read rule for Authenticated Users), and one explicit
rule for A (`tasks/older_disk.yml`, which changes nothing when the file and
the folder are so already, the inherited rules that the folder gives
included, and runs only after setup or a way back). It reads and writes the
file with the functions of the procedure, from its copy on the host: the
take of ownership for the Administrators, then the owner and the rules in
one write. A write of the rules that fails puts the owner back, so the file
keeps its owner and its old rules. The play prints A's rules right after it (before A's read
starts the distro) and asserts that the file has the inherited rules of its
folder then; it prints them again after A's read, and the log of window
step 4 holds them after A's stop, with no assert on the inherited rules:
if A's start took them away again, the guard of window step 1 stops the
window with nothing changed, and the log shows both sets. A
stays the owner with full control, so its start and the rule that WSL adds
for its VM need nothing more; A's read after it starts the distro. In the
window, the read against the saved rules of the last way back, the read of
the state and their checks all come before it, so a stop at a check changes
nothing. In the phase `wayback`,
before the way back mode, B removes SYSTEM and the Administrators from the
rules of its own file (its task `strip-rules`), and the way back must still
put back the saved set.

**The end of a task run.** Each wait for a task (the runner of the tasks of
an account, the run of the procedure, the procedure's own runs of the
tasks of an account, and the wait for the boot task) takes a run as ended
only when the task is not `Running` or `Queued`, its last run time is new,
and its last result is not `0x41301` (the task is running). A task that
never ran, as the window task and the tasks of the accounts at the first
window, can read a new run time before its run ends.

**The batch right of A.** The registration of a task of A checks its new
password with a batch logon, so A keeps its own batch right from the setup
to the end of the window. Then the play takes it away, so the way back runs
as it must after the demotion of the real account, with only the group step.
At the end of the phase `wayback`, the play gives it back: the interruption,
the boot task and the removal run A's tasks with no group step. A way back
with the group steps off needs it before the run: the play gives it then.

**The icon of the shortcut.** The distro of the rehearsal has a shortcut with
an icon, so each import in place writes `shortcut.ico` next to the disk file.
WSL writes it as the importing account (the service impersonates its caller)
with `CREATE_ALWAYS`, and an error there fails the import; the unregister
deletes it as the account too, and goes on when it can not (the WSL source
of the installed release, `LxssUserSession.cpp`). A file that an account makes while it is in
Administrators can get the Administrators as its owner, and the CREATOR
OWNER rule of the folder then names the group, not the account. Such an
icon stays the group's: a standard account can not replace it at its
import, nor delete it at its unregister. So before A imports back as a
standard account, the play makes sure that A's folder holds no icon that A
does not own (`tasks/icon.yml`, as SYSTEM, this one file only: it prints the
owner, the rules, the attributes and the times first, and removes the file
only when A is not its owner). The phase `wayback` removes such an icon
after A leaves the group, or before a way back with no group step; the
phase `interrupt` only reads it, and stops when it is there.

**The reads of the icon in the procedure.** The window procedure reads the
icon (`shortcut.ico`) before each import, for the account of that import,
and deletes none. The read of its attributes takes the entry itself and
follows no link: a directory or a link is refused, and its rules are not
read. A read passes with no entry, or with a plain file that has none of the attributes
read-only, hidden, system, directory, reparse point and encrypted, no deny
entry, no object or conditional entry, and an allow rule that gives the
account of the import the rights it needs (`FILE_GENERIC_WRITE`, and
`DELETE` where its unregister deletes it). Only the rules of that account
count, and of the Administrators when the account is a member of the group
(at step 1, the group test of the rehearsal counts as a member, as it joins
right after). Each import also requires its own result of this run: an
import that fails for any other cause fails its check (window step 6 then
goes back, and way back step 5 stops). Window step 1 reads both
folders before any change: the old folder for an import back by the
present account (write and delete), and the folder of the new account for
its import (write and delete). Either
read stops the window with nothing changed, with a stop line that holds
the text of the read in one line of bounded length. After window step 5 no icon may
be left in the old folder, and before window step 6 the read of the folder
of the new account runs again; a fail at either sends the window back.
Before way back step 5 the old folder is read for the import back (write).
A fail there stops the way back before the import, and the procedure only
says so: no step deletes the icon, and the way out is a reviewed change of
the play.

**The boot run of the group test.** The rehearsal shows the group step at
boot only by the owner's word, once for each word: `interrupt` with
`-e wsl_move_rehearsal_boot_group_steps=true` writes a boot request (a file
in the state folder, `boot-request.json`, not one of the copies) after its
window, and the boot run takes the group steps and the group test from it.
A joins Administrators at way back step 1, after the restart, and the end of
the run takes it out whatever the result: when the import back ran, A's
stop task first ends the VM that the import started in the group, the run
waits for no WSL process of A, and then A leaves. The stop runs once in a
run (the time limit of the task holds one): a stop that fails is logged,
and the leave comes all the same. The run removes the request at its
end, only once A is out of the group, so the request holds for one boot run.
A power loss in the run leaves A in Administrators and the request in
place: the next boot run takes A out first, before it reads the marker, and
the check play counts that leave as a problem (A was in the group across a
restart). With no boot run before it, the groups check of the next phase,
or the removal, finds A in the group. The procedure refuses a group test with `restart` on (a restart
in the run would keep A in the group). With a config of the rehearsal (the
one with a boot request file) it also refuses, in each mode, group steps
and a group test that are not equal (from a request or from the boot part
of the config): the rehearsal has no join without its leave. The real
procedure runs the group steps with no group test, and its config has no
boot request file. A boot request that
is not two equal booleans stops the boot run with nothing changed. Only the
boot mode reads the boot request.

**The error text of a failed step.** The procedure runs a task of an
account and takes its result only from a run that ended in this run, as
Task Scheduler reads it for SYSTEM (a new last run time, not `Running` or
`Queued`, a last result that is not `0x41301`), and only with a time from
the start of this run to the moment of the read. The result file and its
time come from the account, so with no such run or result a step does not
pass. After an unregister or an import, a step requires the result of this
run of the account and the exit code of its task. The fail
line of each step that reads such a result (the stop, the unregister and
the import of the window, and the unregister and the import back of the way
back) holds the exit code of the task and the error text of its command
from the result of this run (`failed` and `output`), or says that there is
no result of this run, or that the task did not run to its end in this run.
Each text in a fail line is one line, with each control character and each
line or paragraph separator of Unicode a space, and is cut at 300
characters, the whole line at 1000. The line of a task holds its result as JSON on one line, cut at 2000.
The checks of the log read each line by its start, so the text inside an
error is not read as a group moment, a start or a result.

**Window step 7: no compute system runs.** Before the present account
leaves Administrators, and again after it leaves, the procedure reads, as
SYSTEM, the compute systems of the Host Compute Service through its own API
(`computecore.dll`: `HcsCreateOperation`, `HcsEnumerateComputeSystems` with
no query, `HcsWaitForOperationResult`, `HcsCloseOperation`), so the read
needs no tool on the host. Step 7 requires that the service lists no
compute system at all, of any owner or type. A VM does not run as its
account, and keeps the token that made it. WSL makes VMs of more than one
kind: the VM of a distro has the owner `WSL`, and a WSLC VM has a display
name that its caller chooses, or `WSLC` (WSL: `HcsVirtualMachine.cpp`), so
no owner name marks a VM of WSL. No account has a reason to have a compute
system at this step. A list that is not empty is read again, 2 seconds
apart, at most 10 reads; only a read with no system passes. A call that
fails, or a result that is not a list, fails the check. The fail line names
each system by its type, owner and state, in one bounded line, and never by
its id. Then the second check: no process of the WSL package of the present
account runs. A process counts by its image path: any file in the install
folder of the package (from the key that its installer writes,
`Lxss\MSI`, `InstallLocation`), or a WSL command of Windows in System32
(`wsl.exe`, `wslconfig.exe`, `bash.exe`). A process with no path that
SYSTEM can read counts by name: a named executable of the installer, or a
WSL command of Windows.
The path, not a list of names, is the rule: the installer takes some of its
executables by a pattern, and some of its names are not only WSL's. A fail
before the leave sends the window back while the account is still a
member; a fail after it sends the window back, and way back step 1 puts
the account back. The check is on when the config has no key
`vm_check`. Only a config of the rehearsal (the one with a boot request
file) may set `vm_check: false`, and it does, because the host's own distro
runs during a rehearsal; the scope check of the procedure refuses that
value in any other config, and a value that is not true or false. Each mode of the
procedure, the boot run and a forward run that goes back among them, runs
the group step of way back step 1 with a config that is not the
rehearsal's, and a test shows it.

**What stays unproven here.** The procedure and the boot task restart the
host only with `restart` on in their config, and the rehearsal keeps it
off: they log "would restart", and a unit test shows that the line runs only
with the switch on. The restarts of the rehearsal come from the play. The
boot run has the group steps off by default, so a standard A imports back
at boot, a case of its own: the real present account is still in
Administrators at a way back that comes before window step 7. The boot run
of the group test (above) shows the group step at boot, once for each word
of the owner. No temporary account is in Administrators across a restart in
either case, a power loss in the boot run aside (above). The check for a
session of the owner (site 1) is not built: the procedure refuses a config with it
on. After a way back with no restart, it starts the start tasks of the
present account (the third case of the design, a session of the owner, ends
the same way); in the group test, A leaves Administrators first and the play
starts them. Way back step 6 enables only the tasks that step 2 disabled (a
list in the state folder, written before the marker of a started window
and removed at the end of a way back); with no list, the way back moves
nothing. The import writes `DefaultUid` and the default
distribution, and only these are read back: window step 6 and way back
step 5 check the path of the registration, not its values. A power loss in
the phase `window` or `wayback` leaves A in Administrators across the
restart until the group check of the next phase or the removal, which logs
the leave; the check play counts that leave as a problem. The design speaks of
the installed version of WSL: the scripts read it as the file version of
`C:\Program Files\WSL\wslservice.exe`, as SYSTEM, in the same way at each
place where a version is compared. That the write of the owner and the
rules in one call names the new owner in a CREATOR OWNER rule is the
documented behaviour of Windows, not shown on a host by the unit tests. A
rule for OWNER RIGHTS that the account writes on its file could take the
right to write the rules from the Administrators after the take of
ownership: if so, the write fails and the way back stops, with the file at
the old path, the Administrators as its owner and its rules as they were. Not shown on a host either: that WSL writes the
rule of its VM as an explicit rule, keeps the protection of the file, and
removes the rule at a clean shutdown; the phases print what they read.

**The removal.** `remove-before-restart` first ends a run of the window
task or the boot task. Each removal phase then takes A and B out of every
local group but Users, whatever the state, and logs each leave.
`remove-before-restart` then has each account stop its distro, removes the
disk files (only the named paths under the folder), has each account
unregister with no file at its path, and removes the tasks. It keeps the
boot task for the second restart, unless the last marker is that of a
started window. The profiles stay loaded until a restart (each run of an S4U
task adds a reference to the profile that the system does not release).
After the second restart, `remove` runs no task of an account. It reads the
run of the boot task at that boot, removes the profiles (a loaded profile
stops it before the tasks go, and the message says what stays), the tasks,
the rights, the values
of the sign-in screen, the accounts and the folder. It prints the SIDs of
the accounts for the exit check:

```sh
uv run ansible-playbook playbooks/windows-move-rehearsal-check.yml --limit WSL-02 \
  -e wsl_move_rehearsal_removed_sids=<SID>,<SID>
```

It reads no account, right, tile, task, profile or folder of the rehearsal
left.

## The move of the distro

Per host, `wsl_move` (`group_vars/windows_hosts.yml`, off by default) adds
the role `windows_wsl_move`: step 3 of the account change (Design Doc 0004,
section 9). It makes the account that owns the distro, gives it its rights,
and puts the procedure of the move window on the host. No apply starts the
window. Site 2 (`host_vars/WSL-02`) has it on. Each other host has it off.

- **The start tasks of the new account.** `wsl_move_new_start_tasks`
  (`group_vars/windows_hosts.yml`) is the copy of each start task that the
  WSL roles register on the host: the watchdog, the address task, the
  keepalive where it is on, and each UDP relay. It must be the copy of each
  task of `wsl_move_present_tasks` apart from the compaction, which has no
  copy. The play stops before any change on another list.
- **Step 4: the SSH user.** On site 2, `ansible_user` is the administrator
  account. `windows_desktop_user`, the account that owns the distro, is
  `wsl_move_present_account` until the done switch and
  `wsl_move_new_account` from it. The jump logs in to the distro as the
  user of the distro (`wsl_distro_user`, from `wsl_move_present_account`).
  An apply as an account other than the present one stops before any write
  until an apply as the present account kept the reads of today and
  registered its window tasks.

- **Variables.** The names of the two accounts and the folder of the disk
  file today are in the encrypted variables of the host only:
  `wsl_move_present_account`, `wsl_move_new_account` and
  `wsl_move_present_folder`. The role has no default for them, and each
  task that takes one has `no_log`. `wsl_move_new_start_tasks` names the
  start tasks of the new account.
- **The tasks of the present account.** Window step 2 disables each task of
  `wsl_move_present_tasks`, the compaction among them. A way back that ends
  with no restart starts only `wsl_move_present_start_tasks`: the
  long-running tasks, with no time limit. The compaction and each periodic
  task wait for their own triggers.
- **The version of WSL.** `wsl_move_rehearsed_wsl_version`, in the
  encrypted variables of the host, is the version of WSL that the rehearsal
  of the move ran on. At each apply, before any write, the play reads the
  installed version and stops on another one.
- **Paths.** The role folder is `C:\ProgramData\tnwks-wsl-move`, with
  `disk`, `state` and `log` under it. Each path is in the long, normalized
  form (a drive, then names joined by one backslash). The folder of the new
  disk file (`wsl_move_disk_dir`) is on the volume of the disk file today,
  as the move is a rename and never a copy. The play stops before any
  change when either is not so.
- **The account.** A local account, a member of Users only, with the right
  to log on as a batch job, each other logon denied, and off the sign-in
  screen. Its password is random and kept nowhere: each registration of
  one of its tasks sets a new one (`s4u_user`).
- **The folder of the disk file.** SYSTEM and the Administrators have full
  control. The account may make files in the folder, and change or delete
  only the files that it made. A marker file is written once, and the
  account may only read it.
- **What step 3 reads of today.** The values of the registration, the
  default distro, the VM address and `.wslconfig` of the present account,
  kept in the state folder. They are read once, while Ansible logs in as
  the present account. The `.wslconfig` of the new account is the one
  section `[wsl2]` of the present account, with the keys `memory`,
  `processors`, `swap` and `kernelCommandLine` only.
- **The procedure.** The scripts are those of the rehearsal of the move
  (`roles/windows_wsl_move_rehearsal/files`), and each copy on the host must
  have the SHA-256 of the role's file. The installed version of WSL is
  saved once. The scripts, the configs and the tasks change only while no
  window is started.
- **The tasks.** The new account has its probe and its stop, unregister and
  import tasks. The present account registers its own stop, unregister and
  import tasks while Ansible logs in as it. SYSTEM has the window task and
  the boot task of the way back. The window task reads its run request
  (`state\request.json`), which the start of the window writes.
- **The probe.** After the tasks, the play runs the probe of the new
  account and stops unless it reads a medium token, no Administrators group
  and a profile under `C:\Users`.
- **Off or reverted.** The role removes nothing. The account and its
  profile, its four user rights, its UserList value,
  `C:\ProgramData\tnwks-wsl-move` with its disk folder and marker, the nine
  tasks (the boot task runs at each boot and acts only after
  `window-started`) and the manual start of seclogon stay. Their removal is
  its own change with the owner's go, and the removal of the account needs
  a restart.

## The start tasks of the account that owns the distro

Each WSL role registers a copy of each of its start tasks for the account
that owns the distro (`wsl_move_new_account`, in the encrypted variables of
the host), with the name of the task and the suffix `-account`
(`wsl_move_task_suffix`). No apply replaces a task of the present account.
The phase of a host (`wsl_move_phase`, `group_vars/windows_hosts.yml`) sets
what the play does:

| Phase | When | The tasks | The folders of the start tasks |
| --- | --- | --- | --- |
| `off` | `wsl_move` off (each host today) | As before. No copy | One writer, as before |
| `move` | `wsl_move` on, `wsl_move_done` off | Each copy is registered disabled. No apply changes the enabled state of a start task that exists, of either account (`keep_task_enabled_state`): the move window enables and disables them | Both accounts may write |
| `done` | `wsl_move_done` on | Each copy is kept enabled. The play no longer registers the start tasks of the present account | Only the account that owns the distro may write |

A way back turns `wsl_move_done` off first. The copies run with no
elevation, so `wsl_move` needs `wsl_start_unelevated`. Each copy is
registered as SYSTEM, which sets a new random password on the account for
each registration (`s4u_user`). The copy of the watchdog names the copy of
the keepalive. From `done` on, the WSL service task starts the copy of the
keepalive. With `running`, the module does not start a task that is
disabled while the state is the task's own.

The copy of the address task of the LAN bridge (`tnwks-lan-bridge-ip-account`)
writes the same address file. While the move runs, both accounts may write
it, so after the restart of the window the copy writes the address of the
VM, and the sync (SYSTEM) reads it and moves the port proxy. The sync waits
45 seconds after a VM NIC event, and the address task 15 seconds. The
handler of the role runs the address task that is enabled.

Each UDP relay has a copy too (`<task>-account`). A relay and its copy
listen on the same port, and the window runs one of them only. While the
move runs, both accounts may write the logs of the relays, and the handler
of the role restarts the relays that are enabled.

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
  so CI writes it there and WSL picks it up at its next restart. The VM's
  memory is `wsl_memory` (40GB; WSL-02 sets 45GB in host_vars).
- **Ethernet** (`host_settings_ethernet`, the Realtek 2.5GbE): power
  management "Allow the computer to turn off this device" off, and the
  advanced properties `*EEE`, `AdvancedEEE`, `EnableGreenEthernet` and
  `PowerSavingMode` at 0. Empty on a host on Wi-Fi (WSL-02): not checked.
- **Wi-Fi adapter disabled** (`host_settings_disabled_adapters`), so Windows
  never fails over to Wi-Fi (which resets the WSL vNIC). `Disabled` or
  `Not Present` both pass: a device disabled in Device Manager shows as
  `Not Present`. Empty on WSL-02, which stays on Wi-Fi.
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

The Terraform roots that opt in get the same kind of nightly check, with
alerts `TerraformDrift` and `TerraformDriftCheckStale`. Its runner has no
way to the Windows host, so it reports through a Pushgateway instead
([ci.md](../operations/ci.md#terraform-drift)).

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
