# Business continuity and disaster recovery

How the household keeps working when something breaks, and how to rebuild.
What is backed up, and the restore commands, are in
[`backups.md`](backups.md); this page is the plan around them.

## What matters, in order

| Tier | Service | Depends on | Target to restore (RTO) | Data loss accepted (RPO) |
| --- | --- | --- | --- | --- |
| 1 | Internet, Wi-Fi, DNS, DHCP | MikroTik router, SW03, the APs | hours | none (config in git, switch config in Drive) |
| 2 | Home Assistant, Mealie, the kitchen display | WSL cluster, Postgres | 1 day | 24 h of config, minutes of database |
| 3 | Everything else on the cluster | WSL cluster | days | 24 h, or none (rebuildable) |
| 3 | Monitoring | WSL cluster | days | all history |

Tier 1 does not depend on the cluster. The router serves DNS and DHCP itself,
and the APs keep running their last config with no controller. So a dead
desktop means no Home Assistant and no dashboards, but the house stays online.

## Single points of failure

| What | If it fails | Mitigation |
| --- | --- | --- |
| The desktop (the whole WSL cluster, local-path PVCs, the UniFi controller) | Tier 2 and 3 down | Off-site backups (S3, Drive); rebuild from git |
| MikroTik RB5009 | No internet, DNS, DHCP, VPN | Config in Terraform; break-glass sheet in Drive `Backup/tnwks-ops-router-break-glass.txt`. The old VyOS box and EdgeRouter are kept powered off as a fallback |
| Aruba SW03 | Wired LAN and the APs down | Config in Drive `Backup/aruba` |
| An AP | One room without Wi-Fi | Replace, add it to `environments/prod/unifi`, adopt ([`../network/unifi.md`](../network/unifi.md)) |
| ISP | No internet | None; LAN services keep working |
| AWS prod account | Cluster backups, SOPS KMS, Cognito logins | age keys still decrypt every secret; Drive copies of UniFi and switch config |
| GitHub | Source of truth, CI | Local clones on the desktop; Flux's own copy |
| Google account | Drive copies | Cluster backups are in S3 |

## Accounts and keys needed to recover

Everything else can be rebuilt from these. If a credential's only copy is on the
desktop, a dead desktop takes it too: that is what the off-site kit is for.

| Key or account | Where it lives | Off-site copy |
| --- | --- | --- |
| GitHub account (2FA) | the owner | recovery codes in Drive `Backup/github-recovery-codes.txt` |
| age keys: personal (`~/.config/sops/age/keys.txt`) and per-cluster (`homelab-wsl.txt`, Secret `flux-system/sops-age`) | desktop, cluster | **off-site kit** |
| AWS KMS `kms-sops` (second way into every SOPS file) | AWS management account, via Roles Anywhere | n/a (needs the AWS account) |
| Roles Anywhere CA key | `environments/prod/aws/rolesanywhere/ca-key.sops.yaml` | covered by the age keys |
| `KOPIA_PASSWORD` (decrypts the kopiur backups) | Secret `kopiur-system/kopiur-repository` (SOPS) | **off-site kit** (the SOPS copy needs a working age key) |
| Terraform Cloud | the owner's TFC account | n/a |
| AWS root / admin access | the owner | **off-site kit**: root MFA recovery |
| Google account | the owner | its own recovery options |
| Router break-glass | Drive | **off-site kit**, printed |
| Future PKI root CA key | not yet created | **off-site kit** only, never online |

### Off-site keys

Plan: one small **recovery kit**, kept as two copies in two places (one at home,
one off-site), checked every year at the drill.

- **What goes in it:** the age private keys, `KOPIA_PASSWORD`, the router
  break-glass sheet, AWS root MFA recovery, and later the PKI root CA key and
  its certificate.
- **Media:** paper plus flash.
  - Paper: an age key is a single 74-character line, so print it as text and
    a QR code. Paper in a safe outlasts any flash device.
  - Flash: two **industrial or pSLC** SD cards or USB sticks, not consumer
    ones. Unpowered consumer flash can lose data within a few years, so read
    and re-write the cards at each drill. Encrypt the bundle with a passphrase
    you can remember (`age -p`, or a VeraCrypt or LUKS container) so a lost card
    leaks nothing.
- **PKI:** when the internal CA exists, generate the root on an offline
  machine, keep its key only in this kit, and run day-to-day issuance from an
  intermediate. The Roles Anywhere CA can move under that root at the same
  time.

Until the kit exists, the age keys and `KOPIA_PASSWORD` live only on the
desktop and in the cluster. AWS KMS still decrypts SOPS, but `KOPIA_PASSWORD`
is itself a SOPS secret, so losing both the desktop and the AWS account
would leave the kopiur backups unreadable.

## Scenarios

### An app's data is broken (bad upgrade, corrupt config)

1. Postgres: point-in-time restore to just before the change, into a scratch
   Cluster first if unsure ([`backups.md`](backups.md#postgres)).
2. A config PVC: scale the app to 0, kopiur `Restore` into the PVC
   ([`backups.md`](backups.md#a-pvc-kopiur)), scale back up.

RTO: under an hour. RPO: minutes (Postgres), last night (PVCs).

### The Postgres Cluster is lost

Recovery bootstrap from S3, as in [`backups.md`](backups.md#postgres). Every
app database comes back together. RTO: about an hour.

### The desktop's disk dies, or the WSL cluster is unrecoverable

The house stays online (tier 1 is independent). To rebuild:

1. Replace the disk or machine; reinstall Windows, WSL and Docker Desktop.
2. Restore the personal age key to `~/.config/sops/age/keys.txt` from the
   off-site kit, and reissue the workstation Roles Anywhere cert
   (`task aws:ra-cert`).
3. Rebuild the cluster: [`../wsl/bootstrap.md`](../wsl/bootstrap.md). Flux
   recreates every app from git; the `sops-age` Secret comes from the kit.
4. Postgres: switch the Cluster to `bootstrap.recovery`
   ([`backups.md`](backups.md#postgres)). Until then apps that need it fail
   their readiness checks; that is expected.
5. The kopiur repository and the STS refresher come up on their own (the
   backup certificate is in git, under SOPS). Restore each PVC in the backed-up
   list once its app has created its PVC.
6. UniFi: restore the newest `.unf` from Drive. The APs reconnect on their own
   once the router's `unifi` DNS record points at the new desktop.
7. Reinstall the desktop timers: `task wsl-host:lan-bridge`,
   `task wsl-host:unifi-backup`, `task wsl-host:aruba-backup`.

RTO: a day. RPO: last night for PVCs, minutes for Postgres.

### The router dies

Replace it with another RB5009 and apply the Terraform
(`task mikrotik:apply`, [`../network/ci.md`](../network/ci.md)). Until then,
the break-glass sheet covers manual access; the powered-off VyOS and
EdgeRouter are the emergency fallback ([`../network/access.md`](../network/access.md)).

### The switch dies

Replace it with any managed switch and rebuild the VLAN config from the newest
file in Drive `Backup/aruba` ([`backups.md`](backups.md#aruba-sw03)). The
VLAN layout is also in [`../network/architecture.md`](../network/architecture.md).

### The house is lost (fire, theft)

Everything needed is off-site: the repo (GitHub), Terraform state (TFC),
backups (S3 and Drive), and the keys (off-site kit). Rebuild on new hardware
in the order above: router, switch, APs, then the cluster.

### The AWS account is lost or compromised

- Lost: the S3 backups go with it. SOPS still works on age. Stand up a
  new bucket (any S3 provider: point `BACKUP_S3_BUCKET` and the
  ClusterRepository and ObjectStore at it) and take fresh backups at once.
- Compromised: remove `homelab-wsl-backups` from
  `rolesanywhere_backups_common_names` and apply, which revokes the cluster's
  access immediately. Versioning keeps 30 days of history even if
  objects were deleted; the backup role can't change versioning or lifecycle.

### GitHub is lost

Push a local clone (desktop) to a new remote, or copy Flux's checkout out of
`source-controller` before touching the cluster. Then re-point Flux
(`flux bootstrap github`) at the new remote.

### Secrets: a SOPS key is lost

Every file has three age recipients plus AWS KMS, so losing one key is
harmless: re-key with `sops updatekeys` ([`sops.md`](sops.md)). Losing all of
them makes the secrets unrecoverable; recreate them from the vendors and
re-encrypt.

### Flux has drifted

```bash
flux suspend kustomization flux-system
# investigate the divergence
flux reconcile kustomization flux-system --with-source
flux resume kustomization flux-system
```

### MS-01: Ceph storage lost

MS-01 is down. When it runs again, its Postgres backs up through the same
barman-cloud config (its own `serverName`, `homelab-ms-01`). If Rook Ceph is
unrecoverable but the nodes are healthy: scale workloads down, delete the
CephCluster (not the operator), wipe the Ceph disks, let Flux recreate it, then
restore Postgres and PVCs from S3.

## Drills

Once a year, and after any change to the backup setup:

- [ ] Postgres: restore into a scratch Cluster (`postgres-restore`) and query
      a table from each app database. Delete it.
- [ ] kopiur: restore one PVC's newest snapshot into a scratch PVC and compare
      a few files with the live one.
- [ ] UniFi: open the newest `.unf` in Drive (it should be from last night).
- [ ] Aruba: the newest `Backup/aruba` file ends in `end`.
- [ ] Credentials: the backup certificate isn't within 60 days of expiry
      ([`backups.md`](backups.md#credentials)); renew if it is.
- [ ] Off-site kit: read each card, check the keys decrypt a SOPS file and
      unlock the kopiur repository, re-write the cards, update the paper copy
      if anything changed.

Drill log:

| Date | What | Result |
| --- | --- | --- |
