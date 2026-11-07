"""Build bounded observability inventory from the existing failover catalogue."""

from pathlib import Path
import json
import shutil
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]


def databases(sites):
    """Use each cluster's selected Flux graph, including database-only apps."""
    result = []
    for site in sites:
        command = (["kustomize", "build"] if shutil.which("kustomize")
                   else ["kubectl", "kustomize"])
        rendered = subprocess.run(command + ["--load-restrictor=LoadRestrictionsNone",
                                  str(ROOT / "kubernetes/clusters" / site["id"] / "apps")],
                                  check=True, capture_output=True, text=True).stdout
        for obj in yaml.safe_load_all(rendered):
            if not obj or obj.get("kind") != "Kustomization":
                continue
            spec = obj["spec"]
            app = spec.get("postBuild", {}).get("substitute", {}).get("CNPG_APP")
            if app:
                name = app + "-pg"
            elif obj["metadata"]["name"] == "cloudnative-pg-cluster":
                path = ROOT / spec["path"] / "cluster.yaml"
                name = yaml.safe_load(path.read_text())["metadata"]["name"]
                app = "shared-postgres"
            else:
                continue
            result.append({"name": name, "app": app, "namespace": "databases",
                           "cluster": site["cluster"], "source": obj["metadata"]["name"]})
    return sorted(result, key=lambda db: (db["cluster"], db["name"]))


def inventory():
    source = json.loads((ROOT / "bin/site-failover-apps.json").read_text())
    sites = [
        {"id": "wsl", "cluster": "homelab-wsl", "probe_url": "http://prometheus-homelab-wsl.monitoring.svc.cluster.local:9090/-/ready"},
        {"id": "wsl-02", "cluster": "homelab-wsl-02", "probe_url": "http://prometheus-homelab-wsl-02.monitoring.svc.cluster.local:9090/-/ready"},
    ]
    apps = []
    for name, spec in source.items():
        if name.startswith("_"):
            continue
        dbs = spec.get("postgres", [])
        if isinstance(dbs, str):
            dbs = [dbs]
        else:
            dbs = [db["cluster"] for db in dbs]
        apps.append({"name": name, "namespace": spec["namespace"], "prefix": spec["prefix"],
                     "workloads": spec.get("workloads", ["deployment/" + item for item in spec.get("deployments", [name])]),
                     "clusters": [site["cluster"] for site in sites], "databases": dbs,
                     "tier": "warm" if spec.get("lease") else "movable",
                     "fencing_required": bool(spec.get("lease"))})
    return {"version": 1, "sites": sites, "apps": apps, "databases": databases(sites)}


if __name__ == "__main__":
    target = ROOT / "observability/generated/inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(inventory(), indent=2) + "\n")
