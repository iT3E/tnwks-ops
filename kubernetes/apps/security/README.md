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
| homelab-wsl-02 | `homelab-wsl-02-worker-1` | 18 GiB (`talos_clusters.wsl-02.memory_nodes`) |
| homelab-wsl | `homelab-wsl-worker-2` | 13 GiB |

A Job, a CronJob and the pods a controller makes (the scan jobs of Trivy
Operator) carry the selector too. A chart takes it through its values.

## Memory

The limit of each part. A part does not go over its line without a change
to this table first, because the sum is the budget of the node.

| Part | Runs on | Memory limit |
| --- | --- | --- |
| Trivy Operator | each cluster | 512 MiB for the operator, 512 MiB for each container of a scan job, one scan job at a time as a rule (two for a short while in a burst of scans). On homelab-wsl the worst sum is 3584 MiB by limits, for a pod of 6 containers. A full cycle on homelab-wsl-02 peaked at 316 MiB for the operator and 97 MiB for the containers of one scan job together, 413 MiB or less in all; the owner kept these limits with that measure, for homelab-wsl too, on 2026-11-15 (`trivy-operator/README.md`, "Memory") |
| Prowler job | homelab-wsl-02, one run at a time: one for each system each day, at different hours | 1536 MiB for an AWS run, 768 MiB for the Cloudflare run (`prowler/README.md`, "Memory"). An AWS run is not measured yet: the first runs ended above 768 MiB |
| The converter | homelab-wsl-02 | 128 MiB |
| Trail copy job | homelab-wsl-02 | 128 MiB |
| API checks job | homelab-wsl-02 | 128 MiB |
| The TLS proxy | homelab-wsl-02 | 64 MiB |
| The credential refresher | homelab-wsl-02 | 64 MiB |
| The audit reader | each cluster, on the control plane node | 128 MiB |
| Wazuh indexer (step 2) | homelab-wsl-02 | 3 GiB, with a heap of 1.5 GiB |
| Wazuh manager (step 2) | homelab-wsl-02 | 1280 MiB |
| Wazuh dashboard (step 2) | homelab-wsl-02 | 320 MiB |

| Budget | Sum |
| --- | --- |
| homelab-wsl | 1 GiB |
| homelab-wsl-02, worker 1 | 6 GiB. Step 1: about 2.7 GiB (2754 MiB). The parts that always run, by their limits: the operator of Trivy 512, the converter 128, the TLS proxy 64. The jobs, by their measured peak or else their limit: two scan jobs 2 x 97 (in a burst), Prowler 1536 (its limit, until an AWS run measures its peak), the trail copy 128, the API checks 128, the credential refresher 64. Step 2: 4.6 GiB (4672 MiB) for the Wazuh server. The two steps sum to about 7.3 GiB (7426 MiB), about 1.3 GiB over the budget: a part goes down, or the budget up, before step 2. The measured peak of an AWS run replaces the 1536 |

The audit reader reads files of the control plane node, so it does not run
on `SECURITY_NODE`, and its 128 MiB are not in the budget of worker 1.

The budget counts the limit of each part that always runs and the measured
peak of each job. By that count Trivy takes 512 MiB and the peak of its
largest scan job: 97 MiB for the containers of one job together, in a full
cycle on homelab-wsl-02 (`trivy-operator/README.md`, "Tests", step 9). By
the limits of a job of 6 containers alone, the sum is 3584 MiB, over the
budget, but only while such a pod has its scan, once a day. The owner chose
the count of the measure, and kept the limits as they are, for homelab-wsl
too, on 2026-11-15 (`trivy-operator/README.md`, "Memory").
