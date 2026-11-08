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

### Dashboard ownership

One provider owns each UID:

| Provider | Boards | Retire one by |
| --- | --- | --- |
| sidecar (`grafana_dashboard` ConfigMaps) | generated `tnwks-*` boards, chart mixins | moving it to a mounted provider, then removing its file |
| `unpoller`, `nginx`, `alertmanager` (mounted) | pinned references (`upstream/catalogue.json`) | removing its catalogue entry |
| `legacy`, `windows` (mounted) | kept hand-made boards in `grafana/app/dashboard/` | removing its file |

The sidecar writes into an emptyDir after Grafana starts. With deletion on,
Grafana would delete and re-create every sidecar board on each restart, so the
sidecar provider keeps `disableDelete: true`. Then a removed file doesn't retire
its board; it leaves an ordinary, unprovisioned dashboard behind. Mounted
providers' files exist at startup, so they delete safely. Checked against the
pinned Grafana with a persistent database:

- In one apply, a mounted provider takes over boards the sidecar provider owned.
- Removing a file then deletes its board while Grafana runs, and it stays gone
  after a restart.
- The other boards keep their UIDs.

`validate_grafana.py` asserts this split.

The generated boards are provisioned on both sites (Grafana runs on one at a
time). `home` is the generated Homelab board. The hand-made board it replaced,
Homelab (classic), pinned one Windows host by name and was retired by removing
its file from the `legacy` provider. Its panels live on, without a pinned host,
on Kubernetes & GitOps, Hosts & Capacity (with the Windows Exporter board's
`server` variable), Internet and Network & Routing's Router tab. Grafana 13 accepts the v2 file in
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
`IDENTITY` in `observability/dashboards/queries.py` reduces those with `max`, which keeps the worst state
and lets a real answer replace an observer's Unknown.

A timeseries names its series by the labels its query keeps, from the first
`by (...)` (`legend()` in `observability/dashboards/model.py`), else by site, app and instance. Until
2026-11-07 every panel used the fallback, so about twenty (HTTP requests by
ingress, Cilium drops by reason, notifications by integration, ...) showed
several series under one site name.

### Moving between levels

House systems (Home Assistant, L0) → Homelab (L1) → a domain board (L2) → a
diagnose board (L3). The same three rules hold on every board:

- **A click on data goes one level down, in one click.** A tile, a table value
  or a graph point has at most one link, it opens on the first click (Grafana's
  `oneClick`, set in `layout.one_click()`), and its name says where: "Open
  Internet", "Open Host". Homelab's Needs attention rows each open their own
  area's board (a hidden `board` column, `ROW_BOARD`): a Flux row opens
  Kubernetes, a host row Hosts.
- **The top bar goes up and sideways.** Every board below Homelab has "↑ Homelab";
  a diagnose board also has "↑" to its domain board (`Board.parent`), and a domain
  board "↓" to each of its diagnose boards. The "Dashboards" list has every board.
- **The runbook is the ↗ icon in a panel's header**, never a data link: a click on
  a value never leaves Grafana.

In Home Assistant, the "House systems" heading opens Monitoring (Grafana's
Homelab). The area tiles keep their own tap (the area's state history): the
Monitoring dashboard is admin-only and a tile's tap can't differ by user, so
pointing the tiles at it would hand the household a page they can't open.

`test_operator_links_preserve_multiple_sites_and_time_without_exposing_operator_links_to_house`
and `test_every_level_links_up_and_down` hold these rules.

Every hop keeps the incident's time range. Homelab's panels link to their domain
board with the selected sites and datasource (L1 → domain). Domain panels with an
entity board link to it on the clicked series or row instead: an ingress to the
NGINX board (ingress-nginx's own), a node to the mixin's Node (Pods), a Windows
host to Host, a Wi-Fi device to Wi-Fi clients, a WAN device to WAN clients, an
access point to Home Wi-Fi (domain → entity; see "Diagnose boards" below).
Telegram alerts open their `dashboard_url` from an hour before the alert started
to now, or to 15 minutes after it resolved. Every alert that reaches a person has
one: its domain board, scoped to the site the alert is about (generated alerts'
`k8s_cluster`) or else the site whose Prometheus raised it
(`$externalLabels.cluster`). `tests/test_alert_links.py` fails on an alert
without one. The charts' own rules (kube-prometheus mixins, Kopiur,
tuppr) carry a runbook instead; Telegram links those to the Alerts board for the
site that raised them, at Grafana's address beside Alertmanager's external URL. `tests/browser_check.py` clicks the
hops in a disposable Grafana (Homelab to Sites, an access point to Home Wi-Fi, a
WAN device to WAN clients, then "↑ Internet" back up) and checks that one click
lands on the target with the time range and selection kept.
`test_every_drilldown_lands_on_a_board_that_takes_its_variables` fails on a link
to a UID nothing deploys, or one that sets a picker the target doesn't have.

### Diagnose boards

The L3 boards answer "which one, and why" once a domain board says where.
Each has an entity picker (`variables=`, `Variable` in `model.py`: the values of
one label from Prometheus over the selected range) and is reached from the domain
board's panels:

| Board | Picker | Reached from | Answers |
| --- | --- | --- | --- |
| Host (`tnwks-host`) | one Windows host | Hosts & Capacity's Host state rows and host graphs, Wi-Fi's host uplink traffic, Databases' disk free | USE per resource: a tab each for CPU, memory, disk and network, busy (utilization), queued or paging (saturation) and errors, with the WSL VM's own CPU and Linux pressure stall (PSI) beside the Windows view |
| Wi-Fi clients (`tnwks-wifi-clients`) | wireless devices | Wi-Fi's "Clients with weak signal" | worst-first tables (weak signal, low satisfaction, most retries, reconnects) over every device; graphs of signal, satisfaction, retry rate and throughput for the picked ones; where each is associated |
| WAN clients (`tnwks-wan-clients`) | devices from flow export | Internet's Upload and "Top WAN devices now" | top downloaders and uploaders now, data per device and per destination over the range, and Internet latency beside per-device upload, crosshair-synced, to line a spike up with its sender |

The worst-first tables ignore the picker so they always cover everything; the
graphs follow it, and a click on a table row picks that entity
(`sort="asc"|"desc"` orders a row table by its value). A diagnose board's empty
list is the good case ("None below -70 dBm"), not missing evidence.

Homelab's Hosts tile is the worst of `homelab:host_state` (`host_records()` in
`observability/rules.py`): each Windows host's resources graded from
`homelab:host_usage`, the measured value. Site 1's Prometheus scrapes both hosts'
exporters (measured 2026-11-08), so it alone observes them; `IDENTITY` reduces a
second observer's copy like the other states. A host whose exporter is down
reads Unknown on every resource.

| Resource | Measured | Degraded | Failed |
| --- | --- | --- | --- |
| CPU | busy share of all logical processors, over 15 minutes | above 85% | above 95% |
| Memory | share of RAM not available to new work (standby cache counts as available), over 15 minutes | above 90% | above 97% |
| Disk latency | seconds per operation on the slowest volume doing over 1 operation a second, over 15 minutes (an idle disk is 0) | above 50 ms | above 200 ms |
| Disk space | free share of the fullest volume (`HarddiskVolume*` system partitions excluded) | below 10% | below 5% |

Measured 2026-11-07, and why some queries look the way they do:

- **Windows counts the WSL VM's CPU as privileged time.** About 2.5 busy VM cores
  showed inside 3.5 privileged host cores, so Host's CPU busy covers the
  cluster. The VM has fewer processors than the host (its `.wslconfig` limit), so
  Host also shows the VM's own busy share and CPU pressure: the VM can be full
  while the host has room.
- **Memory is graded on RAM in use, not commit charge** (changed 2026-11-08).
  Commit charge is memory promised against RAM plus the page file's *current*
  size. The WSL VM commits its whole `.wslconfig` memory (site 1: 38.8 GB
  committed, 16.8 GB resident), so commit reads high: 93% of the limit while 70%
  of RAM was in use, and the page file barely touched (under 1% of 13 GB used).
  The page file is automatically managed and grows when commit nears the limit:
  site 1's limit rose from 73.9 to 76.9 GiB over the week before. A commit
  graded against a moving limit was Degraded most of the time for no shortage.
  Host still shows commit charge, and `WindowsHostCommitHigh` (critical, above
  98% for 5 minutes) catches the one real case: commit pinned at the limit
  because the page file can't keep up or has reached its cap. One large
  allocation (qmd's 32 GiB GPU reservation in WSL) can fail before the file
  grows; a fixed minimum page file size is the fix if that recurs.
- **Disk latency** is the latency counter's rate over the operation counter's
  (`windows_logical_disk_*_latency_seconds_total`), seconds per operation, on
  real volumes only (`HarddiskVolume*` are the hidden system partitions).
- **UniFi refreshes every 2 minutes**, so client rates use a 10-minute window,
  not `$__rate_interval` (2 minutes, too few samples to make a rate). Retry rate
  is retries / (packets + retries), as UniFi shows it: retries over attempts
  read 350% on a near-idle device.
- **Client names aren't per site.** One controller sees the whole house, and only
  the poller's own copy (`source_site=""`) is read, so the Wi-Fi clients board
  ignores the site picker. WAN clients does too: the flow rules sum the site
  label away and only site 1's router exports flows.
- **Flow export doesn't see everything.** WAN clients' "Download attributed" is
  the share of the line's download that flows put a device to; it read 35%, so
  per-device numbers are a ranking, not an account.

Device names are runtime data from UniFi and the router's DHCP leases. The
repository never names a household device: the boards hold queries, and the
pickers fill in when someone signed in to Grafana opens them. Screenshots of
these boards stay off GitHub, as with every live render.

The kept classic boards stay for now: Home Wi-Fi has the access points' radio
detail (channel use, airtime) the L3 boards don't, and WAN Traffic and the
Windows exporter board can be retired once the diagnose boards have been used
through an incident.

### Design system

The boards share one visual vocabulary, set in code and held by
`tests/test_design.py`; nothing is styled in the UI. `observability/dashboards/design.py`
has the tokens, `recipes.py` turns a declared panel into a visualization,
`layout.py` places it.

- **State is a word on a colour, and colour means state.** `STATE` maps 0–6 to
  Unknown, Healthy, Degraded, Failed, Standby, Not applicable and Maintenance.
  A stat colours its tile (solid: axe can't judge text on Grafana's gradient), a
  table its cell (Grafana's white or black text keeps 4.5:1 on each). The base colour is grey, so a value without a mapping can't
  read as healthy. Other numbers are the neutral text colour. A series takes its
  site's colour (`SITES`: 3:1 on both themes, none of them a state colour).
- **Answer first.** A sectioned tab runs headline (stats), status (state
  tables), trends (graphs) and detail (other tables), each a row with its own
  auto grid (`SECTIONS`): six tiles across, tables two across (at three they cut
  off columns), About folded at the end. A panel's section follows from its kind
  and unit (`role()`); `role=` overrides it.
- **Fleet tables.** `per_site="<label>"` turns a per-site table into one row per
  value of the label and a column per site (`groupingToMatrix`, rows sorted,
  sites in `CLUSTERS` order). The series must be unique by the label and site. A
  pair with no series is an uncoloured dash, and a fleet table has no per-row
  Diagnose link.
- **Words, not codes.** Headers come from `LABELS` (a raw label name fails the
  tests), booleans are words (`words=`), and a value's unit, mappings and links
  sit on its own column only. A label a row doesn't have is a dash: Grafana would
  print the panel's empty-state words in the cell.
- **Zero is normal.** The `failures` unit is a per-site count over the selected
  range, neutral at zero and red from one, where a graph drew a flat line on an
  empty axis. Graphs of zero-based units start at 0; ratios stop at 100%.

Contrast was checked on 2026-11-07 with axe-core 4.10.2's colour-contrast rule
on every tab of every board in both themes, against live data: no violations.
What axe can't judge (Grafana's overlapping table cells, header pseudo-content)
was measured against its nearest opaque background instead, and all of it meets
4.5:1, or 3:1 for large text.

A new panel is a `p()` call. A new label needs a `LABELS` entry; a state metric
takes `unit="state"`. A new site is an entry in `CLUSTERS` and a colour in `SITES`
(`test_a_third_site_is_one_more_entry`).

To look at a change before it merges, `observability/screenshot.py --out <dir>`
screenshots every board tab (dark at 1600 px, Homelab light, House at 412 px)
from this checkout in a throwaway pinned Grafana, read-only against site 1's
Thanos Query (`--boards` to pick, `--toggles` to try a feature flag). Those
show real access point, camera and host names, so they stay off GitHub and it
refuses an output directory inside the repo. CI's browser rehearsal keeps its
fixture screenshots as the `grafana-fixture-screenshots` artifact.

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
request; its 768Mi limit is peak headroom, not idle consumption. The reference
boards are provisioned from git rather than downloaded during pod startup
(`grafana/upstream/README.md`). Removing the `network` and `plausible` providers
on 2026-11-07 retired the MikroTik and Plausible boards; their panels are
generated now.

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
| `mesh` | Cilium agents' and KVStoreMesh's remote-cluster readiness, and the site's own probe of its peer (`homelab_peer_probe_success`) | all ready and the probe passes (no probe: readiness alone). Readiness alone isn't enough: in the partition drill, site 2's control plane reached site 1 around the downed tunnel while pod traffic failed |

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
evidence is left out, so `homelab:recovery_state` stays Unknown. The site that runs
the app (desired replicas above 0) shows Not applicable (5): nothing there needs to
take it over, and the restore drill only runs where the app is off.

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

## Site 2's Wi-Fi dependency

Site 2's Windows host reaches the router over Wi-Fi only; site 1's is wired.
The Wi-Fi & LAN board's Site dependency tab measures that path from both ends:
each host's NICs with a link and their negotiated rate and traffic
(windows_exporter), and the site 2 host's signal, access point and association
uptime as UniFi reports it (`SITE2_WIFI_CLIENT` in `observability/dashboards/queries.py`,
its computer name; no household device is named in the repository). A reassociation resets
the uptime and interrupts the site tunnel and its BGP sessions, so read it with
the Network board's History tab. First measured 2026-11-07: site 1 wired at
1 Gbit/s, site 2 on Wi-Fi at 300 Mbit/s and -72 dBm on channel 149.

## Databases outside CloudNativePG

MongoDB (UniFi), Immich's Postgres, Plausible's ClickHouse and the two Valkeys
have no exporter. `observability/inventory.py` (`OTHER_DATABASES`) lists them,
and `homelab:other_database_state` reads their workloads from kube-state-metrics:
Healthy when every desired replica is ready, Standby at zero replicas, Failed
otherwise, Unknown when the site doesn't report the workload. Ready means the
pod passes its readiness checks. ClickHouse has none, so Plausible's content
probe requires both `"postgres":"ok"` and `"clickhouse":"ok"` from its health
endpoint. The Databases & Backups board shows the states and the database
containers' restarts. A probe can require several strings; each must appear.

## Event history

Failover, Flux, upgrade and network events are kept as Prometheus series, so
each site's own Prometheus holds its events for 15 days and the archive for 35.
Either site reads them locally, without Loki (site 2 has none). Each board's
History tab lists the events in the selected time range.

| Board | Events | From |
| --- | --- | --- |
| Kubernetes & GitOps | Git/OCI revisions fetched, Helm installs and upgrades (chart and app version, deployed or failed), Flux Ready transitions, node Talos/kubelet/kernel versions | collector (API event times), kube-state-metrics |
| Sites & Failover | serving-state changes per app and site, shared-VIP route changes, workload availability transitions | recording rules, collector |
| Network & Routing | router BGP session resets, Cilium BGP session changes, path hop changes | MKTXP, Cilium agents, recording rules |

There are two kinds of row:

- **Event times from the API.** The value is when Kubernetes or Flux says the
  event happened (`homelab_flux_source_revision_timestamp_seconds`,
  `homelab_flux_release_deployed_timestamp_seconds`,
  `homelab_flux_ready_transition_timestamp_seconds`,
  `homelab_workload_transition_timestamp_seconds`). A scrape gap delays a row
  but doesn't move it. Only the latest event per label set survives between
  scrapes, so two upgrades within one scrape interval show as one.
- **Sampled changes** (`changes()`, `resets()` over the range). These count
  transitions between samples, so a flap shorter than the scrape or rule
  interval can be missed. A BGP session reset is the exception: the uptime
  going back counts it even when the session recovered between scrapes.

An empty list is the normal state ("None in range"); whether each source is
reporting is on the Observability board. A HelmRepository's revision is an index
digest that changes on any upstream publish, so it isn't recorded as an event.

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
`homelab:shared_vip_route`: the preferred site while it carries routes, else
the other. A site carries routes while its router session is up and it sends the
router at least one prefix: with a site's nodes down, its distro's FRR keeps the
session up and announces nothing. It's inferred from the router's live BGP
sessions, prefix counts and the declared local preference, not read from the
router's route table. The WSL-01
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
Objectives tab, so an incomplete window reads as incomplete.

Pyrra's multi-window burn-rate alerts are on (`ErrorBudgetBurn`, with the SLO
and site as labels): critical for a fast burn (14x over 30 m and 3 m, 7x over
3 h and 15 m), warning for a slow one (2x over 12 h and 1 h, 1x over 2 d and
3 h). The longest of those windows is 2 days, so they don't need a full 14-day
window. They were turned on 2026-11-07, once the targets were final. Replayed
over the history then (about 6 h), only `shared-vip-names` on site 1 would have
fired, during the site-loss drill, when eight names served only from site 2
really were down.
The Pyrra UI is `pyrra.<internal domain>`, on site 1's local Prometheus.

The 28-day view needs no evaluator over the archive: the Objectives tab computes
each SLO's indicator over 28 days at query time (`avg_over_time(...[28d])`, from
the same `slos.yaml` definitions), next to its 28-day window coverage, the share
of the window the chosen datasource holds from the indicator's earliest sample.
Through the Global datasource the archive supplies the weeks local retention
lacks; Local can't cover more than 15 days, and the coverage says so. It's a
comparison, not an alerting objective: burn-rate alerts come from the 14-day
Pyrra rules.

### Recovery objectives per app

Each app has a recovery point objective (RPO) and, if it's warm with PVCs, a
restore time objective (RTO), in `observability/inventory.py` (`OBJECTIVES`,
overridden per app in `APP_OBJECTIVES`): 30 h, a daily backup plus slack, and
1 h. They were set on 2026-11-07, with the choice delegated by the owner: every
app shares the daily schedule and the weekly drill, so no app overrides them yet.

- **Recovery point.** `homelab:app_backup_age_seconds` is the age of the app's
  oldest newest-backup over every PVC with a running Kopiur schedule on the site
  (Kopiur's last success for the PVC's SnapshotPolicy) and every primary
  database there (Barman's). `homelab:app_rpo_state` is 1 within the objective
  and 3 past it. It's Unknown when a scheduled PVC or a primary has no backup
  evidence, because a fresh backup of one piece can't stand in for another. A
  site with nothing scheduled, like a standby, has no row.
- **Restore time.** The collector times each app's last successful standby
  restore drill, from each Restore's creation to its Ready condition, taking
  the slowest PVC (`homelab_restore_last_duration_seconds`).
  `homelab:app_rto_state` compares that with the objective and stays Unknown
  until a drill reports. The site that runs the app shows Not applicable (5),
  since the drill restores into the standby. This covers the data restore, not
  the whole failover.
- **Coverage.** The collector reports which SnapshotPolicy covers each
  catalogue PVC and whether a running schedule uses it
  (`homelab_app_backup_scheduled`). A PVC no site schedules has an unbounded
  recovery point; the Databases & Backups board lists these on its Objectives
  tab, next to the states.

## Household view

The household's view (L0) is a **House systems** section on Home Assistant's
Home dashboard: ten words (Internet, Wi-Fi, Automation, Cameras, Apps,
Devices, Backups, Storage, Away access, Monitoring), each Healthy, Degraded,
Failed or Unknown. The household already
uses Home Assistant on the wall displays, so it needs no second Grafana.

- **Each site certifies its own share.** `homelab:house_state{domain}`
  (`house_records()` in `rules.py`) is recorded on each site's Prometheus from
  what that site serves: its Internet probe; the APs or cameras that worked at
  some point in the last day against those working now (something off for a
  whole day drops out, so a disabled camera doesn't count, and an exporter that
  stops reporting leaves no row, so it reads Unknown); Home Assistant where
  it's active; the worst of the apps active there; whether its own collector
  reports. Since 2026-11-07 also: the worst of its apps' recovery points and
  database backups (Backups); of the devices Home Assistant had today, the share
  available now, under 95% Degraded and under half Failed, where the worst week
  kept 96.7% (Devices: lights, switches, sensors, climate, locks, covers, fans,
  media players, the vacuum; not automations or helpers); its fullest Windows
  drive, under 10% free Degraded, under 5% Failed (Storage); and the Cloudflare
  tunnel's edge connections, 4 normally (Away access). A site has no row for
  something it has no part in.
- **Home Assistant combines the sites**
  (`config/packages/house_systems.yaml`). It asks Thanos Query, on whichever
  site it runs, once a minute. Internet takes the best site (one line; site 2's
  Wi-Fi isn't the house's Internet), and so does Away access (one tunnel is
  enough). Wi-Fi, Automation, Cameras, Devices, Backups and Storage take the
  worst site with a part in them, Unknown when none has. Apps is Unknown when a
  site is missing. Monitoring is Degraded when some sites report, Failed when
  none do.
- **Privacy by construction.** Only those aggregates reach Home Assistant:
  the queries are fixed in git and carry no host, camera, AP or person. The
  Monitoring tab, which frames the operator Grafana (whose viewer can query
  every datasource), is admin-only.
- **Tests.** promtool fixtures cover a site's share (a failed app, partial
  cameras, a domain with no part) and nothing working. The combining templates
  were rendered against synthetic answers: normal, a site lost, site 2's
  Internet probe failing, a failed app, partial cameras, and no answer.

The isolated household Grafana that `observability/dashboards/boards/house.py` still generates (`House`,
`kiosk_proxy.py`) is not deployed; this replaces it.

## Preview features

Grafana 13.3 has 398 feature flags, 63 on by default; the boards need none
beyond those defaults (v2 tabs and the auto grid come from `dashboardNewLayouts`
and `grafana.dashboardAutoGridDefault`). Since 2026-11-07 six off-by-default
flags are on trial, set in the HelmRelease's `grafana.ini` `[feature_toggles]`:

| Flag | What it changes |
| --- | --- |
| `preserveDashboardStateWhenNavigating` | A drilldown keeps the chosen sites and datasource. |
| `table.autoColumnWidths` | Table columns fit their content: site names no longer truncate (`homelab-wsl-(`). |
| `grafana.dashboardsAutoHeightPanels` | Auto-grid panels can size to their content. |
| `dataviz.tabularNums` | Fixed-width digits, so values align in tables and stats. |
| `enableColorblindSafePanelOptions` | Adds colorblind-safe palette options to panel settings. |
| `recentlyViewedDashboards` | Recently opened boards on the dashboards page. |

The trial is bounded by the rehearsal: `rehearse_grafana.py` reads the same
flags from the HelmRelease, starts the pinned Grafana with them, checks through
Grafana's flag evaluation API that each one is on, and runs the browser check
of every board, tab and reference. The stable fallback is deleting a line:
nothing generated depends on a flag. Flags change between Grafana releases, so
a version bump reruns the rehearsal before it ships.

`grafana.visualDesignRefresh` was rendered both ways on 2026-11-07, Homelab and
Sites in both themes: it changes the navigation's highlight and some spacing,
not the boards, so it stays off.

## Following rollout work

Access, recovery, review and drills are in
[`observability-operations.md`](observability-operations.md). Still open:

1. Household view: check the House systems section on a wall display once it's
   live (see "Household view").
2. 28-day alerting would need a history-aware evaluator (Thanos Ruler); the
   28-day view doesn't. The 14-day burn-rate alerts are on (see "Objectives").

Closed by decision (2026-11-07, the owner delegating): no FRR exporter, because
FRR is seen from both ends of its sessions (the router, each Cilium node) and
through the cross-site probes; and the off-site vantage is healthchecks.io
([`external-monitoring.md`](external-monitoring.md)).

Unsupported evidence remains Unknown. Existing MS-01 manifests and AWS
application backup destinations remain separate from this metrics archive.
