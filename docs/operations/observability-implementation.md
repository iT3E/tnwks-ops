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

The first rollout deferred the recording/alert rules until scrape label
normalization and primary/standby backup policy were in place, and kept the
generated dashboards out of provisioning until the Grafana upgrade passed.

## Global query rollout

Every target now carries `k8s_cluster`. Each Prometheus runs a Thanos sidecar:
mutual-TLS StoreAPI over the mesh, and block upload to Garage. Uploads do not wait
for persistent storage, so the archive starts filling on site 1's emptyDir and a
restart there only risks the unshipped 2h head. Site 2's Prometheus moves to a
40Gi local-path claim with 15d retention. Site 1 stays at 2d until its native
volume exists.

Query answers partially by default rather than erroring on a lost site. Store and
Compactor run only beside the archive. The recording rules are live on both sites;
the backup alert fires only for a site's own primaries.

## Dashboards

The generated boards are provisioned on site 1, the only Grafana so far. `home` is
now the generated Homelab board, and the previous board stays as Homelab (classic)
under its own UID until it has no remaining use. Grafana 13 accepts the v2 file in
`default_home_dashboard_path` and redirects to `/d/default-home-dashboard/`, tabs
intact. Home Assistant's Monitoring tab frames that home page, so it follows.

Both sites report on both sites, so the global view returns some series twice;
`IDENTITY` in `dashboards.py` reduces those with `max`, which keeps the worst state
and lets a real answer replace an observer's Unknown.

## What the state panels are based on

| State | Source | Today |
| --- | --- | --- |
| Site | Collector success and freshness | Real |
| Database backup | Barman timestamps against expected databases | Real |
| Workload | Replica counts; desired 0 is Standby, not Failed | Real |
| Archive, Internet | Collector reachability probes; any HTTP answer proves the path | Real |
| Service path | `probe_success{observability_target="service"}` | Unknown: no content probes yet |
| Recovery | `homelab_recovery_prerequisite_state` | Unknown: nothing emits fencing or restore evidence yet |

A row is not evidence: a panel can return data and still be Unknown for every row.
Check the values when judging coverage, not just whether a query returns.

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

1. Give site 1 persistent 15d Prometheus storage without placing that growth on E:,
   keeping local metrics and alert evaluation independent of the archive.
2. Add the second independent Grafana and preserve legacy URLs. Validate backlog
   behavior and certificate renewal for the global query path.
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
