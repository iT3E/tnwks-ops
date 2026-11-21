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


def test_both_sites_list_the_policy():
    for site in classlist.SITES:
        text = (ROOT / f"kubernetes/clusters/{site}/apps/kustomization.yaml").read_text()
        assert "- ../../../apps/flux-system/admission/ks.yaml" in text, site


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
