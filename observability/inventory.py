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


# Content probe per app: its health URL through its Service, and a string the
# answer must contain (a 200 from a proxy or an error page isn't the app). The
# collector probes an app only on the site that runs it (desired replicas > 0).
PROBES = {
    "mealie": ("http://mealie.production.svc.cluster.local:9000/api/app/about", '"version"'),
    "transcriber": ("http://transcriber-app.production.svc.cluster.local:8081/api/healthz", '"ok":true'),
    "changedetection": ("http://changedetection.production.svc.cluster.local:5000/", "<html"),
    "immich": ("http://immich-server.selfhosted.svc.cluster.local:2283/api/server/ping", "pong"),
    "firefly": ("http://firefly.production.svc.cluster.local:8080/health", "OK"),
    "media": ("http://sonarr.media.svc.cluster.local:8989/ping", "OK"),
    "home-assistant": ("http://home-assistant.home-automation.svc.cluster.local:8123/manifest.json", "Home Assistant"),
    # Self-signed on the controller itself: transport and content, not the certificate.
    "unifi": ("https://unifi.networking.svc.cluster.local:8443/status", '"up":true'),
    "ombi": ("http://ombi.production.svc.cluster.local:3579/", "<html"),
    "ombi-f": ("http://ombi-f.production.svc.cluster.local:3579/", "<html"),
    "wizarr": ("http://wizarr.production.svc.cluster.local:5690/", "<html"),
    "jellyfin": ("http://jellyfin.production.svc.cluster.local:8096/health", "Healthy"),
    "plausible": ("http://plausible.selfhosted.svc.cluster.local:8000/api/health", '"postgres":"ok"'),
    "grafana": ("http://grafana.monitoring.svc.cluster.local/api/health", '"database": "ok"'),
}


def inventory():
    source = json.loads((ROOT / "bin/site-failover-apps.json").read_text())
    sites = [
        # router_peer / tunnel: the site's BGP connection and WireGuard interface on
        # the router (infrastructure/terraform/environments/prod/mikrotik/site-tunnels.tf).
        {"id": "wsl", "cluster": "homelab-wsl", "probe_url": "http://prometheus-homelab-wsl.monitoring.svc.cluster.local:9090/-/ready",
         "router_peer": "wsl-site1", "tunnel": "wg-site1"},
        {"id": "wsl-02", "cluster": "homelab-wsl-02", "probe_url": "http://prometheus-homelab-wsl-02.monitoring.svc.cluster.local:9090/-/ready",
         "router_peer": "wsl-site2", "tunnel": "wg-site2"},
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
                     "fencing_required": bool(spec.get("lease")),
                     # PVCs the weekly standby restore copies (restore evidence).
                     "pvcs": [pvc["name"] for pvc in spec.get("pvcs", []) if not pvc.get("transfer_only")]})
        if name in PROBES:
            url, expect = PROBES[name]
            apps[-1]["probe"] = {"url": url, "expect": expect}
    return {"version": 1, "sites": sites, "apps": apps, "databases": databases(sites)}


if __name__ == "__main__":
    target = ROOT / "observability/generated/inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(inventory(), indent=2) + "\n")
