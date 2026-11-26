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
  flux-system would. It holds ConfigMaps and one ClusterRole, and no secret.

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


def tree() -> dict[str, str]:
    files = {}
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
    (out / "in/digest.txt").write_text("sha256:" + hashlib.sha256(data).hexdigest())
    (out / "image.txt").write_text(image + "\n")
    (out / "args.txt").write_text("\n".join(kept) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
