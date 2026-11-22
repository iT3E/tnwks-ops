# The read list of the host server: a proposal

**A proposal for the owner's gate, not config.** `readList` in the config of
the host approval plugin of main (`kubernetes/apps/ai/agents/main/agent/config/openclaw.json`)
stays empty until the owner takes these lines. Then a pull request puts
them there, with the exact unit names below. Design Doc of effort 0003,
section 4.2.6, "The read list"; the host server is
[host-mcp.md](host-mcp.md).

## The proposal

In the first step the read list of the plugin holds nine exact lines, and
they hold for the profile `agent` only: the user of the host server in the
distro of site 1. A call to the Windows profile `agent-windows` with the same
line still makes a card, and so does each line outside the list and each
call with another argument. Each line reads the state of the distro with no
free operand, and the user runs each one with no admin right:

1. `uptime`
2. `free -m`
3. `ps -eo comm,pcpu,pmem --sort=-pcpu`
4. `df -h`
5. `ip addr show`
6. `ip route show`
7. `ss -t -l -n`
8. `uname -r`
9. `systemctl status --no-pager --lines=0 UNIT_1 UNIT_2 UNIT_3 UNIT_4 UNIT_5`

`UNIT_1` to `UNIT_5` are the timers and services of the house that the roles
of the Ansible tree install in the distro. The line in the config names them
exactly, in this order. Before the line goes in, a read in the distro
(`systemctl list-units 'tnwks-*' --all --no-legend`) confirms the set; a unit
that the site does not have leaves the line.

| Name | Unit | What it is |
| --- | --- | --- |
| `UNIT_1` | `tnwks-host-check.timer` | the drift check of the host |
| `UNIT_2` | `tnwks-gdrive-sync.timer` | the backup sync |
| `UNIT_3` | `tnwks-aruba-backup.timer` | the backup of the switch config |
| `UNIT_4` | `tnwks-frr.service` | the routing daemon of the site |
| `UNIT_5` | `tnwks-site-routing.service` | the routes of the site |

The list holds no journal line and no `docker` line: the user is in neither
the journal group nor the `docker` group, and those groups read each log or
are root. The PowerShell lines of the ten checks wait for the write step,
because each one is a `run-command`, which the gateway refuses in the read
step. The plugin takes one read profile today, so for those lines it takes a
list for each profile.

## The reason for each line

| Line | Check | Why this form |
| --- | --- | --- |
| `uptime` | `system` | Load and the time since boot |
| `free -m` | `system` | Memory and swap, from `/proc` |
| `ps -eo comm,pcpu,pmem --sort=-pcpu` | `system` | The processes by name, CPU and memory. `comm` holds no arguments, so no secret on a command line reaches the agent |
| `df -h` | `storage-devices` | The space of each file system |
| `ip addr show` | `network` | The addresses |
| `ip route show` | `network` | The routes (the routing of the site runs in the distro) |
| `ss -t -l -n` | `network` | The TCP ports that listen, with no name lookup. No `-p`: the process of another user needs root |
| `uname -r` | `wsl` | The kernel of WSL. The list of the distros is a Windows read, so it waits |
| `systemctl status --no-pager --lines=0 UNIT_1 ... UNIT_5` | `services` | The state of the units of the house. `--lines=0`: no journal lines |

Not covered in the distro by this list: `house-logs` (the user reads no
system journal) and `house-scripts` (a `sha256sum` line with the fixed paths
of the scripts that the distro roles install could cover it later).

## The lab

On 2026-11-22, in the lab form of `observability/tests/test_main_host_server.py`:
the server image of the Deployment with `role = "viewer"`, `group = "prod"`
and the denylist, against a fake Linux host (an unprivileged user, a pinned
host key) on an internal network, and the decision function of the plugin
from the agent image of the main Deployment, with these nine lines as
`readList`.

| Line | Server, viewer on prod | Plugin, `agent` | Plugin, `agent-windows` |
| --- | --- | --- | --- |
| `uptime` | allowed, ran | no card | card |
| `free -m` | allowed, ran | no card | card |
| `ps -eo comm,pcpu,pmem --sort=-pcpu` | allowed, ran | no card | card |
| `df -h` | allowed, ran | no card | card |
| `ip addr show` | allowed, ran | no card | card |
| `ip route show` | allowed, ran | no card | card |
| `ss -t -l -n` | allowed (the fake host has no `ss`) | no card | card |
| `uname -r` | allowed, ran | no card | card |
| `systemctl status ...` | allowed (the fake host has no `systemctl`) | no card | card |

Outside the list, `uptime -p`, `ps aux`, `cat /etc/passwd`,
`ip route get <address>`, `touch <file>` and `ping -c 1 <name>` each make a
card on both profiles. The server refuses `touch` (viewer on prod runs
read-only commands only) and `ping` (the denylist). A listed line with
another argument key makes a card, a listed line from another sender is
refused, and a `run-command` of a listed line makes a card (and the gateway
refuses that tool).
