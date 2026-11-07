# Observability as code

This directory owns the dashboard definitions, evidence exporter, query isolation
boundary and offline acceptance tests. Cluster activation is tracked in
[`observability-implementation.md`](../docs/operations/observability-implementation.md).
The initial rollout selects evidence collectors and the shared PKI on both WSL sites, with Garage only on WSL-02. Dashboard and Thanos activation follows their separate migration checks.

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
workloads. Query keeps the expected endpoints even during failed health checks
and refuses partial responses. Direct local Prometheus remains the independent
fallback. Store has explicit cache, request and concurrency limits. Compaction
uses one worker, 35d raw retention and no downsampling. Garage is pinned to an
explicit archive-node hostname and uses 256Gi data / 8Gi metadata claims. Its S3
listener is pod-local; a TLS proxy provides the cross-site endpoint.

```bash
uv sync --project observability --locked --python 3.13
uv run --project observability python observability/generate.py --check
uv run --project observability ruff check observability
uv run --project observability pytest observability/tests -q
uv run --project observability python observability/validate.py
uv run --project observability python observability/rehearse_archive.py
uv run --project observability python observability/rehearse_grafana.py --browser
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
formats, resizes or moves a disk. The 64Gi C: volume's creation, persistent mount,
Talos bind and permissions remain a separately validated migration.

The Grafana 13.3 browser gate is `tests/browser_check.py`. It must pass against a
disposable upgraded Grafana before production activation; API import alone does
not validate tab navigation, panel rendering, responsive layout or DB migration.
