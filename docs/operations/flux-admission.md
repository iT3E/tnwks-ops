# Flux admission

The admission policy `flux-child-scope` limits what a Flux child outside the
"ask first" class may apply. The two Flux controllers that apply manifests
(`kustomize-controller` and `helm-controller`) are bound to `cluster-admin`,
so without it each manifest in the repository applies with full rights. The
policy is the first step of the far-side control of the class. The Flux
lockdown (a service account for each child) is a later step.

- The policy: [`kubernetes/apps/flux-system/admission/app/policy.yaml`](../../kubernetes/apps/flux-system/admission/app/policy.yaml).
- Its lists: [`classlist.py`](../../kubernetes/apps/flux-system/admission/classlist.py) writes them from git.
- Its tests: [`tests/run.sh`](../../kubernetes/apps/flux-system/admission/tests/run.sh) on a throwaway API server, and
  [`observability/tests/test_flux_admission.py`](../../observability/tests/test_flux_admission.py) in CI.
- Both WSL clusters list it (`kubernetes/clusters/{wsl,wsl-02}/apps/kustomization.yaml`).

## The class

A Flux Kustomization is in the class when its `spec.path` is under a path of
`CLASS_PATHS` in `classlist.py`: the `kubernetes/` part of the "ask first"
class of Owner label. A HelmRelease is in the class when a Kustomization of
the class applies it. Each other child is outside the class.

The policy knows the child of an object by the owner labels that the
controller sets last: `kustomize.toolkit.fluxcd.io/name` and `/namespace`,
or `helm.toolkit.fluxcd.io/name` and `/namespace`. A request of a controller
for an object with no owner label is the work of the controller (an event, a
lease, a finalizer, the storage of a Helm release), and the rules for the
children do not apply to it.

`classlist.py` walks the Flux tree of each site from
`kubernetes/clusters/<site>`, the way Flux builds it, and writes:

| List | What it holds |
| --- | --- |
| `classKustomizations` | each Kustomization of the class, with the parents that apply it on the sites |
| `classHelmReleases` | each HelmRelease that a Kustomization of the class applies |
| `classNamespaces` | each namespace of a Namespace manifest of the class, less the namespaces where a child outside the class puts an object |
| `classPaths` | `CLASS_PATHS` |
| `parentObjects` | for the two parents of the apps, which read the aggregators of `kubernetes/apps`, each object of their build in git |

After a change of the class, of a cluster tree or of a namespace, run
`python3 kubernetes/apps/flux-system/admission/classlist.py write` and
commit the policy. The CI test fails until the lists are the ones of git.
The walk of git gave the same Kustomizations and HelmReleases as the two
live sites on 2026-11-21: 155 and 85 on site 1, 85 and 51 on site 2.

## The rules

A child outside the class may not:

| Rule | What it refuses |
| --- | --- |
| `cluster-object` | an object with no namespace: cluster RBAC, CRDs, namespaces, volumes, cluster network policies and the rest |
| `class-namespace` | an object in a namespace of the class. The HelmChart that the helm controller makes beside its chart source is its own work |
| `rbac` | a Role or a RoleBinding |
| `flux-kinds` | a Flux Kustomization or an ImageUpdateAutomation |
| `helmrelease-spec` | a HelmRelease with a service account, a kubeconfig, a target or storage namespace of the class, or the post render strategy `nohooks` |
| `privileged-pod` | a pod spec with `privileged`, a host path, the host network, PID or IPC namespace, a host port or an added capability, in any namespace |
| `takeover` | a change of an object whose owner labels name a child of the class |

For each request of the two controllers:

| Rule | What it checks |
| --- | --- |
| `kustomization-spec` | A Kustomization outside the class has a path in the form `a/b/c`, not in a class path and not above one (so not the root). It has no service account, no kubeconfig and no `commonMetadata` key of Flux. It substitutes from `cluster-settings`, `site-overrides` and `cluster-secrets` only |
| `kustomization-class` | A Kustomization of the class comes from one of its parents in git, with a path of the class |
| `helmrelease-class` | A HelmRelease of the class comes from a Kustomization of the class |
| `parent-objects` | A parent of the apps applies Kustomizations and the objects of its build in git only |

An update of a controller that keeps the spec of a Kustomization or a
HelmRelease (a finalizer, an annotation) passes the last three rules.

## Failure and deletion

- `failurePolicy: Fail`. An error of an expression refuses that one request
  of a controller. Only the child of that request fails its apply. The other
  children go on, and the policy matches no other user.
- The policy runs in the API server, so no webhook can be down.
- The policy and its binding are cluster objects of the Kustomization
  `flux-admission` (a child of the class). Flux removes them only when the
  files leave `main`, and Owner label holds that change of the App. A child
  outside the class can not make or change a cluster object, so none is in
  its inventory, and the garbage collection of a child deletes only the
  objects of its inventory.
- A person with `cluster-admin` can delete them. Flux makes them again at
  the next reconcile of `flux-admission` (30 minutes, or 1 minute after a
  failure), unless that Kustomization is suspended.

## Remote sources

kustomize-controller runs with `--no-remote-bases=true` on both sites (a
patch in `kubernetes/clusters/<site>/flux-system/kustomization.yaml`). A
kustomization that names a remote base or resource (a URL, a git address)
then fails its build, so a URL in a folder outside the class can not bring
in a manifest that the policy can not see, such as an admission object. No
build of the clusters uses one: only `kubernetes/bootstrap`, which no
Kustomization builds, and a CI test keeps it so.

- **How it is applied.** The Kustomization `flux-system` applies the
  patched Deployment at its next reconcile (10 minutes). The Deployment
  rolls: the new pod starts, takes the leader lease when the old pod
  releases it at its stop (a few seconds, at most the lease of 35 seconds),
  and the old pod goes. Reconciles of Kustomizations wait for that time
  and then go on; an apply that was running is retried. No object changes,
  and the helm controller does not restart.
- **The way back.** Revert the change; Flux rolls the controller again. By
  hand, the owner suspends `flux-system`
  (`flux suspend kustomization flux-system -n flux-system`), removes the
  argument from the Deployment, and resumes after the revert is on `main`.

## Roll-out

1. The binding starts with `Audit` and `Warn`. A request that a rule refuses
   passes, with an audit annotation, a warning in the log of the controller
   and a count of `apiserver_validating_admission_policy_check_total`.
2. After one reconcile cycle on both sites, read the result (below).
3. A second change turns the binding to `Deny`. It also changes
   `test_the_binding_is_still_audit_and_warn`.

The objects that the rules refuse on 2026-11-21, from the replay of the 1680
live objects with a Flux owner of site 1 on a throwaway API server, and from
git for site 2:

| Object | Rule | Sites |
| --- | --- | --- |
| ClusterRole and ClusterRoleBinding `hajimari` (HelmRelease `default/hajimari`) | `cluster-object` | 1 |
| Deployment `production/frigate` (privileged, a host path) | `privileged-pod` | 1 |
| StatefulSet `home-automation/zwave-js-ui` (privileged, a host path) | `privileged-pod` | 1 |
| StatefulSet `media/qbittorrent` (`NET_ADMIN` on its VPN sidecar) | `privileged-pod` | 1 and 2 |

Two CRDs of site 1 carry the owner labels of a HelmRelease that no longer
exists (`tools/node-feature-discovery`). No controller applies them, so no
request comes. The folders of the four apps of the table are in the class
(`CLASS_PATHS` and the list of Owner label), so the rules refuse no live
object.

## Read the audit result

On site 1:

```bash
kubectl get validatingadmissionpolicy flux-child-scope -o jsonpath='{.status}'
kubectl -n flux-system logs deploy/kustomize-controller --since=2h | grep -c 'flux-child-scope/'
kubectl -n flux-system logs deploy/helm-controller --since=2h | grep -c 'flux-child-scope/'
```

On both sites, through the Thanos query of site 1 (both sites keep the
series of this policy with `enforcement_action` "audit" or "deny"). The API
server counts a check only when it fails, so no series means no request
that the policy would refuse:

```promql
sum by (cluster, enforcement_action) (
  increase(apiserver_validating_admission_policy_check_total{policy="flux-child-scope"}[1d])
)
```

The names of the objects are in the audit stream `k8s-audit` of each site,
with the annotation `validation.policy.admission.k8s.io/validation_failure`.
That a series is absent proves nothing when the policy is not on the site,
or when the Prometheus of the site did not scrape. So also check that:

- the Prometheus of each site was up over the window:
  `min_over_time(up{job="apiserver"}[1d])` has a series for each `cluster`;
- `flux-admission` reconciles on site 1 (`flux get kustomization
  flux-admission`) and on site 2
  (`gotk_reconcile_duration_seconds_count{cluster="homelab-wsl-02",kind="Kustomization",name="flux-admission"}`
  grows; site 1 keeps no reconcile series).

## The live check

`bin/flux-admission-check` sends each test object of the design as a server
side dry run, as the service account of `kustomize-controller`, with the
owner labels of a child outside the class: cluster RBAC, an object in `ai`,
a Kustomization with a class path, and a privileged pod in `home-automation`,
`media` and `production`. The API server stores nothing. With `Audit` and
`Warn` each object passes with a warning. With `Deny` each one is refused.
The owner runs it with a kubeconfig that may impersonate:

```bash
bin/flux-admission-check                                         # site 1
bin/flux-admission-check --context <site 2> --parent flux-system/cluster-apps-wsl-02
```

The two test objects of decryption (a SOPS file of another folder, by a
relative path and by a link) are content of git. Owner label holds them.

## Break-glass

When the policy refuses an apply that must go through now, the owner, by
hand:

1. Stops Flux from making the binding again:
   `flux suspend kustomization flux-admission -n flux-system`.
2. Turns the binding to a warning only, or deletes it:
   `kubectl patch validatingadmissionpolicybinding flux-child-scope --type merge -p '{"spec":{"validationActions":["Audit","Warn"]}}'`
   or `kubectl delete validatingadmissionpolicybinding flux-child-scope`.
3. Reconciles the child: `flux reconcile kustomization <name> -n flux-system --with-source`.
4. Fixes the cause in git (the class, the lists or the manifest), then
   `flux resume kustomization flux-admission -n flux-system`, which applies
   the binding of `main` again.

An agent never suspends or resumes a Kustomization.

## Not in this step

- **Admission objects.** The API server runs no admission policy on a
  webhook configuration or an admission policy, so this policy can not
  refuse one (the test `test_5_admission_objects_are_out_of_reach` shows it).
  Owner label holds a pull request of the App that adds or changes a Flux
  object or an admission object, a path out of its folder, or a link
  ([`ci.md`](ci.md), "Agent pull requests"). The Flux lockdown closes it on
  the cluster.
- **Pods of an operator.** A pod that an operator makes from a resource of
  its own (a database cluster, a runner set) is not in a request of Flux.
- **Decryption.** The children outside the class still decrypt with the main
  key. The design asks for a key of their own; that needs a new key and a new
  encryption of their files, which is work of the owner. Until then Owner
  label holds a pull request of the App that touches an encrypted file, a
  path out of its folder or a link, which are the ways to reach an encrypted
  file of the class.
- **Deletes.** The policy checks create and update. A delete of a
  controller (the garbage collection of a child, the uninstall of a Helm
  release) is not checked. The Flux lockdown closes it.
- **Reach at run time.** What the apps outside the class reach in their own
  namespaces (their Secrets, service accounts and egress) stays open.
