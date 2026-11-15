# trivy-operator

Trivy Operator checks each cluster that lists `ks.yaml`, with the same values
on each cluster. It is the source `trivy` of the security registry
(`security/README.md`). It checks:

- **The settings of each workload and each role**, in the operator's own
  process, with the checks that are in its image.
- **The arguments of the API server, the controller manager and the
  scheduler**, from their pods (the infra assessment).
- **The controls of the CIS Kubernetes Benchmark**, as the compliance report
  `k8s-cis-1.23`, every 6 hours. The registry reads one control as
  `k8s-cis-1.23/<control ID>`.
- **The images of each workload**, one scan job at a time as a rule, for the
  vulnerabilities that have a fixed version. A report lives 24 hours, then
  the image has a new scan. The registry reads the sum as
  `image-vulnerabilities`.

The release is Trivy Operator 0.36.1 (chart 0.38.1) with Trivy 0.76.2. The
images are pinned by digest in `app/helmrelease.yaml`. The digest of a tag
comes from the registry: `crane digest mirror.gcr.io/aquasec/trivy-operator:0.36.1`.
The image values keep the form `registry`, `repository` and `tag`, as other
HelmReleases of this repository do, so Renovate finds them.

## The parts

| File | What it is |
| --- | --- |
| `ks.yaml` | The Flux Kustomization `security-trivy-operator`. It waits for `kube-prometheus-stack`, for the CRD of the ServiceMonitor |
| `app/helmrelease.yaml` | The chart, from the HelmRepository `aquasecurity` (`kubernetes/flux/meta/helm/aquasecurity.yaml`) |
| `app/pvc.yaml` | `trivy-cache`, 4 GiB: the vulnerability databases for the scan jobs. The scan cache is in the memory of each scan |
| `app/networkpolicy.yaml` | What the operator and a scan job can reach |
| `app/rbac.yaml` | The Role that lets the operator create and delete Jobs, and read pod logs, in `security` only ("Its roles") |

The operator and its scan jobs run in the namespace `security`, on
`SECURITY_NODE` (`../README.md`, "Placement").

## The node collector is off

The node collector is a job on each node that reads the files of the kubelet
and of etcd from the host. It needs a host path, the host PID namespace and
root. The chart starts it in the namespace of the operator, and the
`baseline` level of `security` refuses such a pod. To run it, the operator
must move to a namespace at the `privileged` level. Then a fault in the
operator could start a privileged pod on each node. So the collector is off:
`nodeCollector.excludeNodes` names a label that each node has.

- **What stays:** each check of the workloads and the roles, the infra
  assessment of the control plane pods, the image scans, and each control of
  `k8s-cis-1.23` that reads the API server and not a node.
- **What is lost:** the controls of `k8s-cis-1.23` that read a node: 1.1.x
  (the files of the control plane), 4.1.x (the files of the kubelet) and
  4.2.x (the settings of the kubelet). Talos keeps these files on a read-only
  root, and its machine config sets the kubelet. The registry names ten of
  them. The rule of the result series leaves them out, so they read as "no
  evidence" and never as a pass (below).
- **A stricter step, later:** a CI check of the kubelet settings in the
  machine config of each cluster (`talos/`), or the node collector in a
  namespace of its own when the chart can put it there.

The infra assessment needs no node collector. It reads the pods of the
control plane, and `operator.infraAssessmentScannerEnabled` stays on.

## Its roles

The chart's ClusterRole gives create and delete on Jobs, and a read of pod
logs, in each namespace, and no value of the chart narrows it. The operator
needs them only in `security`: with
`vulnerabilityReports.scanJobsInSameNamespace` off (the chart's default),
it creates each scan job in its own namespace
(`pkg/vulnerabilityreport/builder.go`), deletes only the jobs that its
controller selects in that namespace (`pkg/vulnerabilityreport/controller/scanjob.go`),
and reads the log of the pod of such a job (`pkg/kube/logs.go`). So:

- **A post-render patch** in `app/helmrelease.yaml` removes create and
  delete from the Jobs rule of the ClusterRole, and removes its `pods/log`
  rule. Its test ops check the order of the chart's rules: if a chart
  release moves a rule, the upgrade fails, and no rule gets wider.
- **`app/rbac.yaml`** gives create and delete on Jobs, and get on
  `pods/log`, in `security` only.
- **What stays in the ClusterRole:** get, list and watch on Jobs, because
  the cache of the operator watches Jobs in each namespace, and the reads of
  the workloads and roles that the checks need. The operator reads no
  Secret outside `security`.

A fault in the operator can not create a Job in another namespace, such as
one at the `privileged` level, or read the log of a pod outside `security`.

A scan job runs as the user `nobody` (65534), with each capability dropped
and a read-only root. The exposed secret scan is off: no check of the
registry reads its reports, and each report keeps the place and a masked
match of a secret in the namespace of the workload.

## The result series

The rules are in `observability/rules.py` (group `homelab.security`), and
each site runs them for its own series. The rules of the registry run on
homelab-wsl-02 only, so the job `peer-federate` there brings the two series
of homelab-wsl (`kubernetes/clusters/wsl-02/apps/kustomization.yaml`).
homelab-wsl lists `trivy` in its `sources` (`security/registry/systems.yaml`)
from the pull request after its first result, so the late alert does not
fire before the first scans. From then, when homelab-wsl or that scrape is
down, its last sample leaves the 10-minute window of the rule, and
`SecurityResultLate` fires for `trivy` on homelab-wsl 10 minutes after that
(`for: 10m`), about 20 minutes after the last sample, next to the alert of
the lost site. That is the federation, not a fault of the operator. The rules give
the two series of the
contract of the registry (`security/README.md`, "The series"), with
`source="trivy"` and the cluster as `system`:

| Series | From | Value |
| --- | --- | --- |
| `homelab_security_check_result`, `check="k8s-cis-1.23/<control ID>"` | `trivy_compliance_info` of the report `k8s-cis-1.23` | The failed resources of a control that fails, and 0 for a control that passes |
| `homelab_security_check_result`, `check="image-vulnerabilities"` | `trivy_image_vulnerabilities` | The critical vulnerabilities with a fix, over the distinct images that run on the cluster now (`kube_pod_container_info` and `kube_pod_init_container_info`). Each running container counts one report: the one with the digest of `image_id`, or, when no report has that digest, the one with the same namespace, container and reference ("The digest of a report", below). The report of an image that no longer runs (an old ReplicaSet, whose report can stay for a day) counts only for a container of the same namespace, container and reference that has no report of its own digest. 0 when the scan reports have none. No series when no running image is known (kube-state-metrics down). A report counts for an hour after its last sample, so a report that is gone during its new scan changes nothing. No series when there is no scan report. While an image that runs on the cluster has had no report for an hour, a fail stays and a pass is "no evidence": a failed scan never reads as a pass, and never hides a known fail. The pods of the scan jobs, a container that has not started (no `image_id` yet) and an image with no registry digest are not counted |
| `homelab_security_last_result_timestamp_seconds` | `homelab_trivy_compliance_report_update_timestamp_seconds`, which kube-state-metrics reads from the status of the report | The time at which the operator wrote the report: at the first tick of its cron (`0 */6 * * *`, the chart's default) after the report object is made, so up to 6 hours after the first start of the operator, then at each tick (`pkg/compliance/clustercompliancereport.go` of the release) |

A result also has the label `severity`, in lower case. Trivy counts a control
with no input as a pass, so the rule leaves out the controls that have no
input on these clusters: 1.1.x, 4.1.x and 4.2.x (the node collector), and 2.x
(Talos runs etcd as a service of the OS, not as a pod).

The alert `SecurityFindingNew` fires when a result of high or critical
severity is above 0 and is above each value of the day before, or was 0 one
hour before, or has no value in the day before (`security/README.md`, "When
an alert fires").

A private image, or an image of a registry that the policy does not name,
has no report. The check `image-vulnerabilities` of its cluster then shows a
fail, or "no evidence" in place of a pass, and `SecurityImagesNotScanned`
fires after 36 hours, until the image has a report. For an image that no
scan can read, the owner mutes the alert with an entry of the accepted list
(`security/README.md`, "When an alert fires").

A rollout: the new image counts from its first report, and the old one
stops counting when its pods stop. Until the new image has a report, a pass
is "no evidence" for those minutes.

Known limits of the alert `SecurityFindingNew`:

- A 0 that lasts less than an hour between two fails alerts at most once:
  the term "was 0 one hour before" reads the value of exactly one hour
  before.
- A new critical vulnerability in an image that replaces another with the
  same count does not raise the sum, so it does not alert. A rollout to an
  image with more criticals alerts.
- During a rolling update, the old and the new image both run, and both
  count. An update that takes longer than the 10 minutes of the alert, with
  both images reported, alerts once.

### The digest of a report

The operator scans the image of the pod spec, by its reference, and the
report has the digest that the registry gave for that reference at the scan.
`image_id` has the digest that the node pulled. For most images they are the
same digest, but not always:

- **The tag moved.** The node pulled the tag before it was pushed again, and
  the scan reads the new image. The pod keeps the old digest until it starts
  again, and the operator scans the reference, so no report can have it.
- **An index and a platform image.** One side has the digest of the image
  index of a multi-platform image, and the other the digest of the image of
  one platform.

So the rules join a report to a running container by its digest first, and
else by the namespace, the container name and the reference
(`<registry>/<repository>:<tag>`). The second join is used only when no
report has the digest of the container, so a container counts one report.
When two reports have the reference and neither has the running digest (the
tag moved twice, and the report of an old ReplicaSet is still there), the
second join keeps the report with the most critical vulnerabilities. The
metric has no time of the scan, so the rules can not pick the newest report,
and the worst one never hides a fail.
The reference of the pod spec is read as the operator reads it: Docker Hub is
`index.docker.io`, a single name is in `library/`, and no tag is `latest`. A
pod spec with a digest joins by its digest only: the operator writes the tag
of such a reference in another form.

What the second join can miss, or hide:

- **A tag that moved.** The report of the new image counts for a pod that
  still runs the old one, until the pod starts again. The board shows the
  number of such containers ("Images scanned by tag only", from
  `homelab:security_trivy_containers_by_reference:count`). The report is of
  the image that the tag points to now: restart the workload, or pin a
  digest, to make it exact. When the count of a cluster stays above 0 for 7
  days, `SecurityImagesScannedByTag` tells the owner (`security/README.md`).
- **Two workloads in one namespace** with the same container name and the
  same reference cover for each other: the report of one counts for the
  other.
- **A leftover report** (an old ReplicaSet, up to its lifetime of 24 hours)
  counts for a container of the same reference, so it can hold back
  `SecurityImagesNotScanned` for up to that time.

A report of the same reference in another namespace, or for another
container, does not count.

## Exceptions

An exception names a check, never a result. A check that does not apply to
Talos, and each finding that the owner accepts, is a line of the encrypted
list of accepted findings (`security/README.md`, "Accepted findings"). The
rule of the result series reads the series that the reader of that list
serves, `homelab_security_accepted_until_timestamp_seconds`: a check of
`trivy` that the list accepts for the system has the value 0 until its end
date. There is no second list here. The `ignoreFile` of the chart applies
only to the image scans, and not to the compliance report, so it is not the
place for an exception.

The reader runs on homelab-wsl-02, where the Secret of the list is. The
job `peer-federate` of homelab-wsl brings its series of `trivy` there
(`kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml`).
The rule of the result series reads the accepted findings with no
`source_site` matcher, so on homelab-wsl the federated copy mutes them: the
fixture "an accepted check gives 0 until its end" of `observability/rules.py`
has the copy from homelab-wsl-02 and the result of homelab-wsl. The rule
keeps the last value of the list for a day, so when homelab-wsl-02 or the
federation is down, an accepted finding stays muted for up to a day and
does not alert as new (the fixture "an accepted check of homelab-wsl stays
muted while the federated list is gone for hours"). After a day with no
copy of the list, the mute stops. For the same reason, an entry that the
owner takes out of the list before its end stays muted for up to a day.
Until the reader runs, no check is accepted, and an exception shows as a
fail.

## What it can reach

Default-deny (`../namespace.yaml`) denies the rest.

| Pod | In | Out |
| --- | --- | --- |
| The operator | Prometheus of this cluster on 8080 (metrics). The kubelet on 9090 (probes) | The API server. No host outside the cluster |
| A scan job | Nothing | DNS, and port 443 of the hosts below |

The hosts of a scan job are the registries of the images that run on the two
clusters, and the hosts where a registry sends the layers:

| Registry | Hosts |
| --- | --- |
| The Google mirror, also for the vulnerability database | `mirror.gcr.io` |
| GitHub | `ghcr.io`, `pkg-containers.githubusercontent.com` |
| Docker Hub | `docker.io`, `index.docker.io`, `registry-1.docker.io`, `auth.docker.io`, `production.cloudfront.docker.com` |
| Quay | `quay.io`, `*.quay.io` |
| Kubernetes | `registry.k8s.io`, `*-docker.pkg.dev` (the manifests, in a region that depends on the client), `cdn.registry.k8s.io` (the layers) |
| Codeberg | `codeberg.org` |
| AWS public | `public.ecr.aws`, `*.cloudfront.net` |
| Microsoft | `mcr.microsoft.com`, `*.data.mcr.microsoft.com` |

Each of these redirects was read before the merge: a `GET` of a manifest
and of a layer, and the `Location` of each answer. `mirror.gcr.io` and
Codeberg serve the layers from their own host.

Two patterns are wide. `*.cloudfront.net` permits each CloudFront
distribution of any owner, and `*-docker.pkg.dev` each Artifact Registry
region. A scan job holds no token and no pull secret, and its only input is
the image reference, so the risk is a scan job that sends data to such a
host after a fault in Trivy. The registries give no narrower name.

Trivy sends a version check and usage data to its vendor unless
`scan.skip-version-check` and `scan.disable-telemetry` are both on. The
values set both (`trivy.configFile`), and the policy names no host of the
vendor. `trivy.offlineScan` keeps a scan off the package indexes, such as
the one of Maven. The operator downloads no bundle of checks: the checks are
the ones in its image, and `policiesBundle` is empty, so CI does not test an
artifact that is not a container image. `operator.accessGlobalSecretsAndServiceAccount` is off, so
the operator reads no pull secret, and an image of a private registry has no
scan.

## Memory

| Part | Request | Limit | Source |
| --- | --- | --- | --- |
| The operator | 256 MiB | 512 MiB | `../README.md`, "Memory". On homelab-wsl-02 it works at about 168 MiB at rest. The first limit, 256 MiB, killed it 3 times in the first burst of scans (236 MiB in the last sample before a kill). `GOMEMLIMIT` is 460 MiB, 90% of the limit (go.dev/doc/gc-guide) |
| Each container of a scan job | 64 MiB | 512 MiB | `../README.md`. One job at a time as a rule (`operator.scanJobsConcurrentLimit: 1`). The full cycle on homelab-wsl-02 (one scan container at 25 MiB, one job at 97 MiB, 2026-11-15) ran while one container of a job scanned at a time; a job is not measured since the scan cache is in memory (below) |

The operator counts its scan jobs from its cache before it starts one
(`pkg/operator/jobs/limit_checker.go` of the release). A job that it has
just made can be missing from that count, so in a burst of scans two jobs
can run at the same time for a short while. A burst comes after a start of
the operator, each day when the reports end after 24 hours, and with a
rollout of many images.

A scan job has one container for each container of the workload, its init
containers too, each with the limit, and they run at the same time. On
`homelab-wsl` the largest pods have 6 containers (Thanos query,
`count by (namespace, pod)` of `kube_pod_container_info` plus
`kube_pod_init_container_info`, on the day of this change). So the worst sum
of Trivy on that cluster, while such a pod has its scan:

| By | The operator | The scan job | Sum |
| --- | --- | --- | --- |
| Limits | 512 MiB | 6 x 512 MiB | 3584 MiB |
| Estimate from a local test, scan cache in memory | 512 MiB (its limit) | 1032 to 1572 MiB, up to 2808 MiB with large old images | 1544 to 2084 MiB, up to 3320 MiB |
| Measured on homelab-wsl-02, one container of a job that scans | 316 MiB | 97 MiB | 413 MiB or less |

The measured line is the peak of a full cycle on homelab-wsl-02, in samples
of one minute (step 9 of "Tests"): the operator at 316 MiB of its 512 MiB,
one scan container at 25 MiB at most, and the containers of one scan job
together at 97 MiB at most. No sample had more than one scan job. The two
peaks need not be at the same time, so their sum is an upper bound.
homelab-wsl runs more workloads, and the cache of the operator grows with
them, so the operator can take more there; its limit leaves 196 MiB above
the peak of homelab-wsl-02. The owner kept the limits with that measure,
for homelab-wsl too (2026-11-15). That cycle ran with the scan cache in
files on `trivy-cache`: one container of a job took the lock of the cache
and scanned, and the others stopped after 5 seconds ("When a scan fails").
So 97 MiB is about one container that scans, not a job.

The scan cache (what Trivy found in each layer of an image) is now in the
memory of each scan. So all the containers of a job scan at the same time,
and each reads each layer of its image again, each day: its peak is the one
of a first scan, and it downloads the layers again. The estimate line comes
from a local test in Docker, with the pinned Trivy and the settings of this
app, which measured each container alone. In jobs of 6 containers, the
largest container peaked at 172 MiB in one job, and at 262 MiB in another
with critical vulnerabilities only. With every severity, as these values
have, one container of a large old image peaked at 468 MiB, and at 512 MiB
in a run with the file cache: at its limit. The test did not sum a job, so the
estimate of a job is 6 times its largest container, and the operator counts
at its limit, as the budget counts it. It is an estimate, not a measure. The
first full cycle after the change measures it on each cluster (step 10 of
"Tests"), and the owner decides the line of `../README.md` with it.

Each container of a scan job also has a CPU limit of 500m, so a job of 6
containers can take 3 cores of `SECURITY_NODE` for the length of the scan.
With the file cache, the other containers stopped after 5 seconds; now all
of them scan at once, so the 3 cores are real, next to the other pods of
that node. The init container of a job downloads the database (about 140 MB
compressed) to `trivy-cache`, and a later job reads it from there. Without
the cache, each job would download it again.

**A container at its limit.** The kernel kills it (`OOMKilled`). The job
fails, and the operator writes the reports of the other containers and
deletes the job and its pod at once (`pkg/vulnerabilityreport/controller/scanjob.go`
of the release, with no `scanJobTTL`). So the chart rule `KubeJobFailed`,
which needs the failed Job for 15 minutes, is not expected to fire. The image of that
container has no report. The next scan of the workload ("When a scan
fails") runs the same image with the same limit and fails the same way,
each time, until the limit or the image changes. The signals: the log of
the operator has "Scan job container" with `status.reason` `OOMKilled`, and
`SecurityImagesNotScanned` fires after 36 hours.

## When a scan fails

`kubectl -n security get jobs -l app.kubernetes.io/managed-by=trivy-operator`
lists the scan jobs, and the log of the operator says why one failed. The
usual causes:

- **A new registry.** The scan of an image from a host that the policy does
  not name fails. Add its hosts to `app/networkpolicy.yaml` and to the table
  above.
- **A private image.** The operator reads no pull secret (above).
- **Containers of one job at the same time.** A scan job has one container
  for each container of the workload, and they run at the same time. With
  the scan cache in files on `trivy-cache`, each container opens one file
  with a lock that only one process holds (`pkg/cache/fs.go` of Trivy, a
  timeout of 5 seconds), so the others stopped with "unable to initialize
  fs cache: cache may be in use by another process: timeout", and their
  images had no report. The values keep the scan cache in the memory of
  each scan (`cache.backend: memory` in `trivy.configFile`); the databases
  stay on `trivy-cache`, and a scan opens them read-only, which several
  processes can do at once. The operator has the same issue in Standalone
  mode upstream, with no fix in the pinned release.
- **The Java database, two downloads at once.** A scan of an image with
  Java files reads the Java database on `trivy-cache`. When the database is
  past its next update and was downloaded more than a day ago, the scan
  downloads it again, and the download removes the directory first
  (`pkg/javadb/client.go` and `pkg/downloader/download.go` of Trivy). With
  the file cache, one container of a job scanned, so one downloaded. With
  the scan cache in memory, two containers of one job with Java files can
  download it at the same time, and one of them can fail ("Unable to
  initialize the Java DB"), with no report until the next scan of the
  workload. This is a risk the values leave open, not tested. The init
  container of a scan job downloads the vulnerability database only
  (`--download-db-only`, `pkg/plugins/trivy/image.go` of the operator
  release), the release has no setting that adds the Java database there,
  and the settings file of Trivy reaches the scan containers too.
  `trivy.skipJavaDBUpdate` alone stops each update, and a first scan on an
  empty volume then fails. A job that updates the Java database on
  `trivy-cache` once a day, with `trivy.skipJavaDBUpdate` on the scans,
  would leave one download a day, at a time that it chooses.

A container whose scan fails gets no second try of its own: the operator
deletes the job, and writes the reports of the containers that ended well.
It starts a new scan of the workload when it checks the workload again: at
the resync of its cache (every 10 hours, with up to 10% jitter, the default
of controller-runtime), at a restart, or when another report of the
workload ends after 24 hours.

## What a change applies

A merge that changes `app/` makes Flux upgrade the HelmRelease on each
cluster that lists `ks.yaml`. The operator restarts when its own settings
change (the ConfigMap `trivy-operator-config`, whose checksum is on its
pod). It then checks the workloads again, and a scan job starts for each
image that has no report. A change of the settings of Trivy alone (the
ConfigMap `trivy-operator-trivy-config`, such as `trivy.configFile`)
restarts nothing: the next scan job reads it, and an
image with no report gets its scan at the next check of its workload ("When
a scan fails").

**A chart release that moves a rule.** A `test` op of the post-render patch
fails, and the upgrade stops before Helm applies a resource: the running
operator and its narrow roles stay, and the HelmRelease is not ready
(`homelab_flux_resource_ready`). helm-controller applies the CRDs of the new
chart before the upgrade (`crds: CreateReplace`), so the new CRDs are in the
cluster; they add fields, and the old operator works with them. The fix:
read the rules of the new chart's ClusterRole, and change the two indexes of
the patch. `bin/trivy-operator-roles` checks the rendered cluster-wide
roles of each cluster that lists the app: each ClusterRole of the chart, and
each ClusterRole that a ClusterRoleBinding gives to the service account
`trivy-operator`. Outside the reports, a rule with a write, or a rule that
names `secrets` or `*`, fails it (the cluster-wide read of Secrets that
`accessGlobalSecretsAndServiceAccount: false` keeps away). A rule of
`nonResourceURLs` with a verb other than get, list or watch fails it. A log
read fails it, and so does a binding to a ClusterRole that is not in the
render. Run it
in each pull request of a chart release (Tests, below); it is not yet a step
of `.github/workflows/kubernetes.yaml`.

## The way back

1. Remove `ks.yaml` from the `apps/kustomization.yaml` of the cluster. Flux
   prunes the HelmRelease, the PVC and the policies, and Helm removes the
   operator and its roles.
2. Helm leaves the CRDs of the chart (`*.aquasecurity.github.io`) and the
   reports in them. To remove them too:
   `kubectl get crd -o name | grep aquasecurity.github.io | xargs kubectl delete`.

## Tests

Offline, before a merge:

```bash
kustomize build kubernetes/apps/security/trivy-operator/app
bin/trivy-operator-roles kubernetes/clusters/wsl kubernetes/clusters/wsl-02
flate test all -p kubernetes/clusters/wsl-02
flate test all -p kubernetes/clusters/wsl
helm template trivy-operator aquasecurity/trivy-operator --version 0.38.1 \
  --namespace security -f <(yq '.spec.values' kubernetes/apps/security/trivy-operator/app/helmrelease.yaml) \
  > all.yaml
```

To see the roles after the post-render patch, put `all.yaml` in a
`kustomization.yaml` with the `patches` of `spec.postRenderers` and run
`kustomize build`. The ClusterRole `trivy-operator` then has no `pods/log`
rule, and its Jobs rule has get, list and watch only.

After a merge, on the cluster, with reads only:

1. `flux get hr -n security trivy-operator` is ready, and the pod
   `trivy-operator` runs on `SECURITY_NODE`.
2. No node collector job exists:
   `kubectl -n security get jobs -l node-info.collector` is empty.
3. A scan job ends: `kubectl get vulnerabilityreports -A` lists reports,
   and the log of a scan job has no error of a download or of a write to
   the cache (the job runs as `nobody`).
4. The roles are narrow:
   `kubectl auth can-i create jobs -n default --as=system:serviceaccount:security:trivy-operator`
   and `kubectl auth can-i get pods/log -n default --as=...` say `no`, and the
   same two in `-n security` say `yes`.
5. `kubectl get clustercompliancereport k8s-cis-1.23` has a status.
6. Prometheus has `trivy_compliance_info` and `trivy_image_vulnerabilities`
   for this cluster, and `homelab_security_check_result{source="trivy"}` and
   `homelab_security_last_result_timestamp_seconds{source="trivy"}` for its
   system.
7. The CPU of `SECURITY_NODE` while the largest scan job runs (6 containers
   with a limit of 500m each):
   `sum(rate(container_cpu_usage_seconds_total{namespace="security",pod=~"scan-vulnerabilityreport-.+"}[5m]))`.
8. The memory of the operator stays under its limit for a full cycle:
   `max_over_time(container_memory_working_set_bytes{namespace="security",container="trivy-operator"}[1d])`.
9. On homelab-wsl-02, before homelab-wsl lists the app: the peak memory of
   the scan job of the pod with the most containers (a Cilium agent pod has
   6), summed over its containers:
   `max_over_time(sum by (pod) (container_memory_working_set_bytes{namespace="security",pod=~"scan-vulnerabilityreport-.+",container!=""})[1d:1m])`,
   with `kubectl -n security get jobs -l app.kubernetes.io/managed-by=trivy-operator`
   for the job of that pod. Write it in "Memory" in place of the estimate.
10. After a change of the scan cache or of a limit, the first full cycle on
    each cluster: step 9 on that cluster (the sum of the largest job), step
    8 (the operator), step 7 (the CPU of `SECURITY_NODE`, and of the other
    pods of that node), and no "Scan job container" error with
    `status.reason` `OOMKilled` in the log of the operator. Write them in
    "Memory" in place of the estimate.
