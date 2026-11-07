# Local S3 for observability: community review and WSL-02 design

Reviewed 2026-11-06. Scope: replace the previously unspecified/cloud S3 destination for the **new observability archive** with self-hosted storage on `homelab-wsl-02`. No AWS service is required for this archive. This does not migrate existing database/PVC backups or claim that a single-host object store is an independent backup.

## Recommendation

Use **Garage on WSL-02 as the first candidate**, deployed through the repository's existing bjw-s app-template/Flux pattern, with one server and one data copy. It fits the bounded metrics archive and has concrete Home Operations examples. Treat the single-node design as an explicit availability/durability compromise, not an HA S3 service. Gate adoption on an isolated pinned-version Thanos integration and crash/restart test; RustFS is the fallback if Garage's API/durability behavior fails those requirements.

Keep Prometheus persistent on both sites for 15d. Upload to a dedicated Garage `thanos` bucket and retain at least 35d raw history there. Keep Query and Store Gateway on each site, a direct-local Prometheus fallback, and one compactor on WSL-02. The service, DNS and dashboard must identify the archive as hosted on site 2; a shared ingress VIP cannot make the data exist on site 1.

## What the community actually uses

These are inspected public repository configurations, not verification of those operators' running clusters or a popularity census. Search indexes and kubesearch are useful discovery tools, but can lag current git. In this review an older cached onedr0p manifest said `cephObjectStores: []`; fetching the exact current git revision showed the object store below. Use pinned source before copying settings.

| Source | Current evidence | What applies here |
| --- | --- | --- |
| [onedr0p/home-ops](https://github.com/onedr0p/home-ops/blob/bbacc080ee4fcbd4c75a58f0828212f2612ab217/kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml) | At revision `bbacc080`, Ceph RGW with two gateways, 1 GiB memory request per gateway, three-copy metadata/data pools, and a 100 GiB data-pool quota | A valid pattern when Ceph and independent storage hosts already exist. Do not bring up that storage stack solely for one WSL host's metrics archive. |
| [drag0n141/home-ops](https://github.com/drag0n141/home-ops/blob/master/kubernetes/apps/storage/garage/app/helmrelease.yaml) | Garage v2.5.0 via app-template, persistent data/meta paths, internal S3 route, non-root security context; configured 100 MiB request / 512 MiB memory limit | Closest simple pattern to the existing repository. Those are configured budgets, not observed RAM measurements. |
| [alexwaibel/home-ops](https://github.com/alexwaibel/home-ops/blob/main/kubernetes/apps/storage/garage/app/helmrelease.yaml) | Garage v2.4.1 via app-template, replication factor 1, metadata snapshots, 512 MiB memory limit | Confirms a single-server community pattern. Its NFS persistence/LMDB choices are not recommendations to copy for this host. |
| [Pumba98/flux2-gitops](https://github.com/Pumba98/flux2-gitops/blob/main/apps/garage/hr-garage.yaml) | Garage v2.5.0 StatefulSet through app-template, `--single-node`, separate metadata/store claims, 512 MiB request / 2 GiB memory limit | Another single-node example; resource settings vary by workload. |
| [rcdailey/home-ops](https://github.com/rcdailey/home-ops/blob/d71373b6a45d191166ae9262a14322c0167012db/kubernetes/apps/storage/garage-instance/garages3instance.yaml) | A GarageS3Instance references an external Garage endpoint. Its operator configuration manages S3 resources | Distinguish management of buckets/keys from deploying the storage server. Evaluate the [operator](https://github.com/rcdailey/garage-s3-operator) for declarative lifecycle, not as proof of server HA. Pin it if selected; do not copy the example's floating OCI `latest`. |
| [bluevulpine/flux-talos](https://github.com/bluevulpine/flux-talos) | README describes external Garage on TrueNAS for Thanos and other cluster workloads | Evidence of Garage used with Thanos, and an example of separating the object store from the consuming Kubernetes cluster. |

No private Discord conversations were accessed. The recommendation rests on public manifests and upstream behavior, not a claimed community consensus.

## Alternatives

| Option | Assessment for this first deployment |
| --- | --- |
| Garage | Preferred candidate: small S3-focused service and familiar GitOps patterns. Core object and multipart operations are supported, but AWS IAM policy semantics, versioning and object lock are not equivalent to AWS S3. |
| RustFS | Credible alternative, especially if broader S3 features are needed. GitHub's release API reports stable **1.0.4**, published 2026-10-25; the old “pre-GA” criticism is no longer current. Its single-disk topology also requires migration to a new deployment to expand into a distributed topology. [Upstream project](https://github.com/rustfs/rustfs). |
| SeaweedFS | Viable alternative. Current upstream provides **`weed mini` as a single-process S3 server**; it is inaccurate to say it always requires deploying four separate services. Its broader storage roles are more than this archive currently needs. [Upstream project](https://github.com/seaweedfs/seaweedfs). |
| Rook/Ceph RGW | Fits the existing MS-01 design and onedr0p's actual hardware. Disproportionate for a single Windows disk and nested Talos workers sharing one host; additional pods do not create independent disk/host failure domains. |
| MinIO Community | Do not select for a new deployment: the [upstream repository is archived](https://github.com/minio/minio). This was verified against GitHub's current API (`archived: true`), not just articles about licensing. |

Garage's [S3 compatibility matrix](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/) covers the relevant object/list/multipart APIs. Its authorization is per-key/per-bucket rather than AWS IAM policy evaluation. Thanos integration still needs testing with the selected releases; a compatibility matrix does not certify every client's behavior.

## Initial topology and disk budget

| Item on WSL-02 | Initial capacity budget |
| --- | ---: |
| Garage object data, native local ext4 | **256 GiB** |
| Garage metadata and bounded metadata snapshots | **8 GiB** |
| Thanos compactor scratch, separate directory/claim | **64 GiB** |
| Prometheus, 15d local | **24 GiB** |
| Store Gateway cache | **4 GiB** |
| Grafana local DB/plugins | **4 GiB** |
| Bounded local event/log path | **8 GiB** |
| Total nominal capacities | **368 GiB** |

This moves the previous remote S3 budget onto WSL-02; it does not add another 256 GiB copy of the same archive elsewhere. The estimated 35d logical object population is still roughly 60–80 GiB under the projected ingestion scenario, with 100–150 GiB of operational object-store space budgeted before choosing the larger 256 GiB data capacity. Garage metadata, tombstones/garbage collection, incomplete uploads and compaction overlap need measuring. Do not assume another compression multiplier on already-compressed TSDB chunks.

The measured site 2 C: free space was about 1.28 TiB. Even fully occupying these nominal capacities would leave roughly 939 GiB of that snapshot's free space before other growth. Local-path PVC sizes generally do not preallocate/enforce a hard quota; monitor actual Linux filesystem usage **and** Windows physical free space. Keep metadata and objects local to ext4; separate claims on the same host are organizational boundaries, not disk redundancy.

One Garage process with `replication_factor = 1` does not incur a 3× replica multiplier. Three copies spread over Talos containers on the same Windows SSD would consume capacity without surviving loss of that host/disk. Later host-independent replication is a new design/migration: changing Garage's replication factor is not a supported rolling switch. [Garage configuration reference](https://garagehq.deuxfleurs.fr/documentation/reference-manual/configuration/).

## Incremental CPU and memory

These are **initial engineering budgets**, not measured Garage usage on this host. The community examples establish plausible configuration ranges, not runtime performance.

- Garage: start with a **100m CPU / 256 MiB RAM request**, **1 GiB memory limit**, and target approximately **200–500 MiB ordinary working set**. Expect about **0.05–0.2 CPU core** under the intended light traffic; allow roughly one busy core during uploads, reads, scrub or cleanup. Measure actual working set, page-cache pressure and latency before lowering the limit.
- Optional bucket/key operator: allow roughly **50–150 MiB** if adopted, and verify its RBAC, credential rotation, deletion/retention and licensing/maintenance behavior before adding it. A git-managed idempotent bootstrap Job is also possible for the single bucket; no hand-created live buckets/keys.
- Increase the previous site 2 additional-RAM headroom target from **6 GiB to about 7 GiB** for the full observability project including local object storage. Ordinary total added working set becomes approximately **2–4 GiB**, depending on queries and the event path; compaction/query overlaps remain the peak case.
- Keep compaction concurrency 1. Garage, Prometheus and compactor share site 2's physical disk; independent pods do not remove I/O contention.

Local storage removes the archive's AWS/object-service bill and WAN dependency. Site 1 uploads and historical reads now traverse the site's LAN/Wi-Fi/tunnel path to WSL-02. The projected site 1 upload volume averages only around 0.1 Mbit/s, but historical reads and recovery produce bursts. Compactor-to-Garage traffic stays local to site 2, although both compete for disk I/O. No WAN egress/storage-price assumptions from the prior remote-S3 estimate apply to this local bucket.

## Durability, availability and retention contract

Garage's [single-node quick start explicitly warns against production use because it lacks redundancy](https://garagehq.deuxfleurs.fr/documentation/quick-start/). For this plan, accepting that compromise is reasonable only because the archive contains operational metrics with independent local Prometheus buffers; it is not the only copy of critical application data. No object-store software can turn this one host/SSD into independent storage.

- If site 1 fails, the surviving site 2 UI can use the archive, subject to the latest unuploaded head being unavailable.
- If site 2 fails or is partitioned, site 1 keeps current/local 15d monitoring and alerting; older archive reads and site 2's current data are unavailable. Show **Archive unavailable / site 2**, not a healthy empty result. Site 1 can upload its backlog after recovery only while those blocks remain locally retained.
- If site 2's disk is permanently lost, the single-copy archive is lost. Site 1 retains whatever is still in its own local Prometheus; losing the bucket is not automatically recoverable from configuration/git.
- Database/PVC backup destinations are outside this change. Moving app backups onto the same host as the primary application needs a separate recovery design; do not silently repoint them to this archive.

For a single copy, explicitly test SQLite metadata, `metadata_fsync = true`, `data_fsync = true`, persistent metadata snapshots and process/host restart recovery. The documented defaults do not imply synchronous single-copy durability; SQLite's documented synchronous setting is NORMAL when metadata fsync is enabled, so do not promise zero acknowledged-write loss on power failure. Keep `consistency_mode = "consistent"`; test the pinned version's actual settings/semantics. Preserve node identity, metadata, object data and configuration together in recovery procedures.

Use Thanos compactor retention (35d raw initially), not a generic S3 expiration rule that can remove component files of live blocks. Disable long-term downsampling initially unless its additional retention/storage is budgeted. Configure cleanup for incomplete uploads separately and test deletion/garbage-collection timing.

## Integration and GitOps requirements before deployment

1. Pin the Garage release/digest and Thanos release; build a disposable local instance with the intended filesystem/security context. No feature-branch manifests are hand-applied to a live cluster.
2. Verify signed requests, exact configured region, path-style access, TLS trust, timeouts and cross-site network policy. Internal-only S3 endpoint, site 2 origin; admin/RPC/metrics ports are separate from public S3 access. No browser OAuth interstitial in the S3 API path.
3. Reconcile layout/bucket/access keys through a git-owned idempotent Job or pinned controller. Prove reruns/restarts preserve keys/data and that removing/recreating a manifest cannot unintentionally erase a bucket. Keep secrets in SOPS or generated Kubernetes Secrets; never put an admin token in Grafana.
4. Give Store Gateways read-only keys. Give uploaders and compactor separately scoped bucket credentials; Garage's bucket-level write permission is not AWS prefix-level delete isolation. Start with a dedicated metrics bucket, not shared application-backup prefixes.
5. Exercise actual Sidecar upload, multipart/abort, read/list/pagination, fresh/cold Store Gateway queries, compactor rewrite/delete, region errors, process restart and loss of the site 2 endpoint. An HTTP health response alone is insufficient.
6. Test a full compactor cycle, object cleanup, metadata snapshots and disk/RAM/CPU peaks; record actual maximum scratch and object bytes. Retain direct-local fallback while validating strict/partial history coverage.
7. Monitor filesystem/Windows disk headroom, Garage capacity/metadata, S3 request errors/latency, unuploaded-block age, compactor failures/retention lag and synthetic bucket read/write. Keep alert evaluation off the archive's dependency chain.

The first deliverable is a proven local metrics archive with an honest outage mode. Host-independent replication or a second independent backup destination can be added later when capacity exists; this research does not assume or require AWS for either.
