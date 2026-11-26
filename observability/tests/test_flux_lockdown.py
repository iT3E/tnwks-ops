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
- the flags that stay off.

The last test runs the controller of git on a throwaway API server
(lockdown/tests/run.sh), with Docker: who reads the decryption Secret, who
applies, and what a missing account does.
"""

import os
import subprocess
import sys

import pytest
import yaml
from test_hand_written_rules import ROOT, docker

LOCKDOWN = ROOT / "kubernetes/apps/flux-system/lockdown"
sys.path.insert(0, str(ROOT / "kubernetes/apps/flux-system/admission"))
sys.path.insert(0, str(LOCKDOWN))

import classlist
import lockdown

SITES = classlist.SITES
CLASS_KEY = "sops-age-class"


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


@pytest.mark.parametrize("site", SITES)
def test_the_class_runs_as_the_controller_once_its_root_does(site):
    """Step 2b. Without the flag the account kustomize-controller has the
    rights that it has today, so naming it changes nothing. Once the root of
    a site names it, cluster-apps and each Kustomization of the class name
    it too, so the flag (2e) can not leave a class child without it."""
    ks = lockdown.kustomizations(site)
    root = account(ks[0][1])
    assert root in (None, "kustomize-controller"), root
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
    the Secret of the patch, and kustomize-controller has the patch that
    refuses remote bases."""
    assert secret_of(lockdown.root(site)["spec"]) == "sops-age"
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
