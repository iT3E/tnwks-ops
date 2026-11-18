"""The lists of the admission policies of NFS volumes
(kubernetes/apps/storage/nfs-server/admission/nfsvolumes.yaml) match git.

The rules themselves run on a throwaway API server in
admission/tests/run.sh. These tests check what that harness can not see: that
each NFS volume in the manifests of this repository is on the list of pods,
that the list holds nothing else, that the volumes of the shares come in
pairs, and that the bindings are still Audit and Warn only.
"""

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ADMISSION = ROOT / "kubernetes/apps/storage/nfs-server/admission"
CLUSTER = ROOT / "kubernetes/clusters/wsl/apps/kustomization.yaml"
# The users of the two exports of today, and the namespace that each one's
# pod runs in.
USERS = {
    "kubernetes/apps/production/sites/app/deployment.yaml": "production",
    "kubernetes/apps/tools/actions-runner-controller/runner/app/helmrelease.yaml": "tools",
    "kubernetes/apps/networking/unifi/app/gdrive-copy.yaml": "networking",
}


def policies() -> dict[str, dict]:
    docs = [d for d in yaml.safe_load_all((ADMISSION / "nfsvolumes.yaml").read_text()) if d]
    return {f"{d['kind']}/{d['metadata']['name']}": d for d in docs}


def allowed(policy: str) -> list[list[str]]:
    """The list of the variable `allowed`: a CEL list of lists of strings."""
    (variable,) = [
        v
        for v in policies()[f"ValidatingAdmissionPolicy/{policy}"]["spec"]["variables"]
        if v["name"] == "allowed"
    ]
    return ast.literal_eval(variable["expression"])


def nfs_sources(node):
    if isinstance(node, dict):
        if isinstance(node.get("nfs"), dict) and "server" in node["nfs"]:
            yield node["nfs"]
        for value in node.values():
            yield from nfs_sources(value)
    elif isinstance(node, list):
        for value in node:
            yield from nfs_sources(value)


def nfs_in_git() -> dict[str, list[dict]]:
    found = {}
    for path in sorted((ROOT / "kubernetes").rglob("*.yaml")):
        text = path.read_text()
        if not re.search(r"^\s*nfs:\s*$", text, re.MULTILINE):
            continue
        sources = [s for d in yaml.safe_load_all(text) if d for s in nfs_sources(d)]
        if sources:
            found[str(path.relative_to(ROOT))] = sources
    return found


def test_each_nfs_volume_in_git_is_on_the_list_of_pods():
    pods = allowed("nfs-pod-volumes")
    found = nfs_in_git()
    assert set(found) == set(USERS), (
        "a new user of an NFS export goes on the list of nfsvolumes.yaml"
    )
    for path, sources in found.items():
        for source in sources:
            assert [USERS[path], source["server"], source["path"]] in pods, (path, source)


def test_the_list_of_pods_holds_only_the_users_of_today():
    used = {
        (USERS[p], s["server"], s["path"]) for p, sources in nfs_in_git().items() for s in sources
    }
    assert {tuple(a) for a in allowed("nfs-pod-volumes")} == used


def test_the_harness_renders_the_same_users():
    text = (ADMISSION / "tests/render.py").read_text()
    for path, namespace in USERS.items():
        assert f'("{namespace}", ' in text and f'"{path}"' in text, path


def test_each_share_has_one_volume_to_write_and_one_to_read():
    server = {s["server"] for sources in nfs_in_git().values() for s in sources}
    assert len(server) == 1
    shares = {}
    for name, entry_server, path, read_only, namespace, claim in allowed("nfs-persistent-volumes"):
        assert entry_server in server, name
        assert read_only in ("true", "false"), name
        assert namespace.startswith("agents-"), name
        assert (
            name
            == f"nfs-vault-{namespace.removeprefix('agents-')}-{'ro' if read_only == 'true' else 'rw'}"
        )
        shares.setdefault(path, set()).add((read_only, namespace, claim))
    assert shares == {
        "/vault-main": {("false", "agents-main", "vault-rw"), ("true", "agents-main", "vault-ro")},
        "/vault-finance": {
            ("false", "agents-finance", "vault-rw"),
            ("true", "agents-finance", "vault-ro"),
        },
    }


def test_the_bindings_audit_and_warn_and_deny_nothing_yet():
    # The first step. The change that turns them to Deny changes this test.
    found = policies()
    for name in ("nfs-pod-volumes", "nfs-persistent-volumes"):
        binding = found[f"ValidatingAdmissionPolicyBinding/{name}"]["spec"]
        policy = found[f"ValidatingAdmissionPolicy/{name}"]["spec"]
        assert binding["policyName"] == name
        assert binding["validationActions"] == ["Audit", "Warn"]
        assert "matchResources" not in binding, "each namespace"
        assert policy["failurePolicy"] == "Fail"


def test_homelab_wsl_lists_the_policies():
    resources = yaml.safe_load(CLUSTER.read_text())["resources"]
    assert "./nfs-admission-ks.yaml" in resources
    (ks,) = [
        d for d in yaml.safe_load_all((CLUSTER.parent / "nfs-admission-ks.yaml").read_text()) if d
    ]
    assert ks["spec"]["path"] == "./kubernetes/apps/storage/nfs-server/admission"
    assert yaml.safe_load((ADMISSION / "kustomization.yaml").read_text())["resources"] == [
        "./nfsvolumes.yaml"
    ]
