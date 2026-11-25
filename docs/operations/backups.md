# Backups

What the homelab backs up, where it goes, how it authenticates, and how to get
it back. The scenario playbooks that use these backups are in
[`disaster-recovery.md`](disaster-recovery.md).

## At a glance

| What | Tool | When | Where | Kept | RPO |
| --- | --- | --- | --- | --- | --- |
| Postgres (every app database) | CloudNativePG barman-cloud plugin | WAL continuously, base backup 01:00 | S3 `cnpg/<cluster>/` | 30 days, point-in-time | minutes |
| App config PVCs (list below) | kopiur (Kopia) | nightly 02:00-03:00 | S3 `kopiur/` | 3 latest, 7 daily, 4 weekly, 6 monthly | 24 h |
| Agent platform PVCs (list below) | kopiur (Kopia), repository `agents` | daily, by each policy | S3 `kopiur-agents/` | 3 latest, 7 daily, 4 weekly, 6 monthly | 24 h |
| Finance agent PVC (list below, not yet applied) | kopiur (Kopia), repository `agents-finance` | daily | S3 `kopiur-finance/` | 3 latest, 7 daily, 4 weekly, 6 monthly | 24 h |
| Main agent PVC (list below, no backup yet) | kopiur (Kopia), repository `agents-main` (not in git yet) | daily | Its own prefix | 3 latest, 7 daily, 4 weekly, 6 monthly | 24 h |
| UniFi controller | controller auto-backup (`.unf`), `unifi-gdrive-copy` → nfs-server `/gdrive-sync` → `tnwks-gdrive-sync` | 02:00, 02:15, copied 02:30 | controller PVC + Google Drive `Backup/nfs-gdrive-sync/unifi` | 14 on the controller, 30 on the export, 30 in Drive | 24 h |
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

Every app has its own CNPG cluster (`kubernetes/components/cnpg-cluster`, an
`<app>-db` Flux Kustomization). Each archives WAL and takes a daily base
backup to `s3://<bucket>/cnpg/<app>/<site>/`, from its own ObjectStore; an app
with a standby on the other WSL site (`cnpg-standby`) has a replica there that
re-archives under that site's name. Anything in a CNPG cluster is covered by
barman-cloud; there is nothing per-database to configure.

The shared `databases/postgres` cluster is retired on homelab-wsl (all its
databases were imported into the apps' own clusters first). Its last base
backup (2026-11-07 01:00 UTC) and WAL stay under `cnpg/homelab-wsl/`.

### PVCs (kopiur)

`kubernetes/apps/kopiur-system/kopiur/policies/`, one file per PVC:

| App | PVC | Why |
| --- | --- | --- |
| Home Assistant | `home-automation/config-home-assistant-0` | `.storage` (integrations, device and entity registries, auth), dashboards, custom components. Recorder history is in Postgres; `configuration.yaml` and packages are also in git |
| Mealie | `production/mealie-config` | Recipe images and the app's secrets. Recipes are in Postgres |
| UniFi | `networking/config-unifi-0` | Includes the controller's `.unf` auto-backups. The MongoDB PVC is skipped: a `.unf` restore is the supported path |
| changedetection | `production/config-changedetection-0` | Watch list and history |
| Firefly III | `production/firefly-upload` | Transaction attachments (none yet). The ledger is in Postgres; the Passport keypair and `APP_KEY` are in git (SOPS) |
| Transcriber | `production/transcriber-audio` | Clip audio (~7 MB per hour of speech, kept 90 days). Transcripts are in Postgres; the models download again. Runs on homelab-wsl-02, so its snapshots are in that site's repository (`kopiur-homelab-wsl-02/`) |
| Valheim | `selfhosted/valheim-data` | World ZIP archives under `config/backups/`. Snapshots run on homelab-wsl-02 every hour at :20 after Valheim's :05 backup. Kopia keeps 24 hourly, 7 daily, 4 weekly and 6 monthly snapshots |
| finance-sync | `production/finance-sync-state` | The rollback copy, on homelab-wsl only: the state the jobs feeding Firefly kept as files until 2026-11-02. It now lives in Postgres (`finance-sync-pg`), so failover and the standby restore don't copy this PVC (`docs/apps/finance-sync.md`) |

The mover must run as the app's user (`mover.securityContext`): kopia fails
the snapshot on any file it can't read. Home Assistant and changedetection are
568 (their private files use 0600), Firefly 33 (www-data), the others (including
finance-sync) 1000.

To add one: copy a policy file, change the name, namespace, PVC and user, add the
namespace to `allowedNamespaces` in `repository/clusterrepository.yaml` if it is
new, and list the file in `policies/kustomization.yaml`.

The agent platform keeps its policies with each app instead, in its own trust
domain's directory, since only homelab-wsl runs it
(`docs/apps/agent-platform.md`, "The pilot agent"). They write to the
repository of the agent trust domains (ClusterRepository `agents`, prefix
`kopiur-agents/`), which has its own password and its own role, not to the
shared one (`docs/apps/agent-platform.md`, "Backups of the agent trust
domains"):

| App | PVC | Policy | Why |
| --- | --- | --- | --- |
| Pilot agent | `agents-pilot/agent-state` | `agent-state-v2` | The state of the product (sessions, workspace) and its archives. Mover user 1000 |
| Pilot Signal daemon | `agents-pilot/signal-state` | `signal-state` | The keys of the test Signal account and the archive of each start. Mover user 65532 |
| Finance agent | `agents-finance/agent-state` | `agent-state`, in the repository `agents-finance` | The state of the finance agent and its archives. Mover user 1000. In git, not applied until the volume is ([the finance repository](#the-finance-repository)) |
| Finance share | `agents-finance/vault-ro` | `vault`, in the repository `agents-finance` | The notes of the finance share, read through its read-only claim (an NFS volume, `copyMethod: Direct`). Mover user 1000, the uid of the share ([agent-platform.md](../apps/agent-platform.md), "The vault shares") |
| Main agent | `agents-main/agent-state` | `agent-state`, in the repository `agents-main` | The state of the main agent and its archives. Mover user 1000. The volume is applied at step 1 of its cutover; the policy is in git and not applied until the repository `agents-main` is `Ready`, which comes in the steps of [the finance repository](#the-finance-repository) |

The snapshots of the pilot agent from before the move (policy `agent-state`)
stay in the shared repository until they are deleted; the runbook restores
one of them (`docs/apps/agent-platform-runbook.md`, "A snapshot from before
the move").

Those namespaces are `restricted` and deny by default, so the policy sets the
mover's `podSecurityContext` too, and the namespace has a `backup-mover`
policy that lets the movers reach S3 and the API server.

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

The repository of the agent trust domains has a chain of its own, beside
this one (`kubernetes/apps/kopiur-system/agents-repository/`):

```
cert CN homelab-wsl-agent-backups  (Secret kopiur-system/aws-backup-certificate-agents, SOPS)
  -> CronJob kopiur-system/aws-backup-credentials-agents, every 30 min (:15, :45)
  -> role tnwks-rolesanywhere-backups-agents: list, read and write kopiur-agents/ only
  -> 4-hour STS session -> Secret aws-backup-sts-agents in kopiur-system/
       the repository of the agent trust domains; kopiur projects a fresh copy
       into the namespace of the trust domain before every mover run
```

The finance trust domain has a third chain, the same way
(`kubernetes/apps/kopiur-system/agents-finance-repository/`): one repository
for each trust domain.

```
cert CN homelab-wsl-agents-finance-backups  (Secret kopiur-system/aws-backup-certificate-agents-finance, SOPS)
  -> CronJob kopiur-system/aws-backup-credentials-agents-finance, every 30 min (:05, :35)
  -> role tnwks-rolesanywhere-backups-agents-finance: list, read and write kopiur-finance/ only
  -> 4-hour STS session -> Secret aws-backup-sts-agents-finance in kopiur-system/
       the repository agents-finance; kopiur projects a fresh copy into
       agents-finance before every mover run
```

- Same trust anchor and CA as the SOPS workstation certificates
  (`environments/prod/aws/rolesanywhere/`), separate role and profile
  (`modules/aws/rolesanywhere.tf`). The role trusts only CN
  `homelab-wsl-backups`; removing it from
  `rolesanywhere_backups_common_names` and applying revokes the cluster
  immediately. The role of the agents repository trusts only CN
  `homelab-wsl-agent-backups` and is an entry of
  `rolesanywhere_backups_prefixed` (`environments/prod/aws/main.tf`); its
  profile and role ARNs reach the CronJob as `RA_AGENT_BACKUPS_PROFILE_ARN`
  and `RA_AGENT_BACKUPS_ROLE_ARN` in `kubernetes/clusters/wsl/cluster-settings.yaml`.
  Without those two keys the refresher stops at its start (`missing env`) and
  `KubeJobFailed` fires.
- A refresher outage leaves the last session valid for up to 4 hours. A
  failed run raises `KubeJobFailed`.
- In the namespaces of the agent platform (label
  `agents.home.arpa/trust-domain`) an admission rule refuses each ConfigMap
  write of a mover, whose roles otherwise reach every ConfigMap there
  (`docs/apps/agent-platform.md`, "The backup mover and ConfigMaps"). The
  mover still gets the projected copy above.
- **The certificate expires 2027-11-02.** Renew before then:

  ```bash
  T=$(mktemp -d)
  infrastructure/terraform/environments/prod/aws/rolesanywhere/issue-cert.sh homelab-wsl-backups "$T" 365
  # put $T/homelab-wsl-backups.pem / .key into tls.crt / tls.key:
  sops kubernetes/apps/kopiur-system/aws-backup-credentials/app/secret-certificate.sops.yaml
  rm -rf "$T"
  ```

  The certificate of the agents repository goes in with no value on the
  screen (`sops --set` takes each file as a JSON string), one year after it
  was issued:

  ```bash
  T=$(mktemp -d)
  F=kubernetes/apps/kopiur-system/agents-repository/app/secret-certificate.sops.yaml
  infrastructure/terraform/environments/prod/aws/rolesanywhere/issue-cert.sh homelab-wsl-agent-backups "$T" 365
  bin/sops --set "[\"stringData\"][\"tls.crt\"] $(jq -Rs . < "$T/homelab-wsl-agent-backups.pem")" "$F"
  bin/sops --set "[\"stringData\"][\"tls.key\"] $(jq -Rs . < "$T/homelab-wsl-agent-backups.key")" "$F"
  rm -rf "$T"
  ```

- **`KOPIA_PASSWORD`** (`kopiur-system/kopiur-repository`, SOPS) encrypts every
  kopiur snapshot. Without it the `kopiur/` data is unreadable, so it belongs on
  the off-site key media with the age key ([BCP](disaster-recovery.md#off-site-keys)).
  The agents repository has its own `KOPIA_PASSWORD`
  (`kopiur-system/kopiur-repository-agents`, SOPS): without it the
  `kopiur-agents/` data is unreadable. It is made once, straight into the
  encrypted file, and never shown:

  ```bash
  bin/sops --set "[\"stringData\"][\"KOPIA_PASSWORD\"] \"$(openssl rand -hex 32)\"" \
    kubernetes/apps/kopiur-system/agents-repository/app/secret-password.sops.yaml
  ```

  Its off-site copy is that encrypted file, copied as it is to the kit next to
  the age key that opens it. Set the value only before the repository is
  created: Kopia creates the repository with it.
- After a fresh install the STS Secrets hold placeholders until the first
  refresh. Run one now instead of waiting:

  ```bash
  kubectl -n kopiur-system create job --from=cronjob/aws-backup-credentials sts-now
  # the agents repository
  kubectl -n kopiur-system create job --from=cronjob/aws-backup-credentials-agents sts-agents-now
  ```

  If kopiur already tried with the placeholders, the ClusterRepository sits in
  `Failed` with "The AWS Access Key Id you provided does not exist". Delete its
  failed discovery Job (`kubectl -n kopiur-system delete job s3-discovery`) and
  it re-bootstraps; then delete any `Pending` Snapshots and create new ones.
  Barman retries WAL on its own.

### The finance repository

One repository for each trust domain: the finance trust domain gets its own
role, prefix, certificate, password and refresher
(`kubernetes/apps/kopiur-system/agents-finance-repository/`). The prefix is
`kopiur-finance/`, not `kopiur-agents-finance/`: the list deny of the bucket
policy for `agents` matches each prefix that starts with `kopiur-agents`, so
it would refuse the list of the finance role. The Terraform module refuses
such a pair.

Set it up in this order, one merge for each step. The repository never
bootstraps with the placeholder session, and no policy comes before its
grant:

1. **The role.** The entry `agents-finance` of
   `rolesanywhere_backups_prefixed` (`environments/prod/aws/main.tf`): the
   role, the profile, and the two deny statements of the bucket policy for
   `kopiur-finance/`. The apply runs in the Deploy run of the merge, with
   no stop of its own: the merge is the approval
   (`.github/workflows/terraform.yaml`).
2. **The certificate and the password, before the merge of step 3.** The two
   SOPS files went into git with the placeholder `pending`, encrypted to the
   age recipients of `.sops.yaml` only. Add the KMS recipient first, then
   the values, with no value on the screen:

   ```bash
   D=kubernetes/apps/kopiur-system/agents-finance-repository/app
   bin/sops updatekeys -y "$D/secret-certificate.sops.yaml"
   bin/sops updatekeys -y "$D/secret-password.sops.yaml"
   T=$(mktemp -d)
   infrastructure/terraform/environments/prod/aws/rolesanywhere/issue-cert.sh homelab-wsl-agents-finance-backups "$T" 365
   bin/sops --set "[\"stringData\"][\"tls.crt\"] $(jq -Rs . < "$T/homelab-wsl-agents-finance-backups.pem")" "$D/secret-certificate.sops.yaml"
   bin/sops --set "[\"stringData\"][\"tls.key\"] $(jq -Rs . < "$T/homelab-wsl-agents-finance-backups.key")" "$D/secret-certificate.sops.yaml"
   rm -rf "$T"
   bin/sops --set "[\"stringData\"][\"KOPIA_PASSWORD\"] \"$(openssl rand -hex 32)\"" "$D/secret-password.sops.yaml"
   # The password check: "ok" for 64 hex characters (the pattern above),
   # "not ok" for the placeholder or anything else. No value is shown.
   bin/sops -d --extract '["stringData"]["KOPIA_PASSWORD"]' "$D/secret-password.sops.yaml" \
     | tr -d '\n' | awk '{ v = v $0 } END { print (v ~ /^[0-9a-f]+$/ && length(v) == 64) ? "ok" : "not ok" }'
   ```

   Copy `secret-password.sops.yaml` as it is to the off-site kit, next to
   the age key that opens it ([BCP](disaster-recovery.md#off-site-keys)).
3. **The session.** The two keys `RA_AGENTS_FINANCE_BACKUPS_PROFILE_ARN` and
   `RA_AGENTS_FINANCE_BACKUPS_ROLE_ARN` in
   `kubernetes/clusters/wsl/cluster-settings.yaml`, from the outputs
   `rolesanywhere_backups_prefixed_profile_arns` and
   `rolesanywhere_backups_prefixed_role_arns` for `agents-finance`, and the
   line of `agents-finance-repository/ks.yaml` in
   `kubernetes/clusters/wsl/apps/kustomization.yaml`. After the merge, run
   the refresher once:

   ```bash
   kubectl -n kopiur-system create job --from=cronjob/aws-backup-credentials-agents-finance sts-agents-finance-now
   kubectl -n kopiur-system get job sts-agents-finance-now   # 1/1
   kubectl -n kopiur-system get secret aws-backup-sts-agents-finance -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
   ```

4. **The repository, only when the password check prints `ok`.** Kopia
   creates the repository with the password it finds at its first
   bootstrap, the placeholder too, and a new password does not change an
   existing repository. So run the check of step 2 again on the branch of
   this step, right before its merge:

   ```bash
   D=kubernetes/apps/kopiur-system/agents-finance-repository/app
   bin/sops -d --extract '["stringData"]["KOPIA_PASSWORD"]' "$D/secret-password.sops.yaml" \
     | tr -d '\n' | awk '{ v = v $0 } END { print (v ~ /^[0-9a-f]+$/ && length(v) == 64) ? "ok" : "not ok" }'
   ```

   On `not ok`, stop: set the password (step 2) first. On `ok`, list
   `clusterrepository.yaml` in
   `agents-finance-repository/app/kustomization.yaml`. It grants
   `agents-finance`. Then `kubectl get clusterrepository agents-finance`
   shows `Ready`. (The certificate needs no check of its own: with the
   placeholder, the refresh of step 3 fails.)
5. **The policy, after the volume.** At the cutover of the finance agent,
   list `pvc.yaml` with the inbox pod first, so the volume is made on the
   node of the agent, then `backup.yaml` in the next pull request
   (`kubernetes/apps/ai/agents/finance/agent/app/kustomization.yaml`; steps 1
   and 2 of [The cutover of the finance agent](../apps/agent-platform-runbook.md#the-cutover-of-the-finance-agent)).
   The first snapshot runs at once (`runOnCreate`):
   `kubectl -n agents-finance get snapshots`.

The way back is the same steps in the reverse order: the policy first (its
delete needs the grant, see
[Move a trust domain to another repository](../apps/agent-platform.md#move-a-trust-domain-to-another-repository)),
then the repository, then the credentials, then the role. The snapshots stay
in `kopiur-finance/` after the policy and the repository go. Without the role
entry the bucket policy no longer keeps that prefix to one role.

The certificate is renewed as the one of the agents repository above, with
the CN `homelab-wsl-agents-finance-backups` and the file
`agents-finance-repository/app/secret-certificate.sops.yaml`.

### The main repository

The main trust domain gets its own repository in the same steps as
[the finance repository](#the-finance-repository), one merge for each
step, with these names:

| Part | Finance | Main |
| --- | --- | --- |
| Entry of `rolesanywhere_backups_prefixed` | `agents-finance` | `agents-main` |
| Prefix | `kopiur-finance/` | `kopiur-main/` (not `kopiur-agents-main/`, for the reason above) |
| Certificate CN | `homelab-wsl-agents-finance-backups` | `homelab-wsl-agents-main-backups` |
| Role | `tnwks-rolesanywhere-backups-agents-finance` | `tnwks-rolesanywhere-backups-agents-main` |
| Directory | `kopiur-system/agents-finance-repository/` | `kopiur-system/agents-main-repository/` |
| Keys of `cluster-settings.yaml` | `RA_AGENTS_FINANCE_BACKUPS_PROFILE_ARN`, `RA_AGENTS_FINANCE_BACKUPS_ROLE_ARN` | `RA_AGENTS_MAIN_BACKUPS_PROFILE_ARN`, `RA_AGENTS_MAIN_BACKUPS_ROLE_ARN` |
| ClusterRepository and its grant | `agents-finance`, grants `agents-finance` | `agents-main`, grants `agents-main` |
| The policy after the repository | `finance/agent/app/backup.yaml` | `main/agent/app/backup.yaml` |

Step 1, the role, is in `environments/prod/aws/main.tf`. The other steps
follow it, in the order of the finance repository.

## Checking backups

```bash
# Postgres: last base backup, WAL archiving
kubectl -n databases get backups
kubectl cnpg status postgres -n databases     # "Continuous Backup status"
# kopiur
kubectl get snapshotpolicies,snapshots -A
# STS session expiry
kubectl -n databases get secret aws-backup-sts -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
kubectl -n kopiur-system get secret aws-backup-sts-agents -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
kubectl -n kopiur-system get secret aws-backup-sts-agents-finance -o jsonpath='{.metadata.annotations.backups\.tnwks\.us/expires}'
kubectl get clusterrepositories
# Desktop timers (Drive copy of the gdrive-sync export, switch config)
systemctl list-timers 'tnwks-*.timer'
journalctl -u tnwks-gdrive-sync -u tnwks-aruba-backup -n 20
# The writer on the site that runs UniFi
kubectl -n networking get cronjob unifi-gdrive-copy
```

Alerts: `PostgresBackupStale` (no base backup in 30 h, per CNPG cluster), `PostgresWALArchivingStalled` (archiving failing for 15 min),
`LastFailedArchiveTime` (cnpg rules); kopiur's own PrometheusRule (failed or
stale snapshots; `KopiurBackupStale` is patched in the HelmRelease to skip a
policy whose schedule is suspended on a standby site, from the collector's
`homelab_app_backup_scheduled`); `KubeJobFailed` for the credentials CronJobs
(each refresher). The desktop
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
data with `psql`, then delete it. Pin it to `homelab-wsl-worker-1`
(`spec.affinity.nodeSelector`): `local-path-ext4` exists only on that node,
and a pod scheduled to worker-2 leaves the PVC unprovisionable. The same
applies to a recovery of the real Cluster on a rebuilt cluster if its PVC is
gone. The 2026-11-02 drill recovered all nine databases to the latest WAL in
80 seconds.

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
picks a specific one. Scale the app back up when the Restore's phase is
`Completed` (`Ready=True`, reason `RestoreSucceeded`).
On a rebuilt cluster, the PVCs are new and empty: do the same once the app has
created its PVC.

For Valheim, restore to a scratch PVC and extract the desired ZIP from
`config/backups/`. The snapshot contains the archives only. Mirroring it to the
live PVC with `enableFileDeletion: true` would delete the live world and server
files.

The volumes of the agent platform restore from the repository `agents`, by
the steps in `docs/apps/agent-platform-runbook.md` ("Restore the state of an
agent"), which also restore a snapshot taken before the move.

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

## Into Google Drive: the gdrive-sync drop box

Backups bound for the desktop's Google Drive (`G:\My Drive\Backup\nfs-gdrive-sync`)
go through one NFS export, so nothing in the clusters ever touches the Drive:

1. **Writers** (cluster jobs on either site) write to nfs-server's `/gdrive-sync`
   export (`10.5.0.3:/gdrive-sync`, host network on homelab-wsl's worker-1). Only
   the six cluster nodes may write, and every client is squashed to uid/gid 1000,
   the owner of the `0700` export root. Today: `networking/unifi-gdrive-copy`
   (02:15, on the site that runs UniFi), the newest `.unf` into `unifi/`.
2. **The desktop** (`tnwks-gdrive-sync.timer`, 02:30, role `wsl_backups`) mounts the
   export read-only and only that one Drive folder, both inside the unit's own
   mount namespace, copies files it doesn't have yet (never rewriting one), and
   keeps the newest 30 per subfolder. The mounts go away with the run.

Adding a writer: a job (or CronJob) with an `nfs:` volume on
`10.5.0.3:/gdrive-sync`, its own subfolder, umask 077. The node it runs on must
be in the export's client list (`storage/nfs-server` ConfigMap). The Drive copy
picks it up on the next run.

