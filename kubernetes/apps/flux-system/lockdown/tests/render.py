"""Writes the inputs of test_flux_lockdown.py (run.sh calls it).

The test runs in a plain Python image with no YAML parser, so this step, in
the Python of the repository (observability), writes:

- crds.json: each CRD of the Flux install of git, which the controller
  watches;
- image.txt and args.txt: the image of kustomize-controller and the
  arguments of its container in git (the same on both sites), less the
  address of the events and the leader election, which the throwaway API
  server does not need;
- srv/source.tar.gz and digest.txt: the artifact of a test tree, which a
  plain HTTP server gives to the controller as the GitRepository of
  flux-system would. It holds ConfigMaps and one ClusterRole, an object of
  each kind of the role flux-child (step 2a), and a Role and a Pod, a
  Deployment and a StatefulSet with no replicas for each of two waits, and
  no secret (its one Secret holds a placeholder), and a folder of two
  child Kustomizations for a parent that patches one of them, as the parent
  of the apps of a site does (step 2b), and a folder of two more for a
  parent with the patches of accounts.py (step 2c), with a HelmRelease;
- patches.json: the patches that accounts.py writes for those two;
- role.json: the role flux-child of git
  (kubernetes/apps/flux-system/accounts/role/clusterrole.yaml), and
  role-noread.json: the same role less its reads of ReplicaSets and Pods,
  as the control of the wait.

  python3 render.py <output directory>
"""

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import accounts
import lockdown

# The test tree: a folder for each case, with a kustomization.yaml.
CONFIGMAPS = {
    "probe": ("probe", "who"),
    "tenant": ("tenant", "app"),
    "tenant-class": ("tenant", "app-class"),
    "tenant-none": ("tenant", "app-none"),
    "kept": ("tenant", "kept"),
    "kept2": ("tenant", "kept2"),
    "other": ("tenant", "other"),
}


# An object of each kind that the role flux-child grants, in the namespace
# tenant, for the account of a child with that role only.
CENSUS = [
    {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "census"}, "data": {"a": "b"}},
    {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "census"},
     "stringData": {"README": "a placeholder of the test, not a secret"}},
    {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "census"},
     "spec": {"selector": {"app": "census"}, "ports": [{"port": 80}]}},
    {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": "census"},
     "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}},
    {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "census"},
     "spec": {"selector": {"matchLabels": {"app": "census"}}, "template": {
         "metadata": {"labels": {"app": "census"}},
         "spec": {"containers": [{"name": "app", "image": "registry.invalid/app:1"}]}}}},
    {"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": "census"},
     "spec": {"serviceName": "census", "selector": {"matchLabels": {"app": "census-set"}}, "template": {
         "metadata": {"labels": {"app": "census-set"}},
         "spec": {"containers": [{"name": "app", "image": "registry.invalid/app:1"}]}}}},
    {"apiVersion": "batch/v1", "kind": "CronJob", "metadata": {"name": "census"},
     "spec": {"schedule": "0 0 * * *", "jobTemplate": {"spec": {"template": {"spec": {
         "restartPolicy": "Never", "containers": [{"name": "app", "image": "registry.invalid/app:1"}]}}}}}},
    {"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {"name": "census"},
     "spec": {"rules": [{"host": "census.invalid", "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": {
         "service": {"name": "census", "port": {"number": 80}}}}]}}]}},
    {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "census"},
     "spec": {"podSelector": {}, "policyTypes": ["Ingress"]}},
    {"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule", "metadata": {"name": "census"},
     "spec": {"groups": []}},
    {"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": {"name": "census"},
     "spec": {"selector": {"matchLabels": {"app": "census"}}, "endpoints": []}},
    {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "metadata": {"name": "census"},
     "spec": {"interval": "30m", "chart": {"spec": {"chart": "app-template", "version": "4.0.0",
              "sourceRef": {"kind": "HelmRepository", "name": "bjw-s", "namespace": "flux-system"}}}}},
]
# The kinds of the test that the role does not grant.
NOT_GRANTED = {
    "census-role": {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "census"},
                    "rules": []},
    "census-pod": {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "census"},
                   "spec": {"containers": [{"name": "app", "image": "registry.invalid/app:1"}]}},
}

# For a wait on health: a Deployment and a StatefulSet with no replicas, so
# that kube-controller-manager makes them Ready with no node. The status
# readers still list their ReplicaSets and Pods.
WAIT = ("census-wait", "census-noread")
# The reads that the wait needs, which role-noread.json drops.
WAIT_READS = {("apps", "replicasets"), ("", "pods")}


def wait_objects(name: str) -> list[dict]:
    pods = {"metadata": {"labels": {"app": name}},
            "spec": {"containers": [{"name": "app", "image": "registry.invalid/app:1"}]}}
    return [
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name},
         "spec": {"replicas": 0, "selector": {"matchLabels": {"app": name}}, "template": pods}},
        {"apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": name + "-set"},
         "spec": {"replicas": 0, "serviceName": name, "selector": {"matchLabels": {"app": name + "-set"}},
                  "template": {**pods, "metadata": {"labels": {"app": name + "-set"}}}}},
    ]


def noread(role: dict) -> dict:
    rules = [r for r in role["rules"]
             if not {(g, x) for g in r["apiGroups"] for x in r["resources"]} <= WAIT_READS]
    assert len(rules) < len(role["rules"]), "the role of git has no reads for the wait"
    return {**role, "metadata": {"name": "flux-child-noread"}, "rules": rules}


# The children of a parent (step 2b): it names the account of the class in
# the first one by a patch, as the parent of the apps of a site does for its
# direct children of the class.
PATCHED = ("who-patched", "who-unpatched")
# The children of a parent with the patches of accounts.py (step 2c): each
# names its own account, and the HelmRelease of the first its own.
NAMED = {"named-hr": ("./census-hr", ["census-hr"]), "named-probe": ("./probe", [])}
# A patch that the first one has from its site before (as the wait of the
# media apps of site 2): the patch of accounts.py keeps it.
SITE_PATCHES = [{"target": {"kind": "HelmRelease"}, "patch": yaml.safe_dump(
    [{"op": "add", "path": "/spec/install", "value": {"disableWait": True}}])}]


def tree() -> dict[str, str]:
    files = {}
    files["parent/children.yaml"] = yaml.safe_dump_all([
        {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
         "metadata": {"name": name, "namespace": "flux-system"},
         "spec": {"interval": "1m", "retryInterval": "5s", "timeout": "30s", "path": "./probe", "prune": True,
                  "wait": False, "sourceRef": {"kind": "GitRepository", "name": "flux-system"}}}
        for name in PATCHED])
    files["parent-named/children.yaml"] = yaml.safe_dump_all([
        {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
         "metadata": {"name": name, "namespace": "flux-system"},
         "spec": {"interval": "1m", "retryInterval": "5s", "timeout": "30s", "path": path, "prune": True,
                  "wait": False, "sourceRef": {"kind": "GitRepository", "name": "flux-system"}}}
        for name, (path, _) in NAMED.items()])
    files["census-hr/helmrelease.yaml"] = yaml.safe_dump({**CENSUS[-1], "metadata": {"name": "census-hr"}})
    for folder, resource in (("parent-named", "children.yaml"), ("census-hr", "helmrelease.yaml")):
        files[f"{folder}/kustomization.yaml"] = yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            **({"namespace": "tenant"} if folder == "census-hr" else {}), "resources": [resource]})
    # The patch of the site, in the kustomization.yaml of the parent.
    files["parent-named/kustomization.yaml"] = yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["children.yaml"],
        "patches": [{"target": {"kind": "Kustomization", "name": "named-hr"}, "patch": yaml.safe_dump({
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
            "metadata": {"name": "not-used"}, "spec": {"patches": SITE_PATCHES}})}]})
    files["parent/kustomization.yaml"] = yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["children.yaml"]})
    files["census/objects.yaml"] = yaml.safe_dump_all(CENSUS)
    files["census/kustomization.yaml"] = yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "namespace": "tenant",
        "resources": ["objects.yaml"]})
    for folder in WAIT:
        files[f"{folder}/objects.yaml"] = yaml.safe_dump_all(wait_objects(folder))
        files[f"{folder}/kustomization.yaml"] = yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "namespace": "tenant",
            "resources": ["objects.yaml"]})
    for folder, doc in NOT_GRANTED.items():
        files[f"{folder}/object.yaml"] = yaml.safe_dump(doc)
        files[f"{folder}/kustomization.yaml"] = yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "namespace": "tenant",
            "resources": ["object.yaml"]})
    for folder, (namespace, name) in CONFIGMAPS.items():
        files[f"{folder}/configmap.yaml"] = yaml.safe_dump({
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": namespace}, "data": {"case": folder}})
    files["class/clusterrole.yaml"] = yaml.safe_dump({
        "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
        "metadata": {"name": "lockdown-class"}, "rules": []})
    for folder in [*CONFIGMAPS, "class"]:
        resource = "clusterrole.yaml" if folder == "class" else "configmap.yaml"
        files[f"{folder}/kustomization.yaml"] = yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": [resource]})
    return files


def artifact(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for name, text in sorted(files.items()):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def main(out: Path) -> None:
    components = lockdown.flux_system("wsl-02") / "gotk-components.yaml"
    docs = [d for d in yaml.safe_load_all(components.read_text()) if d]
    crds = [d for d in docs if d.get("kind") == "CustomResourceDefinition"]
    assert any(d["metadata"]["name"] == "kustomizations.kustomize.toolkit.fluxcd.io" for d in crds)
    (deploy,) = [d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"] == "kustomize-controller"]
    image = deploy["spec"]["template"]["spec"]["containers"][0]["image"]
    args = {site: lockdown.controller_args(site, "kustomize-controller") for site in lockdown.classlist.SITES}
    assert len({tuple(a) for a in args.values()}) == 1, args
    kept = [a for a in args["wsl-02"] if not a.startswith(("--events-addr", "--enable-leader-election"))]
    assert lockdown.flag(kept, "--default-service-account") is None, "git has the flag: the case without it is gone"
    data = artifact(tree())
    (out / "srv").mkdir()
    (out / "srv/source.tar.gz").write_bytes(data)
    (out / "in").mkdir()
    (out / "in/crds.json").write_text(json.dumps(crds))
    role = lockdown.ROOT / "kubernetes/apps/flux-system/accounts/role/clusterrole.yaml"
    (doc,) = [d for d in yaml.safe_load_all(role.read_text()) if d]
    (out / "in/role.json").write_text(json.dumps(doc))
    (out / "in/role-noread.json").write_text(json.dumps(noread(doc)))
    (out / "in/patches.json").write_text(json.dumps([
        accounts.patch_of(n, hrs, SITE_PATCHES if n == "named-hr" else None) for n, (_, hrs) in NAMED.items()]))
    (out / "in/digest.txt").write_text("sha256:" + hashlib.sha256(data).hexdigest())
    (out / "image.txt").write_text(image + "\n")
    (out / "args.txt").write_text("\n".join(kept) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
