"""The lists of the admission policies of NFS volumes
(kubernetes/apps/storage/nfs-server/admission/nfsvolumes.yaml) match git.

The rules themselves run on a throwaway API server in
admission/tests/run.sh. These tests check what that harness can not see: that
each NFS volume of a pod in the manifests of this repository is on the list
of pods, that the list holds nothing else, that the volumes of the shares
come in pairs, that the PersistentVolumes in git are the ones of the list
and each one and its claim name each other, and that the bindings are still
Audit and Warn only.
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
# The volumes and claims of the share of each trust domain with a vault.
SHARES = {
    "main": ROOT / "kubernetes/apps/ai/agents/main/vault-share",
    "finance": ROOT / "kubernetes/apps/ai/agents/finance/vault-share",
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


def docs_with_nfs():
    for path in sorted((ROOT / "kubernetes").rglob("*.yaml")):
        text = path.read_text()
        if re.search(r"^\s*nfs:\s*$", text, re.MULTILINE):
            for doc in yaml.safe_load_all(text):
                if doc:
                    yield str(path.relative_to(ROOT)), doc


def nfs_in_git() -> dict[str, list[dict]]:
    """The NFS volumes of pods, by file: each NFS source but a PersistentVolume."""
    found = {}
    for path, doc in docs_with_nfs():
        if doc.get("kind") == "PersistentVolume":
            continue
        sources = list(nfs_sources(doc))
        if sources:
            found.setdefault(path, []).extend(sources)
    return found


def volumes_in_git() -> dict[str, tuple[str, dict]]:
    """Each PersistentVolume with an NFS source, by name, with its file."""
    found = {}
    for path, doc in docs_with_nfs():
        if doc.get("kind") == "PersistentVolume" and "nfs" in doc["spec"]:
            assert doc["metadata"]["name"] not in found, doc["metadata"]["name"]
            found[doc["metadata"]["name"]] = (path, doc)
    return found


def claims_of_share(domain: str) -> dict[str, dict]:
    """The claims of a share, by namespace and name. The namespace is the
    targetNamespace of its Flux Kustomization, as Flux applies it."""
    (ks,) = [d for d in yaml.safe_load_all((SHARES[domain] / "ks.yaml").read_text()) if d]
    namespace = ks["spec"]["targetNamespace"]
    docs = [d for d in yaml.safe_load_all((SHARES[domain] / "app/claims.yaml").read_text()) if d]
    claims = {}
    for doc in docs:
        assert doc["kind"] == "PersistentVolumeClaim"
        assert doc["metadata"].get("namespace", namespace) == namespace
        claims[(namespace, doc["metadata"]["name"])] = doc
    return claims


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


def test_the_volumes_in_git_are_the_volumes_of_the_list():
    found = volumes_in_git()
    listed = allowed("nfs-persistent-volumes")
    assert sorted(found) == sorted(entry[0] for entry in listed)
    for name, server, path, read_only, namespace, claim in listed:
        source, doc = found[name]
        spec = doc["spec"]
        assert source.startswith("kubernetes/apps/ai/agents/"), source
        assert spec["nfs"] == {"server": server, "path": path, "readOnly": read_only == "true"}, (
            name
        )
        assert spec["claimRef"] == {"namespace": namespace, "name": claim}, name
        assert spec["persistentVolumeReclaimPolicy"] == "Retain", name
        assert spec["storageClassName"] == "", name
        assert "namespace" not in doc["metadata"], name


def test_each_volume_names_its_claim_and_each_claim_its_volume():
    volumes = volumes_in_git()
    claims = {}
    for domain in SHARES:
        claims.update(claims_of_share(domain))
    by_ref = {
        (doc["spec"]["claimRef"]["namespace"], doc["spec"]["claimRef"]["name"]): name
        for name, (_, doc) in volumes.items()
    }
    assert sorted(by_ref) == sorted(claims)
    for key, claim in claims.items():
        name = by_ref[key]
        volume = volumes[name][1]["spec"]
        spec = claim["spec"]
        assert spec["volumeName"] == name, key
        assert spec["storageClassName"] == "", key
        assert spec["accessModes"] == volume["accessModes"], key
        assert parse_size(spec["resources"]["requests"]["storage"]) <= parse_size(
            volume["capacity"]["storage"]
        )
        assert "selector" not in spec and not claim["metadata"].get("labels"), key


def test_the_writer_mounts_hard_and_the_readers_soft():
    for name, (_, doc) in volumes_in_git().items():
        spec = doc["spec"]
        options = spec["mountOptions"]
        assert options[0] == "nfsvers=4.1", name
        if spec["nfs"]["readOnly"]:
            assert name.endswith("-ro") and spec["accessModes"] == ["ReadOnlyMany"], name
            assert options == ["nfsvers=4.1", "soft", "timeo=50", "retrans=2", "actimeo=3"], name
        else:
            assert name.endswith("-rw") and spec["accessModes"] == ["ReadWriteMany"], name
            assert options == ["nfsvers=4.1", "hard"], name


def test_each_share_is_applied_after_the_policies():
    for domain, base in SHARES.items():
        (ks,) = [d for d in yaml.safe_load_all((base / "ks.yaml").read_text()) if d]
        spec = ks["spec"]
        assert ks["metadata"]["name"] == f"agents-{domain}-vault-share"
        assert spec["targetNamespace"] == f"agents-{domain}"
        assert spec["path"] == f"./{base.relative_to(ROOT)}/app"
        assert {"name": "nfs-admission"} in spec["dependsOn"]
        assert spec["wait"] is True
        parent = yaml.safe_load((base.parent / "kustomization.yaml").read_text())["resources"]
        assert "./vault-share/ks.yaml" in parent
        assert yaml.safe_load((base / "app/kustomization.yaml").read_text())["resources"] == [
            "./volumes.yaml",
            "./claims.yaml",
        ]


def parse_size(size: str) -> int:
    units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
    number, unit = re.fullmatch(r"(\d+)(Ki|Mi|Gi|Ti)", size).groups()
    return int(number) * units[unit]
