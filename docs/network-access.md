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
- On Windows, use the WireGuard for Windows app: WSL egresses through Windows,
  so one tunnel covers both.
- WireGuard also answers on the out-of-band port, so a direct cable to `ether8`
  plus a tunnel with endpoint `192.168.88.1:51820` tests `wg01` without the
  network (used on the bench).
- Client `AllowedIPs` (split tunnel), at minimum: `10.10.30.0/24`,
  `10.98.0.0/24`, `10.10.90.0/24`, `10.10.140.0/24`, `10.60.10.0/24`.
- Terraform runs over `wg01`: set `routeros_url` in SOPS to
  `https://10.10.30.1`. From the WSL desktop this works as long as Windows has
  the tunnel up, since WSL egresses through Windows.

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
