"""The admission policy of the Flux children
(kubernetes/apps/flux-system/admission, docs/operations/flux-admission.md).

The lists of the policy are the class of git: classlist.py walks the Flux
trees of both sites the way Flux builds them and writes them. These tests
check that the policy holds the lists of git now, that each site lists the
policy, that only the two parents of the apps read files outside the class,
and that the binding is still Audit and Warn. The last test runs the rules
on a throwaway API server (admission/tests/run.sh), with Docker.
"""

import ast
import importlib.util
import os
import re
import subprocess
import sys

import pytest
import yaml
from test_hand_written_rules import ROOT, docker

ADMISSION = ROOT / "kubernetes/apps/flux-system/admission"
sys.path.insert(0, str(ADMISSION))

import classlist


def docs():
    return {d["kind"]: d for d in yaml.safe_load_all((ADMISSION / "app/policy.yaml").read_text()) if d}


def test_the_lists_of_the_policy_are_the_class_of_git():
    text, want = classlist.current()
    assert text == want, "run: python3 kubernetes/apps/flux-system/admission/classlist.py write"


def test_the_walk_finds_each_child_of_each_site():
    data = classlist.lists()
    for site in classlist.SITES:
        counts = data["sites"][site]
        assert counts["kustomizations"] > 50 and 0 < counts["class"] < counts["kustomizations"], (site, counts)
    assert "flux-system/flux-admission" in data["classKustomizations"]
    assert data["classKustomizations"]["flux-system/flux-admission"] == [
        "flux-system/cluster-apps", "flux-system/cluster-apps-wsl-02"]


def test_only_the_parents_of_the_apps_read_files_outside_the_class():
    # A child of the class whose build reads a file outside the class would
    # apply that file with the rights of the class. The two parents of the
    # apps read the aggregators and the ks.yaml of the children outside the
    # class; the rule parent-objects holds what they may apply.
    assert sorted(classlist.lists()["parentObjects"]) == [
        "flux-system/cluster-apps", "flux-system/cluster-apps-wsl-02"]


def test_the_class_paths_are_the_kubernetes_folders_of_owner_label():
    # Owner label holds a change in the class; the policy holds what a child
    # outside it applies. The two lists must name the same folders.
    tree = ast.parse((ROOT / ".github/scripts/owner-label-check.py").read_text())
    (ask_first,) = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], "id", None) == "ASK_FIRST"]
    folders = [p for p in ask_first if p.startswith("kubernetes/") and p.endswith("/")]
    assert sorted(folders) == sorted(classlist.CLASS_PATHS)


# The groups of the operators that the rule operator-pods keeps to the
# class (app/policy.yaml, variables.operatorPods).
OPERATOR_GROUPS = ("actions.github.com", "agents.x-k8s.io", "extensions.agents.x-k8s.io",
                   "postgresql.cnpg.io", "barmancloud.cnpg.io")


@pytest.mark.parametrize("site", classlist.SITES)
def test_no_child_outside_the_class_applies_an_object_of_an_operator_of_the_class(site):
    """D149: the database folders of the apps are in the class, so a new app
    with a database puts its db folder in the class (classlist.py and
    ASK_FIRST) before the policy denies it."""
    found = {}
    for child in classlist.walk(site).values():
        if classlist.is_class_path(child.path):
            continue
        for doc in child.build.docs:
            if classlist.api_group(doc) in OPERATOR_GROUPS:
                found.setdefault(child.key, set()).add(doc["kind"])
    assert not found, found
    policy = (ROOT / "kubernetes/apps/flux-system/admission/app/policy.yaml").read_text()
    assert all(f"'{g}'" in policy for g in OPERATOR_GROUPS)


SUBSTITUTION = ROOT / "kubernetes/apps/flux-system/substitution/app/secret.yaml"
# A variable of Flux; a doubled dollar sign is an escape, which Flux writes
# as one.
VARIABLE = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(:=[^}]*)?\}")


@pytest.mark.parametrize("site", classlist.SITES)
def test_the_reduced_secret_holds_each_key_of_the_children_outside_the_class(site):
    keys = set(yaml.safe_load(SUBSTITUTION.read_text())["stringData"])
    (settings,) = [d for d in yaml.safe_load_all((ROOT / f"kubernetes/clusters/{site}/cluster-settings.yaml").read_text()) if d]
    known = keys | set(settings["data"])
    missing = {}
    for child in classlist.walk(site).values():
        if classlist.is_class_path(child.path):
            continue
        inline = set((child.doc["spec"].get("postBuild") or {}).get("substitute") or {})
        for doc in child.build.docs:
            for m in VARIABLE.finditer(yaml.safe_dump(doc)):
                if m.group(2) is None and m.group(1) not in known | inline:
                    missing.setdefault(child.key, set()).add(m.group(1))
    assert not missing, f"add these keys to {SUBSTITUTION.relative_to(ROOT)}, or a default: {missing}"


def test_both_sites_list_the_policy():
    for site in classlist.SITES:
        text = (ROOT / f"kubernetes/clusters/{site}/apps/kustomization.yaml").read_text()
        assert "- ../../../apps/flux-system/admission/ks.yaml" in text, site
        assert "- ../../../apps/flux-system/substitution/ks.yaml" in text, site


def objects_check():
    path = ADMISSION / "app/scripts/admission_objects.py"
    spec = importlib.util.spec_from_file_location("admission_objects", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_objects_check_reads_the_class_of_the_policy():
    check = objects_check()
    kustomizations, helmreleases = check.class_lists(docs()["ValidatingAdmissionPolicy"])
    data = classlist.lists()
    assert kustomizations == set(data["classKustomizations"])
    assert helmreleases == set(data["classHelmReleases"])


def test_the_objects_check_finds_an_admission_object_owned_outside_the_class():
    check = objects_check()
    k, h = {"flux-system/flux-admission"}, {"cert-manager/cert-manager"}

    def hook(name, labels):
        return {"kind": "MutatingWebhookConfiguration", "metadata": {"name": name, "labels": labels}}

    objects = [
        hook("policy", {"kustomize.toolkit.fluxcd.io/namespace": "flux-system",
                        "kustomize.toolkit.fluxcd.io/name": "flux-admission"}),
        hook("cert-manager-webhook", {"helm.toolkit.fluxcd.io/namespace": "cert-manager",
                                      "helm.toolkit.fluxcd.io/name": "cert-manager"}),
        hook("by-hand", {}),
        hook("outside-k", {"kustomize.toolkit.fluxcd.io/namespace": "flux-system",
                           "kustomize.toolkit.fluxcd.io/name": "mealie"}),
        hook("outside-h", {"helm.toolkit.fluxcd.io/namespace": "production",
                           "helm.toolkit.fluxcd.io/name": "mealie"}),
    ]
    lines, unowned = check.findings(objects, k, h)
    assert unowned == 1
    assert [line.split()[2] for line in lines] == ["name=outside-k", "name=outside-h"]
    assert check.findings(objects[:3], k, h) == ([], 1)


def test_the_objects_check_may_only_read():
    rules = [d for d in yaml.safe_load_all((ADMISSION / "app/objects-check.yaml").read_text())
             if d and d["kind"] == "ClusterRole"]
    assert [r["verbs"] for role in rules for r in role["rules"]] == [["get", "list"]]


def test_the_policy_matches_the_two_controllers_and_fails_closed():
    policy = docs()["ValidatingAdmissionPolicy"]
    assert policy["spec"]["failurePolicy"] == "Fail"
    (condition,) = policy["spec"]["matchConditions"]
    for account in ("kustomize-controller", "helm-controller"):
        assert f"system:serviceaccount:flux-system:{account}" in condition["expression"]


def test_the_binding_is_still_audit_and_warn():
    # The change that turns it to Deny changes this test, after a read of
    # the audit counts of both sites (docs/operations/flux-admission.md).
    assert docs()["ValidatingAdmissionPolicyBinding"]["spec"]["validationActions"] == ["Audit", "Warn"]


def test_the_rules_on_a_throwaway_api_server():
    docker()
    env = dict(os.environ, PYTHON=sys.executable)
    env.pop("KUBECONFIG", None)
    result = subprocess.run(["bash", str(ADMISSION / "tests/run.sh")], capture_output=True, text=True,
                            timeout=900, check=False, env=env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-12000:]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# A remote source of kustomize: a URL, a git address, a query of a ref, or a
# host name before the first slash.
REMOTE = re.compile(r"://|^git@|^git::|[?&](?:ref|version)=|^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::[0-9]+)?/")


@pytest.mark.parametrize("site", classlist.SITES)
def test_kustomize_controller_refuses_remote_bases(site):
    # A URL in a kustomization outside the class would bring in any
    # manifest, an admission object among them, which the policy can not see.
    patches = yaml.safe_load((ROOT / f"kubernetes/clusters/{site}/flux-system/kustomization.yaml").read_text())["patches"]
    args = [op["value"] for p in patches if (p.get("target") or {}).get("name") == "kustomize-controller"
            for op in yaml.safe_load(p["patch"])]
    assert args == ["--no-remote-bases=true"]


def test_no_build_of_flux_uses_a_remote_source():
    # With the flag, a build with one fails. Only the bootstrap of Flux, which
    # no Kustomization builds, has one.
    built = {child.path for site in classlist.SITES for child in classlist.walk(site).values()}
    found = []
    for path in ROOT.glob("kubernetes/**/kustomization.yaml"):
        for doc in yaml.safe_load_all(path.read_text()):
            for key in ("resources", "components", "bases", "crds"):
                found += [(str(path.relative_to(ROOT)), entry) for entry in (doc or {}).get(key) or []
                          if isinstance(entry, str) and REMOTE.search(entry)]
    assert {path for path, _ in found} == {"kubernetes/bootstrap/kustomization.yaml"}, found
    assert not any(p == "kubernetes/bootstrap" or p.startswith("kubernetes/bootstrap/") for p in built)


@pytest.mark.parametrize(
    ("api_version", "group", "kustomization", "helmrelease"),
    [
        ("kustomize.toolkit.fluxcd.io/v1", "kustomize.toolkit.fluxcd.io", True, False),
        ("helm.toolkit.fluxcd.io/v2", "helm.toolkit.fluxcd.io", False, True),
        ("v1", "", False, False),
        ("", "", False, False),
        # Groups that a substring or prefix match takes: "fluxcd.io" in the
        # version, or a group that starts with a Flux group.
        ("evil.fluxcd.io/v1", "evil.fluxcd.io", False, False),
        ("kustomize.toolkit.fluxcd.io.example.com/v1", "kustomize.toolkit.fluxcd.io.example.com", False, False),
        ("helm.toolkit.fluxcd.io.example.com/v2", "helm.toolkit.fluxcd.io.example.com", False, False),
        ("example.com/kustomize.toolkit.fluxcd.io/v1", "example.com", False, False),
        ("xkustomize.toolkit.fluxcd.io/v1", "xkustomize.toolkit.fluxcd.io", False, False),
    ],
)
def test_the_walk_takes_the_api_group_of_a_document_exactly(api_version, group, kustomization, helmrelease):
    for kind, expect, flux_group in (
        ("Kustomization", kustomization, classlist.KUSTOMIZE_GROUP),
        ("HelmRelease", helmrelease, classlist.HELM_GROUP),
    ):
        doc = {"apiVersion": api_version, "kind": kind, "metadata": {"name": "x"}}
        assert classlist.api_group(doc) == group
        assert classlist.is_kind(doc, flux_group, kind) is expect
        if kind == "Kustomization":
            assert (classlist.flux_kustomizations([doc]) == [doc]) is expect
