# The user of the host server

The host server of the main agent (`kubernetes/apps/ai/agents/main/host-mcp`)
reads the state of the hosts of a site over SSH. It signs in as one local
user, made for it on the Windows host and in the WSL distro. That user is in
no admin group, its key holds only for the address from which the cluster
connects, and it can not change its key file or the startup files of its
shell ([Limits](#limits) names the limits on Windows). So a read can
not reach the profile of the owner, the age key or the secrets of the house.
This page tells how Ansible makes that user, what a first apply changes,
and the way back. The design is section 4.2.6, "The host server", of the
Design Doc of effort 0003 in the CoE repository (task 6.21 of its plan).

## What makes it

| Piece | What |
| --- | --- |
| `group_vars/all/host_mcp.yml` | `host_mcp_agent_enabled: false`, and the four values, empty. A site turns the switch on in `group_vars/site_<name>.yml`. `host_mcp_agent_apply: false`: the guard ([below](#the-guard)) |
| `host_vars/<host>/secrets.sops.yml` | the values, encrypted: `host_mcp_agent_user` and `host_mcp_agent_public_key` on both hosts of the site, `host_mcp_agent_source_windows` on the Windows host, `host_mcp_agent_source_distro` on the distro |
| role `windows_host_mcp_user` (tag `host-mcp`) | the local account, its rights, its profile, its startup folders, and the key file `C:\ProgramData\tnwks-host-mcp\authorized_keys` |
| role `windows_sshd` (tag `sshd`) | the account at the end of `AllowUsers`, and a `Match User` block of its own (`Install-TnwksWindowsSshd.ps1 -AgentUser`) |
| role `wsl_host_mcp_user` (tag `host-mcp`) | the user of the distro, its home owned by root, its startup files and its key file |

Each role runs only where the switch is on, and stops before any change
when a value is empty or not in its form. Where it runs, it changes the host
only with `-e host_mcp_agent_apply=true` ([The guard](#the-guard)). No task prints the name, the key
or the source address: those tasks carry `no_log`, or a label with none of
them.

## On the Windows host

- **The account.** A local account, member of Users only. Its password is
  random, made in the run, and kept nowhere: the host server signs in with
  its key. A member of any other group stops the run, and an admin looks
  first.
- **Its rights.** It may not log on at the keyboard
  (`SeDenyInteractiveLogonRight`) or by Remote Desktop
  (`SeDenyRemoteInteractiveLogonRight`). It keeps the network logon, which
  the SSH server uses for a key sign-in. It has no tile on the sign-in
  screen (`SpecialAccounts\UserList`).
- **Its profile.** The role makes the profile before the first sign-in
  (`CreateProfile` of `userenv.dll`), so that the startup folders of
  PowerShell exist with their rules first. The Administrators own
  `Documents\WindowsPowerShell` and `Documents\PowerShell` below it, the
  user may only read them, and they hold nothing. Each PowerShell line of
  the read list also runs with `-NoProfile`.
- **Its key.** `C:\ProgramData\tnwks-host-mcp\authorized_keys` holds one
  line, `from="<source network>",restrict <key>`. Only SYSTEM and the
  Administrators change that folder. `restrict` turns off each forwarding
  and the terminal.
- **The SSH server.** `AllowUsers` names the account after the admin
  accounts, and its block is:

  ```text
  Match User <agent>
         AuthorizedKeysFile __PROGRAMDATA__/tnwks-host-mcp/authorized_keys
         DisableForwarding yes
         PermitTTY no
  ```

  So a key file in the profile of the user counts for nothing.
- **The checks of each run** (also in a check run): the startup folders
  are empty, and the user can read no profile and no `.ssh` folder of an
  account of `AllowUsers`, and no host key of the SSH server
  (`host_mcp_agent_private_paths_windows` adds paths). A finding stops the
  run with no change.

## In the distro

- **The user.** Its own group only (`groups: []`), no sudo rule, never in
  the `docker` group (that group is root). Its password field is `*`, so
  only its key signs it in.
- **Its home.** root owns the home (mode 0750, group of the user) and each
  item in it: `.bashrc`, `.profile` and `.bash_logout` (a header only),
  `.ssh` and `.ssh/authorized_keys`, with the same `from=` and `restrict`
  line. A login runs the commands of the host server with these files and
  the files of the system under `/etc` only.
- **The checks of each run:** `sshd -T` for the user and the source
  address lets it in by key; `sudo -l` finds no rule; as the user, a read of
  the home of the account of the distro, its `.ssh` and `.kube` folders,
  its age key, `/root`, `/etc/shadow` and the ed25519 host key fails
  (`host_mcp_agent_private_paths_distro_extra` adds paths); the home holds
  only the items above, all owned by root.
- **How it reads as the user.** Ansible connects as an admin that is not
  root and becomes root. A task that became the user would need Ansible to
  share its module files with it, which needs an ACL tool, and the role
  does not need one. So each read runs as root and drops to the user inside the
  command (`runuser -u <user> --`, no shell). First `id -un` through the
  same drop must give the user, and a read of `/etc/passwd`, a control, must
  pass: a drop that failed can not pass as "read none".

## The guard

The owner's rule: nothing on a host changes without his word. So the two
roles change a host only in a run that passes `-e host_mcp_agent_apply=true`,
which only the owner's run does. Without it, as in each CI run, each task
that can change the host runs in check mode, and the task "No drift of the
user of the host server" fails the run when one of them finds a difference.
Its message names the items (the account, its key file, ...) and no value,
and nothing is changed. The flag is never set in a file.

What a CI run from `main` does on a site:

| State of the site | The two roles in CI (apply, or the nightly check) |
| --- | --- |
| Switch off (each site today) | Skipped; nothing changes |
| Switch on, the user present and equal to the values | The checks run and pass; `changed=0` |
| Switch on, the user missing or different | The run fails at "No drift of the user of the host server", with the items that differ; nothing changes, and the later roles of that host do not run in that run. So each role of the user comes after each role that applies: last in the distro, and on Windows only the two rehearsals (off unless given by hand) follow it |

`windows_sshd` has the same form (`windows_sshd_apply`): with the switch on
and no `task wsl-host:sshd` yet, CI fails its drift check and changes
nothing. It runs before the user role on Windows, so in that state it stops
the Windows roles after it, as any drift of sshd does today. A test runs the role of the distro in a container in each of these
states (`security/tests/test_host_mcp_apply_guard.py`).

## State

| Item | State |
| --- | --- |
| The roles | In main, run by both playbooks where the switch of the site is on |
| The values of site 1 | In the encrypted variables of its two hosts |
| The switch of site 1 | On in `group_vars/site_wsl.yml`, in the pull request that the owner applies from before its merge |
| Site 2 | Off: no value, no switch |

## A first apply

The owner runs it from a checkout of the branch of the switch, on the
workstation of site 1, **before** the merge, and only when the last nightly
check of `main` is green for site 1: that check runs `windows_sshd` and each
other role, so the run starts from a host with no other drift. Each command is a task of
`.taskfiles/WslHostTasks.yml`; what goes after `--` reaches
`ansible-playbook` as it is, so `-e host_mcp_agent_apply=true` goes there.
With `--check` (step 1) the flag changes nothing: it shows the changes of the
apply without a drift failure.

```bash
# 1. What the apply would change, on both hosts of site 1; changes nothing.
task wsl-host:windows-check -- --limit site_wsl --tags host-mcp -e host_mcp_agent_apply=true
task wsl-host:distro-check -- --limit 'wsl_distros:&site_wsl' --tags host-mcp -e host_mcp_agent_apply=true
# 2. On Windows: the account, its rights, profile, startup folders and key file.
task wsl-host:windows-apply -- --limit site_wsl --tags host-mcp -e host_mcp_agent_apply=true
# 3. On Windows: the whole sshd config, the admin keys, the firewall rule and
#    the service, to the form of the script; with the switch on, AllowUsers
#    names the user and its Match block is added. One UAC prompt; sshd restarts once.
task wsl-host:sshd
# 4. In the distro: the user, its home and its key file.
task wsl-host:distro-apply -- --limit 'wsl_distros:&site_wsl' --tags host-mcp -e host_mcp_agent_apply=true
# 5. No change left, without the flag as CI runs: sshd-check reports no
#    drift (a second UAC prompt; it changes nothing), and the full check of
#    site 1 shows changed=0.
task wsl-host:sshd-check
task wsl-host:check -- --limit site_wsl
```

Step 1 lists the Windows account, its sign-in screen value, its key folder
and its key file, and the distro account. The rest needs the account, so
steps 2 and 4 make it. Steps 2 and 4 end with the checks of "On the Windows
host" and "In the distro". The checks run after the changes of the same
run: a finding stops the run, what the run made before it stays, and the
way back below removes it. Only a member of another group stops the
Windows account task before a change. Until step 3 no sign-in of the user is possible on
Windows: `AllowUsers` does not name it. `task wsl-host:sshd` runs on the
Windows host of this workstation only, which is the host of site 1.

The host check of the pull request runs `--check` of the branch on each
site with no flag, so it fails on site 1 until his run: the draft shows a
red check, and the merge helper refuses it. After his run the lead runs that
check again (the CI form of step 5: every tag, no flag); when it is green,
the pull request leaves draft and merges, and its CI apply finds nothing to
change.

**If the pull request merged before his run.** A push to `main` runs the CI
apply of both playbooks on each site, with every tag
([ansible.md](../operations/ansible.md)), and with no flag. On site 1,
`windows_sshd` on Windows and the role of the user in the distro would find
the user missing and fail at their drift checks; nothing changes
([The guard](#the-guard)). On Windows the roles after `windows_sshd` (the
host settings, the role of the user and the rehearsals) would not run until
then. The apply and each nightly check of `main` would report that drift
until his run. So the pull request stays a draft, and merges after his
apply.

## The way back

- **Stop the sign-in at once:** remove the key file
  (`C:\ProgramData\tnwks-host-mcp\authorized_keys`, and
  `.ssh/authorized_keys` in the home of the user in the distro). A key
  sign-in fails from then on. CI does not put a deleted key file back: with
  no flag it reports drift.
- **Take the user out:** a pull request that turns the switch of the site
  off, then `task wsl-host:sshd` from it (the config is again as before:
  no block, and `AllowUsers` without the user). In the distro, `userdel`
  of the user and a removal of its home. On the Windows host, as an admin:
  `Remove-LocalUser`, the removal of its profile (`Win32_UserProfile` of
  its SID), of its value under `SpecialAccounts\UserList`, of its SID from
  the two deny rights, and of `C:\ProgramData\tnwks-host-mcp`. Nothing else
  on the hosts changes in the apply, so nothing else needs a way back.
- **Before the merge:** the same, but `task wsl-host:sshd` runs from a
  checkout of `main`, where the switch is off, and the draft pull request
  is closed. Both key files are owned by root or the Administrators, so
  each removal runs as an admin.

## Limits

- **The source on Windows is the WSL network of the host.** The cluster
  connects to the SSH server of Windows through the NAT of the distro, and
  the address of the distro changes at a WSL restart. So the `from=` of the
  Windows key names the WSL network of the host, in network form: it admits
  the distro and the Windows host itself, and so each process on either,
  not the cluster alone. No narrower form exists on that path. There, the
  private key in the Secret of the host server is the real control. In the
  distro, `from=` names the Docker network of the nodes of the cluster, the
  same `from` as the key of the runners. Each pod of the cluster leaves
  through a node, so each pod could try the key, as for the key of the
  runners; there too the private key is the control.
- **The profile root and the registry hive on Windows.** The user owns the
  root of its profile and its own registry hive, as each Windows user does.
  It could rename `Documents` and make a new one, or set an environment
  value in its hive (`HKCU\Environment`, for example `PATH` or
  `PSModulePath`) that a later read runs. The role finds an item in the
  startup folders and a change of their rules at its next run, and stops;
  it does not prevent such a change. `-NoProfile` on each PowerShell line
  keeps the profile scripts out of those reads. In the read step each such
  change needs a write, which the gateway refuses or the owner approves;
  a stronger form is a question for the write step.
- **A WSL subnet change** changes the WSL network of the host. After one,
  the lead reads it again and updates `host_mcp_agent_source_windows`, as
  for the sshd bootstrap.
- **The first sign-in is proven later.** The first key sign-in of the user
  is proven only when the host server pod runs, in a later pull request of
  the cluster. If it fails, the host server has no reads, and no other
  access changes.
- **Site 2.** The host server reaches the hosts of site 1 only. The roles
  are ready for site 2, but its switch stays off until the design names
  the path from the cluster to those hosts.
