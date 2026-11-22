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
| `group_vars/all/host_mcp.yml` | `host_mcp_agent_enabled: false`, and the four values, empty. A site turns the switch on in `group_vars/site_<name>.yml` |
| `host_vars/<host>/secrets.sops.yml` | the values, encrypted: `host_mcp_agent_user` and `host_mcp_agent_public_key` on both hosts of the site, `host_mcp_agent_source_windows` on the Windows host, `host_mcp_agent_source_distro` on the distro |
| role `windows_host_mcp_user` (tag `host-mcp`) | the local account, its rights, its profile, its startup folders, and the key file `C:\ProgramData\tnwks-host-mcp\authorized_keys` |
| role `windows_sshd` (tag `sshd`) | the account at the end of `AllowUsers`, and a `Match User` block of its own (`Install-TnwksWindowsSshd.ps1 -AgentUser`) |
| role `wsl_host_mcp_user` (tag `host-mcp`) | the user of the distro, its home owned by root, its startup files and its key file |

Each role runs only where the switch is on, and stops before any change
when a value is empty or not in its form. No task prints the name, the key
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
  line, `from="<source address>",restrict <key>`. Only SYSTEM and the
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

## A first apply

The values come first: the lead reads the source addresses and makes the
key pair, and writes them in the encrypted files of the two hosts. The
switch of the site is a change of its own, in a pull request that the
owner approves. Merging a change applies it
([ansible.md](../operations/ansible.md)), and the role `windows_sshd` only
checks in CI, so the Windows half of the change runs from a workstation
before the merge. From a checkout of that branch:

```bash
# 1. What the apply would change (changes nothing).
task wsl-host:windows-check -- --limit site_wsl --tags host-mcp
task wsl-host:distro-check -- --limit 'wsl_distros:&site_wsl' --tags host-mcp
task wsl-host:sshd-check   # DRIFT: AllowUsers ... and the user of the host server
# 2. The account, its rights, profile, folders and key file.
task wsl-host:windows-apply -- --limit site_wsl --tags host-mcp
# 3. AllowUsers and the Match block (UAC prompt; sshd restarts).
task wsl-host:sshd
# 4. The user of the distro.
task wsl-host:distro-apply -- --limit 'wsl_distros:&site_wsl' --tags host-mcp
# 5. Both checks show changed=0, and sshd shows no drift.
task wsl-host:sshd-check
task wsl-host:check -- --limit site_wsl
```

Then the merge: its CI apply finds nothing to change. Until step 3, no
sign-in of the user is possible: `AllowUsers` does not name it.

## The way back

- **Stop the sign-in at once:** remove the key file
  (`C:\ProgramData\tnwks-host-mcp\authorized_keys`, and
  `.ssh/authorized_keys` in the home of the user in the distro). A key
  sign-in fails from then on.
- **Take the user out:** a pull request that turns the switch of the site
  off, then `task wsl-host:sshd` from it (the config is again as before:
  no block, and `AllowUsers` without the user). In the distro, `userdel`
  of the user and a removal of its home. On the Windows host, as an admin:
  `Remove-LocalUser`, the removal of its profile (`Win32_UserProfile` of
  its SID), of its value under `SpecialAccounts\UserList`, of its SID from
  the two deny rights, and of `C:\ProgramData\tnwks-host-mcp`. Nothing else
  on the hosts changes in the apply, so nothing else needs a way back.

## Limits

- **The source address on Windows is the distro.** The cluster connects
  to the SSH server of Windows through the NAT of the distro. So the
  address that Windows sees for the cluster is the address of the distro,
  and the `from=` of the Windows key admits each process in the distro, not
  the cluster alone. No narrower form exists on that path. There, the
  private key in the Secret of the host server is the real control. In the
  distro, `from=` names the node network of the cluster.
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
- **A WSL subnet change** changes the address that the SSH server of
  Windows sees. After one, the lead reads it again and updates
  `host_mcp_agent_source_windows`, as for the sshd bootstrap.
- **Site 2.** The host server reaches the hosts of site 1 only. The roles
  are ready for site 2, but its switch stays off until the design names
  the path from the cluster to those hosts.
