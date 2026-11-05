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
├── roles/               # one per piece of host config, windows_* and wsl_*; each
│                        # carries its scripts and units in files/ and templates/
└── scripts/             # the few things Ansible can't do from inside WSL (README)
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
| `site_<name>`   | one Windows host plus its distro (`site_wsl`, `site_wsl02`)   |                                    |

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

| Site         | Windows host      | Distro                   | Managed from                       |
| ------------ | ----------------- | ------------------------ | ---------------------------------- |
| `site_wsl`   | `DESKTOP-HKE05KI` | `DESKTOP-HKE05KI-ubuntu` | its own distro, CI runner          |
| `site_wsl02` | `WSL-02`          | `WSL-02-ubuntu`          | the DESKTOP-HKE05KI workstation    |

### Adding a second Windows host

1. Bootstrap its sshd (`Install-TnwksWindowsSshd.ps1`, elevated, from that
   host's own distro: `scripts/invoke-elevated.sh`) and pin its host key in
   `inventory/known_hosts`. sshd listens on that host's vEthernet (WSL)
   address only, so another controller jumps through the distro's sshd:
   `ansible_ssh_common_args` with a `ProxyCommand` in its host_vars (see
   `host_vars/WSL-02/main.yml`), with the distro's sshd reachable on the LAN
   through the `2222` LAN-bridge port, limited to the controller's address.
2. Add it to `windows_hosts`, its distro to `wsl_distros`, and a
   `site_<name>` group with both in `inventory/hosts.yml`. Without
   `ci_runner` the CI workflow skips the site.
3. `host_vars/<host>/main.yml` (`ansible_host`, per-host role vars such as
   `coral_detector_devices`) and `host_vars/<host>/secrets.sops.yml`
   (`ansible_user`). A role's list left empty skips the role on that host
   (`coral_detector_devices`, `udp_relays`, `usb_attach_bound_devices`;
   distro: `wsl_lan_bridge_instances`, `wsl_usb_attach_devices`,
   `wsl_backups_timers`); `windows_wsl_maintenance_enabled: false` skips the
   watchdog.
4. `task wsl-host:check -- --limit site_<name>,localhost`, then apply.

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

## CI

Two workflows:

- **`ansible.yaml`** (GitHub-hosted, no secrets): every PR touching
  `infrastructure/ansible/` syntax-checks each playbook and runs ansible-lint.
- **`ansible-hosts.yaml`**: the host playbooks (`windows-host.yml`,
  `wsl-distro.yml`) against each site, from the site's own self-hosted runner.

| Event          | Runs                                                         |
| -------------- | ------------------------------------------------------------ |
| PR (owner's)   | `--check --diff` of the PR head; summary posted on the PR    |
| push to `main` | apply                                                        |
| 12:30 UTC      | `--check`, counts written for the drift alerts               |
| dispatch       | check or apply (`task wsl-host:ci-check` for a check)        |

How a run goes:

1. **plan** (`ubuntu-24.04`, no secrets): reads the `site_*` groups and their
   `ci_runner` from the inventory, one matrix entry per site. It runs the
   controller play (downloads, the Coral libusb shim built with mingw-w64)
   and uploads `~/.cache/tnwks-ops` as an artifact, so the runner needs no
   compiler.
2. **run** (`runs-on: <ci_runner>`, one at a time per site): downloads that
   cache, then `ansible-playbook windows-host.yml wsl-distro.yml --limit
   site_<name>,localhost --skip-tags aruba-credentials`. The controller play
   finds everything in place, and asserts the shim's SHA-256
   (`coral_detector_shim_sha256`), so a different toolchain fails instead of
   replacing the DLL.

**Public logs.** The repo is public, and so are its Actions logs and PR
comments. The job log, job summary and PR comment carry only the per-host
recap counts and the names of changed and failed tasks
(`scripts/ci-report.py`). The full output, with the diffs, is the run's
`ansible-<site>-log` artifact, encrypted to the owner's age key:

```bash
age -d -i ~/.config/sops/age/keys.txt ansible-site_wsl.log.age | less
```

**The runners.** `tnwks-ansible-<CLUSTER_NAME>`, one scale set per cluster
(`kubernetes/apps/tools/actions-runner-controller/ansible-runner`), separate
from `tnwks-ops-runner` (network devices). Its Secret holds:

- a CI SSH key: admin on the Windows hosts (`windows_sshd_authorized_keys`)
  and `wsl_distro_user` on the distros (`wsl_ci_authorized_keys`, from the
  cluster's Docker network only, no forwarding);
- the Ansible CI age key, a recipient only of `infrastructure/ansible`
  secrets (`.sops.yaml`). It can't read the Aruba credentials, which is why
  the runs skip `aruba-credentials`; that one file is applied from a
  workstation (`task wsl-host:backups`).

A runner pod reaches its site's Windows host at the vEthernet (WSL) address,
like Frigate reaches the Coral detector, and the distro at the Docker
network's gateway (`wsl_distro_ssh_host`). The distro's connection is local
only when the controller's hostname is the Windows host's
(`wsl_distro_windows_host`), so the same inventory works from a workstation
and from a pod.

**Which runner runs which site** is the site's `ci_runner`
(`group_vars/site_<name>.yml`). With a second WSL host, each site can name
the other cluster's runner, so a run never configures the host its own
runner lives on. Reaching another host's Windows sshd and distro then needs
them listening beyond their WSL network (`Install-TnwksWindowsSshd.ps1
-ListenAddress`, `wsl_distro_ssh_host`) and the runner's key allowed from
there (`wsl_ci_authorized_keys`).

Merging a change applies it. Read the PR's check summary first; the check
covers exactly what the apply will run.
