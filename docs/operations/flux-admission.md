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

The `db/` folder of each app with a database is in the class (D149), and
the app folder stays outside it. The database operator makes the
pods of a database cluster from a template of its own, with the access of
the operator, so its objects are a change of the class. The namespace
`databases`, where these folders put their clusters, is then a namespace of
the class.

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
It is `observability/tests/test_flux_admission.py`, in the job `tests`
of the Observability workflow. That job runs on each change under
`kubernetes/` and of `.github/scripts/owner-label-check.py`
(`.github/ci/domains.toml`), and `Build Success`, a required check of
`main`, needs it. So a merge that adds an app or an object to a parent of
the class without `classlist.py write` can not pass.
The walk of git gave the same Kustomizations and HelmReleases as the two
live sites on 2026-11-21: 155 and 85 on site 1, 85 and 51 on site 2.

The walk follows a patch of a `kustomization.yaml` that changes the
`spec.path` of a Flux Kustomization, as Flux does: the apps of site 2 move
two children to folders of their own that way. It reads one form, a
strategic merge patch with no target that names one Kustomization of that
build. Another form that could change a `spec.path` (a JSON patch of the
spec of a Kustomization, or a patch of a `spec.path` with a target) stops
`classlist.py`.

The walk reads this repository only. A Kustomization that it reaches with
a `sourceRef` other than the GitRepository `flux-system` of the namespace
`flux-system` stops it, with the name of that Kustomization: the objects of
another source are not in git, so the lists can not hold them. A
`spec.path` (or a component) that is not in the repository stops it too,
as a build of nothing would pass with no object. A file with such a child
that no cluster lists is not reached, so it does not stop the walk. To
apply the manifests of another project, vendor them into a folder of this
repository; to trust another source is a change of the design.

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
| `operator-pods` | an object from which an operator makes a pod of its own template: a runner set (`actions.github.com`), a sandbox (`agents.x-k8s.io`, `extensions.agents.x-k8s.io`), and each object of the database operator and of its backup plugin (`postgresql.cnpg.io`, `barmancloud.cnpg.io`): a cluster, a pooler, a backup, a scheduled backup, an object store. The database folders are in the class (above), so no child outside it needs one |
| `takeover` | a change of an object whose owner labels name a child of the class |

For each request of the two controllers:

| Rule | What it checks |
| --- | --- |
| `kustomization-spec` | A Kustomization outside the class has a path in the form `a/b/c`, not in a class path and not above one (so not the root). It has no service account, no kubeconfig and no `commonMetadata` key of Flux. It substitutes from `cluster-settings`, `site-overrides` and the reduced Secret `cluster-secrets-apps` only |
| `kustomization-class` | A Kustomization of the class comes from one of its parents in git, with a path of the class |
| `helmrelease-class` | A HelmRelease of the class comes from a Kustomization of the class |
| `parent-objects` | A parent of the apps applies Kustomizations and the objects of its build in git only |

An update of a controller that keeps the spec of a Kustomization or a
HelmRelease (a finalizer, an annotation) passes the last three rules.

## Substitution

The class Kustomization `flux-substitution` makes the reduced Secret
`cluster-secrets-apps` on each site: the keys of `cluster-secrets` that the
children outside the class use, and no other. Flux fills its template from
`cluster-secrets`. In `kubernetes/clusters/<site>/apps.yaml` the parent of
the apps gives each child the reduced Secret, and a second patch gives the
full one to the direct children of the class, by a list of names that
`classlist.py write` keeps. The parent itself substitutes no key of
`cluster-secrets`, because its build reads the `ks.yaml` of the children
outside the class.

Flux substitutes in strict mode (below), so a variable of a child with no
key and no default stops the build of that child. So before this merge the
owner runs
`python3 kubernetes/apps/flux-system/admission/substitution_names.py` (with
PyYAML and the key of the files). It decrypts each encrypted file of a child
outside the class in memory and prints only its path and the names of its
variables that are neither keys of the reduced Secret of its site nor
settings of the site nor inline substitutes of the child. It reads each
object and each form of a variable as the CI test below does, with the same
functions of `classlist.py`; a form that Flux can not read prints as one
fixed marker, with no text of the file. It prints, writes and logs no
value. An empty output (exit 0) means the switch stops no build;
a name in the output goes into the template of the reduced Secret of that
site first.

Each site has a template of its own, because the two `cluster-secrets` do
not hold the same keys:

| Site (`kubernetes/clusters/<site>`) | Kustomization file | Template |
| --- | --- | --- |
| `wsl` | `kubernetes/apps/flux-system/substitution/ks.yaml` | `kubernetes/apps/flux-system/substitution/app/secret.yaml` |
| `wsl-02` | `kubernetes/clusters/wsl-02/apps/flux-substitution-ks.yaml` | `kubernetes/clusters/wsl-02/apps/flux-substitution/secret.yaml` |

Flux substitutes in strict mode: it is the default of kustomize-controller
since v1.9, and no flag of ours sets it. A bare `${VAR}` whose key is
missing stops the build of the whole Kustomization (`BuildFailed`, "variable
not set (strict mode)"). A key with an empty value is not missing: the
variable becomes an empty string. So a template names only the keys of the
`cluster-secrets` of its site, and gets no empty default for a key that the
site lacks: with one, a child outside the class would get an empty value
where today its build stops.

When a child outside the class starts to use a key of `cluster-secrets`, add
the key to the template of each site that runs the child (a change of the
class). The CI tests fail until each variable of a template is a key of the
`cluster-secrets` of its site (the key names of
`kubernetes/clusters/<site>/cluster-secrets.sops.yaml`, read with no value),
and each variable of a child outside the class is a key of the template of
its site or a setting of the site.

A third CI test covers each Kustomization of each site that substitutes, in
the class or outside it (`test_each_variable_of_each_site_has_a_definition_in_that_site`).
It takes the `postBuild` of the child with the patches of its parent, and
checks each variable of its manifests, and of the patches of each
`kustomization.yaml` that its build reads, against the sources that it
names on that site. The sources are its inline `substitute`, and the key
names of each ConfigMap and Secret of `flux-system` that git makes there:
the objects of the walk, and the SOPS files that a kustomization lists, by
key name only. A source that the test can not read fails the test, unless
it is `optional`, like `site-overrides`.

The forms of a variable are the ones of the envsubst of Flux
(`fluxcd/pkg/envsubst` v1.9.0, in kustomize-controller v1.10.1), as a lab of
that library in strict mode shows:

- `${NAME}` and each form that reads the value (`/`, `:`, `#`, `%`, `^`,
  `,` after the name, or `#` before it) stop the build when the name has no
  definition.
- `:=`, `:-`, `:+`, `:?` and `=` after the name are a default, and do not
  stop it. A variable inside the default still counts.
- `-`, `+` or `?` alone after the name, a `${` with no name and a `${` with
  no closing brace stop the build on each site, so the test fails on them.
- `$${NAME}` is an escape.
- A variable in a YAML comment does not stop the build, and the test skips
  it: the controller substitutes each object of the build after a round
  trip through JSON, which drops each comment. A line of a block string
  that starts with `#` is text, not a comment, so a variable there counts.

Its limits:

- It reads only the objects that `classlist.py` builds. It does not read
  what a `configMapGenerator` or a `secretGenerator` makes, or a SOPS file
  that no kustomization lists. A source of that kind fails the test with a
  message that says so.
- It checks names, not values: a key with an empty or wrong value passes.
- It reads a patch target of the parent by group, version, kind, name,
  namespace or label selector, and a patch with no target by the kind and
  name of the object that it names. Another form fails the test.
- A patch of a `kustomization.yaml` that sets `postBuild` fails the test,
  because the walk does not apply it.

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
2. After one reconcile cycle on both sites, the owner reads the result
   (below), with the positive control of each site. A reconcile applies the
   output of a chart only at a Helm install or upgrade, so the read also
   needs a replay of the chart output of both sites, rendered from git, on
   the throwaway API server of the tests: no refusal, or only objects of
   apps that then join the class.
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

A second replay on 2026-11-21 sent the full output of git for each site,
rendered as the CI diff job renders it (each Kustomization and the templates
and hooks of each chart: 1661 objects of site 1 with its 85 HelmReleases,
1013 of site 2 with its 50), and found the same objects, no other. Three
CRDs of site 1 carry the owner labels of a HelmRelease that no longer exists
(`tools/node-feature-discovery`, `networking/external-dns`). No controller
applies them, so no request comes. The CRDs of the `crds` folder of a chart
are not in a render; Helm applies them at a first install, and at an
upgrade only for a HelmRelease with `crds: Create` or `CreateReplace`, and
each of those is in the class on both sites. The folders of the four apps of
the table are in the class (`CLASS_PATHS` and the list of Owner label), so
the rules refuse no object of either replay.

A third replay on 2026-11-21 sent the render of git of each site, in the
same way, to the policy with the database folders in the class: 1668
objects of site 1 and 1020 of site 2, and the rules refuse none. The keys
of the reduced Secret stay the same: each of the eight is still a variable
of a child outside the class.

## Read the audit result

On site 1:

```bash
kubectl get validatingadmissionpolicy flux-child-scope -o jsonpath='{.status}'
kubectl -n flux-system logs deploy/kustomize-controller --since=2h | grep -c 'flux-child-scope/'
kubectl -n flux-system logs deploy/helm-controller --since=2h | grep -c 'flux-child-scope/'
```

On both sites, through the Thanos query of site 1. The API server counts a
check of a policy only when a validation refuses: it adds one to
`apiserver_validating_admission_policy_check_total` for each action of the
binding ("audit", "warn" or "deny") and each rule that fails. A check that
allows adds nothing, and the series appears at the first refusal (the test
`test_8_the_count_has_refusals_only` shows it at the version of the
clusters). The scrape of the API server keeps, on each site, only the series
of this policy with `enforcement_action` "audit" or "deny" (and, on site 1,
the series of the policies of NFS volumes and of the backup mover); the
"warn" series and the duration series are dropped. So no series is the
expected result, and it proves nothing alone. Read it in three steps:

1. **The window.** From the merge of the policy (or the last read) to now,
   no series of this policy on either site. A restart of the API server
   resets the count, so read the highest value in the window:

   ```promql
   sum by (cluster, enforcement_action) (
     max_over_time(apiserver_validating_admission_policy_check_total{policy="flux-child-scope"}[1d])
   )
   ```

2. **The positive control.** The owner runs the live check (below) on each
   site. It sends 7 test objects as the two controllers, and each one fails
   at least one rule. After two scrapes, the query above shows a series with
   "audit" of at least 7 for each `cluster` (with `Deny` and `Audit`: "deny"
   and "audit", at least 7 each). That proves that the policy evaluates the
   requests of the controllers on that site and that the series reaches
   Thanos. With no series after the run, the read is void: find the break
   first.
3. **After the control.** The count of each site stays at the value of the
   control for the rest of the window. Any rise is a request of a Flux
   child that the policy refuses.

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
`media` and `production`. It sends one more privileged pod in `production`
as `helm-controller`, with the owner labels of a HelmRelease outside the
class. Each object adds to the count of the site, so the run is the positive
control of the read (above). The API server stores nothing. With `Audit` and
`Warn` each object passes with a warning. With `Deny` each one is refused.
The owner runs it with a kubeconfig that may impersonate:

```bash
bin/flux-admission-check                                         # site 1
bin/flux-admission-check --context <site 2> --parent flux-system/cluster-apps-wsl-02
```

The two test objects of decryption (a SOPS file of another folder, by a
relative path and by a link) are content of git. Owner label holds them.

## The daily check of admission objects

The API server runs no admission policy on a webhook configuration, an
admission policy or a binding, so the policy can not refuse one. The CronJob
`flux-system/flux-admission-objects` (05:41 UTC, both sites) lists each of
them, reads the owner labels of Flux, and fails when one names a
Kustomization or a HelmRelease outside the class. It takes the class from the
live policy, and its account may only get and list admission objects. A
failure raises `KubeJobFailed`; on site 1 the AI board shows the CronJob on
its Checks tab. On 2026-11-21 site 1 had 25 admission objects, each owned by
a child of the class. To read a failure:
`kubectl -n flux-system logs job/<job> | grep 'flux-admission-objects kind='`.

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
  object or an admission object, a remote source, a path out of its folder,
  or a link ([`ci.md`](ci.md), "Agent pull requests"). The daily check
  `flux-admission-objects` lists each admission object that a child outside
  the class owns. The Flux lockdown closes it on the cluster.
- **Pods of other operators.** The rule `operator-pods` holds the operators
  of the clusters today. A pod that another operator makes from a resource
  of its own is not in a request of Flux: a new operator needs a line in
  that rule.
- **Decryption.** The children outside the class still decrypt with the main
  key. The design asks for a key of their own; that needs a new key and a new
  encryption of their files, which is work of the owner. Until then Owner
  label holds a pull request of the App that touches an encrypted file (by
  its name or by its content), a path out of its folder or a link. These are
  the three ways to reach an encrypted file of the class: a path, a link,
  and a copy of its content under another name.
- **Deletes.** The policy checks create and update. A delete of a
  controller (the garbage collection of a child, the uninstall of a Helm
  release) is not checked. The Flux lockdown closes it.
- **Reach at run time.** What the apps outside the class reach in their own
  namespaces (their Secrets, service accounts and egress) stays open. So
  does a Service with `externalIPs`, or an Ingress or a certificate that
  claims a host name of the class, from a namespace outside it.
- **Fields for a later step.** `kustomization-spec` does not yet refuse
  `spec.decryption` or a `spec.components` entry with `..`, and
  `helmrelease-spec` does not yet refuse `install.createNamespace` (a new
  empty namespace with no owner label). Owner label holds each Flux object
  of a pull request of the App until then.
