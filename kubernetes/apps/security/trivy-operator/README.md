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
- **The images of each workload**, one scan job at a time, for the
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
| `app/pvc.yaml` | `trivy-cache`, 4 GiB: the vulnerability database for the scan jobs |
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
of homelab-wsl (`kubernetes/clusters/wsl-02/apps/kustomization.yaml`). When
homelab-wsl or that scrape is down, its series go stale after 5 minutes, and
`SecurityResultLate` fires for `trivy` on homelab-wsl 10 minutes later, next
to the alert of the lost site. That is the federation, not a fault of the
operator. The rules give the two series of the
contract of the registry (`security/README.md`, "The series"), with
`source="trivy"` and the cluster as `system`:

| Series | From | Value |
| --- | --- | --- |
| `homelab_security_check_result`, `check="k8s-cis-1.23/<control ID>"` | `trivy_compliance_info` of the report `k8s-cis-1.23` | The failed resources of a control that fails, and 0 for a control that passes |
| `homelab_security_check_result`, `check="image-vulnerabilities"` | `trivy_image_vulnerabilities` | The critical vulnerabilities with a fix, over the distinct images that run on the cluster now (`kube_pod_container_info` and `kube_pod_init_container_info`, by the digest of `image_id`). The report of an image that no longer runs (an old ReplicaSet, whose report can stay for a day) does not count. 0 when the scan reports have none. No series when no running image is known (kube-state-metrics down). A report counts for an hour after its last sample, so a report that is gone during its new scan changes nothing. No series when there is no scan report. While an image that runs on the cluster has had no report for an hour, a fail stays and a pass is "no evidence": a failed scan never reads as a pass, and never hides a known fail. The pods of the scan jobs, a container that has not started (no `image_id` yet) and an image with no registry digest are not counted |
| `homelab_security_last_result_timestamp_seconds` | `homelab_trivy_compliance_report_update_timestamp_seconds`, which kube-state-metrics reads from the status of the report | The time at which the operator wrote the report: at its start, then every 6 hours |

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
| The operator | 128 MiB | 256 MiB | `../README.md`, "Memory". The operator and one scan job measured 218 MiB together on a test cluster |
| Each container of a scan job | 64 MiB | 512 MiB | `../README.md`. One scan container measured 56 MiB at peak. One job at a time (`operator.scanJobsConcurrentLimit: 1`) |

A scan job has one container for each container of the workload, its init
containers too, each with the limit, and they run at the same time. On
`homelab-wsl` the largest pods have 6 containers (Thanos query,
`count by (namespace, pod)` of `kube_pod_container_info` plus
`kube_pod_init_container_info`, on the day of this change). So the worst sum
of Trivy on that cluster, while such a pod has its scan:

| By | The operator | The scan job | Sum |
| --- | --- | --- | --- |
| Limits | 256 MiB | 6 x 512 MiB | 3328 MiB |
| Estimate | 162 MiB | 6 x 56 MiB | 498 MiB |

The estimate is not a measure of a job of 6 containers: on the test cluster
the operator and a scan job of one container took 218 MiB, of which 56 MiB
was the scan container, and the estimate takes that container 6 times. A
scan of a pod of 6 containers on homelab-wsl-02 (the Cilium agent pods have
6 there too) measures it before homelab-wsl lists the app (step 8 of
"Tests"). The owner decides the limit with that number. Each container of a
scan job also has a CPU limit of 500m, so such a job can take 3 cores of
`SECURITY_NODE` for the length of the scan. Its first container downloads the database (about 140 MB
compressed) to `trivy-cache`, and a later job reads it from there. Without
the cache, each job would download it again.

The signals of a limit that is too low: the pod status `OOMKilled`, and the
alert `KubeJobFailed` for a scan job.

## When a scan fails

`kubectl -n security get jobs -l app.kubernetes.io/managed-by=trivy-operator`
lists the scan jobs, and the log of the operator says why one failed. The
usual causes:

- **A new registry.** The scan of an image from a host that the policy does
  not name fails. Add its hosts to `app/networkpolicy.yaml` and to the table
  above.
- **A private image.** The operator reads no pull secret (above).

## What a change applies

A merge that changes `app/` makes Flux upgrade the HelmRelease on each
cluster that lists `ks.yaml`. The operator restarts. It then checks the
workloads again, and a scan job starts for each image that has no report.

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
8. On homelab-wsl-02, before homelab-wsl lists the app: the peak memory of
   the scan job of the pod with the most containers (a Cilium agent pod has
   6), summed over its containers:
   `max_over_time(sum by (pod) (container_memory_working_set_bytes{namespace="security",pod=~"scan-vulnerabilityreport-.+",container!=""})[1d:1m])`,
   with `kubectl -n security get jobs -l app.kubernetes.io/managed-by=trivy-operator`
   for the job of that pod. Write it in "Memory" in place of the estimate.
