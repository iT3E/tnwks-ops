# cnpg-standby

CNPG's distributed topology for a cluster built by `../cnpg-cluster`: one
manifest, rendered on both sites. The site named by `replica.primary` runs the
primary. The other runs a replica cluster that restores from the primary's
WAL archive in S3 (barman-cloud) and re-archives it under its own `serverName`,
which is what lets it be promoted.

## Use

List it after `cnpg-cluster` in the app's `<app>-db` Flux Kustomization, and
add `replica.primary` with the app's own variable in `db/kustomization.yaml`:

```yaml
# ks.yaml
components:
  - ../../../../components/cnpg-cluster
  - ../../../../components/cnpg-standby
```

```yaml
# db/kustomization.yaml
patches:
  - target:
      kind: Cluster
    patch: |-
      - op: add
        path: /spec/replica/primary
        value: ${MEALIE_PG_PRIMARY:=homelab-wsl}
```

The role Secret (`postgres-<app>`) has to be the same on both sites, so it
lives in the app's `db/` (SOPS, decryptable by both sites' age keys), not in the
shared cluster's directory. On the standby the app itself runs at 0
(`<APP>_REPLICAS: "0"` in its `cluster-settings`).

## Variables

| Variable                | Default                               |                                                                                                     |
| ----------------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `PEER_CLUSTER_NAME`     | none (each site's `cluster-settings`) | the other site's `CLUSTER_NAME`                                                                     |
| `CNPG_PEER`             | `${PEER_CLUSTER_NAME}`                | replica source and recovery source                                                                  |
| `CNPG_PEER_SERVER_NAME` | `${CNPG_PEER}`                        | the peer's archive folder, `cnpg/<app>/<this>/` (bump with a generation after an unplanned promote) |
| `CNPG_SERVER_NAME`      | `${CLUSTER_NAME}`                     | this site's archive folder (shared with `cnpg-cluster`)                                             |

## What it changes

- `bootstrap` becomes `recovery` from the peer. CNPG reads it only when the
  Cluster is created, so on an existing primary it's inert; a Cluster created
  later on either site restores from the other's archive.
- `replica.self` / `replica.source`, and `externalClusters` for both sites
  with `serverName` set explicitly. The plugin otherwise falls back to the
  local Cluster name, which is the same on both sites.

WAL-only for now: replay lags the primary by up to `archive_timeout` (5 min)
while there's activity. Streaming needs the Cluster Mesh and shared
replication CAs (Obsidian spike, 2026-11-05).

Switchover and failback follow CNPG's token flow, described in
`docs/wsl/multi-site.md`.
