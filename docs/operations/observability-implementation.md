# Observability implementation

Deployment is authorized through PRs and Flux. Source preparation and rollout
are separate milestones; work is isolated in git worktrees.

## First rollout

The cluster graphs select shared PKI and a read-only evidence collector on each
WSL site, plus one Garage server on `homelab-wsl-02-worker-1`. Its local-path
volumes are on the roomier C:-backed ext4 host. A declarative CNPG Backup creates
`grafana-before-v13` before the Grafana database schema upgrade.

Garage initializes its single-node layout, metrics bucket and key from git-owned
encrypted credentials. The S3 listener is pod-local; a Caddy HTTPS listener and
Cilium global Service provide the private endpoint. The shared encrypted CA issues
separate client/server certificates. Metadata and data use fsync; automatic
metadata snapshots retain the two most recent copies through Garage's native
retention. The 256Gi data / 8Gi metadata claims are retained on Helm uninstall.
There is one archive copy, not an independent application backup.

Live verification (2026-11-07): PRs [1876](https://github.com/iT3E/tnwks-ops/pull/1876),
[1878](https://github.com/iT3E/tnwks-ops/pull/1878) and
[1881](https://github.com/iT3E/tnwks-ops/pull/1881) deployed through main and Flux.
Both collectors report success for all four source sections. Garage's two
containers are ready with the original claims retained. Both sites verify the
archive certificate and authenticated bucket HEAD (200); anonymous listing is
rejected (403). `grafana-before-v13` completed. Garage uses a Recreate Deployment
and a private memory-backed credential copy: fsGroup-readable Secret projections
cannot satisfy Garage's strict owner-only mode check directly.

The collector lists workload/Flux/CNPG state, retains declared inventory and emits
bounded freshness/probe evidence. Its service account cannot read Secrets or
mutate resources. The inventory derives from the existing failover catalogue and
each cluster's rendered Flux graph; retired databases do not remain expected.

The first rollout intentionally defers the new recording/alert rules until scrape
label normalization and primary/standby backup policy are in place. It also keeps
the generated dashboards outside the live provisioning set until the Grafana
upgrade and its database recovery rehearsal pass.

## Validation evidence

- Six behavior fixtures cover missing/future backups, same-name databases across
  sites, stale observations/probes, incomplete recovery prerequisites and duplicate
  federation/prerequisite observations. Unit and HTTP tests cover missing inventory,
  failed collectors, escaping and the aggregate-only kiosk query boundary.
- Kustomize builds cover all new components; Helm schema/render checks include the
  five new workloads. Cluster graph validation enforces WSL-02-only archive and
  compactor placement. The proposed CNPG Backup passes server dry-run admission.
- The disposable Garage/Thanos test covers S3 read/write/range/list/delete,
  certificate-verified HTTPS, StoreAPI mutual TLS, historical queries, normal
  restart, process-kill recovery, cache rebuild and unavailable-store failure.
- The disposable Grafana 13.3 test verifies stable v2 UIDs, tab layouts, folders,
  four datasources and provisioning deletion. Browser tests exercise the 12
  operator dashboards and check that both sites, including Unknown evidence,
  remain visible. Desktop/tablet screenshots are local test artifacts.

Synthetic tests do not establish production query peaks, compactor scratch size,
SSD flush durability under host power loss, or complete application recovery.

The private Grafana PostgreSQL rehearsal also restored the real encrypted backup
as the non-superuser application role (PostgreSQL 18.6, locale C). It preserved
33 dashboard UIDs, four datasource identities, three users and their password
hashes through 12.3.1 → 13.3.0 and recreation against the migrated database. A
fresh restoration of the original backup then reproduced the Grafana 12 baseline.
The clone used an internal Docker network with alert execution disabled; fixtures,
database and extracted provisioning never existed as plaintext host files.

The maintained Grafana 14.4 chart is separately rendered and checksum-verified.
The renderer rejects unauthenticated requests, and a real Grafana 13 callback
produces a PNG with its shared token. Rendering is capped at one concurrent
request; its 768Mi limit is peak headroom, not idle consumption. The six reference
exports retain their existing providers/UIDs and are provisioned from git rather
than downloaded during pod startup.

## Following rollout work

1. Prepare the dedicated 64Gi C: ext4 monitoring mount on site 1, fail-closed
   startup/bind behavior, a dedicated storage class and real pod UID permissions.
   Migrate Prometheus to persistent 15d local storage without placing that growth
   on E:. Keep local metrics and alert evaluation independent of the archive.
2. Normalize source/observer/database labels, preserve peer federation provenance,
   add sidecars and per-site Query/Store, and one bounded WSL-02 compactor. Validate
   cross-site endpoints, backlog behavior and certificate renewal.
3. Rehearse the existing Grafana Postgres schema migration and database restore;
   upgrade the maintained chart/image and renderer authentication. Add the second
   independent Grafana, preserve legacy URLs, and cut over `home` without duplicate
   provisioning sources. Finish upstream dashboard vendoring and navigation.
4. Deploy the isolated household Grafana and aggregate-only backend, revoke old
   kiosk operator access/sessions and test all datasource APIs. Define fresh domain
   evidence before any household status can be healthy.
5. Add Pyrra with complete-history 28d evaluation and local short-window alerts.
   A local 28d recording on 15d retention is invalid. Budget a history-aware rule
   evaluator if the selected Pyrra path requires it.
6. Complete FRR/BGP/mesh, expected Wi-Fi APs, content probes, fencing/restore
   attestations, structured operation events, pinned hardware, survivor capacity
   and alert/runbook links. Unsupported evidence remains Unknown.

Record live verification and rollout commit IDs here as each stage completes.
Existing MS-01 manifests and AWS application backup destinations remain separate
from this new metrics archive.
