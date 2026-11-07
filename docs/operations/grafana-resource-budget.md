# Grafana redesign: measured baseline and resource budget

Measured 2026-11-06 using read-only Kubernetes/Prometheus, Docker mount inspection, Windows storage/memory queries, and host disk usage. No mounts, retention settings, workloads or storage were changed. New-component figures below are engineering estimates, not measurements of software that has not been deployed.

## Recommendation

Keep the proposed dashboards and resilience work. Use 15d local Prometheus retention plus at least 35d raw history in self-hosted Garage S3 on WSL-02; no AWS destination for this archive. See the companion local-S3 research for the single-host compromise and adoption gates. Give site 1 a dedicated 64 GiB ext4 VHDX on C: for monitoring, with a 32 GiB Prometheus PVC. Give site 2 a 24 GiB Prometheus PVC and host the single Thanos compactor there with a 64 GiB scratch budget. Put Query and a bounded Store Gateway on each site, retaining direct local Prometheus access.

Reserve roughly 3 GiB additional RAM on site 1 and 7 GiB on site 2 for the full project, including query/compaction bursts; ordinary working sets should be lower. Start with the light query-only path and measure before enabling object-store history and compaction. These reservations are headroom targets, not a recommendation to raise every pod request/limit by that amount.

## Measured storage and compute

| Measurement | Site 1 | Site 2 |
| --- | --- | --- |
| Windows physical RAM | 63.84 GiB | 63.77 GiB |
| Windows available RAM, snapshot | about 19.9 GiB | about 21.6 GiB |
| Current Prometheus head series | about 153,000 | about 92,500 |
| Ingestion, available 6h-window samples | about 3,045 samples/s | about 1,494 samples/s |
| Prometheus working set, snapshot | about 781 MiB | about 477 MiB |
| Prometheus CPU | 26m snapshot; about 120m p95 over available history | about 11m, 15-minute rate |
| Compressed block production | about 0.60 GiB/day, extrapolated from a full 2h block | about 0.50 GiB/day, extrapolated from 2.89h of full/partial blocks |
| Recent TSDB history available | about 5.5h | about 5.7h |
| Free Windows storage | C: about 203 GiB; E: about 42 GiB | C: about 1.28 TiB |

The Grafana process plus its two provisioning sidecars currently uses about 330 MiB on site 1; its separate renderer is another roughly 20 MiB while idle. Rendering screenshots is a different, burstier workload.

Site 1's WSL distro is registered under E:; the VHDX file length is about 343 GiB. Linux reports roughly 632 GiB available inside its virtual filesystem, but E: has only about 42 GiB physically free. A large virtual disk does not create physical capacity. [Microsoft explains the virtual/physical distinction](https://learn.microsoft.com/en-us/windows/wsl/disk-space).

C: and E: are separate physical SSDs. A C:-backed monitoring disk therefore distributes disk I/O as well as capacity. Moving the entire current distro onto C: is not feasible with only 203 GiB free; a separate volume is the appropriate scope.

Site 1's WSL configuration is 12 processors / 40 GiB RAM. Its three Talos nodes report the shared kernel's memory and CPU capacity, not three independent physical machines. Do not add their allocatable memory or CPU to infer host capacity. Likewise, do not add Windows available RAM to WSL MemAvailable: those are views of overlapping physical resources.

The short sample followed recent Prometheus restarts and loss of emptyDir history. It cannot establish a normal full-day/week p95, growth trend, or future Thanos peak. Site 2's partial-block extrapolation is less precise than site 1's complete block. Validate again after 48–72h of uninterrupted persistent collection and after each new telemetry family.

## Prometheus disk model

Site 1's complete 2h block occupies 53,661,696 bytes and contains 23,714,146 samples: about 2.26 bytes/sample including block overhead, or 0.60 GiB/day. Site 2 currently has 65,083,829 bytes of blocks covering about 2.89h, or 0.50 GiB/day. This uses actual blocks instead of relying solely on the generic bytes/sample heuristic.

Forecasts below use 1.5–2 times current block production for new telemetry, plus a provisional 2 GiB for WAL/head and operating data per site. The multiplier is a capacity scenario, not a measured growth rate. Filesystem/compaction safety space is additional and is covered by the larger PVC targets.

| Local retention | Site 1 projected data use | Site 2 projected data use | Initial PVC capacities, site 1 / site 2 |
| --- | --- | --- | --- |
| 7d | 8–11 GiB | 7–9 GiB | 16 / 16 GiB |
| 15d — recommended | 16–20 GiB | 13–18 GiB | 32 / 24 GiB |
| 35d, fully local alternative | 34–44 GiB | 28–38 GiB | 64 / 48 GiB |

Keep the retention-size cap below the disk budget, initially around 24 GiB on a 32 GiB PVC and 18 GiB on a 24 GiB PVC, then verify that both time and size retention actually deliver 15d. The current 6GB limit must change. [Prometheus documents head/WAL space, the first-triggering retention policy, and recommended disk safety margin](https://prometheus.io/docs/prometheus/latest/storage/).

Longer retention increases disk and historical query work; it does not multiply the live-head RAM requirement by the number of retained days. Active series, churn, scrape frequency, joins/histograms, recording rules and query concurrency drive memory/CPU. Budget both series and queries, not just disk.

The 35d goal is **queryable history**, not a requirement to retain the same 35d locally and in S3. With 15d locally, each site still has two weeks of diagnostics if Garage/site 2 or the inter-site path fails; queries for older history must show that limitation explicitly. The local Garage archive preserves site 1's uploaded data if site 1 fails. If site 2 fails, that archive is unavailable; permanent loss of its single disk loses the archive. This is not host-independent storage for site 2. Usually the newest unuploaded head is still local-only.

Long-window SLO queries must actually use that history. In particular, a generated 28d recording rule evaluated by a local Prometheus with 15d retention cannot produce a complete 28d result merely because its datasource has a Thanos endpoint elsewhere. Keep short-window burn alerts local; compute long-window compliance from complete Thanos-visible underlying SLIs and test the Pyrra UI/rule path. Do not present a truncated local 28d recording as authoritative. If the selected workflow requires all long-window recordings locally, use the fully local 35d option or separately budget a history-aware evaluator instead.

## Added runtime cost: estimates above current usage

Assumptions: bounded Cilium/Hubble labels, roughly no more than 50,000 additional active series per site at first, one-minute overview refresh, at most about 20 visible L1 panels, few interactive users, no duplicate renderer on site 2, and no fleet-wide high-cardinality HTTP/client-label capture. New logs are bounded infrastructure/events rather than all application/debug logs.

| Addition | Expected ordinary added RAM | Expected ordinary CPU | Burst / sizing note |
| --- | --- | --- | --- |
| More telemetry, collectors, KSM and Prometheus rules | 350–900 MiB per site | 0.05–0.20 core per site | Depends most on active series, churn and histogram labels. Measure and gate each family. |
| Thanos Sidecar | 50–150 MiB per site | 0.005–0.02 core per site | No separate full TSDB copy; uploads and StoreAPI add I/O. |
| Thanos Query | 100–300 MiB per site | 0.02–0.10 core per site | Allow 0.5–1 GiB for normal heavier queries; broad 35d queries may need more or must be rejected/bounded. |
| Thanos Store Gateway | 200–500 MiB per site | 0.01–0.08 core per site | Initially allow 1–2 GiB including caches/mmap/query buffers; validate with real history. |
| Pyrra API/controller and small SLO set | 100–200 MiB per site | 0.005–0.03 core per site | Generated recording-rule cost is included in telemetry/Prometheus above. |
| Second Grafana, sidecars, SQLite; no renderer | 350–600 MiB on site 2 | 0.02–0.10 core | Allow around 1 GiB aggregate for ordinary bursts, then measure upgraded Grafana. |
| Local event/log path on site 2 | 200–500 MiB on site 2 | 0.01–0.05 core | Assumes low-volume infrastructure/event logs. Arbitrary full log ingestion is not included. |
| Garage object store on site 2 | 200–500 MiB ordinary; initially allow 1 GiB | 0.05–0.20 core | Single copy, native ext4, S3 API and metadata durability checks required; optional bucket/key controller another 50–150 MiB. |
| One compactor on site 2 | 100–300 MiB idle; roughly 0.5–2 GiB while working | near-idle between work; about 1 core while compacting | Reserve up to 4 GiB initially; concurrency 1. This is the least certain component until actual historical blocks are tested. |

Allow another 0–100 MiB on the existing Grafana for the new catalogue under the assumed load; a version upgrade's exact memory change is not predictable from documentation. Generated JSON, navigation, folders and accessible value mappings have negligible persistent resource cost relative to queries. A separate restricted kiosk organization need not mean another process.

| Full-project incremental envelope, including local Garage | Site 1 | Site 2 |
| --- | --- | --- |
| Ordinary added working set | about 1–2 GiB | about 2–4 GiB |
| Busy-query/compaction headroom | about 2–3.5 GiB | about 4.5–7 GiB |
| Ordinary additional CPU | about 0.1–0.4 core | about 0.2–0.8 core |
| Short busy periods | about 1–2 cores | about 1–4 cores |

These ranges overlap and are not hard upper bounds. Start with about 3 GiB extra physical-RAM headroom on site 1 and 7 GiB on site 2, confirm no sustained memory PSI/swap/refault pressure, then tune actual requests/limits. Do not lower current Prometheus limits based only on the short quiet sample. Windows gaming, inference and other desktop workloads share the host budget.

**Query-only Thanos is much smaller:** sidecar plus Query is roughly 150–450 MiB per site under the assumed load and needs no history/cache PVC. Object-store history adds Store Gateway, one compactor, a Garage server on site 2 and their disk budgets. Neither a query frontend nor a Thanos Ruler, Redis, memcached, bucket web UI or local MinIO is required for this scale; local Prometheus remains the rule/alert owner.

## Where disk goes with Thanos history

| Component | Site 1 | Site 2 | Meaning |
| --- | --- | --- | --- |
| Prometheus, 15d | 32 GiB PVC | 24 GiB PVC | Working local metrics and WAL/head |
| Store Gateway | 4 GiB initial cache budget | 4 GiB initial cache budget | Index headers/cache, not another full history copy; validate growth |
| Grafana | Existing 10 GiB PVC | 4 GiB new budget | Plugins, local SQLite on site 2, preferences; not metrics storage |
| Additional bounded events/logs | Existing stack; measure growth | 8 GiB initial disk budget | Provisional until event/log ingestion is measured |
| Compactor scratch | None | 64 GiB capacity budget | One job at concurrency 1, shared bucket, not one per site |
| Garage object data, 35d raw history | None | 256 GiB capacity | Shared metrics bucket on site 2; about 60–80 GiB projected logical blocks, 100–150 GiB operational object-store budget before the larger capacity margin |
| Garage metadata and bounded snapshots | None | 8 GiB capacity | Same host/disk failure domain; no replica multiplier or independent backup |

The recommended site 2 budgets now total about 368 GiB of nominal capacity (24+4+4+8+64+256+8), including the self-hosted object store. This adds the previously remote S3 capacity to the host; it does not assume another remote archive copy. They are **not all immediately occupied disk bytes**. Local-path PVC requests generally do not preallocate or enforce those amounts. A VHDX capacity also differs from current physical file allocation. Add filesystem/physical disk alerts and actual retained-byte measurements.

A compactor may need source blocks plus output covering its maximum compaction range. With a 14d range and site 1 producing up to about 1.2 GiB/day, `2 × 14 × 1.2 ≈ 34 GiB` precedes safety space; 64 GiB on the roomy host is prudent. The existing MS-01 chart's concurrency 4 and 15 GiB scratch configuration is unsuitable to copy as-is. [Thanos describes scratch usage and how concurrency multiplies it](https://thanos.io/tip/components/compact.md/). One compactor outage delays compaction/retention; it need not take down the live query paths.

Set and test Store Gateway cache/chunk limits explicitly rather than inherit server-sized defaults. Its documented defaults include a 250 MB index cache and a 2 GB chunk pool ceiling; that is a ceiling, not proof that it allocates all 2 GB at startup. [Store Gateway configuration](https://thanos.io/tip/components/store.md/).

With uploads enabled, follow the pinned [Sidecar block-compaction settings](https://thanos.io/tip/components/sidecar.md/). A query-only sidecar and an uploading sidecar need different local-compaction handling. Do not accidentally lose old data by switching storage or claiming an empty PVC has the old history.

The S3 estimate excludes long-term downsample retention, unrelated backup buckets and unrestricted logs/traces. Garage lacks S3 object versioning/lock semantics; the metrics archive does not require them. Site 1 uploads average roughly 0.1 Mbit/s across the local site path, with larger historical-read/recovery bursts. Site 2's own uploads and compactor-to-Garage traffic are local, but compete for physical disk I/O. This archive requires no AWS object service or WAN transfer; measure LAN/Wi-Fi contention instead.

## Proposed mount design, not executed

1. Create a dedicated **64 GiB ext4 VHDX backed by C:**, mounted persistently into the WSL distro. At its maximum data allocation that would leave roughly 139 GiB of today's C: free, before other Windows growth and filesystem/VHD overhead. Keep a separate physical-space reserve for Windows updates and normal use.
2. Bind that Linux mount into the selected Talos worker(s) and add a dedicated `local-path-observability` storage class. Directories must be node-specific; scheduling/node affinity and mount propagation/permissions must be validated with the real pod UID/GID.
3. Make the worker startup depend on the mount. Refuse startup when absent so a normal empty directory does not silently place data back in the E:-backed root filesystem. Manage mount/startup/configuration through the existing Windows/WSL/Talos tooling and git workflow.
4. Give Prometheus 32 GiB there and bound Store Gateway/event usage. Do not put the compactor's scratch on site 1. Keep the remaining capacity as growth/migration room rather than fill it with other applications.
5. Test reboot/mount/permission behavior before changing production placement. Do not recreate a USB-bearing worker merely to experiment with a bind mount. A mount and storage migration is separate implementation/maintenance work.

Use native ext4 inside the VHDX, not a TSDB directly under `/mnt/c/...` through DrvFS, and do not use the NFS bulk class for Prometheus. [Prometheus requires suitable local filesystem semantics](https://prometheus.io/docs/prometheus/latest/storage/); [Microsoft documents VHD attachment to WSL](https://learn.microsoft.com/en-us/windows/wsl/wsl2-mount-disk).

The E: pressure exists independently of this project: Prometheus currently occupies only about 0.58 GiB on that host. Moving new monitoring writes to C: prevents adding the forecasted tens of GiB to E:, but does not reclaim the existing distro footprint or solve all current growth. Deleting files within a VHDX also need not immediately return equivalent physical space to Windows; any cleanup/compaction needs its own inventory and maintenance plan.

## Checkpoints before increasing the budget

- Measure 48–72h of persistent block bytes/day and active-series churn; include a busy weekday and a backup/restore window before tightening limits.
- Add Flux, Cilium, mesh, FRR and probes in bounded batches. Start with a 50k/site additional-series budget; no per-client/IP/URL Hubble labels by default.
- Test Query/Store Gateway with 7d and 35d bounded dashboards; record peak working set, query duration, CPU, disk I/O and response completeness.
- Test one full compactor cycle and scratch maximum before enabling longer retention/downsampling.
- Keep current and projected host/WSL memory/physical-disk headroom visible. Kubernetes allocatable and PVC nominal capacity are not sufficient physical capacity checks in this nested setup.

## Self-hosted archive amendment

Garage on WSL-02 replaces the unspecified remote S3 destination. See the local-S3 research for community manifests, alternatives, durability/API limits and the pinned-version integration gate. Its ordinary working-set estimate raises the full project headroom target for site 2 to about 7 GiB. This remains a resource forecast, not an applied storage or deployment change.
