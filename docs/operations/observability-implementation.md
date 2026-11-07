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

Alertmanager / Overview (`alertmanager-overview`, folder Kubernetes) is the
kube-prometheus mixin's board, pinned in `grafana/upstream/catalogue.json` from
the stack's own chart version. The chart renders it only with its bundled
Alertmanager, which this stack doesn't use. Its UID replaces an empty board made
in the UI on 2026-11-01: file provisioning takes over a dashboard with the same
UID, checked against the pinned Grafana. The Alerts board's Alertmanager tab is
the per-site summary; this board is the per-instance detail.

Both sites report on both sites, so the global view returns some series twice;
`IDENTITY` in `dashboards.py` reduces those with `max`, which keeps the worst state
and lets a real answer replace an observer's Unknown.

Every hop keeps the incident's time range. Homelab's panels link to their domain
board with the selected sites and datasource (L1 → domain). Domain panels with an
entity board link to it on the clicked series or row instead: an ingress to the
NGINX board, a router to MikroTik, a node to the mixin's Node (Pods), a Windows
host to its exporter board, an access point to Home Wi-Fi (domain → entity).
Telegram alerts open their `dashboard_url` from an hour before the alert started
to now, or to 15 minutes after it resolved. Every alert that reaches a person has
one: its domain board, scoped to the site the alert is about (generated alerts'
`k8s_cluster`) or else the site whose Prometheus raised it
(`$externalLabels.cluster`). `tests/test_alert_links.py` fails on an alert
without one. `tests/browser_check.py` clicks both
hops in a disposable Grafana and checks the target, time range and selection.

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
| `fence` | a lease naming this site blocks promotion (3). Otherwise met only by the other site's fence attestation for the app, at most 5 minutes old (below); none leaves it Unknown |

`homelab_restore_last_success_timestamp_seconds` feeds the Sites & Failover board.
Restore remains Unknown until a completed drill covers every required PVC. Its
age is conservatively measured from the restore object's creation. The absence of
copied PVCs does not prove a PostgreSQL database restore or an application RTO.

Fence attestations: each minute the fence Job on each site records, per app whose
lease it read, the lease, a result and the time in
`databases/site-lease-fence-status` (Flux creates it, `ssa: IfNotPresent`, and
never resets it). `clear` means the lease doesn't name that site. `absent`,
`replica`, `fenced` or `fencing` mean it does, and none of the site's Clusters for
the app can write. An unreadable lease or an API error writes nothing, so the
attestation ages. Each collector publishes its own site's attestations
(`homelab_fence_last_verified_timestamp_seconds`, `homelab_fence_attestation_info`)
and reads the other site's from that site's Prometheus over the mesh. So the
standby is only ready when the site it would take over from has proven, within
5 minutes, that its fence reads this app's lease and would act on it. With that
site down, the fence stays Unknown: the lease is its fence when it returns.

`homelab_fence_job_last_success_timestamp_seconds` reports only fleet Job liveness.
`homelab_lease_authority_reachable` reports whether the current DNS answer is
recognized, and `homelab_site_lease_blocks_local` reports its local restriction.

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

## Archive evidence

Each site's collector reads the Compactor's block index through the global
`observability-compactor` Service and reports, per source site, the newest and
oldest archived sample, block count and bytes. `homelab:archive_lag_seconds` is
how old the newest archived data is: 1-3 h is normal, because Prometheus cuts a
2 h block once its head spans 3 h, and `ObservabilityArchiveBehind` fires past
4 h. `homelab:archive_history_seconds` is how far back the archive answers, and
`homelab:archive_free_ratio` is Garage's free space (`ObservabilityArchiveSpaceLow`
under 10%). A Store or Compactor that's merely running proves none of this.

First verified 2026-11-07: both sidecars uploaded (site 1 one block, site 2 two,
no failures), the Compactor synced them with nothing to compact and not halted,
and a range query restricted to the Store (`storeMatch[]`) returned site 2's
08:00-09:00Z data from the archive alone. Garage had 964 of 1081 GB free.

## Mesh evidence

The clustermesh-apiserver's own metrics (mesh API, KVStoreMesh, and etcd's
leader and size) are scraped on both sites. The `mesh` hop of
`homelab:network_path_state` needs both the site's Cilium agents and its
KVStoreMesh to have the other cluster ready; either one alone fails it.

Flux resources come from the evidence collector, not kube-state-metrics custom
resources: it lists every Kustomization, HelmRelease, GitRepository,
OCIRepository and HelmRepository with its Ready condition and suspension, which
is what the custom-resource config would add. HelmCharts are left out: a
HelmRelease's Ready covers its chart.

## Ingress and shared-VIP evidence

Each site's collector uses every name on the shared VIPs (`dns-sites.tf`) as a
client would: the router's DNS answer must be that name's VIP (A only; the
router answers AAAA with NXDOMAIN, which musl treats as no such name), TLS must
verify against the system CAs, and a `GET /` must be routed: any answer but the
default backend's 404 or a 5xx (an app's own 400, or a sign-in's 302 or 401,
still proves the path). `homelab:ingress_state` is per site and name, with the DNS
match, HTTP status and certificate expiry beside it. Each site's internal
ingress adds an `X-Homelab-Site` header naming itself, read into
`homelab_ingress_served_by_info`.

A pod, like the WSL host, reaches its own site's ingress for either VIP (each
distro prefers its own cluster's announcement), so these probes show each
site's readiness to serve every name, not the LAN client's route. That route is
`homelab:shared_vip_route`: the preferred site while its router session is up,
else the other. It's inferred from the router's live BGP sessions and the
declared local preference, not read from the router's route table. The WSL-01
host reaching site 1 on `10.10.95.200` is this design, not a misroute.

## Objectives

Pyrra runs on both sites (`monitoring/pyrra`). Flux owns it and the
ServiceLevelObjectives in `app/slos.yaml`; its operator owns the PrometheusRules
it generates from them, with generic `pyrra_*` series for Grafana. The first
SLOs are probe-based (`bool_gauge`): the Internet path, Home Assistant's name, all
shared-VIP names, and the MQTT broker. Each is per site, labelled with it
(`pyrra.dev/k8s_cluster`), since each Prometheus holds only its own collector's
probes.

The rules run on each site's own Prometheus, which keeps 15 d, so the windows are
14 d. A 28 d window there would quietly cover only part of itself; it needs a
history-aware evaluator over the archive (Thanos Ruler) first.
`homelab:slo_window_coverage_ratio` is the share of the window a site's
Prometheus holds, shown beside the availabilities on the Observability board's
Objectives tab, so an incomplete window reads as incomplete. Targets are
provisional and burn-rate alerts are off until a full window gives a baseline.
The Pyrra UI is `pyrra.<internal domain>`, on site 1's local Prometheus.

## Following rollout work

Access, recovery, review and drills are in
[`observability-operations.md`](observability-operations.md). Still open:

1. The household Grafana and its aggregate-only backend: revoke the old kiosk
   operator access, test every datasource API, and define fresh domain evidence
   before any household status can be Healthy.
2. 28-day objectives over the archive. A local 28-day recording on 15-day
   retention is invalid, so it needs a history-aware evaluator such as Thanos
   Ruler. Also per-app recovery objectives, and Pyrra burn alerts once the
   14-day windows are covered.
3. The distros' FRR view of BGP, structured operation events with local
   availability on both sites (site 2 has no Loki), and an off-site client
   vantage.

Unsupported evidence remains Unknown. Existing MS-01 manifests and AWS
application backup destinations remain separate from this metrics archive.
