# Ansible

Ansible configures what sits below Kubernetes: the Windows host under each
WSL cluster, its WSL distro, the RouterOS router's post-bootstrap checks, and
the Talos cluster bootstrap. Everything is in `infrastructure/ansible/`.

## Layout

```
infrastructure/ansible/
├── ansible.cfg          # paths resolve against this file; the taskfiles set ANSIBLE_CONFIG
├── pyproject.toml       # the pinned ansible-core (+ ansible-lint), locked in uv.lock
├── requirements.yml     # the pinned collections
├── inventory/
│   ├── hosts.yml        # every host, in groups
│   ├── known_hosts      # pinned SSH host keys
│   ├── group_vars/      # all/ (connection, talos_clusters), windows_hosts, wsl_distros, mikrotik
│   └── host_vars/       # per host; secrets in <host>/secrets.sops.yml
├── playbooks/
│   ├── 01-05-*.yml      # Talos cluster bootstrap and upgrade (on the controller)
│   ├── windows-host.yml # the Windows hosts
│   ├── wsl-distro.yml   # the WSL distros
│   ├── wsl-qmd.yml      # opt-in: QMD on a WSL distro
│   ├── mikrotik-bootstrap.yml
│   └── library/         # tnwks_win_scheduled_task
└── roles/               # one per piece of host config, windows_* and wsl_*
```

## Controller

One ansible-core for every playbook, from `pyproject.toml` and `uv.lock`, run
through `uv run --locked`. The taskfiles do that through the `ANSIBLE` var
(`{{.ANSIBLE}} ansible-playbook ...`) and set `ANSIBLE_CONFIG`, so a
workstation and a CI runner run the same versions.

```bash
task ansible:init   # once per clone: the uv environment and the collections
task ansible:hosts  # the inventory as a tree
task ansible:ping   # reach every Windows host and WSL distro
task ansible:lint   # syntax-check every playbook, ansible-lint
```

Collections go to `~/.ansible/collections-tnwks-ops` (`collections_path`), away
from anything another Ansible on the machine uses.

## Inventory

One inventory, `inventory/hosts.yml`; choose hosts with `--limit`, never with
another inventory file.

| Group           | Hosts                                                         | Connection                         |
| --------------- | ------------------------------------------------------------- | ---------------------------------- |
| `windows_hosts` | Windows hosts under a WSL cluster, by Windows computer name   | OpenSSH, PowerShell, key in SOPS   |
| `wsl_distros`   | the WSL distro on each, `<computer>-<distro>`                 | local from a workstation           |
| `mikrotik`      | RouterOS devices                                              | REST from the controller           |
| `site_<name>`   | one Windows host plus its distro (`site_wsl`)                 |                                    |

The Windows host's inventory name is its computer name because that is also
the `instance` label of its windows_exporter metrics.

Talos nodes aren't inventory hosts (they have no SSH). The bootstrap
playbooks run on the controller and read the cluster named by
`-e talos_target=<wsl|ms-01>` from `talos_clusters` in
`group_vars/all/talos.yml`.

Secrets are SOPS files in `host_vars/<host>/secrets.sops.yml`, decrypted by the
`community.sops.sops` vars plugin (`vars_plugins_enabled`). SSH checks host
keys against `inventory/known_hosts` (`group_vars/all/connection.yml`); after a
host key changes, compare `ssh-keyscan` with the key file on the host before
re-pinning.

### Adding a second Windows host

1. Bootstrap its sshd (`Install-TnwksWindowsSshd.ps1`) and pin its host key in
   `inventory/known_hosts`.
2. Add it to `windows_hosts`, its distro to `wsl_distros`, and a
   `site_<name>` group with both in `inventory/hosts.yml`.
3. `host_vars/<host>/main.yml` (`ansible_host`, per-host role vars such as
   `coral_detector_devices`) and `host_vars/<host>/secrets.sops.yml`
   (`ansible_user`).
4. `task wsl-host:check -- --limit site_<name>`, then apply.

## Running playbooks

| What                         | Task                                                     |
| ---------------------------- | -------------------------------------------------------- |
| Windows host, drift          | `task wsl-host:windows-check` (`--check --diff`)         |
| Windows host, apply          | `task wsl-host:windows-apply`                            |
| WSL distro, drift / apply    | `task wsl-host:distro-check` / `distro-apply`            |
| both                         | `task wsl-host:check` / `apply`                          |
| one role                     | `task wsl-host:windows-check -- --tags monitoring`       |
| one site                     | `task wsl-host:check -- --limit site_wsl`                |
| Talos bootstrap              | `task bootstrap:wsl`, `task bootstrap:ms-01`             |
| RouterOS post-bootstrap      | `task mikrotik:bootstrap`                                |

Every role is idempotent, and `--check` after an apply reports `changed=0`.
See [windows-ansible.md](../wsl/windows-ansible.md) for the Windows roles,
check mode and the nightly drift check.
