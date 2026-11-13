# Talos Machine Configs

Two clusters, both running Talos Linux:

- **WSL cluster** — Talos in Docker on the WSL2 desktop; runs real workloads
  alongside the MS-01 cluster (USB-passthrough hosts for Coral, Z-Wave)
- **MS-01 cluster** — bare-metal Talos on 3x Minisforum MS-01 i9-13900H

## Directory Layout

```
talos/
├── wsl/
│   ├── cluster-config.yaml   # talosctl cluster create configuration
│   └── README.md             # Bootstrap notes for Docker mode
└── ms-01/
    ├── controlplane.yaml     # MS-01 control plane machineconfig
    ├── worker.yaml           # MS-01 worker machineconfig
    ├── patches/              # Machineconfig patches
    │   ├── cluster-discovery.yaml
    │   └── cni-none.yaml
    └── README.md
```

## Usage

### WSL cluster (Docker mode)

```bash
task bootstrap:wsl
# or manually:
talosctl cluster create --config talos/wsl/cluster-config.yaml
```

### MS-01 cluster (bare metal)

```bash
task bootstrap:ms-01
# or manually:
talosctl apply-config --nodes <IP> --file talos/ms-01/controlplane.yaml
```

## The audit policy

The API server of a Talos control plane node writes an audit log to
`/var/log/audit/kube/` on the node. The audit policy says which requests get
a line. With no policy in the machine config, Talos gives each request a
line. A cluster of the security platform has the policy below, in
`talos/<cluster>/patches/audit-policy.yaml`. The audit reader sends the
lines to Loki as the security stream `k8s-audit`
(`kubernetes/apps/monitoring/vector/README.md`).

| The policy keeps | The policy does not keep |
| --- | --- |
| Each request on a Secret, a read too, by each user: a person, a service account, a kubelet, a part of the control plane | The writes of leases and of events |
| Each command in a pod, each attach, each forwarded port, and each request through the proxy of a pod, a Service or a node | The health and the version URLs of the API server |
| Each write: create, update, patch and delete, by each user | The reads (`get`, `list`, `watch`) of the control plane, of a kubelet and of a service account |
| Each read of a user that is no service account, no kubelet and no part of the control plane | |

Each line has the level `Metadata`: who, what, when and the answer code. No
line has the body of a request, so no line has the data of a Secret. The
API server writes one line for each request, when its answer is complete.

| Cluster | File | Form |
| --- | --- | --- |
| homelab-wsl-02 | `talos/wsl-02/patches/audit-policy.yaml` | The document `KubeAuditPolicyConfig` (the multi-document machine config of talosctl 1.14) |

The rules are the same in each file. `security/tests/test_audit_policy.py`
holds that, and it gives sample requests to the rules in the order of the
API server.

**To apply a policy,** run the command at the top of its file. A merge of a
change to the file applies nothing. The command needs no reboot of the node:
Talos writes the policy file, and the API server starts again. With one
control plane node, the API of the cluster is away for about a minute. The workloads keep running, and a controller that holds a leader
lease can start again.

**The way back** is the second command at the top of the file: the policy
with one rule, a line for each request.

A new cluster gets the patches that its entry in `talos_clusters` lists
(`controlplane_patches`, in the Ansible inventory).
