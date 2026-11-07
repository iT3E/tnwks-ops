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
mutual-TLS StoreAPI over the mesh, and block upload to Garage. Both keep 15d
locally: site 2 on a 40Gi local-path claim, site 1 on its
[own volume](#site-1-prometheus-volume).

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
| Service backend | `probe_success{observability_target="service"}` | Active site's Service content; client DNS, TLS, ingress and sign-in need separate probes |
| Recovery | `homelab_recovery_prerequisite_state` | Database/backup/restore evidence; no positive per-app former-primary fence attestation yet |

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

## Site 1 Prometheus volume

Site 1's metrics sit on `C:\WSL\observability.vhdx`, a 20Gi expandable ext4 disk,
so their growth stays off E:. The `windows_observability_storage` role creates it
once, and the task `tnwks-observability-mount` attaches and mounts it at boot and
every minute after. It never formats a disk it finds at startup.

The disk mounts on the distro inside worker-1's `/var` Docker volume, at
`lib/rancher/k3s/storage/observability`. Docker's data root is a shared mount and
Talos' `/var` is its slave, so the mount reaches the running node with no restart.
Worker-1 keeps its normal lifecycle, and a missing disk stops only the pods using
the `local-path-observability` class. The node carries most of the site's
databases and Flux, so it must not depend on a metrics disk.

The bare mountpoint is immutable (`chattr +i`). containerd creates a missing
hostPath itself, so without that a pod starting before the mount would write to
the distro disk on E:. It fails and retries instead. The provisioner's setup script
also refuses a new claim until the disk's marker is there, since the mountpoint is
only immutable after the first mount. Talos' kubelet has its own
`/var/lib/rancher`, so a `hostPath` with `type: Directory` under this path fails
forever; leave the type off.

- **Compaction:** `tnwks-vhdx-compact` pauses the mount task, like every task that
  calls `wsl.exe`, and starts it again afterwards.
- **Destroying worker-1:** Docker can't remove the volume while the disk is mounted
  or the mountpoint is immutable. Disable the task, `umount` the path and
  `chattr -i` it first. A new worker gets a new volume, and the task finds it.
- **Reclaim:** the class is `Retain`. Deleting the PVC leaves the data on the disk.

## Grafana on both sites

Grafana is a warm app, like Firefly (`docs/wsl/multi-site.md`, "Apps"): one name,
`grafana.<internal domain>`, on the homelab-wsl-first shared VIP. homelab-wsl runs
it (`GRAFANA_REPLICAS`). homelab-wsl-02 keeps it at 0, with a replica cluster of
`grafana-pg` replaying the WAL archive. `task failover APP=grafana TO=homelab-wsl-02`
moves it, and the global Service lets either site's ingress reach it. Users,
preferences and UI-made dashboards move with the database. The provisioned boards
are on both sites already.

- **Losing site 1:** set `site_leases.grafana`, then
  `task failover APP=grafana TO=homelab-wsl-02 -- --promote`. Until then the name
  has nowhere to go. The sign-in still goes through homelab-wsl's oauth2-proxy, as
  for every app on the VIPs, so with site 1 down only the kiosk (JWT) and local
  admin logins work. Use the admin account in `grafana-admin-secret`.
- **Renderer:** runs on both sites, idle on the standby. The chart can't scale it to 0.
- **Logs:** site 2 has no Loki, so the Loki datasource errors while Grafana runs
  there. Metrics are complete through each site's Thanos Query.
- **Replaced** the separate `grafana-02` instance (SQLite, local login), which
  didn't match the other apps' multi-site setup.

## Network path evidence

`homelab:network_path_state{k8s_cluster, hop}` gives each site's path to the router
by hop: 1 healthy, 2 degraded, 3 failed, 0 Unknown.

| hop | Source | Healthy when |
| --- | --- | --- |
| `tunnel` | mktxp, the router's WireGuard peer (`wg-site1`, `wg-site2`) | handshake within 180 s |
| `router-bgp` | mktxp BGP sessions (`wsl-site1`, `wsl-site2`) | established; Failed when the router is observed but the session is gone |
| `node-bgp` | Cilium agents' sessions with the distro's FRR | all nodes (some: Degraded) |

`homelab:router_bgp_prefixes` counts the routes the router accepted from each site.
Only homelab-wsl's mktxp observes the router, so homelab-wsl-02's own rules leave
the router hops Unknown, and the dashboards keep the real answer. The router-side
names come from `observability/inventory.py` (sites), matching `site-tunnels.tf`.

## Service path evidence

`homelab:service_state{k8s_cluster, app}` comes from a content probe: the collector on
the site that runs an app (desired replicas above 0) requests its health URL through
its Service. The probe passes on a 2xx whose body contains an expected string, so an
error page or a proxy's 200 doesn't count. The site at 0 shows Standby (4), from its
replica count. With neither observed, the state is Unknown. URLs and strings are in
`observability/inventory.py` (`PROBES`). Each probe has a 3 s timeout, and they run
in parallel within the 20 s scrape.

## Recovery evidence

`homelab_recovery_prerequisite_state{k8s_cluster, app, prerequisite}`, per warm app and
site: could this site take the app over? 1 met, 3 not met. A prerequisite with no
evidence is left out, so `homelab:recovery_state` stays Unknown.

| prerequisite | Met when |
| --- | --- |
| `database` | every one of the app's Postgres clusters on this site is healthy (primary or replica) |
| `backup` | each has a completed base backup on this site within 30 h; future timestamps fail |
| `restore` | the latest restore of every required PVC on this site completed within 8 days; running workloads do not substitute for a drill. No copied PVCs (Grafana) satisfies only this prerequisite |
| `fence` | a lease naming this site explicitly blocks promotion. A positive former-primary fence attestation is not produced yet, so an unblocked lease leaves this prerequisite Unknown |

`homelab_restore_last_success_timestamp_seconds` feeds the Sites & Failover board.
Restore remains Unknown until a completed drill covers every required PVC. Its
age is conservatively measured from the restore object's creation. The absence of
copied PVCs does not prove a PostgreSQL database restore or an application RTO.

`homelab_fence_job_last_success_timestamp_seconds` reports only fleet Job liveness.
The fence Job exits successfully even when a lease cannot be read; it is not an
app-specific attestation. `homelab_lease_authority_reachable` reports whether the
current DNS answer is recognized, and `homelab_site_lease_blocks_local` reports its
local restriction. Neither certifies that a former primary cannot write.
`homelab_fence_last_verified_timestamp_seconds` is deliberately absent until an
owning mechanism produces that proof. Recovery cannot be Healthy without it.

Recovery prerequisite and workload samples expire after 180 seconds. Workload
counts deduplicate overlapping collector pods; a missing component leaves its app
Unknown even if another component is ready. The overview header counts distinct
selected sites and does not color a single selected reporting site as degraded.

## MQTT evidence

Each site's collector logs in to mosquitto through its global Service as user
`observability` (`secret-mqtt.sops.yaml`, the same entry in mosquitto's password
file). It reads the retained `$SYS/broker/clients/connected`, and subscribes to
nothing else. `homelab:mqtt_state` is whether the broker accepted that login, and
`homelab_mqtt_clients_connected` is the client count. Delivery between clients
isn't measured.

## Following rollout work

1. Validate backlog behavior and certificate renewal for the global query path.
2. Deploy the isolated household Grafana and aggregate-only backend, revoke old
   kiosk operator access/sessions and test all datasource APIs. Define fresh domain
   evidence before any household status can be healthy.
3. Add Pyrra with complete-history 28d evaluation and local short-window alerts.
   A local 28d recording on 15d retention is invalid. Budget a history-aware rule
   evaluator if the selected Pyrra path requires it.
6. Complete FRR/BGP/mesh, expected Wi-Fi APs, content probes, fencing/restore
   attestations, structured operation events, pinned hardware, survivor capacity
   and alert/runbook links. Unsupported evidence remains Unknown.

Record live verification and rollout commit IDs here as each stage completes.
Existing MS-01 manifests and AWS application backup destinations remain separate
from this new metrics archive.
