# Exposing WSL cluster services to the LAN

The `homelab-wsl` cluster runs inside a Talos-in-Docker setup on a Windows host.
MetalLB advertises VIPs on the docker bridge subnet `10.5.0.0/24`, which is only
reachable from inside that WSL distro. To make `*.tnwks.local` services
accessible to the rest of the LAN, we chain three forwarders together:

```
LAN device
  → VyOS DNS rewrites *.tnwks.local → <windows-host-ip>
  → Windows: netsh portproxy on :80/:443/:1883 → 127.0.0.1
  → WSL2 localhost-forwarding maps 127.0.0.1 → eth0 inside the WSL distro
  → socat (systemd unit) bridges 0.0.0.0:port → MetalLB VIP
  → MetalLB IP (10.5.0.200 ingress, 10.5.0.203 mqtt) inside the cluster
```

Why each hop exists:

- VyOS rewrite: the LAN can't route `10.5.0.0/24` (it lives behind WSL/Hyper-V)
  and we want hostnames, not raw IPs.
- Windows portproxy: WSL2 mirrors `127.0.0.1` between the Windows host and the
  Linux distro, but that mirroring only happens for traffic *originated* on the
  Windows host. portproxy is what flips inbound LAN traffic onto that mirror.
- socat in WSL: Windows can only forward to `127.0.0.1` inside the WSL VM.
  MetalLB VIPs live on the docker bridge, not on `127.0.0.1`, so we need a
  user-space bridge.

## Current MetalLB allocations (homelab-wsl)

| Service                              | VIP        | Ports            |
|--------------------------------------|------------|------------------|
| `ingress-nginx-internal-controller`  | 10.5.0.200 | 80, 443          |
| `k8s-gateway`                        | 10.5.0.201 | 53/UDP           |
| `ingress-nginx-controller` (public)  | 10.5.0.202 | 80, 443          |
| `mosquitto`                          | 10.5.0.203 | 1883             |

The public ingress controller (`.202`) is intentionally **not** bridged here —
that one is fronted by the cloudflared tunnel and should never be reachable
from the LAN side.

## One-time setup on the WSL distro

```bash
sudo apt-get install -y socat

sudo install -m 0755 \
  infrastructure/wsl-host/tnwks-lan-bridge.sh \
  /usr/local/bin/tnwks-lan-bridge

sudo install -m 0644 \
  infrastructure/wsl-host/tnwks-lan-bridge@.service \
  /etc/systemd/system/tnwks-lan-bridge@.service

sudo systemctl daemon-reload
sudo systemctl enable --now \
  tnwks-lan-bridge@http.service \
  tnwks-lan-bridge@https.service \
  tnwks-lan-bridge@mqtt.service
```

Verify each unit is active:

```bash
systemctl status 'tnwks-lan-bridge@*.service'
ss -tlnp | grep -E ':80|:443|:1883'
```

## One-time setup on Windows (run in an elevated PowerShell)

```powershell
cd \\wsl$\Ubuntu\home\it3e\git-public\tnwks-ops\infrastructure\wsl-host
.\Sync-TnwksLanBridge.ps1
.\Register-TnwksLanBridgeTask.ps1
```

`Sync-TnwksLanBridge.ps1` is idempotent and is what actually installs the
portproxy rules. `Register-TnwksLanBridgeTask.ps1` registers a Scheduled Task
that re-runs the sync at every user logon and whenever Hyper-V logs a vmswitch
NIC-up event — that handles the WSL VM IP changing across reboots.

Confirm:

```powershell
netsh interface portproxy show v4tov4
Get-ScheduledTask -TaskName tnwks-lan-bridge-sync
```

## VyOS DNS rewrite

On the VyOS box, point all `*.tnwks.local` lookups at the Windows host. With
the dnsmasq-style forwarding-options set:

```
set service dns forwarding domain 'tnwks.local' addresses '10.10.91.142'
commit ; save
```

(Replace `10.10.91.142` with the current Windows host IPv4 if it changes.)

If you'd rather have the LAN hit `k8s-gateway` directly for `tnwks.local`, you
can instead add a TCP-only forwarder for port 53 — but `k8s-gateway` only
listens on UDP, and netsh portproxy is TCP-only, so the cleanest path is to
keep DNS authority on VyOS and have the Windows host only forward HTTP(S) +
MQTT.

## What to do when something doesn't work

- LAN device gets `NXDOMAIN` for `prometheus.tnwks.local`: VyOS forward rule
  missing or stale; check `show dns forwarding statistics`.
- `curl https://prometheus.tnwks.local` from the LAN times out: portproxy is
  pointing at a stale WSL IP. Run the Scheduled Task once
  (`Start-ScheduledTask -TaskName tnwks-lan-bridge-sync`) or just reboot.
- Connection refused on the Windows host: the socat units inside WSL aren't
  running. `systemctl status tnwks-lan-bridge@http`.
- 502 from the ingress: not a LAN-bridge issue — the upstream Service has no
  endpoints. Debug with `kubectl -n <ns> get endpoints`.
