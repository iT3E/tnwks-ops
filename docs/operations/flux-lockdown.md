# Flux lockdown

The Flux lockdown closes two gaps that the admission policy
[`flux-child-scope`](flux-admission.md) leaves: it can not see an admission
object, and a child outside the "ask first" class decrypts with the main
key. Two controls close them:

- **Accounts.** The flag `--default-service-account` of kustomize-controller
  (helm-controller does not get it, "The flag"). The root and each Kustomization of the class run as
  `kustomize-controller`. Each child outside the class runs as an account
  of its own with the role `flux-child` in the namespaces where it applies
  objects, and each HelmRelease outside the class as an account in its own
  namespace with that role (below).
- **Keys.** The class decrypts with a class key (Secret `sops-age-class`),
  the files outside the class with an apps key (Secret `sops-age-apps`).

The steps: 0 the proofs and tests (this page); 1 the new public keys in
`.sops.yaml`; 2a the accounts; 2b the class runs as `kustomize-controller`;
2c the children outside the class name their accounts; 2d the decryption
Secrets (the root last, in 2d-root); 2e the flag; 3 the old recipients leave `.sops.yaml`; then the old
Secret `sops-age` goes. Steps 1 and 3 are for the whole repository, step 2
goes site by site, site 2 first.

- The proofs: [`kubernetes/apps/flux-system/lockdown/tests/run.sh`](../../kubernetes/apps/flux-system/lockdown/tests/run.sh)
  runs the kustomize-controller of git on a throwaway API server.
- The tests of git: [`observability/tests/test_flux_lockdown.py`](../../observability/tests/test_flux_lockdown.py),
  with the reads of [`lockdown.py`](../../kubernetes/apps/flux-system/lockdown/lockdown.py).
- The cases of the policy: [`admission/tests`](../../kubernetes/apps/flux-system/admission/tests/test_flux_admission.py).

## What the proofs show

The controller runs with the image and the arguments of git, as the
account `flux-system/kustomize-controller` bound to `cluster-admin`, as on
the clusters. A probe policy refuses each ConfigMap of a test namespace
with the name of the user of the request, so the status of a Kustomization
shows the user that the controller applies as.

| Proof | Result | What it means for the steps |
| --- | --- | --- |
| A Kustomization with no account, and one that names `kustomize-controller` | Both apply as `system:serviceaccount:flux-system:kustomize-controller`, with its rights (a ClusterRole applies) | 2b changes nothing: the user and the rights stay, and the policy matches that user as before |
| A parent that names `kustomize-controller` in one of its children by a patch, as the parent of the apps does in 2b | That child has the account in its spec and applies as `kustomize-controller`; its sibling with no account applies as the same user | The patch of the parent reaches its children of the class, and the user does not change |
| A Kustomization that names an account of its own | It applies as that account | The policy matches the two controllers by user name, so it does not see that account. Before 2c, the policy must match the accounts of the children, and its rule `kustomization-spec` must allow an account of the child |
| Which identity reads the decryption Secret | The controller, as itself. A child account with no right on Secrets of `flux-system` builds with `sops-age-apps` and with `sops-age-class`, and a missing Secret reads "not found", not "forbidden" | An account outside the class needs no `get` on the apps Secret. An account does not hold the class key back, so the policy refuses `sops-age-class` in a Kustomization outside the class (below) |
| A Kustomization that comes to name a missing account | Its apply fails ("forbidden"), its inventory stays, and no object is pruned. A delete of it removes no object | A missing account in 2c or 2e stalls one child and keeps its running objects. Do not delete such a child to fix it: its objects stay with no owner |
| A Kustomization whose account has only the role `flux-child` of git | It applies an object of each kind of the role (12 objects). A Role or a Pod fails its apply ("cannot create") | The role is enough for the builds outside the class, and it grants no more than its kinds |
| The same account with `wait: true`, on a Deployment and a StatefulSet | It is Ready. With the role less its reads of ReplicaSets and Pods, the wait fails ("replicasets.apps is forbidden") | Both controllers read the ReplicaSets and Pods of a workload as the account when they wait for its health, so the role must read them |
| Each side with its own key (step 2d), with age keys made for the run and Secrets of placeholders that sops encrypts to them | A child of the class with `sops-age-class` builds a file of the class key. A child outside the class, with the role `flux-child`, builds a file of the apps key with `sops-age-apps`. With `sops-age-class` it can not decrypt that file ("decryption failed"), and applies nothing | Each file opens only with the Secret of its side. With the policy, which refuses `sops-age-class` outside the class, a child outside the class can not use the class key |
| The flag `--default-service-account=default`, after a restart with the arguments of git of a flagged site | A Kustomization with no account applies as `flux-system/default`. One that applied before the flag and names no account fails and prunes nothing. The ones that name an account apply as before: the parents that name `kustomize-controller` and their children, the own accounts of `accounts.py`, the role `flux-child` and both keys of 2d | 2e merges only when each object of the site names an account (the test below) |

Two reads of the source of the controllers of git add to this:

- kustomize-controller v1.10.1 also reads the sources of
  `postBuild.substituteFrom` as itself (`generator.SubstituteVariables` with
  the client of the controller), so the rule of the policy on substitution
  stays after the lockdown.
- helm-controller v1.7.1 acts as the account of a HelmRelease in the
  namespace of the HelmRelease (`kube.WithImpersonate(obj.Spec.ServiceAccountName,
  obj.GetNamespace())`). The account `helm-controller` is in `flux-system`,
  so a HelmRelease of the class in another namespace can not name it.
  So the flag on helm-controller would need an account in the namespace
  of each HelmRelease of the class, with the rights of its chart, which
  is why helm-controller does not get it ("The flag").

## The policy

The rule `kustomization-spec` refuses a Kustomization outside the class
that decrypts with `sops-age-class`. No child names that Secret before 2d,
so the rule changes nothing at its merge. The cases of the harness:

- a child outside the class that names `kustomize-controller`, or
  `sops-age-class`, is refused;
- a child outside the class with `sops-age-apps` passes;
- a Kustomization of the class, and the parent of the apps from the root,
  with `kustomize-controller` and `sops-age-class`, pass;
- a HelmRelease of the class with an account of its namespace passes.

The policy also matches the accounts of the children outside the class
(step 2c0, before any child names one):

- `flux-system/flux-child-<name>`, the account of the Kustomization
  `flux-system/<name>`, and `<namespace>/flux-release-<name>`, the account
  of the HelmRelease `<namespace>/<name>`. Each request of such an account
  is a request of that child, outside the class, so each rule of the
  children outside the class applies to it.
- A Kustomization outside the class may name only its own account, and a
  HelmRelease outside the class only its own.
- A HelmRelease that the account of a child applies must name its own
  account (`helmrelease-spec`). One with none would run as helm-controller,
  which has cluster-admin, and the chart could read any object of the
  cluster. The rule holds where the children apply as their accounts: on
  site 2 since its step 2c. On site 1 the children still apply as
  kustomize-controller and name no account, so a missing account passes
  there until the 2c of site 1. The CI test "a HelmRelease of a child with
  its own account" holds git to it. This, not the flag on helm-controller,
  is what holds the HelmReleases outside the class to their accounts.
- The account of a HelmRelease may write the storage of its own release,
  in its own namespace: the Secret `sh.helm.release.v1.<its name>.v<n>`,
  where `<n>` is digits only. No account of a child writes a token Secret.
  Token requests, exec and eviction are subresources, which the policy does
  not match: RBAC holds them, and the role of the accounts grants none.
- No pod of any user runs as such an account (the policy
  `flux-child-pods`).
- No Kustomization or HelmRelease of the class, and no pod spec of git,
  names such an account (a CI test).

## The accounts

Step 2a adds the accounts and their role, site by site, site 2 first. No
child names one until step 2c.

- The role `flux-child`
  ([`accounts/role/clusterrole.yaml`](../../kubernetes/apps/flux-system/accounts/role/clusterrole.yaml))
  holds the kinds that the children outside the class apply: ConfigMaps,
  Secrets, Services, volume claims, Deployments, StatefulSets, CronJobs,
  Ingresses, network policies, the rules and monitors of Prometheus, and
  HelmReleases. It reads ReplicaSets and Pods, because both controllers
  read them as the account when they wait for the health of a Deployment
  or a StatefulSet. It has no pod writes, no impersonation, no service
  account tokens, no RBAC and no other Flux kind.
- For each Kustomization outside the class, the account
  `flux-system/flux-child-<name>`, with a RoleBinding to `flux-child` in
  each namespace where its build applies objects, its SOPS files too (SOPS
  leaves the namespace of their metadata in the clear).
- For each HelmRelease outside the class, the account
  `<namespace>/flux-release-<name>`, with a RoleBinding to `flux-child`
  there.
- No account mounts a token, and the account `default` of `flux-system`
  gets no binding.

[`lockdown/accounts.py`](../../kubernetes/apps/flux-system/lockdown/accounts.py)
writes `kubernetes/apps/flux-system/accounts/<site>/accounts.yaml` from git
for each site in its `SITES`. Run
`python3 kubernetes/apps/flux-system/lockdown/accounts.py write` after a
change of the children outside the class. The Kustomization
`flux-accounts` of the site (`accounts/ks-<site>.yaml`) applies them.

## The class account

Step 2b, site by site: the class names `kustomize-controller`. Without the
flag of 2e that account is the user that Flux applies as today, with the
same rights, so nothing changes on the site. After 2e, a Kustomization of
the class with no account would run as `default` and fail.

On a site, three places name it, all in the files of that site:

- the root `flux-system`: a patch of the Flux install
  (`kubernetes/clusters/<site>/flux-system/kustomization.yaml`);
- the two children of the root, the parent of the apps and the meta
  Kustomization: `spec.serviceAccountName` in their files
  (`apps.yaml`, `meta.yaml`);
- each direct child of the class of the parent of the apps: a patch of
  that parent that targets their names. `classlist.py write` writes the
  names (the same generated list as the patch of `cluster-secrets`), and
  the patch has no label selector, so it also reaches the children that
  turn off their substitution.

The ks.yaml files of the apps are shared by both sites, so 2b changes none
of them. The test "The class account" holds it: once the root of a site
names the account, each Kustomization of the class does too.

Site 2 has it first. Site 1 follows with the same three places in
`kubernetes/clusters/wsl/`.

## The own accounts

Step 2c, site by site: each child outside the class names its own account
of 2a, so it applies as that account, with the role `flux-child` in its
own namespaces.

- Each Kustomization outside the class: `spec.serviceAccountName:
  flux-child-<name>`.
- Each HelmRelease of its build: `spec.serviceAccountName:
  flux-release-<name>`, by a patch of that Kustomization (`spec.patches`).
  helm-controller then installs and upgrades the release as that account,
  and writes the release storage as it (the policy allows its own storage
  only).

The ks.yaml and HelmRelease files of the apps are shared by both sites, so
the accounts go in the files of the site: a patch of the parent of the apps
for each child (`kubernetes/clusters/<site>/apps.yaml`, between the markers
of `accounts.py`). `accounts.py write` writes those patches for each site
in its `NAMED`, and `accounts.py check` fails until they are written.
`lockdown.py` reads them, as Flux applies them.

A patch of the parent sets the whole `spec.patches` list of the child (a
strategic merge of a list replaces it). So `accounts.py` reads each child
as the build of its parent makes it, with the patches of its site, and
writes the patches that the child has first, unchanged, then the account
patches. On site 2 that keeps the wait patch of the media apps
(`install.disableWait` and `upgrade.disableWait` for sonarr, radarr,
prowlarr and recyclarr). The wait patch stays in one place, the
kustomization.yaml of the apps of the site, and `accounts.py check` fails
when the two differ.

The proofs:

- the controller harness applies the patches that `accounts.py` writes: each
  child names its own account and applies as it, with the role of git, and
  its HelmRelease names its own account and keeps the patch that its site
  gave it before;
- the admission harness sends each Kustomization and HelmRelease of git as
  the user that it applies as, and each other object of each child that
  names its account (its build, and the Secrets of its SOPS files by name
  and type) as that account: the policy refuses none;
- the CI test "own account": a child outside the class names no account
  but its own, and on a site of `NAMED` each one names it;
- the CI test "only the accounts": the spec that Flux applies to each child
  outside the class is the spec without the patches of `accounts.py`, but
  for its account and the account patches after the patches it had.

Site 2 has it first. A revert of the pull request puts the children back
on `kustomize-controller`, as before.

## The decryption Secrets

Step 2d, site by site: each Kustomization that decrypts names the Secret
of its side.

- The parent of the apps and each child of the class: `sops-age-class`.
- Each child outside the class: `sops-age-apps`.
- The root: `sops-age-class` too, in step 2d-root (below).
- The Secret `sops-age` stays on the site until step 3 has soaked, so a
  revert decrypts again.

The files of the site set them: the spec of the parent of the apps, and its
two patches of substitution. The first patch gives each child the apps key;
the second, for the children of the class only, gives the class key after
it. The ks.yaml files that both sites share do not change. `KEYED` in
`lockdown.py` lists the sites of step 2d.

The root goes last, in its own pull request (step 2d-root): the patch of
the root in `kubernetes/clusters/<site>/flux-system/kustomization.yaml`
names `sops-age-class`, and `KEYED_ROOTS` in `lockdown.py` lists the site.
No read can show that the private key in `sops-age-class` matches its public
key in `KEYS`, and a root that can not decrypt its own build can not apply
the revert either. So 2d-root merges only once the children of the class
decrypt with the class key on the site (each one Ready at the merge commit
of 2d). If it still fails, use the break-glass "The root can not decrypt".
`lockdown.py list <site>` names them: the rows that start with `class`, name
`sops-age-class`, and build one or more encrypted files (the fourth column).

Before the merge, the map test shows that each encrypted file that a
Kustomization builds holds the public key of the Secret that it names (the
recipients of phase 1). Both Secrets must be on the site, with the key
`age.agekey` (the command in "Break-glass").

## The flag

Step 2e, site by site: kustomize-controller gets
`--default-service-account=default`, by a patch of the Flux install in
`kubernetes/clusters/<site>/flux-system/kustomization.yaml`. A Kustomization
that names no account then runs as `flux-system/default`, which no binding
of git names, so it applies nothing. Its apply fails and prunes nothing.
`FLAGGED` in `lockdown.py` lists the sites.

- Each Kustomization of the site names an account before the flag: the
  root, the parent of the apps, the meta Kustomization and each child of
  the class name `kustomize-controller` (2b), and each child outside the
  class names `flux-child-<name>` (2c). The CI test "the flag" holds it.
- The Kustomizations that name an account apply as before (the harness,
  Flag test 4), so the merge changes no object of the site.
- helm-controller does not get the flag. It acts as the account of a
  HelmRelease in the namespace of the HelmRelease, and the HelmReleases of
  the class name none: under the flag they would run as `default` of their
  namespace and fail. Each would need an account with the rights of its
  chart in its own namespace, which for most charts of the class means
  cluster rights, in 31 namespaces on site 2. Anyone who can make a pod in
  a namespace gets the rights of its accounts, so that is more paths to
  cluster-admin, not fewer. The HelmReleases outside the class already
  run as their own accounts (2c).

`lockdown.py list <site>` prints the account that git gives each
Kustomization, for the reads before and after the merge.

## The tests of git

`observability/tests/test_flux_lockdown.py` reads each Kustomization and
HelmRelease of each site as Flux applies it: the spec with the patches of
its parent, and for the root the patches of
`kubernetes/clusters/<site>/flux-system/kustomization.yaml`.

| Test | What it checks | Step |
| --- | --- | --- |
| The map | Each encrypted file that a Kustomization of a site builds holds the public key of the Secret that it names (`KEYS` in `lockdown.py`, by site) | 1, 2d, 3 |
| The keys of the map | Each is an age public key and a recipient of a rule of `kubernetes/` in `.sops.yaml` | 1 |
| The rules of the keys | The rule of `.sops.yaml` that sops takes for each encrypted file under `kubernetes/` gives a file of the class the class key of each site that builds it, and a file outside the class the apps key of each site that builds it. No file of the class gets an apps key, and no file outside the class a class key. A new file in a folder of the class or of a child outside it gets the same | 1 |
| The keyed site | On a site of `lockdown.KEYED`, each Kustomization that names a decryption Secret, or builds an encrypted file, names `sops-age-class` in the class and `sops-age-apps` outside it, and none names `sops-age`. The root names `sops-age-class` on a site of `lockdown.KEYED_ROOTS`, and `sops-age` on another. On a site not keyed, none names the new Secrets | 2d, 2d-root |
| The class account | Once the root of a site names `kustomize-controller`, `cluster-apps` and each Kustomization of the class do too, and no child of the class names it before the root does. No child outside the class names an account of the controllers | 2b |
| The own account | A Kustomization or a HelmRelease outside the class names no account but its own (`flux-child-<name>`, `flux-release-<name>`); on a site of `accounts.NAMED` each one names its own, as `accounts.py write` writes the patches of the parent | 2c |
| Only the accounts | The spec that Flux applies to each child outside the class, with the patches of `accounts.py`, is the spec without them, but for its account and the patches that give its HelmReleases theirs, after the patches that it had (of its file and of its site) | 2c |
| The flag | A controller of a site has `--default-service-account` only when each Kustomization (kustomize-controller) or each HelmRelease (helm-controller) of the site names an account. Each account that an object names is a ServiceAccount of git in the namespace of the object | 2a, 2c, 2e |
| The flag of a flagged site | kustomize-controller of a site of `lockdown.FLAGGED` has `--default-service-account=default`, and of another site none. helm-controller has none | 2e |
| The list | `lockdown.py list <site>` prints a row for each Kustomization: side, account, decryption Secret, encrypted files, name | 2d, 2e |
| The flags that stay off | No `--sops-age-secret`, `--default-decryption-service-account` or `--no-cross-namespace-refs` | all |
| The accounts | `accounts.yaml` of each site is what `accounts.py` writes: an account for each child outside the class, with its bindings. No token is mounted | 2a |
| The role | `flux-child` has the kinds of the builds outside the class and of the charts of their HelmReleases, and nothing else | 2a |
| The default account | No binding of git names `flux-system/default`, and `flux-child` is bound to the accounts of the children only | 2a, 2e |
| No other patch | No patch of a `kustomization.yaml` sets the account or the decryption of a Flux object where `lockdown.py` does not read it | all |

The map holds the public keys only: git never holds a private key, and the
test reads the public recipients of the sops block of each file, no value.
Step 1 adds the class key and the apps key of each site to `KEYS` and to
`.sops.yaml`; step 3 removes `sops-age`.

In `.sops.yaml`, the general rule of `kubernetes/` is for the class, so it
gets the class keys of both sites. Two rules come before it:

- the folders of the apps outside the class
  (`kubernetes/apps/{cache,default,home-automation,media,production,selfhosted}/`)
  get the apps keys of both sites, and no class key;
- the folders of the class inside those namespaces (the entries of
  `CLASS_PATHS` there: the four apps that need a right of the class, and
  the database folders) come first again, with the class keys.

The rule of `kubernetes/clusters/wsl-02/` gets the class key of site 2.
After the merge, the owner runs `sops updatekeys` over the encrypted files
under `kubernetes/`, so each file holds the keys of its rule. Until then
nothing reads the new keys: each Kustomization still names `sops-age`.

## The flags on the Flux of git

Flux v2.10.0, from `gotk-components.yaml` of both sites:

| Flag | kustomize-controller v1.10.1 | helm-controller v1.7.1 |
| --- | --- | --- |
| `--default-service-account` | yes | yes |
| `--no-cross-namespace-refs` | yes | yes |
| `--sops-age-secret` | yes | no |
| `--default-decryption-service-account` | yes | no |

The install of git has no source-watcher: no Deployment and no account,
only a subject in the ClusterRoleBinding `crd-controller-flux-system`.
Step 2e puts the flag on kustomize-controller only. helm-controller does
not get it: under the flag, each HelmRelease of the class would need an
account with cluster rights in its own namespace (31 namespaces on site
2), and anyone who can make a pod in a namespace gets the rights of its
accounts. That is more paths to cluster-admin, not fewer (see "The flag").

## Break-glass

A failed apply prunes nothing, so a child that stalls keeps its running
objects. The way back of each step is a revert of its pull request; Flux
applies the revert. The owner acts by hand only when Flux can not apply the
revert. An agent never suspends or resumes a Kustomization.

`<site>` is the kube context of the site. Name it on each line: the default
context of the workstation is site 1.

| | Site 1 | Site 2 |
| --- | --- | --- |
| Tree | `kubernetes/clusters/wsl` | `kubernetes/clusters/wsl-02` |
| Root | `flux-system/flux-system` | `flux-system/flux-system` |
| Parent of the apps | `flux-system/cluster-apps` | `flux-system/cluster-apps-wsl-02` |
| Where the owner acts | the workstation | the distro of site 2 |

**A child or a HelmRelease fails after 2a, 2b, 2c or 2d.** Revert the pull
request, then ask for the reconcile of the parent:

```bash
flux --context <site> reconcile kustomization <parent of the apps> -n flux-system --with-source
```

**One child fails after 2e** ("system:serviceaccount:flux-system:default"
in its Ready message): it names no account. Revert the pull request, or
add the account to it in git. Do not delete it: its objects would stay
with no owner.

**Many children fail after 2e.** Revert the pull request of the flag. The
root applies the Deployments of Flux as `kustomize-controller` (2b), so the
revert goes through. If the root itself fails:

1. `flux --context <site> suspend kustomization flux-system -n flux-system`
2. `kubectl --context <site> -n flux-system edit deployment kustomize-controller`
   (and `helm-controller`, if it has the flag): remove the argument
   `--default-service-account=default`. The controller restarts without it.
3. After the revert is on `main`:
   `flux --context <site> resume kustomization flux-system -n flux-system`.

**A build can not decrypt after 2d.** Check that both Secrets are on the
site. The default table of kubectl comes from the API server as metadata
only, so no data reaches the client: each row must show `Opaque` and 1 key
(the key name `age.agekey` was checked when the Secret was made):

```bash
kubectl --context <site> -n flux-system get secret sops-age-class sops-age-apps
```

Then revert 2d. The old Secret `sops-age` stays on each site until both
sites have soaked after step 3, so a revert of 2d decrypts again.

**The root can not decrypt after 2d-root.** The root fails its own build,
so it can not apply the revert. Revert the pull request of 2d-root, then:

1. `flux --context <site> suspend kustomization flux-system -n flux-system`
2. Put the root back on the old Secret:
   ```bash
   kubectl --context <site> -n flux-system patch kustomization flux-system --type merge \
     -p '{"spec":{"decryption":{"secretRef":{"name":"sops-age"}}}}'
   ```
3. After the revert is on `main`:
   `flux --context <site> resume kustomization flux-system -n flux-system`.
   The root then applies the revert with `sops-age`, and the parent and the
   children follow.

**The policy refuses a child of the class.** Use the break-glass of
[`flux-admission.md`](flux-admission.md#break-glass).

**A child names a missing account.** Revert, or add the account. Do not
delete the Kustomization: a delete leaves its objects with no owner.
