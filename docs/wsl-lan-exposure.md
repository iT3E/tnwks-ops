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
TCP side re-derives it in `Sync-TnwksLanBridge.ps1`, which a Scheduled Task
(`Register-TnwksLanBridgeTask.ps1`) runs at logon and on Hyper-V `vmswitch`
NIC-up events. The UDP relays re-resolve it every 60 seconds.

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
| `k8s-gateway` | 10.5.0.201 | 53/UDP | not exposed: the router answers DNS | (`dns`, unused) |
| `ingress-nginx-controller` (public) | 10.5.0.202 | 80, 443 | never exposed: fronted by cloudflared | — |

The router's exports to the desktop:

- **NetFlow v9** (`/ip traffic-flow`) to `10.10.91.142:2055` for
  `monitoring/flow-collector`.
- **Syslog** (`/system logging action remotevector`) to `10.10.91.142:6001`
  over UDP for `monitoring/vector-aggregator`, which writes to Loki as
  `{job="routeros"}`. UDP rather than TCP because RouterOS writes TCP syslog
  with no message framing (no newline, no octet count), which no receiver
  can split reliably.

## Setup

Both tasks are idempotent and run from WSL. The Windows steps run elevated
through `infrastructure/wsl-host/invoke-elevated.sh`, so expect a UAC prompt.

```bash
task wsl-host:lan-bridge   # socat units (Ansible) + Windows portproxy and firewall
task wsl-host:udp-relays   # Windows UDP relays for 2055 and 6001
```

- `wsl-host:lan-bridge` runs `playbooks/wsl-lan-bridge.yml` (role
  `wsl_lan_bridge`: socat, `/usr/local/bin/tnwks-lan-bridge`, the
  `tnwks-lan-bridge@.service` template, one instance per entry in
  `wsl_lan_bridge_instances`), then `Sync-TnwksLanBridge.ps1`, which writes
  the portproxy rules, opens the TCP ports, and removes retired ones.
- `wsl-host:udp-relays` runs `Register-TnwksUdpRelays.ps1`, which lists the
  relays and registers each with `Register-TnwksUdpRelayTask.ps1`: the script
  is copied to `C:\ProgramData\tnwks-udp-relay`, an inbound UDP firewall rule
  is scoped to the senders, and the task runs as the desktop user (WSL distros
  are per-user, so SYSTEM cannot see `Ubuntu`).

To register the TCP sync's Scheduled Task (once per machine), run
`invoke-elevated.sh infrastructure/wsl-host/Register-TnwksLanBridgeTask.ps1`.

Adding a service: give it a fixed VIP in `cluster-settings.yaml`, add a
`case` arm to `tnwks-lan-bridge.sh` and an instance to the role defaults,
then the port to `Sync-TnwksLanBridge.ps1` (TCP) or `Register-TnwksUdpRelays.ps1`
(UDP), and re-run the matching task.

## Checking it

```bash
systemctl status 'tnwks-lan-bridge@*'
ss -tlnu | grep -E ':(80|443|1883|2055|6001)\b'
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
