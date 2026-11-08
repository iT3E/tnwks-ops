# Observability as code

This directory owns the dashboard definitions, evidence exporter, query isolation
boundary and offline acceptance tests. Cluster activation is tracked in
[`observability-implementation.md`](../docs/operations/observability-implementation.md).
Both WSL sites select the evidence collectors, shared PKI, recording rules and Thanos Query; Garage, Thanos Store and the Compactor run only on WSL-02. Site 1 serves the dashboards.

The `dashboards/` package uses the pinned official Grafana Foundation SDK to generate
stable v2 resources: 12 operator dashboards and one household dashboard. Each board
is one module under `dashboards/boards/` that declares its tabs and panels; `design.py`
holds the visual vocabulary (state words and colours, table headers), `queries.py`
the PromQL building blocks, `recipes.py` turns a panel into a visualization and
`layout.py` a board into a dashboard resource. Adding a board is one module plus its
entry in `boards/__init__.py`. `inventory.py`
reads the existing failover catalogue and renders each cluster's Flux graph to
find its actual expected CNPG clusters. Missing upstream data is Unknown; the
collector cannot infer fencing or restore proof from workload readiness.

The generated [panel evidence catalogue](../docs/operations/observability-panel-catalogue.md)
maps every operator and household panel to its source metrics, observation budget,
missing/error behavior, owner and runbook. `panel-contracts.json` is the machine
form. Project recording rules are expanded to their inputs; external mixins keep
their upstream ownership. A configured source does not prove a current observation.
Household aggregates and positive fence attestations remain explicitly planned.

Run a bounded read-only source audit through the existing Thanos Service proxy:

```bash
uv run --project observability python observability/audit_sources.py \
  --kubeconfig ~/.kube/config-homelab-wsl \
  --output observability/.cache/panel-source-audit.json
```

The report retains only known site identities and series counts, marks partial
queries, and never prints upstream response/error bodies. A present metric alone
does not verify an exporter refresh or a complete SLO window; inspect the relevant
collector and freshness evidence too. Current evidence has a 180s observation
budget; daily backup and weekly restore objectives are separate from scrape age.
Operator panels link to their runbooks. Diagnosis links retain incident time and
use Grafana's multi-value query-parameter format to preserve both selected sites.

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
