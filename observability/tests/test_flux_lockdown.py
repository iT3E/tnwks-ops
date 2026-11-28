"""The Flux lockdown (kubernetes/apps/flux-system/lockdown,
docs/operations/flux-lockdown.md).

Each Kustomization and HelmRelease of a site runs as an account, and each
Kustomization decrypts with a key Secret of flux-system. These tests read
git the way Flux applies it (lockdown.py) and check:

- the map: each encrypted file that a Kustomization of a site builds holds
  the age public key (KEYS) of the Secret that the Kustomization names;
- the step of the class account (2b): once the root of a site runs as
  kustomize-controller, cluster-apps and each Kustomization of the class do
  too, and no child outside the class names an account of the controllers;
- the step of the flag (2e): a controller of a site runs with
  --default-service-account only when each Kustomization (for
  kustomize-controller) or each HelmRelease (for helm-controller) of that
  site names an account, and each account that an object names is in git;
- the flags that stay off;
- the accounts of the children outside the class (2a): written from git by
  accounts.py, each with its bindings, the role flux-child with the kinds
  of the builds outside the class and no more, and no binding of the
  account default of flux-system.

The last test runs the controller of git on a throwaway API server
(lockdown/tests/run.sh), with Docker: who reads the decryption Secret, who
applies, and what a missing account does.
"""

import os
import re
import subprocess
import sys

import pytest
import yaml
from test_hand_written_rules import ROOT, docker

LOCKDOWN = ROOT / "kubernetes/apps/flux-system/lockdown"
sys.path.insert(0, str(ROOT / "kubernetes/apps/flux-system/admission"))
sys.path.insert(0, str(LOCKDOWN))

import accounts
import classlist
import lockdown

SITES = classlist.SITES
CLASS_KEY = "sops-age-class"
APPS_KEY = "sops-age-apps"


def account(spec):
    return spec.get("serviceAccountName")


def secret_of(spec):
    return ((spec.get("decryption") or {}).get("secretRef") or {}).get("name")


@pytest.mark.parametrize("site", SITES)
def test_each_encrypted_file_holds_the_key_of_the_secret_that_decrypts_it(site):
    """A Kustomization that builds an encrypted file names a Secret of the
    map, and the file holds the public key of that Secret, so the build
    decrypts on that site. A change of a decryption Secret (2d) or of the
    recipients (phases 1 and 3) that breaks it fails here."""
    wrong = []
    for key, spec, files, _ in lockdown.kustomizations(site):
        secret = secret_of(spec)
        for f in sorted(files):
            if not secret:
                wrong.append(f"{key}: builds {f} and names no decryption Secret")
            elif secret not in lockdown.KEYS[site]:
                wrong.append(f"{key}: names {secret}, which has no key in lockdown.KEYS for {site}")
            elif lockdown.KEYS[site][secret] not in lockdown.recipients(f):
                wrong.append(f"{key}: {f} does not hold the key of {secret} ({site}): run sops updatekeys")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("site", SITES)
def test_a_keyed_site_decrypts_with_the_class_key_and_the_apps_key(site):
    """Step 2d, site by site: on a site of lockdown.KEYED, each Kustomization
    that names a decryption Secret, or builds an encrypted file, names the
    class key in the class and the apps key outside it, and none names the
    old sops-age, but the root until its site is in lockdown.KEYED_ROOTS
    (step 2d-root). On another site, none names the new Secrets yet. The
    map test above checks that each file holds the key of the Secret named."""
    assert set(lockdown.KEYED_ROOTS) <= set(lockdown.KEYED)
    wrong = []
    for key, spec, files, in_class in lockdown.kustomizations(site):
        secret = secret_of(spec)
        if key == "flux-system/flux-system":
            want = CLASS_KEY if site in lockdown.KEYED_ROOTS else "sops-age"
            if secret != want:
                wrong.append(f"{key}: names {secret}, not {want}")
        elif site in lockdown.KEYED:
            want = CLASS_KEY if in_class else APPS_KEY
            if (secret or files) and secret != want:
                wrong.append(f"{key}: names {secret}, not {want}")
        elif secret in (CLASS_KEY, APPS_KEY):
            wrong.append(f"{key}: names {secret} before step 2d of {site}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("site", SITES)
def test_the_map_finds_the_encrypted_files_of_the_site(site):
    """The map test reads files: the root builds the Secret of the cluster,
    and more than ten children build encrypted files."""
    found = {key: files for key, _, files, _ in lockdown.kustomizations(site) if files}
    assert f"kubernetes/clusters/{site}/cluster-secrets.sops.yaml" in found["flux-system/flux-system"]
    assert len(found) > 10, sorted(found)


def test_the_keys_of_the_map_are_public_keys_and_recipients_of_the_rules():
    """Each key of the map is an age public key (never a private key) and a
    recipient of a rule of kubernetes/ in .sops.yaml."""
    rules = yaml.safe_load((ROOT / ".sops.yaml").read_text())["creation_rules"]
    recipients = {r.strip() for rule in rules if rule["path_regex"].startswith("kubernetes/")
                  for r in rule.get("age", "").split(",")}
    for site, keys in lockdown.KEYS.items():
        assert site in SITES
        for secret, key in keys.items():
            assert lockdown.AGE_PUBLIC_KEY.fullmatch(key), (site, secret)
            assert key in recipients, (site, secret)


def first_rule(path, rules):
    """The rule of .sops.yaml that sops takes for a path: the first one
    whose path_regex matches it."""
    return next((r for r in rules if re.search(r["path_regex"], path)), None)


def test_the_rules_of_sops_give_the_class_and_the_apps_their_own_keys():
    """Each encrypted file under kubernetes/ gets, from its rule of
    .sops.yaml, the class key of each site that builds it in the class, or
    the apps key of each site that builds it outside; never an apps key on
    a file of the class, and never a class key on a file outside it. A new
    file in a folder of the class (CLASS_PATHS) or of a child outside it
    gets the same, so the rules follow the class."""
    rules = [r for r in yaml.safe_load((ROOT / ".sops.yaml").read_text())["creation_rules"]
             if "kubernetes/" in r["path_regex"]]
    for r in rules:
        # sops reads the rules with the regexp of Go: no lookaround.
        assert "(?" not in r["path_regex"], r["path_regex"]
    class_keys = {site: lockdown.KEYS[site][CLASS_KEY] for site in SITES}
    apps_keys = {site: lockdown.KEYS[site][APPS_KEY] for site in SITES}
    sides = {}
    for site in SITES:
        for _, _, files, in_class in lockdown.kustomizations(site):
            for f in files:
                sides.setdefault(f, set()).add((site, in_class))
        for child in classlist.walk(site).values():
            sides.setdefault(f"{classlist.norm(child.path)}/new.sops.yaml", set()).add(
                (site, classlist.is_class_path(child.path)))
    for path in classlist.CLASS_PATHS:
        sides.setdefault(f"{path}new.sops.yaml", set())
    for f in sorted(ROOT.glob("kubernetes/**/*.sops.y*ml")):
        sides.setdefault(f.relative_to(ROOT).as_posix(), set())
    wrong = []
    for path, side in sorted(sides.items()):
        rule = first_rule(path, rules)
        keys = {k.strip() for k in (rule or {}).get("age", "").split(",")}
        in_class = all(c for _, c in side) if side else classlist.is_class_path(path)
        if side and len({c for _, c in side}) > 1:
            wrong.append(f"{path}: built both in the class and outside it")
        own, other = (class_keys, apps_keys) if in_class else (apps_keys, class_keys)
        for site, _ in side:
            if own[site] not in keys:
                wrong.append(f"{path}: its rule lacks the {CLASS_KEY if in_class else APPS_KEY} key of {site}")
        if not side and not keys & set(own.values()):
            wrong.append(f"{path}: its rule has no {CLASS_KEY if in_class else APPS_KEY} key")
        if keys & set(other.values()):
            wrong.append(f"{path}: its rule has the {APPS_KEY if in_class else CLASS_KEY} key")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("site", SITES)
def test_the_class_runs_as_the_controller_once_its_root_does(site):
    """Step 2b. Without the flag the account kustomize-controller has the
    rights that it has today, so naming it changes nothing. Once the root of
    a site names it, cluster-apps and each Kustomization of the class name
    it too, so the flag (2e) can not leave a class child without it."""
    ks = lockdown.kustomizations(site)
    root = account(ks[0][1])
    assert root in (None, "kustomize-controller"), root
    # And the other way: a child of the class names the account only once
    # the root does, so the root never runs as the default account of 2e
    # while its children name the controller.
    named = [key for key, spec, _, in_class in ks[1:] if in_class and account(spec) == "kustomize-controller"]
    assert root or not named, f"the root of {site} names no account, but {named[:3]} do"
    wrong = []
    for key, spec, _, in_class in ks:
        if in_class and account(spec) not in (None, "kustomize-controller"):
            wrong.append(f"{key} is in the class and runs as {account(spec)}, not kustomize-controller")
        if in_class and root and account(spec) != "kustomize-controller":
            wrong.append(f"{key} is in the class and names no account, but the root of {site} does")
        if not in_class and account(spec) in lockdown.CONTROLLERS:
            wrong.append(f"{key} is outside the class and runs as {account(spec)}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("site", SITES)
def test_a_child_outside_the_class_names_only_its_own_account(site):
    """Step 2c. A Kustomization outside the class names no account or
    flux-child-<its name>, and a HelmRelease outside the class none or
    flux-release-<its name>, as the admission policy allows; on a site of
    accounts.NAMED, each of them names its own, as accounts.py writes it."""
    wrong = []
    named = site in accounts.NAMED
    for key, spec, _, in_class in lockdown.kustomizations(site):
        own = f"flux-child-{key.split('/')[1]}"
        if not in_class and account(spec) not in ((own,) if named else (None, own)):
            wrong.append(f"Kustomization {key} runs as {account(spec)}, not {own}")
    for key, spec, in_class in lockdown.helmreleases(site):
        own = f"flux-release-{key.split('/')[1]}"
        if not in_class and account(spec) not in ((own,) if named else (None, own)):
            wrong.append(f"HelmRelease {key} runs as {account(spec)}, not {own}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("site", SITES)
def test_the_account_patches_change_nothing_but_the_accounts(site):
    """The patch that accounts.py writes for a child replaces its
    spec.patches (a strategic merge of a list). So the spec that Flux
    applies to each child outside the class, with that patch, is the spec
    without it, but for the account of the child and the patches that give
    its HelmReleases theirs, after the patches that it had (of its file and
    of its site, such as the wait of the media apps)."""
    children = classlist.walk(site)
    parent = children[classlist.APPS_PARENT[site]]

    def is_account_patch(p):
        body = yaml.safe_load(p["patch"])
        return str((body.get("spec") or {}).get("serviceAccountName", "")).startswith("flux-child-")

    base = dict(children)
    base[parent.key] = classlist.Child(parent.key, parent.parent, parent.path, {
        **parent.doc, "spec": {**parent.doc["spec"], "patches": [
            p for p in parent.doc["spec"].get("patches") or [] if not is_account_patch(p)]}}, parent.build)
    wrong = []
    for child in children.values():
        if classlist.is_class_path(child.path):
            continue
        before, after = lockdown.spec_of(child, base), lockdown.spec_of(child, children)
        old, new = before.get("patches") or [], after.get("patches") or []
        added = new[len(old):]
        if new[:len(old)] != old:
            wrong.append(f"{child.key}: the patch of its account drops or changes its patches")
        for p in added:
            ops = yaml.safe_load(p["patch"])
            if p["target"].get("kind") != "HelmRelease" or [o["path"] for o in ops] != ["/spec/serviceAccountName"]:
                wrong.append(f"{child.key}: an added patch that sets more than the account of a HelmRelease")
        rest = {k: v for k, v in after.items() if k not in ("serviceAccountName", "patches")}
        if rest != {k: v for k, v in before.items() if k not in ("serviceAccountName", "patches")}:
            wrong.append(f"{child.key}: the patch of its account changes more of its spec")
    assert not wrong, "\n".join(wrong)


def test_the_parent_names_the_accounts_as_accounts_py_writes_them():
    """accounts.py write has run for each site of NAMED: the patches of the
    parent of the apps are the ones it writes, and the sites of NAMED have
    their accounts (SITES)."""
    assert set(accounts.NAMED) <= set(accounts.SITES)
    for site in accounts.NAMED:
        text, want = accounts.parent_text(site)
        assert text == want, "run kubernetes/apps/flux-system/lockdown/accounts.py write"
    for site in set(SITES) - set(accounts.NAMED):
        assert accounts.BEGIN not in accounts.parent_path(site).read_text()


@pytest.mark.parametrize("site", SITES)
def test_no_helmrelease_outside_the_class_runs_as_a_controller(site):
    wrong = [key for key, spec, in_class in lockdown.helmreleases(site)
             if not in_class and account(spec) in lockdown.CONTROLLERS]
    assert not wrong, wrong


def accounts_in_git(site):
    """Each ServiceAccount that git makes on a site, as namespace/name: the
    builds of the walk, the root and the Flux install."""
    out = {f"flux-system/{name}" for name in lockdown.CONTROLLERS}
    entry = classlist.build(ROOT / f"kubernetes/clusters/{site}")
    builds = [(entry.docs, None)] + [(c.build.docs, c) for c in classlist.walk(site).values()]
    for docs, child in builds:
        for d in docs:
            if d.get("kind") == "ServiceAccount" and d.get("apiVersion") == "v1":
                namespace = classlist.namespace_of(d, child) if child else d["metadata"].get("namespace")
                out.add(f"{namespace}/{d['metadata']['name']}")
    return out


@pytest.mark.parametrize("site", SITES)
def test_the_flag_is_on_only_when_each_object_of_the_site_names_an_account_of_git(site):
    """Step 2e. A Kustomization or a HelmRelease that names no account runs
    as the default account under the flag, and one whose account is missing
    fails its apply (the throwaway API server shows both). So a controller
    gets --default-service-account only when each object that it applies on
    the site names an account, and each account that an object names is in
    git (a Kustomization names one in its own namespace, flux-system; a
    HelmRelease one in its own namespace)."""
    made = accounts_in_git(site)
    wrong = []
    ks_flag = lockdown.flag(lockdown.controller_args(site, "kustomize-controller"), "--default-service-account")
    for key, spec, _, _ in lockdown.kustomizations(site):
        name = account(spec)
        if name and f"{key.split('/')[0]}/{name}" not in made:
            wrong.append(f"Kustomization {key} runs as {name}, which git does not make in its namespace")
        if ks_flag is not None and not name:
            wrong.append(f"Kustomization {key} names no account, and kustomize-controller has the flag")
    hr_flag = lockdown.flag(lockdown.controller_args(site, "helm-controller"), "--default-service-account")
    for key, spec, _ in lockdown.helmreleases(site):
        name = account(spec)
        if name and f"{key.split('/')[0]}/{name}" not in made:
            wrong.append(f"HelmRelease {key} runs as {name}, which git does not make in its namespace")
        if hr_flag is not None and not name:
            wrong.append(f"HelmRelease {key} names no account, and helm-controller has the flag")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("site", SITES)
def test_the_flag_of_kustomize_controller_is_on_a_flagged_site_only(site):
    """Step 2e, site by site: kustomize-controller of a site of
    lockdown.FLAGGED has --default-service-account=default, so a
    Kustomization that names no account runs as flux-system/default, which
    has no right (the test of the default account); on another site it has
    no flag. The test above holds it to each Kustomization naming an
    account. helm-controller has no flag: each HelmRelease of the class
    would need an account with cluster rights in its own namespace, which
    is more paths to cluster-admin, not fewer (the runbook, "The flag")."""
    ks_flag = lockdown.flag(lockdown.controller_args(site, "kustomize-controller"), "--default-service-account")
    assert ks_flag == (lockdown.DEFAULT_ACCOUNT if site in lockdown.FLAGGED else None), (site, ks_flag)
    assert lockdown.flag(lockdown.controller_args(site, "helm-controller"), "--default-service-account") is None


@pytest.mark.parametrize("site", SITES)
def test_the_list_of_lockdown_py_prints_each_kustomization(site):
    """lockdown.py list, which the reads of a step compare with the site:
    one row for each Kustomization, with its side, account and Secret."""
    rows = lockdown.rows(site)
    assert len(rows) == len(lockdown.kustomizations(site))
    assert all(len(r.split()) == 5 and r.split()[0] in ("class", "outside") for r in rows)
    assert any(r.endswith(" flux-system") for r in rows)


@pytest.mark.parametrize("site", SITES)
def test_the_flags_that_stay_off(site):
    """--sops-age-secret would give each Kustomization that names no Secret a
    key; --default-decryption-service-account would give each one an account
    for decryption; --no-cross-namespace-refs would stop each HelmRelease
    outside flux-system from reading its chart source there."""
    for name in lockdown.CONTROLLERS:
        args = lockdown.controller_args(site, name)
        for off in ("--sops-age-secret", "--default-decryption-service-account", "--no-cross-namespace-refs"):
            assert lockdown.flag(args, off) is None, (site, name, off)


@pytest.mark.parametrize("site", SITES)
def test_the_install_of_git_reads_as_flux_applies_it(site):
    """The reads of lockdown.py on the install of git: the root decrypts with
    the Secret of the patch (the class key once the root of its site is
    keyed, step 2d-root), and kustomize-controller has the patch that
    refuses remote bases."""
    assert secret_of(lockdown.root(site)["spec"]) == (CLASS_KEY if site in lockdown.KEYED_ROOTS else "sops-age")
    assert lockdown.flag(lockdown.controller_args(site, "kustomize-controller"), "--no-remote-bases") == "true"


SETS = ("serviceAccountName", "decryption", "kubeConfig")
# The two Flux kinds that name an account, by their exact API group.
FLUX_OBJECTS = ((classlist.KUSTOMIZE_GROUP, "Kustomization"), (classlist.HELM_GROUP, "HelmRelease"))


def sets_a_field(op_path: str) -> bool:
    """A JSON patch path that sets the spec, or one of SETS or a field
    inside it, exactly."""
    return op_path in ("", "/spec") or any(op_path == f"/spec/{s}" or op_path.startswith(f"/spec/{s}/")
                                           for s in SETS)


def test_no_kustomize_patch_sets_the_account_or_the_decryption_of_a_kustomization():
    """lockdown.py reads the account and the decryption Secret of a
    Kustomization from its file, the patches of its parent, and for the root
    the patches of the Flux install. A patch of a kustomization.yaml that
    sets one of them elsewhere would not be read, so it fails here."""
    wrong = []
    install = {lockdown.flux_system(site) / "kustomization.yaml" for site in SITES}
    for path in sorted((ROOT / "kubernetes").rglob("kustomization.yaml")):
        if path in install:
            continue
        (kz,) = classlist.load(path)[:1] or [{}]
        for patch in kz.get("patches") or []:
            text = patch.get("patch") or ((path.parent / patch["path"]).read_text() if patch.get("path") else "")
            for body in yaml.safe_load_all(text):
                if isinstance(body, list):
                    hit = any(sets_a_field(str(op.get("path", ""))) for op in body if isinstance(op, dict))
                    if hit and (patch.get("target") or {}).get("kind") in (None, "Kustomization", "HelmRelease"):
                        wrong.append(str(path.relative_to(ROOT)))
                elif isinstance(body, dict) and any(classlist.is_kind(body, g, k) for g, k in FLUX_OBJECTS) and \
                        set(body.get("spec") or {}) & set(SETS):
                    wrong.append(str(path.relative_to(ROOT)))
    assert not wrong, wrong


def test_the_class_key_is_for_the_class_only():
    """The admission policy refuses the Secret of the class key in a
    Kustomization outside the class: the controller reads the decryption
    Secret as itself, not as the account of the child (the throwaway API
    server shows it), so an account of its own does not hold the key back."""
    policy = (ROOT / "kubernetes/apps/flux-system/admission/app/policy.yaml").read_text()
    assert f"'{CLASS_KEY}'" in policy
    for site in SITES:
        for key, spec, _, in_class in lockdown.kustomizations(site):
            assert in_class or secret_of(spec) != CLASS_KEY, (site, key)


ROLE = ROOT / "kubernetes/apps/flux-system/accounts/role/clusterrole.yaml"
# The kinds that the charts of the HelmReleases outside the class apply: the
# chart output is not in git. A get read of site 1 on 2026-11-26 (the kinds
# of the objects with the owner labels of such a HelmRelease) found these,
# and Endpoints, EndpointSlices (from the Services) and HelmCharts (which
# helm-controller makes as itself) beside them.
CHART_KINDS = {("apps", "Deployment"), ("apps", "StatefulSet"), ("batch", "CronJob"), ("", "PersistentVolumeClaim"),
               ("", "Service"), ("monitoring.coreos.com", "ServiceMonitor"), ("networking.k8s.io", "Ingress"),
               ("", "Secret")}
# The plural of each kind of a build outside the class, for the role.
PLURALS = {"ConfigMap": "configmaps", "Secret": "secrets", "Service": "services",
           "PersistentVolumeClaim": "persistentvolumeclaims", "Deployment": "deployments",
           "StatefulSet": "statefulsets", "CronJob": "cronjobs", "Ingress": "ingresses",
           "NetworkPolicy": "networkpolicies", "PrometheusRule": "prometheusrules",
           "ServiceMonitor": "servicemonitors", "HelmRelease": "helmreleases"}


def role_rules():
    (role,) = [d for d in yaml.safe_load_all(ROLE.read_text()) if d]
    assert (role["kind"], role["metadata"]["name"]) == ("ClusterRole", accounts.ROLE)
    assert "aggregationRule" not in role and not role["metadata"].get("labels")
    return role["rules"]


# The kinds that the status readers of kstatus (fluxcd/cli-utils v1.4.0,
# createStatusReaders) list when a controller waits for the health of a
# kind: a Deployment through its ReplicaSets and their Pods, a StatefulSet
# through its Pods. Both controllers wait with the client of the account.
# Every other kind they read on its own, with get.
WAIT_READS = {("apps", "deployments"): {("apps", "replicasets"), ("", "pods")},
              ("apps", "statefulsets"): {("", "pods")}}
READ = {"get", "list", "watch"}


def test_the_role_of_the_children_holds_no_more_than_their_kinds():
    """No pod writes, no impersonation, no service account token, no RBAC,
    no Flux kind but HelmRelease, no wildcard and no subresource; ReplicaSets
    and Pods with reads only."""
    allowed = {("", r) for r in ("configmaps", "secrets", "services", "persistentvolumeclaims")} | {
        ("apps", "deployments"), ("apps", "statefulsets"), ("batch", "cronjobs"),
        ("networking.k8s.io", "ingresses"), ("networking.k8s.io", "networkpolicies"),
        ("monitoring.coreos.com", "prometheusrules"), ("monitoring.coreos.com", "servicemonitors"),
        ("helm.toolkit.fluxcd.io", "helmreleases")}
    read_only = set().union(*WAIT_READS.values())
    verbs = READ | {"create", "update", "patch", "delete"}
    for rule in role_rules():
        assert set(rule) == {"apiGroups", "resources", "verbs"}, rule
        assert set(rule["verbs"]) <= verbs, rule
        for group in rule["apiGroups"]:
            for resource in rule["resources"]:
                if (group, resource) in read_only:
                    assert set(rule["verbs"]) <= READ, (group, resource, rule["verbs"])
                else:
                    assert (group, resource) in allowed, (group, resource)


def test_the_role_of_the_children_reads_what_the_wait_reads():
    """A child that waits for a Deployment or a StatefulSet reads their
    ReplicaSets and Pods as its account; without get, list and watch on
    them the wait fails "forbidden"."""
    granted = {}
    for rule in role_rules():
        for group in rule["apiGroups"]:
            for resource in rule["resources"]:
                granted.setdefault((group, resource), set()).update(rule["verbs"])
    for kind, reads in WAIT_READS.items():
        if kind in granted:
            for read in reads:
                assert READ <= granted.get(read, set()), (kind, read)


@pytest.mark.parametrize("site", SITES)
def test_the_role_of_the_children_holds_each_kind_that_they_apply(site):
    """Each kind that a build outside the class applies, and each kind of
    the charts of its HelmReleases, is in the role; a new kind fails here
    until the role has it."""
    granted = {(g, r) for rule in role_rules() for g in rule["apiGroups"] for r in rule["resources"]}
    kinds = set(CHART_KINDS)
    for child in classlist.walk(site).values():
        if not classlist.is_class_path(child.path):
            kinds |= {(classlist.api_group(d), d["kind"]) for d in child.build.docs}
            if child.build.encrypted:
                kinds.add(("", "Secret"))
    missing = sorted((g, k) for g, k in kinds if (g, PLURALS.get(k, k)) not in granted)
    assert not missing, missing


@pytest.mark.parametrize("site", accounts.SITES)
def test_the_accounts_of_the_children_are_written_from_git(site):
    """accounts.py write has run: each Kustomization outside the class has
    flux-child-<name> in flux-system with a RoleBinding in each namespace
    of its objects, and each HelmRelease outside the class flux-release-<name>
    in its namespace with a RoleBinding there."""
    path = accounts.path_of(site)
    assert path.read_text() == accounts.render(site), "run kubernetes/apps/flux-system/lockdown/accounts.py write"
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    made = {f"{d['metadata']['namespace']}/{d['metadata']['name']}" for d in docs if d["kind"] == "ServiceAccount"}
    bound = {(d["metadata"]["namespace"], s["namespace"], s["name"]) for d in docs if d["kind"] == "RoleBinding"
             for s in d["subjects"]}
    kustomizations, helmreleases = accounts.children(site)
    assert kustomizations and helmreleases
    for name, namespaces in kustomizations.items():
        assert f"flux-system/flux-child-{name}" in made, name
        assert all((n, "flux-system", f"flux-child-{name}") in bound for n in namespaces), name
    for key in helmreleases:
        namespace, name = key.split("/")
        assert f"{namespace}/flux-release-{name}" in made and (namespace, namespace, f"flux-release-{name}") in bound
    for d in docs:
        if d["kind"] == "RoleBinding":
            assert d["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                                    "name": accounts.ROLE}, d["metadata"]
            assert [s["name"].startswith(("flux-child-", "flux-release-")) for s in d["subjects"]] == [True]
        else:
            assert d["kind"] == "ServiceAccount" and d["automountServiceAccountToken"] is False, d["metadata"]
    # The site lists them, as a child of the class.
    listing = (ROOT / f"kubernetes/clusters/{site}/apps/kustomization.yaml").read_text()
    assert f"- ../../../apps/flux-system/accounts/ks-{site}.yaml" in listing
    assert classlist.is_class_path(f"kubernetes/apps/flux-system/accounts/{site}")


def test_a_child_gets_a_binding_where_only_a_sops_file_of_it_applies(tmp_path):
    """A namespace where a child applies only a SOPS file gets a binding:
    the namespace of the metadata of the file (SOPS leaves it in the clear),
    or the one of the outermost kustomization.yaml that sets one."""
    def write(path, doc):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc))

    def kustomization(folder, resources, namespace=None):
        doc = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": resources}
        if namespace:
            doc["namespace"] = namespace
        write(folder / "kustomization.yaml", doc)

    def secret(path, namespace):
        # A placeholder in the form of an encrypted file: no key, no value.
        write(path, {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s", "namespace": namespace},
                     "stringData": {"a": "ENC[placeholder]"}, "sops": {"encrypted_regex": "^(data|stringData)$"}})

    app = tmp_path / "app"
    kustomization(app, ["configmap.yaml", "secret.sops.yaml", "nested"])
    write(app / "configmap.yaml", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c", "namespace": "one"}})
    secret(app / "secret.sops.yaml", "two")
    kustomization(app / "nested", ["secret.sops.yaml"], namespace="three")
    secret(app / "nested/secret.sops.yaml", "four")
    assert accounts.encrypted_namespaces(app) == {"two", "three"}
    # An outer namespace wins over each inner one.
    kustomization(tmp_path / "outer", ["../app"], namespace="five")
    assert accounts.encrypted_namespaces(tmp_path / "outer") == {"five"}
    # With the namespaces of the build, the child is bound in each.
    doc = {"metadata": {"name": "x", "namespace": "flux-system"}, "spec": {"path": str(app)}}
    configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c", "namespace": "one"}}
    child = classlist.Child("flux-system/x", "flux-system/flux-system", str(app), doc,
                            classlist.Build(docs=[configmap], encrypted={"app/secret.sops.yaml"}))
    assert accounts.namespaces_of(child) == ["one", "three", "two"]
    # A target namespace takes each object there.
    doc["spec"]["targetNamespace"] = "six"
    assert accounts.namespaces_of(child) == ["six"]


@pytest.mark.parametrize("site", SITES)
def test_the_default_account_of_flux_system_gets_no_binding(site):
    """Under the flag (2e) a Kustomization with no account runs as
    flux-system/default, which must have no right: no binding of git names
    it, and the role of the children is bound to their accounts only."""
    entry = classlist.build(ROOT / f"kubernetes/clusters/{site}")
    builds = [(entry.docs, None)] + [(c.build.docs, c) for c in classlist.walk(site).values()]
    wrong = []
    for docs, child in builds:
        for d in docs:
            if d.get("kind") not in ("RoleBinding", "ClusterRoleBinding"):
                continue
            namespace = (classlist.namespace_of(d, child) if child else d["metadata"].get("namespace")) or ""
            for s in d.get("subjects") or []:
                subject = f"{s.get('namespace', namespace)}/{s.get('name')}"
                if s.get("kind") == "ServiceAccount" and subject == "flux-system/default":
                    wrong.append(f"{d['kind']} {d['metadata']['name']} binds flux-system/default")
                if s.get("kind") == "Group" and s.get("name") in ("system:serviceaccounts",
                                                                  "system:serviceaccounts:flux-system"):
                    wrong.append(f"{d['kind']} {d['metadata']['name']} binds the accounts of {s['name']}")
            if (d.get("roleRef") or {}).get("name") == accounts.ROLE and \
                    not all(s.get("name", "").startswith(("flux-child-", "flux-release-")) for s in d["subjects"]):
                wrong.append(f"{d['kind']} {d['metadata']['name']} binds {accounts.ROLE} to another account")
    assert not wrong, "\n".join(wrong)


def test_the_controller_on_a_throwaway_api_server():
    docker()
    env = dict(os.environ, PYTHON=sys.executable)
    env.pop("KUBECONFIG", None)
    result = subprocess.run(["bash", str(LOCKDOWN / "tests/run.sh")], capture_output=True, text=True,
                            timeout=900, check=False, env=env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-12000:]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
