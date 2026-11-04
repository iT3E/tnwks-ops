# Exposing WSL cluster services to the LAN

The `homelab-wsl` cluster runs as Talos-in-Docker inside WSL2 on the Windows
desktop (`10.10.91.142`, VLAN 910). MetalLB advertises VIPs on the docker
bridge `10.5.0.0/24`, which only the WSL distro can reach. LAN traffic gets to
them through a chain of forwarders on the desktop:

```
LAN device
  -> router DNS: *.internal.tnwks.us -> 10.10.91.142 (the Windows host)
  -> Windows: netsh portproxy (TCP) or tnwks-udp-relay (UDP) -> <wsl-ip>:port
  -> WSL: socat (tnwks-lan-bridge@<name>) -> MetalLB VIP:port
  -> the cluster Service
```

Why each hop exists:

- **Router DNS:** the LAN cannot route `10.5.0.0/24` (it lives behind WSL and
  Hyper-V), and clients should use names, not raw IPs. The router has one
  static record, `internal.tnwks.us` with `match-subdomain`, pointing at the
  Windows host (generated into `environments/prod/mikrotik/locals.tf`).
- **Windows forwarders:** WSL2's default NAT networking does not accept
  inbound LAN traffic. `netsh interface portproxy` forwards TCP to the WSL
  VM's address; it is TCP-only, so UDP services get a small relay
  (`Start-TnwksUdpRelay.ps1`), one Scheduled Task per port.
- **socat in WSL:** MetalLB VIPs live on the docker bridge, so a user-space
  bridge on the WSL host forwards from its eth0 to the VIP.

The WSL VM gets a new address on every WSL restart and Windows reboot. The
TCP side re-derives it in `Sync-TnwksLanBridge.ps1`, which the Scheduled Task
`tnwks-lan-bridge-sync` runs at logon and 15 seconds after each
Hyper-V VmSwitch EventID 232 (VM NIC connected). That event is written to the
**System** log; the `Microsoft-Windows-Hyper-V-VmSwitch-Operational` log never
receives it, so a trigger on that log never fires. The task runs as the
desktop user from `C:\ProgramData\tnwks-lan-bridge` and logs to `sync.log`
there. The UDP relays re-resolve it every 60 seconds.

> **Not taken:** WSL2 *mirrored* networking (`networkingMode=mirrored` in
> `.wslconfig`) would share the host's addresses and make most of this
> unnecessary. It is unvalidated with Talos-in-Docker's bridge networking, so
> the default NAT mode stays.

## What is exposed

| Service | VIP | Port | Windows side | WSL bridge |
| --- | --- | --- | --- | --- |
| `ingress-nginx-internal-controller` | 10.5.0.200 | 80, 443/TCP | portproxy | `http`, `https` |
| `mosquitto` | 10.5.0.203 | 1883/TCP | portproxy | `mqtt` |
| `flow-collector-netflow` | 10.5.0.204 | 2055/UDP | `tnwks-udp-relay`, from the LAN | `netflow` |
| `vector-aggregator` | 10.5.0.205 | 6001/UDP | `tnwks-udp-relay-syslog`, from the router only | `syslog` |
| `unifi-inform` | 10.5.0.206 | 8080/TCP | portproxy LAN 8080 -> WSL 18080, firewall scoped to `10.10.90.0/24` | `unifi-inform` (listens on 18080: Docker holds 8080) |
| `k8s-gateway` | 10.5.0.201 | 53/UDP | not exposed: the router answers DNS | (`dns`, unused) |
| `ingress-nginx-controller` (public) | 10.5.0.202 | 80, 443 | never exposed: fronted by cloudflared | — |

The Windows host's own monitoring agents (windows_exporter `:9182`,
smartctl_exporter `:9633`) are scraped by the cluster from inside the WSL NAT
subnet, and their firewall rules accept only that subnet, not the LAN. They are
installed and kept in line by the Ansible role `windows_monitoring`
(`task wsl-host:monitoring`; `task wsl-host:monitoring-check` reports drift
without changing anything, see [windows-ansible.md](windows-ansible.md)).

The router's exports to the desktop:

- **NetFlow v9** (`/ip traffic-flow`) to `10.10.91.142:2055` for
  `monitoring/flow-collector`.
- **Syslog** (`/system logging action remotevector`) to `10.10.91.142:6001`
  over UDP for `monitoring/vector-aggregator`, which writes to Loki as
  `{job="routeros"}`. UDP rather than TCP because RouterOS writes TCP syslog
  with no message framing (no newline, no octet count), which no receiver
  can split reliably.

## Setup

Both tasks are idempotent and run from WSL. The Windows halves are Ansible
roles in `playbooks/windows-host.yml`, over the host's OpenSSH server
([windows-ansible.md](windows-ansible.md)); preview them with
`task wsl-host:windows-check -- --tags lan-bridge,udp-relay`.

```bash
task wsl-host:lan-bridge   # socat units (WSL) + the Windows TCP half
task wsl-host:udp-relays   # Windows UDP relays for 2055 and 6001
```

- `wsl-host:lan-bridge` runs `playbooks/wsl-lan-bridge.yml` (role
  `wsl_lan_bridge`: socat, `/usr/local/bin/tnwks-lan-bridge`, the
  `tnwks-lan-bridge@.service` template, one instance per entry in
  `wsl_lan_bridge_instances`), then the Windows role `windows_lan_bridge`.
  That role copies `Sync-TnwksLanBridge.ps1` to
  `C:\ProgramData\tnwks-lan-bridge`, writes `ports.json` there from
  `lan_bridge_tcp_ports`, owns the firewall rules `tnwks-lan-bridge-<port>`
  (and removes those of `lan_bridge_retired_tcp_ports`), and registers the
  `tnwks-lan-bridge-sync` task (desktop user, S4U). When the script or the
  port list changes it runs the task once.
- The portproxy entries are the one part Ansible doesn't write: they point at
  the WSL VM's address, so the script sets them at run time. It leaves an
  entry alone when it already points at the right address, and removes
  entries of retired ports. The `2222` and `18789` entries (SSH and the
  OpenClaw gateway) are outside this bridge and untouched by it.
- `wsl-host:udp-relays` runs the role `windows_udp_relay`: it copies
  `Start-TnwksUdpRelay.ps1` to `C:\ProgramData\tnwks-udp-relay`, scopes each
  inbound UDP firewall rule `tnwks-udp-relay-<port>` to its senders, and keeps
  one task per relay (`udp_relays`) running as the desktop user (WSL distros
  are per-user, so SYSTEM cannot see `Ubuntu`). A new script restarts both
  relays, which drops a few seconds of NetFlow and syslog.

Adding a service: give it a fixed VIP in `cluster-settings.yaml`, add a
`case` arm to `tnwks-lan-bridge.sh` and an instance to the role defaults,
then the port to `lan_bridge_tcp_ports` (TCP; `wsl_port` when the WSL side
must listen on a different port, `remote` to scope the firewall rule) or
`udp_relays` (UDP), and re-run the matching task.

## Checking it

```bash
systemctl status 'tnwks-lan-bridge@*'
ss -tlnu | grep -E ':(80|443|1883|2055|6001|18080)\b'
```

```powershell
netsh interface portproxy show v4tov4
Get-ScheduledTask -TaskName 'tnwks-*'
Get-Content C:\ProgramData\tnwks-udp-relay\udp-relay-6001.log -Tail 5
```

## When something doesn't work

- **`NXDOMAIN` for `<app>.internal.tnwks.us`:** the router's static record is
  missing (`/ip dns static print where name~"internal"` on the router).
- **Timeout from the LAN on 80/443:** the portproxy points at a stale WSL
  address. `Start-ScheduledTask -TaskName tnwks-lan-bridge-sync`, or re-run
  `task wsl-host:lan-bridge`.
- **Connection refused on the Windows host:** a socat unit is down;
  `systemctl status tnwks-lan-bridge@https`.
- **502 from the ingress:** not a bridge problem; the Service has no
  endpoints (`kubectl -n <ns> get endpoints`).
- **No flows or no router logs:** check the relay log for that port
  (`udp-relay-2055.log`, `udp-relay-6001.log`), then the socat unit, then
  the VIP.
