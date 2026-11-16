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
that no role owns gets the same rules from `windows_locked_dirs` (per host).
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
