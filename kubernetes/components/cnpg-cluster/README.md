# cnpg-cluster component

A Kustomize component that gives one app its own CloudNativePG cluster, with
barman-cloud WAL archiving and daily base backups to the app's own S3 prefix.
It replaces a database in the shared `databases/postgres` cluster: one app per
cluster, so each app gets its own restarts, major upgrades, cache and
point-in-time restore (barman restores a whole cluster, never one database).

Nothing uses it yet. Moving an app onto it is a separate PR per app.

## What it creates

All in namespace `databases`, whatever the app's own namespace is:

| Kind              | Name             | What                                                                                                                                                                         |
| ----------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Cluster`         | `<app>-pg`       | Postgres, `CNPG_INSTANCES` instances, small-cluster parameters, the owner role from its Secret, WAL archived by the barman-cloud plugin. Services `<app>-pg-rw`, `-ro`, `-r` |
| `ObjectStore`     | `<app>-pg`       | `s3://${BACKUP_S3_BUCKET}/cnpg/<app>/`, 30-day retention, the `aws-backup-sts` credentials                                                                                   |
| `ScheduledBackup` | `<app>-pg-daily` | Base backup at 01:30, and one right after creation                                                                                                                           |

Backups land in `cnpg/<app>/<serverName>/{wals,base}/`, `serverName` being the
site (`CLUSTER_NAME`): `cnpg/mealie/homelab-wsl/`. Two sites can then share one
ObjectStore per app without mixing timelines. The shared cluster's
`cnpg/homelab-wsl/` and `cnpg/homelab-ms-01/` are left alone.

`databases` is where the credentials refresher writes `aws-backup-sts`
(`kopiur-system/aws-backup-credentials`, `TARGET_NAMESPACES`) and where the
apps' role Secrets (`postgres-<app>`, basic-auth) already live, so no new RBAC
or Secret copies are needed.

## Variables

Set per app in the consuming Flux Kustomization's `postBuild.substitute`.
Anything not set there comes from `cluster-settings`, then from the default.

| Variable                      | Default                | Meaning                                                                                                                               |
| ----------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `CNPG_APP`                    | required               | App name, DNS-1123 (`home-assistant`). Names every object and the S3 prefix. Unset, the objects render as `-pg` and fail to apply     |
| `CNPG_USER`                   | `${CNPG_APP}`          | Owner role. Postgres-style name (`home_assistant`) where the app name has a hyphen                                                    |
| `CNPG_DATABASE`               | `${CNPG_USER}`         | Database created at initdb                                                                                                            |
| `CNPG_USER_SECRET`            | `postgres-${CNPG_APP}` | basic-auth Secret in `databases` with the owner's `username` (= `CNPG_USER`) and `password`. Must exist before the Cluster bootstraps |
| `CNPG_INSTANCES`              | `1`                    | Instances. `cluster-settings` sets it per cluster (1 on both WSL clusters, 3 on MS-01)                                                |
| `CNPG_SIZE`                   | `2Gi`                  | PVC size per instance. Not `CNPG_INSTANCE_SIZE`: that one sizes the shared cluster                                                    |
| `CNPG_SHARED_BUFFERS`         | `32MB`                 | `shared_buffers`. Always with a unit, so it stays a string                                                                            |
| `CNPG_MAX_CONNECTIONS`        | `"20"`                 | `max_connections`. Write an override with the quotes inside: `CNPG_MAX_CONNECTIONS: '"50"'` (see below)                               |
| `CNPG_CPU_REQUEST`            | `10m`                  | Postgres container CPU request. No CPU limit                                                                                          |
| `CNPG_MEMORY_REQUEST`         | `128Mi`                | Postgres container memory request                                                                                                     |
| `CNPG_MEMORY_LIMIT`           | `512Mi`                | Postgres container memory limit                                                                                                       |
| `CNPG_SIDECAR_MEMORY_REQUEST` | `64Mi`                 | barman-cloud sidecar memory request                                                                                                   |
| `CNPG_SIDECAR_MEMORY_LIMIT`   | `256Mi`                | barman-cloud sidecar memory limit                                                                                                     |
| `CNPG_SERVER_NAME`            | `${CLUSTER_NAME}`      | Archive folder under `cnpg/<app>/`. Bump it (`homelab-wsl-g2`) after a restore or an unplanned promote                                |
| `CNPG_BACKUP_SCHEDULE`        | `0 30 1 * * *`         | Base backup cron, six fields, seconds first. Don't start it with `*` (YAML alias)                                                     |
| `CNPG_BACKUP_RETENTION`       | `30d`                  | barman retention, applied per `serverName`                                                                                            |

From `cluster-settings` (no default, every cluster must define them):
`CLUSTER_NAME`, `STORAGE_CLASS_DATABASE`, `BACKUP_S3_BUCKET`.
`SERVICE_MONITOR_ENABLED` defaults to `false`.

**Why the odd quoting on `CNPG_MAX_CONNECTIONS`.** Postgres parameters must be
strings, and the CNPG webhook rejects a number. kustomize drops the quotes
around `"${...}"` before Flux substitutes, so a bare `20` renders as an int.
The quotes have to travel inside the value: the default is `"20"`, an
override is `'"50"'`. Every other variable is a string with a unit or a name,
so it doesn't have this problem.

## Usage

A second Flux Kustomization next to the app's, pointing at a `db/` directory in
the app's folder. The component can't go in the app's own `app/`
kustomization: that one sets `namespace: <app namespace>`, and kustomize would
move the Cluster out of `databases` with everything else.

`kubernetes/apps/production/mealie/ks.yaml`:

```yaml
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: mealie-db
  namespace: flux-system
spec:
  path: ./kubernetes/apps/production/mealie/db
  components:
    - ../../../../components/cnpg-cluster
  dependsOn:
    - name: cloudnative-pg-plugin-barman-cloud
    - name: aws-backup-credentials
    # The role Secret postgres-mealie is still in the shared cluster's
    # kustomization; drop this once it moves into db/.
    - name: cloudnative-pg-cluster
  prune: true
  wait: true # the Cluster's Ready condition gates the app
  sourceRef:
    kind: GitRepository
    name: flux-system
  interval: 30m
  retryInterval: 1m
  timeout: 5m
  postBuild:
    substitute:
      CNPG_APP: mealie
      CNPG_SIZE: 1Gi
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: mealie
  namespace: flux-system
spec:
  dependsOn:
    - name: mealie-db
  # ...unchanged
```

`kubernetes/apps/production/mealie/db/kustomization.yaml`, empty unless the
app needs a patch (a migration, a restore, an extra parameter):

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
```

The global `cluster-settings`/`cluster-secrets` patch from the cluster's
`apps.yaml` merges with the inline `substitute`; inline values win. The app
then connects to `mealie-pg-rw.databases.svc` with the same login it has today.

A role Secret that belongs to the app alone can live in `db/` as
`secret.sops.yaml` (`postgres-<app>`, namespace `databases`, label
`cnpg.io/reload: "true"`). Today they are all in
`kubernetes/apps/databases/cloudnative-pg/cluster/`; each moves with its app.

## Moving an app off the shared cluster (later)

One app at a time, with CNPG's `initdb.import` (`type: microservice`) from the
shared `postgres` cluster: a patch in `db/kustomization.yaml` that adds the
import to the component's `initdb`, plus the external cluster to read from.
The shape (the superuser connection is unverified):

```yaml
patches:
  - target:
      kind: Cluster
    patch: |-
      - op: add
        path: /spec/bootstrap/initdb/import
        value:
          type: microservice
          databases: [mealie]
          source:
            externalCluster: postgres
      - op: add
        path: /spec/externalClusters
        value:
          - name: postgres
            connectionParameters:
              host: postgres-rw.databases.svc
              user: postgres
              dbname: mealie
            password:
              name: postgres-superuser
              key: password
```

`bootstrap` is only read when the Cluster is created, so the patch can be
removed after the import.

## Restore and standby (later)

The component is the primary-side half. Both build on it with patches or a
second component, not by changing this one:

- **Restore.** Replace `bootstrap.initdb` with `bootstrap.recovery` from an
  external cluster that reads the archive
  (`plugin.parameters: {barmanObjectName: <app>-pg, serverName: <old>}`, always
  set `serverName`: the plugin otherwise defaults to the local Cluster name),
  delete the Cluster and its PVC, merge. Set `CNPG_SERVER_NAME` to a new
  generation first: barman refuses to archive a new timeline into a non-empty
  folder. Same flow as the shared cluster in `docs/operations/backups.md`.
- **Standby on homelab-wsl-02.** The same manifests on both sites (same
  Cluster and ObjectStore names, same role Secrets, so wsl-02's age key must
  decrypt them), plus `replica.self/primary/source` and two `externalClusters`
  (`homelab-wsl` and `homelab-wsl-02`) reading `cnpg/<app>/<site>/`. wsl-02
  starts with `bootstrap.recovery` from wsl-01's archive, follows WAL from S3,
  and re-archives to its own `serverName` (`archive_mode=always`), which is
  what lets it be promoted and wsl-01 follow it after a switchover. Promote
  and demote with the token flow of CNPG's distributed topology. Checked
  against the installed CNPG 1.30.1 and plugin v0.15.1 in docs and source, not
  yet run.
- **Prerequisites on wsl-02:** the CNPG operator and plugin, the credentials
  refresher, `BACKUP_S3_BUCKET` in its `cluster-settings`, and its own Roles
  Anywhere certificate and role (read on `cnpg/`, write only on
  `cnpg/*/homelab-wsl-02*/*`), so a mis-rendered `serverName` can't write into
  wsl-01's archive. wsl-02's `STORAGE_CLASS_DATABASE` is `local-path`, which
  is ext4 there.

## Monitoring

`enablePodMonitor` follows `SERVICE_MONITOR_ENABLED`, as on the shared
cluster (the field is deprecated in CNPG 1.30; both move to a PodMonitor
together). The cnpg PrometheusRule
(`kubernetes/apps/databases/cloudnative-pg/cluster-prometheusrules`) covers
each cluster on its own: `PostgresBackupStale` groups by cluster (the pod name
minus its instance number), and `PostgresWALArchivingStalled` fires on failing
archiving, not on an idle database that writes no WAL.

## Cost

Each cluster is its own Postgres plus a barman-cloud sidecar (about 60 MiB).
With these defaults an idle cluster is roughly 120-150 MiB in total. About ten
apps is roughly 1.2-1.5 GiB on the node running them, plus standbys for the
warm tier on the other site (about 0.5 GiB). Apps that need more (Home
Assistant's recorder) raise `CNPG_SHARED_BUFFERS` and the memory limit. CPU,
WAL upload and S3 cost are negligible. On homelab-wsl, worker-1 (where
`local-path-ext4` lives) has no room for this today; that gate comes first.
