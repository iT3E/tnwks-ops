# Observability as code

This directory owns the dashboard definitions, evidence exporter, query isolation
boundary and offline acceptance tests. Cluster activation is tracked in
[`observability-implementation.md`](../docs/operations/observability-implementation.md).
Both WSL sites select the evidence collectors, shared PKI, recording rules and Thanos Query; Garage, Thanos Store and the Compactor run only on WSL-02. Site 1 serves the dashboards.

`dashboards.py` uses the pinned official Grafana Foundation SDK to generate stable
v2 resources: 12 operator dashboards and one household dashboard. `inventory.py`
reads the existing failover catalogue and renders each cluster's Flux graph to
find its actual expected CNPG clusters. Missing upstream data is Unknown; the
collector cannot infer fencing or restore proof from workload readiness.

`collector.py` runs on Python's standard library. Its service account has no
Secret access or mutation verbs. Raw API responses, ConfigMap values and private
identifiers are not logged. `kiosk_proxy.py` allows six exact instant queries,
strips upstream labels, and rejects general PromQL, range and metadata endpoints.
It is a backend boundary; operator Grafana folder permissions alone cannot provide
this isolation. Its dedicated household Grafana and identity cutover are still
required before the kiosk can use it.

`thanos_manifests.py` generates bounded Query, Store and singleton Compactor
workloads. Query runs per site and keeps the expected endpoints even during
failed health checks. It answers partially by default, with a warning Grafana
shows: losing a site must not error every panel, and the evidence rules turn the
gap into Unknown. `partial_response=false` still fails closed, and direct
per-site Prometheus remains the independent fallback. Store and Compactor sit
beside the archive on WSL-02, because a Store on site 1 could not read a Garage
that is down either. Store has explicit cache, request and concurrency limits.
Compaction uses one worker, 35d raw retention and no downsampling. Garage is
pinned to an explicit archive-node hostname and uses 256Gi data / 8Gi metadata
claims. Its S3 listener is pod-local; a TLS proxy provides the cross-site endpoint.

Every series carries `k8s_cluster`, the site it describes: a default Prometheus
scrape class sets it on monitors and probes, and `additionalScrapeConfigs` set it
per target, so a Windows host keeps its own site whichever Prometheus scrapes it.
Do not make it an external label: the Thanos sidecar lets external labels
override series labels, which would rewrite those per-target values. Rules that
aggregate must keep it in their `by` clause or their dashboards go blank.

```bash
uv sync --project observability --locked --python 3.13
uv run --project observability python observability/generate.py --check
uv run --project observability ruff check observability
uv run --project observability pytest observability/tests -q
uv run --project observability python observability/validate.py
uv run --project observability python observability/rehearse_archive.py
uv run --project observability python observability/rehearse_grafana.py --browser --renderer
```

Generation/validation needs `kustomize` or `kubectl kustomize`; neither contacts a
cluster. Archive rehearsal needs Docker and OpenSSL. It uses loopback ports
13103–13113, synthetic samples, temporary credentials and named disposable
containers. It removes only its own containers and temporary data, including on
failure. Registry pulls may populate the Docker image cache. The rehearsal checks
S3 operations, verified HTTPS, mutual TLS StoreAPI, historical queries, normal
restart, SIGKILL recovery, cache rebuild and unavailable-store behavior. A process
kill does not simulate a Windows power loss or prove SSD flush durability.

To change canonical definitions, run `python observability/generate.py` inside the
environment, then review and commit generated files together with the source.
Never hand-edit generated dashboard JSON, copied exporter code, rule fixtures or
Thanos HelmReleases. Image updates must update `versions.py` and regenerated
resources; do not merge a generated-only dependency bump that fails this check.

`storage-preflight.ps1 -Site wsl` is read-only and reports physical Windows drive
headroom, memory and the backing drive of registered WSL images. It never attaches,
formats, resizes or moves a disk. The site 1 Prometheus volume is a separate 20Gi
VHDX on C:, owned by the `windows_observability_storage` role.

The Grafana 13.3 browser gate is `tests/browser_check.py`. It must pass against a
disposable upgraded Grafana before production activation; API import alone does
not validate tab navigation, panel rendering, responsive layout or DB migration.

`rehearse_migration.py` additionally exercises a private encrypted database dump
and provisioning archive: baseline, Grafana 13 migration, pod recreation, and
restoration of the original backup to Grafana 12. Supply `--database-dump`,
`--provisioning-archive` and `--identity`; never commit the fixtures. It uses an
internal Docker network, tmpfs data/configuration and no active alert execution.
Failure logs are encrypted under `.cache/private/`. The ordinary CI rehearsal
uses synthetic metrics and the six checksum-verified upstream exports.
