# Network access and break-glass

How people reach the sensitive parts of the network once the RB5009 is the
router, and how to get back in when the normal path is broken.

## Access model

People reach sensitive systems **only over the admin VPN (`wg01`)**, or from an
admin peer on another listener (your phone, `it-mobile` on `wg02`, matched by its
tunnel address). Services keep their own narrow paths. Nothing is reachable from
the WAN except WireGuard.

| System | Address | People | Services |
| --- | --- | --- | --- |
| RouterOS (SSH 22, REST 443, Winbox 8291) | `10.10.30.1` on `wg01` | `wg01`, admin peers | — |
| RouterOS read-only API (api-ssl 8729) | any router address | — | mktxp, from the WSL host only |
| RouterOS REST 443, for CI | `10.10.32.1` on `wg03` | — | the self-hosted runner `tnwks-ops-runner` only: its peers `10.10.32.10` and `.11` (its own listener and zone; [`ci.md`](ci.md)) |
| EP-R6 SSH, for CI | `10.10.140.140` | the [device access pod](#device-access-pod) | the same runner, through `wg03` (`infrastructure/edgeos`) |
| Radio SSH | `10.60.10.45` (LiteAP), `10.60.10.43` (LiteBeam) | the device access pod | every `wg03` peer (for later CI config plans) |
| Radio web UI 443 | the same two | the device access pod only (source `10.10.32.12`) | — |
| Aruba SW03 management | `10.98.0.251` (untagged native VLAN) | `wg01`, admin peers | — |
| UniFi APs | VLAN 900 | `wg01`, admin peers | UniFi controller (SSH adopt, STUN); APs inform to it |
| UISP radios | VLAN 140, `10.60.10.0/24` via EP-R6 | `wg01`, admin peers | radios connect out to UISP |
| Cameras | VLAN 610 | `wg01` | Frigate RTSP (tcp/udp 554) |

`wg02` (`vpn-mobile`) is otherwise not an admin VPN: other peers on it get the
internet and 910 only. Admin peers are listed in the generator's `ADMIN_PEERS`.

The UniFi controller and UISP UIs run on the WSL cluster behind 910's ingress,
so the router cannot restrict them; they are protected by their own logins.

## Normal access: `wg01`

- Peers: `it-pc01`, `macbook-it`, `xps-it` (`locals.tf`, `wireguard_interfaces`).
- Endpoint: the DDNS hostname (`ddns_host`.`ddns_domain` in SOPS) from outside;
  `10.10.91.1:51820` from home. WireGuard is accepted from the LAN for exactly
  this.
- Router public key: `terraform output wireguard_public_keys`.
- On the desktop the tunnel runs in WSL (`wg-quick`), so it serves WSL tools
  (SSH, Terraform, curl). Windows apps (a browser on the switch UI) do not use
  it; add the WireGuard for Windows app if that is ever wanted.
- WireGuard also answers on the out-of-band port, so a direct cable to `ether8`
  plus a tunnel with endpoint `192.168.88.1:51820` tests `wg01` without the
  network (used on the bench).
- Client `AllowedIPs` (split tunnel), at minimum: `10.10.30.0/24`,
  `10.98.0.0/24`, `10.10.90.0/24`, `10.10.140.0/24`, `10.60.10.0/24`.
- Terraform runs over `wg01` (`routeros_url` in SOPS is
  `https://10.10.30.1`), so the WSL tunnel must be up.

## Device access pod

`device-access` in `tools` on homelab-wsl
(`kubernetes/apps/tools/device-access`) reaches the EP-R6 and the two radios
behind it over the runners' tunnel, without `wg01`. Its wireproxy sidecar
holds the `wg03` peer `10.10.32.12` and forwards, inside the pod only:

| Pod address | Target | Router rule (pair `180-vpn-ci-UISP-140`) |
| --- | --- | --- |
| `127.0.0.1:8022` (`ep-r6`) | EP-R6 SSH `10.10.140.140:22` | every `wg03` peer |
| `127.0.0.1:8023` (`liteap`) | LiteAP radio SSH `10.60.10.45:22` | every `wg03` peer |
| `127.0.0.1:8024` (`litebeam`) | LiteBeam radio SSH `10.60.10.43:22` | every `wg03` peer |
| `127.0.0.1:8443` | LiteAP web UI `10.60.10.45:443` | source `10.10.32.12` only |
| `127.0.0.1:8444` | LiteBeam web UI `10.60.10.43:443` | source `10.10.32.12` only |

Nothing else: no other address on VLAN 140 or elsewhere, and not the router's
REST API, which `wg03` accepts only from the runners. WireGuard binds each
peer's source address to its key, so `10.10.32.12` is this pod.

Who can use it: anyone who can `kubectl exec` or `kubectl port-forward` in
`tools` on homelab-wsl, that is, the cluster admins. It has no Service or
Ingress. It is the interim until an MCP server does this.

```sh
# EP-R6, with CI's key. The login is ssh_user in
# infrastructure/edgeos/ep-r6/secrets.sops.yaml.
kubectl -n tools exec -it deploy/device-access -c shell -- ssh -l <user> ep-r6
kubectl -n tools exec deploy/device-access -c shell -- \
  ssh -l <user> ep-r6 /opt/vyatta/bin/vyatta-op-cmd-wrapper show interfaces
# The radios: airOS password login, so a TTY.
kubectl -n tools exec -it deploy/device-access -c shell -- ssh -l <user> liteap
kubectl -n tools exec -it deploy/device-access -c shell -- ssh -l <user> litebeam
# A radio's web UI in the desktop's browser, at https://localhost:8443
# (LiteAP) or :8444 (LiteBeam). Apply changes with Test mode, which reverts
# them by itself.
kubectl -n tools port-forward deploy/device-access 8443 8444
```

The EP-R6's host key is pinned (`ssh/known_hosts`, from
`infrastructure/edgeos/ep-r6/device.json`). The radios' keys are not yet: the
first login accepts and records each key in the pod's `/tmp`. To pin them,
run `ssh-keyscan -p 8023 127.0.0.1` and `-p 8024` in the pod, add the lines
to `kubernetes/apps/tools/device-access/app/ssh/known_hosts`, and set
`StrictHostKeyChecking yes` for the radios in `ssh/device-access.conf`.

## Break-glass

Work down the list; each step needs less to be working than the one before.

### 1. VPN down, router up: the out-of-band port

`ether8` is never bridged and always accepts SSH, REST and Winbox, ahead of
every other input rule.

1. Cable a laptop to `ether8`, set it to `192.168.88.2/24`.
2. `https://192.168.88.1`, Winbox, or `ssh <admin>@192.168.88.1`.
3. Credentials: `routeros_username`/`routeros_password` (Terraform account) or
   `admin_username` + its SSH key/`admin_password`, all in
   `infrastructure/terraform/environments/prod/mikrotik/secrets.sops.yaml`.

### 2. Reaching the switch, APs or radios without the VPN

The router's own traffic is not subject to the forward chain, so hop through
it from step 1:

```
/system ssh address=10.98.0.251 user=<switch admin>
/system ssh address=<ap or radio address> user=<device admin>
```

Physically: the Aruba S2500 has an RJ45 console port (9600 8N1). UniFi APs and
UISP radios can be factory-reset and re-adopted.

### 3. Router reachable only by console

If even `ether8` does not answer (a broken bridge or address change), use the
RB5009's RJ45 serial console (115200 8N1) and log in with the admin account.
Use Safe Mode (`Ctrl+X`) for any hand edit, so a change that cuts you off is
rolled back when the session drops. Common fixes:

```
/ip firewall filter print where chain=input
/ip firewall filter disable [find comment~"zzz input"]   # stop dropping input
```

### 4. Router config unrecoverable

Everything is in git and SOPS, so rebuild instead of repairing:

1. `/system reset-configuration no-defaults=yes skip-backup=yes`
2. Paste `infrastructure/mikrotik/bootstrap/routeros-bootstrap.rsc` over the
   console or Winbox-by-MAC on `ether8`.
3. `task mikrotik:plan` / `task mikrotik:apply` over `ether8`.

### Secrets for all of the above

Steps 1 to 4 need `secrets.sops.yaml` decrypted, which needs the SOPS age key
or the AWS KMS role. Keep an offline copy of the age key and the RouterOS admin
password somewhere that does not depend on the desktop or the network. Without
them, only the console plus step 4 with fresh credentials remains, followed by
updating SOPS.

## After break-glass

Hand changes are temporary. The next `terraform apply` reverts them, and `main`
is the source of truth. Commit the real fix and apply it; do not leave the
router running on a hand edit.
