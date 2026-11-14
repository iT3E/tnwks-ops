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
| Trivy Operator | each cluster | 512 MiB for the operator, 512 MiB for each container of a scan job, one scan job at a time as a rule (two for a short while in a burst of scans). On homelab-wsl the worst sum is 3584 MiB by limits and 572 MiB or more by an estimate (the operator in a burst, and one measured scan container of 56 MiB, times 6), for a pod of 6 containers (`trivy-operator/README.md`, "Memory") |
| Prowler job | homelab-wsl-02, one run a day | 768 MiB |
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
| homelab-wsl-02, worker 1 | 6 GiB: about 1.4 GiB in step 1 (with the operator of Trivy at 512 MiB), and 4.6 GiB for the Wazuh server in step 2. The two steps take the whole budget |

The audit reader reads files of the control plane node, so it does not run
on `SECURITY_NODE`, and its 128 MiB are not in the budget of worker 1.

The budget counts the limit of each part that always runs and the measured
peak of each job. By that count Trivy takes 512 MiB and the peak of its
largest scan job on homelab-wsl: an estimate of 6 x 56 MiB until a scan of a
pod of 6 containers on homelab-wsl-02 measures it (`trivy-operator/README.md`,
"Tests", step 8). By the limits of that job
alone, the sum is 3584 MiB, over the budget, but only while a pod of 6
containers has its scan, once a day. The owner decides which count holds
for homelab-wsl before Trivy runs there (`trivy-operator/README.md`,
"Memory").
