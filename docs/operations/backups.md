# Backups

What the homelab backs up, where it goes, how it authenticates, and how to get
it back. The scenario playbooks that use these backups are in
[`disaster-recovery.md`](disaster-recovery.md).

## At a glance

| What | Tool | When | Where | Kept | RPO |
| --- | --- | --- | --- | --- | --- |
| Postgres (every app database) | CloudNativePG barman-cloud plugin | WAL continuously, base backup 01:00 | S3 `cnpg/<cluster>/` | 30 days, point-in-time | minutes |
| App config PVCs (list below) | kopiur (Kopia) | nightly 02:00-03:00 | S3 `kopiur/` | 3 latest, 7 daily, 4 weekly, 6 monthly | 24 h |
| UniFi controller | controller auto-backup (`.unf`) + `tnwks-unifi-backup` | 02:00, copied 02:30 | controller PVC + Google Drive `Backup/unifi` | 14 on the controller, 30 in Drive | 24 h |
| Aruba SW03 config | `tnwks-aruba-backup` | 02:45, saved when changed | Google Drive `Backup/aruba` | 60 versions | 24 h |
| Everything declarative | git | every merge | GitHub | full history | 0 |
| Terraform state | Terraform Cloud | every apply | TFC workspaces | TFC history | 0 |

S3 is `tnwks-homelab-backups-654654262098` in the prod AWS account,
`us-west-2` (`infrastructure/terraform/modules/aws/s3_backups.tf`).

## Decisions

- **AWS S3 in the prod account** for anything in the cluster. It is off the
  desktop, already managed by Terraform, and both tools treat S3 as their
  reference backend. Cost is cents a month at this size.
- **Google Drive** (the desktop's Drive for desktop sync, `G:\My Drive\Backup`)
  for single-file artefacts produced outside the cluster: the UniFi `.unf` and
  the switch config. Drive handles a few files well and a Kopia repository
  (thousands of small blobs over WSL's drvfs) badly, so it is never a
  repository target.
- **barman-cloud for Postgres**, the CloudNativePG plugin (bjw-s runs the same).
  Continuous WAL archiving gives point-in-time recovery; a nightly file copy of
  a live database would not even be consistent.
- **kopiur for PVCs**, the Kopia operator onedr0p and bjw-s moved to from
  VolSync + restic. Client-side encryption (`KOPIA_PASSWORD`), deduplication,
  GFS retention, and a `Restore` CR. It is alpha (`v1alpha1`), so the CRDs may
  change between releases: read its changelog before Renovate bumps.
- **`copyMethod: Direct`.** local-path has no CSI snapshots, so the mover reads
  the live PVC read-only on the app's node. That is crash-consistent, which is
  fine for config directories. Databases never go through kopiur.
- **IAM Roles Anywhere, no IAM user.** See [Credentials](#credentials).
- **Daily, small, incremental.** The home uplink peaked at 7.7 Mbit/s over a
  week (median 0.4), so everything has to be small. It is: Postgres is about
  350 MB (base backup well under that compressed), the kopiur set about 100 MB
  before deduplication, and every later run uploads only changes.
  The one steady stream is WAL: Home Assistant's recorder writes about 1.5 GB a
  day uncompressed, so the Cluster sets `wal_compression: zstd` and the plugin
  compresses segments with zstd before upload. Spread over the day that is a
  small fraction of the uplink.
- **Versioning as the undo window.** The bucket keeps deleted and overwritten
  objects for 30 days, and the backup role can't touch bucket settings. A
  leaked session (or a bad retention change) can't destroy history.

## What is backed up

### Postgres

The single `databases/postgres` Cluster holds every app database: `app`
(Mealie), `home_assistant` (recorder history), `bitwarden`, `litellm`,
`plausible` (metadata), the storefront API, `vantrip`, `grafana`. All of it is covered by
barman-cloud; there is nothing per-database to configure. New databases are
covered automatically.

### PVCs (kopiur)

`kubernetes/apps/kopiur-system/kopiur/policies/`, one file per PVC:

| App | PVC | Why |
| --- | --- | --- |
| Home Assistant | `home-automation/config-home-assistant-0` | `.storage` (integrations, device and entity registries, auth), dashboards, custom components. Recorder history is in Postgres; `configuration.yaml` and packages are also in git |
| Mealie | `production/mealie-config` | Recipe images and the app's secrets. Recipes are in Postgres |
| UniFi | `networking/config-unifi-0` | Includes the controller's `.unf` auto-backups. The MongoDB PVC is skipped: a `.unf` restore is the supported path |
| changedetection | `production/config-changedetection-0` | Watch list and history |

To add one: copy a policy file, change the name, namespace and PVC, add the
namespace to `allowedNamespaces` in `repository/clusterrepository.yaml` if it is
new, and list the file in `policies/kustomization.yaml`.

### Deliberately not backed up

| What | Why |
| --- | --- |
| Immich (library, Postgres, model cache) | Not needed |
| Frigate media and config | Config is in git; recordings aren't wanted |
| Vaultwarden `/config` | Not in use. Its database is in Postgres anyway |
| zwave-js-ui | No Z-Wave network is configured (the store has no `settings.json`, so no network keys). Add it the day a network exists: losing the keys means re-pairing every device |
| Mosquitto | Retained messages rebuild as devices publish |
| Grafana PVC | Dashboards are in git, its database is in Postgres |
| Prometheus, Loki, Tempo, Alertmanager | Telemetry |
| Media stack (sonarr, radarr, prowlarr, recyclarr, qbittorrent, jellyfin, ombi, wizarr) | Rebuildable; revisit if re-configuring them ever hurts |
| Plausible ClickHouse | Analytics events; acceptable loss |
| Obsidian CouchDB | Sync relay; the vault lives on the devices |
| peloton-to-garmin, Valkey, `nfs-server-data` | Re-auth, cache, built from git |
| Windows host, the WSL disk image | Out of scope for now |
| MS-01 cluster | Down. Its Postgres Cluster shares the barman config (its own `serverName`) |

## Credentials

```
cert CN homelab-wsl-backups  (Secret kopiur-system/aws-backup-certificate, SOPS)
  -> CronJob kopiur-system/aws-backup-credentials, every 30 min
     aws_signing_helper credential-process -> Roles Anywhere (prod, us-west-2)
  -> role tnwks-rolesanywhere-backups: S3 object read/write on the bucket only
  -> 4-hour STS session -> Secret aws-backup-sts in databases/ and kopiur-system/
       databases: the barman-cloud ObjectStore reads it on each upload
       kopiur-system: the ClusterRepository; kopiur projects a fresh copy into
       the app namespace before every mover run
```

- Same trust anchor and CA as the SOPS workstation certificates
  (`environments/prod/aws/rolesanywhere/`), separate role and profile
  (`modules/aws/rolesanywhere.tf`). The role trusts only CN
  `homelab-wsl-backups`; removing it from
  `rolesanywhere_backups_common_names` and applying revokes the cluster
  immediately.
- A refresher outage leaves the last session valid for up to 4 hours. A
  failed run raises `KubeJobFailed`.
- **The certificate expires 2027-11-02.** Renew before then:

  ```bash
  T=$(mktemp -d)
  infrastructure/terraform/environments/prod/aws/rolesanywhere/issue-cert.sh homelab-wsl-backups "$T" 365
  # put $T/homelab-wsl-backups.pem / .key into tls.crt / tls.key:
  sops kubernetes/apps/kopiur-system/aws-backup-credentials/app/secret-certificate.sops.yaml
  rm -rf "$T"
  ```

- **`KOPIA_PASSWORD`** (`kopiur-system/kopiur-repository`, SOPS) encrypts every
  kopiur snapshot. Without it the `kopiur/` data is unreadable, so it belongs on
  the off-site key media with the age key ([BCP](disaster-recovery.md#off-site-keys)).
- After a fresh install the STS Secrets hold placeholders until the first
  refresh. Run one now instead of waiting:

  ```bash
  kubectl -n kopiur-system create job --from=cronjob/aws-backup-credentials sts-now
  ```

## Checking backups

```bash
# Postgres: last base backup, WAL archiving
kubectl -n databases get backups
kubectl cnpg status postgres -n databases     # "Continuous Backup status"
# kopiur
kubectl get snapshotpolicies,snapshots -A
# STS session expiry
kubectl -n databases get secret aws-backup-sts -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
# Desktop timers (UniFi copy, switch config)
systemctl list-timers 'tnwks-*-backup.timer'
journalctl -u tnwks-unifi-backup -u tnwks-aruba-backup -n 20
```

Alerts: `PostgresBackupStale` (no base backup in 30 h), `PostgresWALArchivingStalled`,
`LastFailedArchiveTime` (cnpg rules); kopiur's own PrometheusRule (failed or
stale snapshots); `KubeJobFailed` for the credentials CronJob. The desktop
timers have no alert yet: check the journal.

## Restoring

Untested backups don't count. Run a restore drill after any change to this
setup and at least once a year ([BCP](disaster-recovery.md#drills)).

### Postgres

Recovery creates a Cluster from the object store; it can't overwrite a running
one. On the same cluster, after losing the database:

1. Edit `kubernetes/apps/databases/cloudnative-pg/cluster/cluster.yaml`:
   replace `bootstrap.initdb` with

   ```yaml
   bootstrap:
     recovery:
       source: origin
       # recoveryTarget:
       #   targetTime: "2026-11-02 08:00:00-08"   # point in time, optional
   externalClusters:
     - name: origin
       plugin:
         name: barman-cloud.cloudnative-pg.io
         parameters:
           barmanObjectName: s3
           serverName: homelab-wsl
   ```

   and change the archiving plugin's `serverName` to a new one
   (`homelab-wsl-2`): barman refuses to archive a new timeline over the old
   folder, which is what protects the backups during a restore.
2. Delete the broken Cluster and its PVC, merge, let Flux create the Cluster.
   It downloads the latest base backup and replays WAL to the end (or the
   target time). The app roles' passwords come back with the data.
3. Afterwards put `bootstrap.initdb` back (it only matters on first boot) and
   keep the new `serverName`.

For a test restore, create a second Cluster (`postgres-restore`) with the same
`bootstrap.recovery` + `externalClusters` and no `plugins` section, check the
data with `psql`, then delete it.

### A PVC (kopiur)

Scale the app to zero, then restore the newest snapshot into the existing PVC:

```yaml
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Restore
metadata:
  name: home-assistant-restore
  namespace: home-automation
spec:
  source:
    fromPolicy:
      name: home-assistant        # newest snapshot of this policy (offset: 1 = the one before)
  target:
    pvcRef:
      name: config-home-assistant-0
  credentialProjection:
    enabled: true
  options:
    enableFileDeletion: true      # exact mirror; omit for an additive restore
  mover:
    securityContext:
      runAsUser: 1000
      runAsGroup: 1000
```

`kubectl get snapshots -n <ns>` lists what exists; `source.snapshotRef.name`
picks a specific one. Scale the app back up when the Restore is `Succeeded`.
On a rebuilt cluster, the PVCs are new and empty: do the same once the app has
created its PVC.

### UniFi

Upload the newest `.unf` (Google Drive `Backup/unifi`, or the controller's
`/config/data/backup/autobackup`) under Settings, System, Backups, Restore. It
keeps the APs' device keys, so nothing needs re-adopting. Without a `.unf`, see
"The controller database is lost" in [`../network/unifi.md`](../network/unifi.md).

### Aruba SW03

The files in Drive `Backup/aruba` are `show running-config` output. To restore,
paste the relevant sections in config mode, or copy the file to the switch as
its startup config over TFTP/SCP and reload. The `enable secret` is masked in
the file and has to be set again.
