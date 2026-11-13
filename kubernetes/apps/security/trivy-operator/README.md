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

The release is Trivy Operator 0.36.1 (chart 0.38.1) with Trivy 0.76.1. Both
images are pinned by digest in `app/helmrelease.yaml`. The digest of a tag
comes from the registry: `crane digest mirror.gcr.io/aquasec/trivy-operator:0.36.1`.

## The parts

| File | What it is |
| --- | --- |
| `ks.yaml` | The Flux Kustomization `security-trivy-operator`. It waits for `kube-prometheus-stack`, for the CRD of the ServiceMonitor |
| `app/helmrelease.yaml` | The chart, from the HelmRepository `aquasecurity` (`kubernetes/flux/meta/helm/aquasecurity.yaml`) |
| `app/pvc.yaml` | `trivy-cache`, 4 GiB: the vulnerability database for the scan jobs |
| `app/networkpolicy.yaml` | What the operator and a scan job can reach |

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
  them. The rule of the result series (`observability/rules.py`) leaves them
  out, so they read as "no evidence" and never as a pass.
- **A stricter step, later:** a CI check of the kubelet settings in the
  machine config of each cluster (`talos/`), or the node collector in a
  namespace of its own when the chart can put it there.

The infra assessment needs no node collector. It reads the pods of the
control plane, and `operator.infraAssessmentScannerEnabled` stays on.

## Exceptions

An exception names a check, never a result. A check that does not apply to
Talos, and each finding that the owner accepts, is a line of the encrypted
list of accepted findings (`security/README.md`, "Accepted findings"). The
rule of the result series reads that list: an accepted check of `trivy` has
the value 0 until its end date. There is no second list here. The
`ignoreFile` of the chart applies only to the image scans, so it is not the
place for an exception.

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
| Kubernetes | `registry.k8s.io`, `cdn.registry.k8s.io` |
| Codeberg | `codeberg.org` |
| AWS public | `public.ecr.aws`, `*.cloudfront.net` |
| Microsoft | `mcr.microsoft.com`, `*.data.mcr.microsoft.com` |

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
| Each container of a scan job | 64 MiB | 512 MiB | `../README.md`. One scan job measured 56 MiB at peak. One job at a time (`operator.scanJobsConcurrentLimit: 1`) |

A scan job has one container for each container of the workload, each with
the limit. Its first container downloads the database (about 140 MB
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
flate test all -p kubernetes/clusters/wsl-02
flate test all -p kubernetes/clusters/wsl
helm template trivy-operator aquasecurity/trivy-operator --version 0.38.1 \
  --namespace security -f <(yq '.spec.values' kubernetes/apps/security/trivy-operator/app/helmrelease.yaml)
```

After a merge, on the cluster, with reads only:

1. `flux get hr -n security trivy-operator` is ready, and the pod
   `trivy-operator` runs on `SECURITY_NODE`.
2. No node collector job exists:
   `kubectl -n security get jobs -l node-info.collector` is empty.
3. A scan job ends: `kubectl -n security get vulnerabilityreports` lists
   reports, and the log of a scan job has no error of a download.
4. `kubectl get clustercompliancereport k8s-cis-1.23` has a status.
5. Prometheus has `trivy_compliance_info` and `trivy_image_vulnerabilities`
   for this cluster.
6. The memory of the operator stays under its limit for a full cycle:
   `max_over_time(container_memory_working_set_bytes{namespace="security",container="trivy-operator"}[1d])`.
