"""Writes the inputs of test_nfs_volumes.py as JSON (run.sh calls it).

The test runs in a plain Python image with no YAML parser, so this step,
in the Python of the repository (observability), turns the policies and the
pods of today's users of the NFS server into JSON:

- the pod of the sites (production), from its Deployment;
- the pod of the sites runner (tools), from the template of its HelmRelease;
- the pod of the copy of the network backups (networking), from its CronJob;
- the PersistentVolumes and claims of the vault shares, each claim with the
  targetNamespace of its Flux Kustomization, as Flux applies it.

  python3 render.py <output directory>
"""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[6]
ADMISSION = Path(__file__).resolve().parents[1]
USERS = [
    ("production", "sites", "kubernetes/apps/production/sites/app/deployment.yaml", "Deployment"),
    ("tools", "sites-builder", "kubernetes/apps/tools/actions-runner-controller/runner/app/helmrelease.yaml", "HelmRelease"),
    ("networking", "unifi-gdrive-copy", "kubernetes/apps/networking/unifi/app/gdrive-copy.yaml", "CronJob"),
]
SHARES = ["kubernetes/apps/ai/agents/main/vault-share", "kubernetes/apps/ai/agents/finance/vault-share"]


def pod_spec(doc: dict) -> dict:
    kind = doc["kind"]
    if kind == "Deployment":
        return doc["spec"]["template"]["spec"]
    if kind == "HelmRelease":
        return doc["spec"]["values"]["template"]["spec"]
    if kind == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    raise ValueError(kind)


def main(out: Path) -> None:
    policies = [d for d in yaml.safe_load_all((ADMISSION / "nfsvolumes.yaml").read_text()) if d]
    users = []
    for namespace, name, path, kind in USERS:
        (doc,) = [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d and d["kind"] == kind]
        users.append({"namespace": namespace, "name": name, "spec": pod_spec(doc)})
    volumes, claims = [], []
    for share in SHARES:
        (ks,) = [d for d in yaml.safe_load_all((ROOT / share / "ks.yaml").read_text()) if d]
        volumes += [d for d in yaml.safe_load_all((ROOT / share / "app/volumes.yaml").read_text()) if d]
        for claim in [d for d in yaml.safe_load_all((ROOT / share / "app/claims.yaml").read_text()) if d]:
            claim["metadata"]["namespace"] = ks["spec"]["targetNamespace"]
            claims.append(claim)
    (out / "policies.json").write_text(json.dumps(policies))
    (out / "users.json").write_text(json.dumps(users))
    (out / "volumes.json").write_text(json.dumps(volumes))
    (out / "claims.json").write_text(json.dumps(claims))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
