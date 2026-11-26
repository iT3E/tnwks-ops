# Flux lockdown

The Flux lockdown closes two gaps that the admission policy
[`flux-child-scope`](flux-admission.md) leaves: it can not see an admission
object, and a child outside the "ask first" class decrypts with the main
key. Two controls close them:

- **Accounts.** The flag `--default-service-account` of the Flux
  controllers. The root and each Kustomization of the class run as
  `kustomize-controller`. Each child outside the class runs as an account
  of its own with `admin` in its own namespace, and each HelmRelease
  outside the class as an account in its own namespace.
- **Keys.** The class decrypts with a class key (Secret `sops-age-class`),
  the files outside the class with an apps key (Secret `sops-age-apps`).

The steps: 0 the proofs and tests (this page); 1 the new public keys in
`.sops.yaml`; 2a the accounts; 2b the class runs as `kustomize-controller`;
2c the children outside the class name their accounts; 2d the decryption
Secrets; 2e the flag; 3 the old recipients leave `.sops.yaml`; then the old
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
| A Kustomization that names an account of its own | It applies as that account | The policy matches the two controllers by user name, so it does not see that account. Before 2c, the policy must match the accounts of the children, and its rule `kustomization-spec` must allow an account of the child |
| Which identity reads the decryption Secret | The controller, as itself. A child account with no right on Secrets of `flux-system` builds with `sops-age-apps` and with `sops-age-class`, and a missing Secret reads "not found", not "forbidden" | An account outside the class needs no `get` on the apps Secret. An account does not hold the class key back, so the policy refuses `sops-age-class` in a Kustomization outside the class (below) |
| A Kustomization that comes to name a missing account | Its apply fails ("forbidden"), its inventory stays, and no object is pruned. A delete of it removes no object | A missing account in 2c or 2e stalls one child and keeps its running objects. Do not delete such a child to fix it: its objects stay with no owner |
| The flag `--default-service-account=default`, after a restart | A Kustomization with no account applies as `flux-system/default`. One that applied before the flag and names no account fails and prunes nothing. The ones that name an account apply as before | 2e merges only when each object of the site names an account (the test below) |

Two reads of the source of the controllers of git add to this:

- kustomize-controller v1.10.1 also reads the sources of
  `postBuild.substituteFrom` as itself (`generator.SubstituteVariables` with
  the client of the controller), so the rule of the policy on substitution
  stays after the lockdown.
- helm-controller v1.7.1 acts as the account of a HelmRelease in the
  namespace of the HelmRelease (`kube.WithImpersonate(obj.Spec.ServiceAccountName,
  obj.GetNamespace())`). The account `helm-controller` is in `flux-system`,
  so a HelmRelease of the class in another namespace can not name it.
  Before the flag goes on helm-controller, each HelmRelease of the class
  needs an account in its own namespace with the rights of its chart.

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
- The account of a HelmRelease may write the storage of its own release,
  in its own namespace: the Secret `sh.helm.release.v1.<its name>.v<n>`,
  where `<n>` is digits only. No account of a child writes a token Secret.
  Token requests, exec and eviction are subresources, which the policy does
  not match: RBAC holds them, and the role of the accounts grants none.
- No pod of any user runs as such an account (the policy
  `flux-child-pods`).
- No Kustomization or HelmRelease of the class, and no pod spec of git,
  names such an account (a CI test).

## The tests of git

`observability/tests/test_flux_lockdown.py` reads each Kustomization and
HelmRelease of each site as Flux applies it: the spec with the patches of
its parent, and for the root the patches of
`kubernetes/clusters/<site>/flux-system/kustomization.yaml`.

| Test | What it checks | Step |
| --- | --- | --- |
| The map | Each encrypted file that a Kustomization of a site builds holds the public key of the Secret that it names (`KEYS` in `lockdown.py`, by site) | 1, 2d, 3 |
| The keys of the map | Each is an age public key and a recipient of a rule of `kubernetes/` in `.sops.yaml` | 1 |
| The class account | Once the root of a site names `kustomize-controller`, `cluster-apps` and each Kustomization of the class do too. No child outside the class names an account of the controllers | 2b |
| The flag | A controller of a site has `--default-service-account` only when each Kustomization (kustomize-controller) or each HelmRelease (helm-controller) of the site names an account. Each account that an object names is a ServiceAccount of git in the namespace of the object | 2a, 2c, 2e |
| The flags that stay off | No `--sops-age-secret`, `--default-decryption-service-account` or `--no-cross-namespace-refs` | all |
| No other patch | No patch of a `kustomization.yaml` sets the account or the decryption of a Flux object where `lockdown.py` does not read it | all |

The map holds the public keys only: git never holds a private key, and the
test reads the public recipients of the sops block of each file, no value.
Step 1 adds the class key and the apps key of each site to `KEYS` and to
`.sops.yaml`; step 3 removes `sops-age`.

## The flags on the Flux of git

Flux v2.10.0, from `gotk-components.yaml` of both sites:

| Flag | kustomize-controller v1.10.1 | helm-controller v1.7.1 |
| --- | --- | --- |
| `--default-service-account` | yes | yes |
| `--no-cross-namespace-refs` | yes | yes |
| `--sops-age-secret` | yes | no |
| `--default-decryption-service-account` | yes | no |

The install of git has no source-watcher: no Deployment and no account,
only a subject in the ClusterRoleBinding `crd-controller-flux-system`. So
the flag of 2e is for kustomize-controller and helm-controller.

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

**Many children fail after 2e.** Revert the pull request of the flag. The
root applies the Deployments of Flux as `kustomize-controller` (2b), so the
revert goes through. If the root itself fails:

1. `flux --context <site> suspend kustomization flux-system -n flux-system`
2. `kubectl --context <site> -n flux-system edit deployment kustomize-controller`
   (and `helm-controller`): remove the argument
   `--default-service-account=default`. The controller restarts without it.
3. After the revert is on `main`:
   `flux --context <site> resume kustomization flux-system -n flux-system`.

**A build can not decrypt after 2d.** Check that both Secrets are on the
site, by key name only (each must print `age.agekey`):

```bash
kubectl --context <site> -n flux-system get secret sops-age-class sops-age-apps \
  -o go-template='{{range .items}}{{.metadata.name}}{{" "}}{{range $k, $v := .data}}{{$k}}{{end}}{{"\n"}}{{end}}'
```

Then revert 2d. The old Secret `sops-age` stays on each site until both
sites have soaked after step 3, so a revert of 2d decrypts again.

**The policy refuses a child of the class.** Use the break-glass of
[`flux-admission.md`](flux-admission.md#break-glass).

**A child names a missing account.** Revert, or add the account. Do not
delete the Kustomization: a delete leaves its objects with no owner.
