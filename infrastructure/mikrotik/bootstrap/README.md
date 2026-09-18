# MikroTik bootstrap

What Terraform cannot do to itself.

Terraform authenticates to the RouterOS REST API, so it cannot be the thing that
creates the API listener, the TLS certificate, the service account it logs in
with, or the IP address it connects over. Those four things live here.

## Files

| File | What it is |
| --- | --- |
| `routeros-bootstrap.rsc` | First-touch console script. Pasted into Winbox/serial on a factory-reset RB5009. |
| `../../ansible/playbooks/mikrotik-bootstrap.yml` | Repeatable post-bootstrap verification: asserts RouterOS 7.x, snapshots the running config, checks `www-ssl`. |
| `../../ansible/inventory/mikrotik.yml` | Inventory. Credentials come from SOPS via env, not from the file. |

## Order

```
routeros-bootstrap.rsc   (console, once, physical access)
      ↓
curl -k -u terraform:<pw> https://10.98.0.1/rest/system/resource
      ↓
task mikrotik:bootstrap  (Ansible verify + snapshot)
      ↓
task mikrotik:tfvars     (regenerate from VyOS source)
      ↓
task mikrotik:plan  →  task mikrotik:apply
```

## Secrets

Create `secrets.sops.yaml` in the Terraform root module before the first apply:

```bash
cd ../../terraform/environments/prod/mikrotik
cp secrets.sops.yaml.example secrets.sops.yaml
# fill in real values
sops -e -i secrets.sops.yaml
```

The repo `.sops.yaml` already covers `infrastructure/terraform/.*\.sops\.ya?ml`,
so the right age recipients and the AWS KMS key are picked up automatically.

Generate the Terraform account password with `openssl rand -base64 24`. Do not
reuse an existing device password: the homelab already has credential-reuse debt
flagged since April.

## Two things that will bite

**Apply with physical access to the router.** The port adds an explicit `input`
chain ending in a drop, and moves VLANs onto a VLAN-filtering bridge. A mistake
in either locks you out. You want console access when that happens, not after.

**`admin` removal is last, not first.** `routeros-bootstrap.rsc` creates the real
accounts before removing the default `admin`. Verify both new accounts work in a
second session before running that line.

## Container package

`/system device-mode update container=yes` requires **physical access**: it forces
a hardware confirmation via reset button or power cycle. This port does not use
containers, so skip it. Relevant only if the container-disposition decisions in
`docs/mikrotik-vyos-port.md` get reversed, which the hardware argues against
(1GB RAM, 1GB NAND, no M.2).
