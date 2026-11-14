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
| homelab-wsl, the live node | `talos/wsl/patches/audit-policy.yaml` | `cluster.apiServer.auditPolicy` of the v1alpha1 machine config. Talos refuses the document while the config has this field |
| homelab-wsl, a new cluster | `talos/wsl/patches/audit-policy-multidoc.yaml` | The document `KubeAuditPolicyConfig` |
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

**How the policy reaches a node.** A merge applies nothing to a running
node: no workflow runs `talosctl`, and the Ansible host playbooks do not read
the patch lists. A running cluster gets the policy only from
`talosctl patch mc --mode=no-reboot`, the command at the top of its file,
run by the owner or with the owner's word.

A rebuilt cluster gets the patches in `config_patches` and
`controlplane_patches` of its entry in `talos_clusters`
(`infrastructure/ansible/inventory/group_vars/all/talos.yml`).
`01-talos-bootstrap.yml` creates a cluster only when the cluster has no
control plane container. Each list is in the multi-document form, which the
pinned Talos needs at create, and each `controlplane_patches` has the policy
as a `KubeAuditPolicyConfig`.

**homelab-wsl has two sets.** Its live node has a v1alpha1 config, from an
older talosctl, and Talos refuses the documents there. So the live node keeps
its v1alpha1 files (`cluster-config.yaml`, `patches/kubelet-image-gc.yaml`,
`patches/talos-api-access.yaml`, `patches/audit-policy.yaml`), with the
`talosctl patch mc` commands in their headers. Its lists name the
multi-document set (`patches/cluster.yaml`, `patches/controlplane.yaml`,
`patches/audit-policy-multidoc.yaml`). A change to a setting goes into both
sets: the tests below fail when the two differ. The tests compare the
settings in the files. Talos 1.14 also generates defaults that the older
release behind the live node did not, and nothing compares those. The
conversion of the live node to the multi-document form ends the double set.

**What CI checks.** A change under `talos/` runs the `ansible` domain
(`.github/workflows/ansible.yaml`):

- `infrastructure/ansible/tests/test_talos_patches.py`: each file under
  `talos/` parses, each directory is a cluster of `talos_clusters`, and each
  YAML file of a cluster is in its lists or is named as no patch or as a file
  of the live node. For homelab-wsl, each field of the v1alpha1 files has
  the same value in the multi-document set, and each setting of the set comes
  from a v1alpha1 file.
- The pinned `talosctl` (`talos/<target>/versions.yaml`) builds the machine
  configs of each Docker cluster from its lists, with throwaway secrets, and
  validates them for container mode. This is what `talosctl cluster create
  docker` builds, without the Docker provisioner.

A change to an audit policy also runs the rule tests above, on each policy
file. CI does not check a live node's config: Talos checks a patch at the dry
run of its apply.
