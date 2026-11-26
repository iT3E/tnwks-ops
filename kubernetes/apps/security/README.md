# security

The namespace of the security platform, on homelab-wsl and homelab-wsl-02:
the scanners, the converter of their results, and the parts that move audit
logs. This file is the contract for an app that goes here.

## The namespace

| What | Value |
| --- | --- |
| Namespace | `security`, on both WSL clusters (`namespace.yaml`, listed in each cluster's `apps/kustomization.yaml`) |
| Pod Security | `baseline`, enforced. No host path, host network, host PID or privileged container: a part that needs one of these goes in a namespace that allows it |
| Network | `default-deny` covers every pod, ingress and egress. Each app brings its own policy, DNS included |
| Flux | A failed HelmRelease here pages (`kubernetes/apps/flux-system/addons/alerts/alertmanager.yaml`) |

An app is a directory here with its `ks.yaml`, and each cluster's
`apps/kustomization.yaml` lists the apps that cluster runs. Nothing is
shared by default: Prowler runs on homelab-wsl-02 only, Trivy Operator on
both.

### Network policies

Both clusters enforce NetworkPolicy and CiliumNetworkPolicy (Cilium). An
app's policy names what it needs and nothing else:

- **DNS.** Through Cilium's DNS proxy, so the same policy can name host
  names (`toFQDNs`). The form is `allow-dns` in
  `kubernetes/apps/ai/agents/workflows/namespace.yaml`, with an
  `endpointSelector` for the app's own pods. Both clusters are one Cluster
  Mesh, so the rule names this cluster's DNS pods
  (`io.cilium.k8s.policy.cluster: ${CLUSTER_NAME}`).
- **Metrics.** Ingress on the metrics port from Prometheus in `monitoring`.
- **Egress.** The API server, another cluster's Service, or a host name on
  the internet: the narrowest rule that works.

## Placement

The kubelet of a WSL node reports the memory of the whole WSL VM, so the
scheduler does not see the memory limit of a node container. Placement is
by a node selector on every pod, with a request and a limit on every
container:

```yaml
nodeSelector:
  kubernetes.io/hostname: ${SECURITY_NODE}
```

| Cluster | `SECURITY_NODE` (cluster-settings) | Limit of that node container |
| --- | --- | --- |
| homelab-wsl-02 | `homelab-wsl-02-worker-1` | 22 GiB (`talos_clusters.wsl-02.memory_nodes`) |
| homelab-wsl | `homelab-wsl-worker-2` | 13 GiB |

A Job, a CronJob and the pods a controller makes (the scan jobs of Trivy
Operator) carry the selector too. A chart takes it through its values.

## Memory

The limit of each part. A part does not go over its line without a change
to this table first, because the sum is the budget of the node.

| Part | Runs on | Memory limit |
| --- | --- | --- |
| Trivy Operator | each cluster | 640 MiB for the operator (512 MiB until the owner raised it on 2026-11-15), 512 MiB for each container of a scan job, one scan job at a time as a rule (two for a short while in a burst of scans). On homelab-wsl the worst sum is 3712 MiB by limits, for a pod of 6 containers. The full cycle on homelab-wsl-02 (316 MiB for the operator, 97 MiB for one scan job) ran while one container of a job scanned at a time, and the owner kept these limits with it, for homelab-wsl too, on 2026-11-15. With the scan cache in memory, all the containers of a job scan at once. Measured on homelab-wsl in its first cycle with the scan cache in memory and one layer at a time (2026-11-16): the operator 361 MiB, one scan container 287 MiB, one scan job 627 MiB (the sum of its containers), one scan job at a time. The local estimate before it was 1032 to 1572 MiB for a job of 6 containers, up to 2808 MiB with large old images (`trivy-operator/README.md`, "Memory") |
| Prowler job | homelab-wsl-02, one run at a time: one for each system each day, at different hours | 896 MiB for an AWS run: the highest peak of four runs, 682 MiB, and 25 % (2026-11-16, one process for each service), with a request of 704 MiB, the highest working set rounded up. 768 MiB for the Cloudflare run (`prowler/README.md`, "Memory") |
| The converter | homelab-wsl-02 | 128 MiB |
| Trail copy job | homelab-wsl-02 | 128 MiB |
| API checks job | homelab-wsl-02 | 128 MiB |
| The TLS proxy | homelab-wsl-02 | 64 MiB |
| The credential refresher | homelab-wsl-02 | 64 MiB |
| The audit reader | each cluster, on the control plane node | 128 MiB |
| Wazuh indexer (step 2) | homelab-wsl-02 | 5 GiB, with a heap of 1.5 GiB (4 GiB until the owner raised it on 2026-11-26, for the memory of the indexer with the agents reporting; 3 GiB until the owner raised it on 2026-11-22, for the budget of step 2) |
| Wazuh manager (step 2) | homelab-wsl-02 | 1280 MiB |
| Wazuh dashboard (step 2) | homelab-wsl-02 | 320 MiB |
| Wazuh account job (step 2) | homelab-wsl-02, at a change of its files | 128 MiB |
| Wazuh settings job (step 2) | homelab-wsl-02, at a change of its files and once a day | 128 MiB |

| Budget | Sum |
| --- | --- |
| homelab-wsl | 1 GiB. Trivy by the count of the budget: the operator by its limit, 640, and one scan job by its measured peak, 627: 1267 MiB while a scan job runs, 243 MiB over the line, and 640 MiB between scans. By the measure alone, 361 + 627 = 988 MiB. The owner accepted one scan job at a time at its measured size |
| homelab-wsl-02, worker 1 | 6 GiB for step 1. For the two steps the owner raised it: about 7.6 GiB (7806 MiB), by a count with one scan job at its measured peak. The owner raised it again for the limit of the indexer: about 8.6 GiB (8830 MiB = 7806 + 1024). The owner raised it a third time for the limit of the indexer on 2026-11-26: about 9.6 GiB (9854 MiB = 8830 + 1024). Step 1: about 2.0 GiB (2028 MiB) with the old measure of a scan job, about 3.8 to 4.9 GiB (3898 to 4978 MiB) with the estimate. The parts that always run, by their limits: the operator of Trivy 640, the converter 128, the TLS proxy 64. The jobs, by their measured peak or else their limit: two scan jobs (in a burst), 2 x 97 by the old measure, which no longer describes a job, or 2 x 1032 to 1572 by the estimate, until a cycle measures them; Prowler 682 (the measured peak of an AWS run), the trail copy 128, the API checks 128, the credential refresher 64. Step 2: about 6.8 GiB (6976 MiB): 6720 MiB for the Wazuh server (5120 for the indexer, 1280 for the manager, 320 for the dashboard), and 128 MiB each for its account job and its settings job. The two steps sum to about 8.8 GiB (9004 MiB) with the old measure, about 10.6 to 11.7 GiB (10874 to 11954 MiB) with the estimate. The owner chose the budget up: the record above, and the limit of the node container at 22 GiB (Placement). The count of this row, with two scan jobs in a burst, is a second view: with the estimate it is above that record until a cycle of scan jobs measures them. |

The audit reader reads files of the control plane node, so it does not run
on `SECURITY_NODE`, and its 128 MiB are not in the budget of worker 1.

The budget counts the limit of each part that always runs and the measured
peak of each job. By that count Trivy took 512 MiB (the limit of the
operator then) and the peak of its
largest scan job, 97 MiB, in a full cycle on homelab-wsl-02
(`trivy-operator/README.md`, "Tests", step 9): 609 MiB, under the 1 GiB of
homelab-wsl. The owner chose the count of the measure, and kept the limits
as they are, for homelab-wsl too, on 2026-11-15. That cycle ran while one
container of a job scanned at a time, so 97 MiB is the sum of the pod
while one container scanned and the others had stopped within 5 seconds.
With the scan cache in memory, all the containers of a job scan at once. By
the estimate of a local test, and with the operator at its limit of 640
MiB, Trivy then takes 1672 to 2212 MiB while a pod of 6 containers has its
scan, up to 3448 MiB with large old images, and 3712 MiB by limits: over the
1 GiB of homelab-wsl, for the length of that scan, once a day. The owner
accepted on 2026-11-15 that Trivy on homelab-wsl goes over its line during
a scan job, and decided that the peak of the first cycle goes in this
table. The first cycle on homelab-wsl (2026-11-16, 07:46 to 10:48 UTC,
after the reports were deleted, in samples of one minute) measured the
operator at 361 MiB of its 640 MiB, the largest scan container at 287 MiB of
its 512 MiB, and the largest scan job at 627 MiB, the sum of its
containers, with at most one scan job at a time and no kill. So Trivy takes
1267 MiB by the count of the budget while a scan job runs (the operator by
its limit), 243 MiB over the 1 GiB of homelab-wsl for the length of that
job, and 988 MiB by the measure alone. The estimate of the local test was
high: it read 5 layers at a time, and it counted 6 times the largest
container. homelab-wsl-02 has no such cycle yet: its line keeps the
estimate.
