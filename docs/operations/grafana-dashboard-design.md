# Grafana dashboard redesign: reviewed plan

Reviewed 2026-11-06 against repository commit `0fb7e829960dda06f281a666952a709f5b7fd15f` and the primary sources linked below. This is a design review and implementation backlog. No live Grafana inventory, metric samples, or cluster health were rechecked in this review; the earlier API audit remains a dated snapshot. Refresh the baseline before each implementation PR because other infrastructure work is ongoing.

## Verdict and decisions

Keep the L0 household → L1 homelab → L2 domain → L3 entity hierarchy, symptom-first overviews, RED/USE detail, stable links, and dashboards as code. These fit [Grafana's dashboard guidance](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/). The largest remaining design issue is whether the dashboards tell the truth during a partial failure. Data coverage, freshness, failover evidence, and usable access during an outage are acceptance requirements.

The owner is open to all seven original proposals. Adopt the following implementation defaults; these record direction, not completed work or permission to interrupt production.

| Original open item | Reviewed decision |
| --- | --- |
| Grafana upgrade | Yes: target a supported 13.3 patch, pin image/chart versions after checking their compatibility. Rehearse migration and restore before rollout. Telemetry hygiene can proceed independently. |
| Grafana per site | Yes: independent instances, local persistent databases, identical provisioned dashboard/datasource UIDs. Retain the existing local CNPG DB on site 1; a local SQLite PVC is sufficient for the single-replica site 2 instance. Never point site 2 at site 1's DB. |
| Cross-site data | Yes: local and per-site Prometheus datasources as fallback, plus a Thanos Query on each site. Routing/mesh are already configured, so do the global-query proof early rather than wait for another network project. |
| Durable retention | Yes: persistent Prometheus on both sites, 15d as an initial floor; target at least 35d queryable raw history for 28d SLOs and comparisons. Size from measurements and account for the existing size cap. |
| Generators and CI | Yes: pinned Foundation SDK, shared helpers, reproducible outputs, query fixtures, and provisioning/browser checks. Prove v2 support in the chosen linter instead of assuming it. |
| Separate household kiosk | Yes: L0 House with its own information and access contract; L1 remains the operator home. |
| SLO tooling | Yes: Pyrra as the initial choice, with a small number of meaningful SLIs and multi-window burn alerts. Keep SLO definitions in git; document ownership of generated PrometheusRules. |

## Corrections to the original plan

### 1. The network work is no longer a future prerequisite

Both WSL cluster settings enable Cilium BGP and Cluster Mesh. Their overlays include the BGP and mesh Kustomizations, and shared-VIP resources exist. This establishes git intent, not a fresh live-health claim. Monitor the current network now. The immediate telemetry gaps are:

- `CILIUM_SERVICEMONITOR_ENABLED` is false on **both** WSL clusters.
- MKTXP still has `bgp = False` and `routing_stats = False`.
- Router → FRR → Cilium route propagation needs telemetry at each hop, not just a BGP session count.
- Cluster Mesh API server/KVStoreMesh have separate metrics settings; the existing general ServiceMonitor switch alone is insufficient. Check these against the pinned Cilium chart. [Cilium metrics reference](https://docs.cilium.io/en/stable/observability/metrics/).

Treat `homelab-wsl` and `homelab-wsl-02` as the current two-site scope. Keep MS-01 extensible through inventory/capability metadata; do not count it as an expected live site without evidence. WSL hosts sharing router, switch, WAN, and power are separate compute failure domains, not independent locations. Show those shared dependencies explicitly.

### 2. Fix label semantics before multi-cluster dashboard queries

Prometheus [external labels](https://prometheus.io/docs/prometheus/latest/configuration/configuration/) are attached when communicating externally; setting one does not add it to all locally stored samples. The proposed universal `{cluster=~"$cluster"}` filters can return nothing against a local Prometheus.

There is also an actual collision: `PostgresBackupStale` derives a `cluster` label containing the **CNPG database cluster** from the pod name. It must not become the Kubernetes site label. Adopt this contract for new normalized metrics:

| Label / variable | Meaning |
| --- | --- |
| `k8s_cluster` | Authoritative Kubernetes cluster identity, available in local and global queries |
| `db_cluster` | CNPG cluster identity, independent of Kubernetes site |
| `observer_cluster` | Where the observation was made, for cross-site probes and shared-device scrapes |
| `site`, `host`, `namespace`, `app` | Bounded inventory dimensions; neutral public identifiers |
| Dashboard variable `cluster` | User-facing cluster picker; filters `k8s_cluster` in new dashboards |
| Legacy `cluster`, federation `source_site` | Compatibility/provenance labels; normalize deliberately, never assume interchangeable |

Add `k8s_cluster` to local scrape/rule outputs and external labels through explicit configuration. For imported peer metrics, preserve the origin rather than relabel them as the scraping site. Migrate DB recording/alert labels and annotations to `db_cluster` together. Adapt upstream mixin queries/variables at their boundary instead of blindly replacing every `cluster` label. Test with the same CNPG name on both sites.

The existing peer federation includes CNPG role/WAL metrics and, on site 1, UniFi metrics. A global query must avoid counting both the originating series and its federated copy. Likewise, both sites observing one Windows host or router are two observations of one resource. Define authoritative sources or explicit observer aggregation. Thanos replica deduplication applies to genuine replicas, not two distinct clusters. Never use the site label as a dedup replica label. [Thanos Query](https://thanos.io/tip/components/query.md/).

Switching from Mixed panels to Thanos is not automatically a datasource-only change: joins, local-label normalization, federation provenance, and panel variables all need regression checks.

### 3. Never hide missing expected infrastructure

The permanent L0/L1 header shows the serving Grafana site, selected clusters, expected versus reporting sites, observation age, and query coverage. A selected site never vanishes from the overview because it stopped reporting. Distinguish:

- Healthy, degraded, failed: measured service condition.
- Unknown/unreachable: expected evidence missing or query failed.
- Stale: evidence exists but its timestamp exceeds the freshness budget.
- Standby, not applicable, maintenance: deliberate states with an explanation.

Use declared expected inventory so disappeared targets still have rows. An `up` query alone cannot find a target removed from discovery. A successful `up` scrape also does not establish exporter freshness or application success. Check collectors' success/last-update metrics independently.

Show/hide is appropriate for capabilities absent by design (for example, no GPU detector at a site), not for hiding failures. [Current grouping documentation](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/create-dashboard/dashboard-groupings/) limits query-result visibility conditions to panels; row/tab rules and exact schema capabilities must be verified on the pinned version. An HTTP datasource error is not equivalent to an empty successful query.

With Thanos, allow partial results for diagnosis only with a conspicuous coverage warning. Readiness/SLO summary computations require complete evidence; use strict queries and/or an independently evaluated completeness gate. Retain a direct local datasource and a fixed local-health page if global querying fails.

### 4. Retention needs a storage and history budget

Both Prometheuses currently use `emptyDir`, `retention: 2d`, and `retentionSize: 6GB`. Changing the time limit alone will not deliver 15d if the size cap deletes blocks earlier. An `emptyDir` survives a container restart within the pod, but not pod replacement.

The audit's 0.65 GB/day estimate implies roughly 10 GB of **blocks alone** for 15d on site 1. Add WAL/head/index/compaction space and growth; measure site 2 separately. Set the size cap below PVC capacity; [Prometheus recommends at most 80–85%](https://prometheus.io/docs/prometheus/latest/storage/). Recheck ingestion after adding Flux, Cilium/Hubble, FRR, and SLO rules.

Prove writable persistent storage under the actual UID/GID/storage class in isolation. Do not work around permissions by broadening production container privileges. Check node and host disk headroom; a local-path PVC is persistent across pod replacement, not a replica or protection from host loss.

A 28d SLO needs a complete 28d observation window, plus room for comparison and delays. Show an explicit history-coverage indicator while data accumulates; a new empty PVC cannot recover old history. Add S3 upload/store gateway/compaction after the query-only proof. Define raw/downsample retention, capacity, S3 cost, and compactor ownership; do not copy the MS-01 Rook-backed chart unchanged onto WSL. Two queriers are useful; two compactors concurrently owning the same stream are not.

### 5. Readiness is evidence, never a guarantee of safe promotion

Rename the proposed overall “safe to switch” tile **Readiness evidence**. Show each prerequisite, its age, and the reason for a blocked or unknown result:

- Desired/default placement, effective override, observed ready workloads, actual serving endpoints, DNS lease value, and observed writable DB primary are separate facts.
- Lease authority reachability, expected holder, gate/fence result, and time since last successful fencing check are required evidence. A TXT probe checks an answer; it does not prove a former primary is fenced.
- CNPG role/timeline, archived WAL position, replay position and replication backlog, backup/repository credentials, writable standby PVC placement and capacity, and required runtime secrets must agree.
- RPO is an estimate with a timestamp and source; replay timestamp lag grows on idle databases and is not automatically data loss. A stale last-WAL timestamp alone can also be normal during idle periods.
- Display last actual RTO/RPO and last restore drill per app. The current weekly standby-restore job covers four configured PVC entries; one successful CronJob does not certify every app, database, or recoverable backup.
- Unknown or missing fencing/recovery evidence blocks a green readiness summary. Dashboards remain read-only; link to the existing runbook/tool for the human decision.

Use the Barman plugin's metrics, including the currently referenced `barman_cloud_cloudnative_pg_io_last_available_backup_timestamp`, rather than obsolete in-tree CNPG backup timestamps. [CNPG documents the deprecation and plugin ownership](https://cloudnative-pg.io/docs/1.28/monitoring/). Missing backup series are an explicit coverage failure, not a zero-age backup. Test metric names and labels against deployed samples before finalizing queries.

### 6. Verify the alert and event UX against the actual alert owners

Provision both Prometheus and Alertmanager datasource pairs with deterministic UIDs. Build a per-site current-alert view from proven Prometheus rule/`ALERTS` data or tested alert-list support; link to each Alertmanager for silenced/inhibited/notification context. Merely adding two Alertmanager datasources does not prove the alert list merges them. [The alert-list documentation](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/alert-list/) describes rule datasource selection and a current-state view, not historical time-range filtering. Verify the exact behavior with the pinned Grafana build before selecting the visualization.

Deduplicate incident summaries intentionally and retain observer/site identity. “No active alerts” is healthy only when both rule evaluation and alert delivery are working. Keep PrometheusRules and Alertmanager configuration as the current owners rather than migrate alert execution into Grafana for the sake of dashboard UX.

For historical events, record structured transitions: time, app, cluster, operation, outcome, reason, revision, and correlation ID. `ALERTS` sampled over a range is alert state, not a durable event log. `changes()` on an ephemeral gauge misses changes while unobserved; ConfigMap creation age is not the age of its latest modification. Add an explicit last-change timestamp and sanitized override summary from the owning mechanism. Do not expose ConfigMap/Secret contents or arbitrary override values as metric labels.

Store events in each site's local log/event path with bounded labels, mirror or archive as appropriate, and query them for annotations/state timelines. Make current-state and historical views visually distinct. Dashboard links from alerts include stable UID/panel, cluster/app scope and incident time; resolved messages retain useful time ranges. Grafana annotation posts can supplement this later, not serve as the only history during a Grafana outage.

## Additional coverage for the holistic view

Add these questions to the L2 catalogue; create a new dashboard only when it answers a distinct operator question.

| Area | Additional panels / evidence |
| --- | --- |
| Routing and mesh | Router/FRR/Cilium sessions and flaps; expected versus installed/advertised prefixes; selected route for each shared VIP; endpoint placement and externalTrafficPolicy; cross-site service success; mesh remote readiness; Hubble drops/retransmits; MTU/MSS/asymmetric-route symptoms. An Established session alone is insufficient. |
| Wi-Fi as a site dependency | AP uplink/port speed and errors, channel utilization, retry rate, RSSI/SNR distributions, roaming/reassociation, DHCP and DNS success, loss/latency seen from a Wi-Fi client or host. Correlate site 2 Windows/Wi-Fi/tunnel events with BGP and mesh health. Aggregate household clients and expose exporter freshness separately. |
| User paths | Synthetic DNS → TCP → TLS → HTTP/content checks from both sites; a LAN/client vantage point; off-site reachability for public paths. Separate identity-provider, oauth2-proxy, ingress, cloudflared and application health. Do not let a probe of an auth redirect certify the application behind it. |
| Storage and recovery | PVC/node/Windows volume capacity and inode pressure, disk/SMART, latency and I/O contention, local-path placement, NFS health, S3 errors, backup credential/certificate expiry, restore validation and coverage gaps. Distinguish intentionally unprotected data from failed protection. |
| Databases beyond CNPG | MongoDB/UniFi, Immich's own Postgres, SQLite/file-backed state, Redis/cache persistence where used, MQTT sessions/backlog. Inventory current placements and supported restore methods before adding exporters. |
| Dependency and security health | TLS expiry and trust, DNS/DHCP/NTP, SOPS/KMS/STS/certificate refresh, mesh/WireGuard certificates/keys' metadata expiry where available, auth availability, and required CI/Flux dependencies. Never export key material. |
| Observability itself | Targets and freshness, series/churn and scrape cost, rules missed/failed, notification delivery and off-site dead-man checks, Thanos stores/partial queries/upload lag/compaction, Grafana DB/renderer, Loki/Tempo/Alloy/Vector health, retention coverage and disk growth. |
| Capacity and resilience | Per-site allocatable headroom, largest-node/host loss, whether movable workloads fit on the survivor, resource requests versus observed peaks, backup/restore time and I/O limits, common router/WAN/power failures. Explicitly identify USB/pinned services that cannot fully fail over. |

Do not turn “holistic” into “every possible metric on the overview.” L1 shows impact, scope, since when, current coverage, and what changed. A domain page diagnoses the path; an entity view supplies detail. Keep health and redundancy separate: a service may work while its standby is unavailable.

## Modern Grafana UX, with compatibility gates

Use modern capabilities where they reduce steps or ambiguity. Keep a small core workflow working without preview features.

| Adopt | Concrete use / gate |
| --- | --- |
| Dynamic tabs, auto grid, scoped variables | L2 tabs by question; compact layouts for desktop/tablet; a tested stable link to each tab. Avoid deeply nested groupings merely because the feature exists. |
| Fleet tables and sparklines | DB, AP, node, backup and exporter fleets; sort by severity then age; retain expected rows when data is absent. Prefer bounded tables over hundreds of repeated panels. |
| Quick filters and grouping | L2/L3 exploration by allowed cluster/namespace/app dimensions. Verify each query actually applies the filter and is scoped; these controls do not regroup arbitrary PromQL automatically. |
| Subtitles, query notices, stable transformation IDs | Explain status meaning, expose query errors, and keep fleet joins stable when a query is empty. |
| State timelines, Canvas and Node graph | Transitions and dependency diagrams; live state, fixed site colors and health labels. A topology edge requires measured evidence or an explicit unknown state. |
| Links and correlations | Tile → domain → entity → logs/traces/runbook; preserve time, namespace/app/site. Add exemplars only where instrumentation and trace retention support them. Never inject credentials into links. |
| Time comparison | Previous day/week for WAN, Wi-Fi, requests and capacity, with history coverage shown. Compare matching periods and sampling resolutions. |
| Shared code helpers | One source for units, thresholds, descriptions, variables, navigation and field overrides; generated dashboards remain independently provisionable. |
| Preview trials | Cross-dashboard variables, data-driven Text, Notebooks and the refreshed theme in isolated preview first. Supply ordinary variable/stat/table/Markdown/runbook fallbacks. Their failure cannot block L0/L1. |

[Grafana 13.1 notes](https://grafana.com/docs/grafana/latest/whatsnew/whats-new-in-v13/whats-new-in-v13-1/) cover scoped variables, quick filters, fleet-table transforms and annotation clustering. [13.3 notes](https://grafana.com/docs/grafana/latest/whatsnew/whats-new-in-v13/whats-new-in-v13-3/) confirm subtitles, time comparison, threshold variables, stable transformation IDs, and the preview status of shared variables, Text, Notebooks and theme changes. Check editions/flags individually; “all modern features are GA OSS” is too broad. A threshold variable is presentation, not automatic synchronization with a Prometheus alert rule: generate both from a shared definition or graph the actual threshold recording rule.

Keep site identity colors distinct from severity. Use words/icons alongside colors; support kiosk viewing distance, browser zoom, keyboard operation, mobile/tablet layouts and timezone/DST. No arbitrary global `min=0` for signed deltas, temperatures or clock offsets. Percentage ceilings apply to bounded ratios, not CPU core totals or ratios that legitimately exceed 100%. A 1m default refresh is sensible; use slower rates for history and faster only for a deliberately faster sampled diagnostic view. A 1m scrape cannot reconstruct a five-second failover.

## SLO design

Start with Internet/DNS reachability, Home Assistant's usable path, critical public-app success/latency, and camera recording continuity. Document service scope, objective/window, good/total events or valid probe intervals, low-traffic behavior, maintenance policy, missing-data policy, and owner/runbook for each. Do not choose an arbitrary number of nines before measuring a baseline.

Backups additionally need per-app recovery-point and drill-freshness objectives; a fleet-wide average must not hide one unprotected app. Distinguish readiness/freshness objectives from request-success SLIs. On low-traffic home apps, synthetic probes provide independent evidence when the request denominator is zero. Lack of observations is unknown rather than a perfect budget.

Use [multi-window burn-rate alerts](https://sre.google/workbook/alerting-on-slos/) with deduplication/inhibition and actionable notification levels. [Pyrra](https://github.com/prometheus-community/pyrra) can generate rules from declarative SLOs. Flux owns its installation and SLO inputs; its controller owns generated rules, with no second Flux owner for the same objects. Keep local alert evaluation/delivery operational without Grafana, Thanos, GitHub or the other site. Do not average site availability to infer service availability; measure the user-visible service path.

## Delivery sequence and acceptance gates

Each row is a small PR or group of independently reviewable PRs. No feature-branch changes are hand-applied to production; local rehearsal, renders and server dry-runs build confidence, then Flux deploys merged intent. A production failure drill is a separately scheduled operation, not a pre-merge test.

| Phase | Deliverable | Required evidence before calling it done |
| --- | --- | --- |
| 0 — baseline and contracts | Recheck versions, rendered configuration, source UIDs, dashboard inventory and actual metric labels; expected site/app/capability inventory; query and visual conventions | Dated git/live evidence distinguished; label collision/federation fixtures; every proposed panel has a source, freshness bound, fallback and runbook owner. |
| 1 — platform safety | Remove unused Angular plugins; provision the Alertmanager overview; durable Prometheus; upgrade rehearsal; stable datasource UIDs; independent site 2 Grafana and auth access | Grafana DB backup and restore proven locally; preserve migration encryption/config secrets; test renderer token; reproduce classic and v2 provisioning, kiosk/auth and database migration against pinned images. Confirm retention time and size limits and host disk budget. |
| 2 — observation paths | Enable Cilium/mesh metrics on both sites after operator CRDs; Flux controller/KSM metrics with RBAC/network access; FRR/router/exporter health; external/client probes; early Thanos sidecar/query proof | Non-mutating evidence from both sites, provenance/dedup tests, complete versus partial query behavior, local fallback, bounded scrape cost. No dependency cycle that makes CNI bootstrap depend on monitoring. |
| 3 — first useful journey | Generator/helpers/CI; Observability, L1 Homelab, Sites & Failover, Databases & Backups; alert navigation | An alert reaches the relevant scoped panel in one link; L1 → cause evidence in at most two drill-downs; unavailable datasource, missing metric, stale exporter and same-name DB fixtures give truthful states. |
| 4 — household and domains | L0 kiosk; WAN/Wi-Fi/BGP/mesh, apps/ingress, hosts/storage, automation/cameras, non-CNPG state | Kiosk explains a fault without jargon or private identity. Network traces router → FRR → Cilium → endpoint and correlates Wi-Fi/site 2. Every domain has explicit unknown/standby behavior. |
| 5 — history and objectives | Durable structured events; S3/Thanos history; Pyrra SLOs; restore/RPO/RTO views; comparisons and capacity | Full SLO window exists or is clearly marked incomplete; backup/drill coverage checked per app; idle DB, no traffic and credential expiry cases tested; local alerts remain independent. |
| 6 — consolidation and operation | Vendor/replace runtime `gnetId` downloads, clean folder/provider ownership, test old links, bounded preview trials, review cadence | Each dashboard has exactly one provisioning owner; old UID links survive; retirement actually removes stale sidecar dashboards; per-site URLs and break-glass access documented; quarterly review and after-incident improvements assigned. |

The [Grafana 13 upgrade guide](https://grafana.com/docs/grafana/latest/upgrade-guide/upgrade-v13/upgrade-v13.0/) requires careful database migration/restore and renderer authentication handling. It also calls out the withdrawn 13.0.0 Git Sync migration bug: do not use that release as an intermediate step. Back up before changing schema; a Helm image rollback alone does not restore the old DB state.

Resilience must cover Grafana, its DB, auth, DNS, ingress and datasource routes together. Provide a fixed URL for each site in addition to any shared VIP; a browser reconnect through a VIP must not depend on the failed site's DB/session. Site 2 also needs local logs/events or an explicit “site 1 logs unavailable” state: its current overlay does not deploy Loki/Tempo/Vector. Both sites still need off-site heartbeat monitoring for total power/WAN loss.

## Verification and maintenance contract

- Pin the [Foundation SDK](https://github.com/grafana/grafana-foundation-sdk), schema, chart, images, plugins and validation tools. Commit deterministic generated resources, upstream revision/license metadata, and the regeneration command. Do not fork chart-generated mixins just to vendor them: their pinned chart is already reproducible; keep targeted overrides small.
- [v2 dashboards require resource-format file provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/). Test the full chart → ConfigMap → sidecar → provider → Grafana path, including environment-variable substitution, ConfigMap's size limit, folder moves, restarts and deletion. The present sidecar uses `disableDelete: true`; removing a source file alone cannot be assumed to retire its dashboard. Plan declarative provider cleanup and verify it locally.
- Use [dashboard-linter](https://github.com/grafana/dashboard-linter) for its supported Prometheus checks; prove coverage of v2 queries, variables, Mixed panels and non-Prometheus panels. Add schema and project-specific checks where unsupported; a linter exit code is not proof that every panel was examined.
- Unit-test important recording rules and representative instantiated panel expressions with `promtool test rules`: counter resets, no series, zero traffic, missing joins, duplicate/federated inputs, idle DBs and both sites' labels. Promtool does not understand literal Grafana macros; render representative `$__rate_interval`/variables first. For counters, [rate first, then aggregate](https://prometheus.io/docs/prometheus/latest/querying/functions/). Gauge aggregation order depends on the question; the original blanket “aggregate first, then over time” instruction is unsafe.
- Run a disposable local Grafana with the pinned providers and fixture data. Browser-check import success, panel errors, small screens, links, permissions, preview-off fallbacks, partial/error states and no-data legends. Set an initial L1 target of ≤5s warm load and ≤20 visible panels, then measure and adjust; limit repeat fan-out, series and returned points. Server dry-runs validate manifests, not actual PromQL results or dashboard UX.
- Follow [Flux's monitoring example](https://github.com/fluxcd/flux2-monitoring-example), including custom-resource KSM configuration, RBAC and PodMonitors. Add monitoring ingress to enforced Flux network policies where needed. Observe readiness, suspension, revision and reconcile failures, not just process uptime.
- Treat kiosk privacy as an access design: in OSS a Viewer can query the organization's datasources, not just the visible dashboard. For a restricted L0, use a separate organization with a datasource backend that exposes only approved aggregate metrics (or a separate instance); folders/hiding panels do not filter data. Backend restrictions must cover the entire exposed API. Review JWT URL leakage/expiry, sanitized text and access paths; keep household identities out of public code, legends and screenshots. [Grafana datasource access model](https://grafana.com/docs/grafana/latest/administration/data-source-management/).
- Keep Git Sync and mutating panel actions deferred. A preview investigation tool is optional; a useful incident workflow remains in the dashboards and git runbooks. Quarterly and after incidents, review unused dashboards, slow queries, unknown-state incidents, stale thresholds, metric cardinality and whether each tile leads to an action.

Acceptance scenarios use fixtures/local components before merge: site 1 lost; site 2 lost; inter-site partition; total WAN/auth failure; primary-versus-lease disagreement; empty or stale exporter; missing expected backup; idle database; failed restore; no traffic; and a schema/provider migration. Later production drills measure actual recovery with the owner's scheduling approval. A dashboard project is complete when it answers those cases accurately and quickly, not simply when every catalogue title exists.

## Resource and local-storage amendment, 2026-11-06

Subsequent read-only measurements and the owner's storage preference refine this review: keep 15d local Prometheus on both sites and provide 35d raw history through **Garage S3 hosted on WSL-02 only**, with no AWS destination for this archive. This does not provide an independently replicated archive for site 2.

Budget a dedicated 64 GiB ext4 VHDX on site 1's C: for new monitoring storage (32 GiB Prometheus PVC), and on site 2 budget 24 GiB Prometheus, 256 GiB Garage data, 8 GiB metadata, 64 GiB compactor scratch and small UI/cache/event volumes. Site 1 E: had about 42 GiB physically free; site 2 C: about 1.28 TiB. Initial extra RAM headroom targets are about 3 GiB / 7 GiB. These are measured-baseline-informed forecasts and capacities, not allocations or live changes.

If site 2 is down, site 1 retains current/local 15d monitoring but cannot read archive history; the interface must show that limitation. Permanent loss of site 2's only data disk loses its single-copy archive. Do not redirect existing application backups as part of this change. Verify long-window SLO queries use complete Thanos history rather than truncated 28d recordings evaluated against 15d local data.

See [the resource budget](grafana-resource-budget.md) and [the local S3 research](grafana-object-storage.md) for calculations, community evidence, alternatives and the integration/durability checks required before deploying the candidate. This amendment takes precedence over earlier assumptions about a remote S3 service or 35d retained locally on every site.
