"""Build bounded observability inventory from the existing failover catalogue."""

import json
import re
import shutil
import subprocess
from pathlib import Path

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
# Per-app recovery objectives, in seconds. rpo: the oldest its newest backup
# (every scheduled PVC and every primary database) may be, a daily backup plus
# slack. rto: the longest its weekly standby restore drill may take to write
# the data back. Set 2026-11-07 (the owner delegated the choice): every app has
# the same daily Kopiur/Barman schedule and the same weekly drill, so one pair
# fits all. Override an app here once its schedule or importance differs.
OBJECTIVES = {"rpo": 30 * 3600, "rto": 3600}
APP_OBJECTIVES: dict[str, dict[str, int]] = {}

# Databases outside CloudNativePG, with no exporter of their own: watched
# through their workload's readiness (kube-state-metrics). Ready means every
# container in the pod passes its readiness check; ClickHouse has none, so for
# it Ready only means running, and Plausible's content probe covers its queries.
OTHER_DATABASES = [
    {"namespace": "networking", "kind": "statefulset", "workload": "unifi", "engine": "mongodb", "app": "unifi"},
    {"namespace": "selfhosted", "kind": "statefulset", "workload": "immich-postgres", "engine": "postgresql", "app": "immich"},
    {"namespace": "selfhosted", "kind": "statefulset", "workload": "plausible", "engine": "clickhouse", "app": "plausible"},
    {"namespace": "selfhosted", "kind": "deployment", "workload": "immich-valkey", "engine": "valkey", "app": "immich"},
    # Site 1 only (kubernetes/clusters/wsl-02 doesn't deploy the cache namespace).
    {"namespace": "cache", "kind": "deployment", "workload": "valkey", "engine": "valkey", "app": "valkey",
     "clusters": ["homelab-wsl"]},
]

# The AI domain (docs/apps/agent-platform.md): the parts, scheduled checks,
# result lines and backups that each trust domain must have. A declared row
# stays on the AI board when its source is gone, as Unknown. `clusters` names
# the sites that run a part (homelab-wsl when it is not set); each other site
# declares the part absent by design, as Not applicable. The trust domain is
# the name after `agents-`, or `shared` for a part that serves each of them.
# tests/test_ai_inventory.py holds these lists to the manifests in git.
AI_SITE = "homelab-wsl"
AI_PARTS = [
    *[{"trust_domain": domain, "part": "Agent pod", "namespace": f"agents-{domain}", "kind": "deployment",
       "workload": "agent"} for domain in ("pilot", "main", "finance")],
    *[{"trust_domain": domain, "part": "Sandbox gateway", "namespace": f"agents-{domain}", "kind": "statefulset",
       "workload": "openshell"} for domain in ("pilot", "main", "finance", "workflows")],
    *[{"trust_domain": domain, "part": "Vault server", "namespace": f"agents-{domain}", "kind": "deployment",
       "workload": "vault-server"} for domain in ("main", "finance")],
    {"trust_domain": "main", "part": "Fetch proxy", "namespace": "agents-main", "kind": "deployment",
     "workload": "fetch-proxy"},
    {"trust_domain": "main", "part": "Cluster MCP server", "namespace": "agents-main", "kind": "deployment",
     "workload": "cluster-mcp"},
    {"trust_domain": "main", "part": "Flux MCP server", "namespace": "agents-main", "kind": "deployment",
     "workload": "flux-mcp"},
    {"trust_domain": "main", "part": "Terraform MCP server", "namespace": "agents-main", "kind": "deployment",
     "workload": "terraform-mcp"},
    {"trust_domain": "main", "part": "Code host MCP server", "namespace": "agents-main", "kind": "deployment",
     "workload": "github-mcp"},
    {"trust_domain": "main", "part": "Host MCP server", "namespace": "agents-main", "kind": "deployment",
     "workload": "host-mcp"},
    {"trust_domain": "finance", "part": "Finance MCP server", "namespace": "agents-finance", "kind": "deployment",
     "workload": "firefly-mcp"},
    {"trust_domain": "finance", "part": "Finance proxy", "namespace": "agents-finance", "kind": "deployment",
     "workload": "firefly-proxy"},
    {"trust_domain": "workflows", "part": "Run receiver", "namespace": "agents-workflows", "kind": "deployment",
     "workload": "run-receiver"},
    {"trust_domain": "shared", "part": "Model gateway", "namespace": "ai", "kind": "deployment", "workload": "litellm"},
    {"trust_domain": "shared", "part": "Model bridge", "namespace": "ai", "kind": "deployment",
     "workload": "model-bridge"},
    {"trust_domain": "shared", "part": "MCP gateway", "namespace": "ai", "kind": "deployment",
     "workload": "mcp-gateway"},
    {"trust_domain": "shared", "part": "Sandbox controller", "namespace": "agent-sandbox-system",
     "kind": "deployment", "workload": "agent-sandbox-controller"},
    # Beside the MCP gateway and the fetch proxy (docs/apps/playwright-mcp.md).
    {"trust_domain": "shared", "part": "Browser tool server", "namespace": "ai", "kind": "deployment",
     "workload": "playwright-mcp"},
]
# Each CronJob of the domain, and the longest time since its last good run:
# a day and two hours for a daily check, two runs and slack for the others.
DAILY = 26 * 3600
AI_SCHEDULES = [
    *[{"trust_domain": domain, "namespace": f"agents-{domain}", "cronjob": cronjob, "within": DAILY}
      for domain in ("pilot", "main", "finance", "workflows")
      for cronjob in ("platform-check-isolation", "platform-check-probe")],
    *[{"trust_domain": "finance", "namespace": "agents-finance", "cronjob": cronjob, "within": DAILY}
      for cronjob in ("platform-check-firefly-probe", "platform-check-firefly-dns")],
    *[{"trust_domain": domain, "namespace": f"agents-{domain}", "cronjob": "platform-check-mcp-gateway-probe",
       "within": DAILY} for domain in ("main", "finance")],
    {"trust_domain": "workflows", "namespace": "agents-workflows", "cronjob": "workflow-providers",
     "within": 35 * 60},
    {"trust_domain": "workflows", "namespace": "agents-workflows", "cronjob": "workflow-sweep", "within": 35 * 60},
    # The token job of the App of main (docs/apps/github-token.md): two runs
    # an hour, as the providers job.
    {"trust_domain": "main", "namespace": "agents-main", "cronjob": "github-token", "within": 35 * 60},
    {"trust_domain": "shared", "namespace": "monitoring", "cronjob": "agent-heartbeat-check", "within": DAILY},
    {"trust_domain": "shared", "namespace": "monitoring", "cronjob": "trace-filter-check", "within": DAILY},
    # The network rule of Prometheus: a reader may not write (step 3 of the
    # AI board; kube-prometheus-stack/app/rule-check.yaml). It runs on each
    # site; this list covers the site of the agents, and KubeJobFailed both.
    {"trust_domain": "shared", "namespace": "monitoring", "cronjob": "prometheus-rule-check", "within": DAILY},
    # The key of the metrics scrape of the model gateway calls no model.
    {"trust_domain": "shared", "namespace": "ai", "cronjob": "litellm-key-check", "within": DAILY},
    # The admission objects that a Flux child outside the "ask first" class
    # owns, which the admission policy of the Flux children can not refuse
    # (kubernetes/apps/flux-system/admission/app/objects-check.yaml). It runs
    # on each site; this list covers the site of the agents, and KubeJobFailed
    # both.
    {"trust_domain": "shared", "namespace": "flux-system", "cronjob": "flux-admission-objects", "within": DAILY},
]
# The `<check> result=<result>` lines of the agent pods, which the Vector
# aggregator turns into homelab_agent_platform_result_timestamp_seconds, and
# the window in which a line must come. The memory index runs at each start
# of the pod, so its window is the local history of Prometheus (15 days).
AI_RESULT_WINDOWS = {"instruction-check": "26h", "daily-backup": "26h", "memory-index": "15d",
                     "automations-sync": "3h", "app-drift": "30m"}
# A line that ends a pass well. Any other result (failed, drift) is a failure.
AI_GOOD_RESULTS = ("ok", "first", "none")
AI_RESULTS = [
    *[{"trust_domain": domain, "check": check} for domain in ("pilot", "main", "finance")
      for check in ("instruction-check", "daily-backup", "memory-index")],
    {"trust_domain": "finance", "check": "automations-sync"},
    {"trust_domain": "workflows", "check": "app-drift"},
]
# The snapshot policies of the state volumes and the vault shares. Their
# recovery point objective is the one of every app (OBJECTIVES).
AI_BACKUPS = [
    {"trust_domain": "pilot", "namespace": "agents-pilot", "policy": "agent-state-v2"},
    *[{"trust_domain": domain, "namespace": f"agents-{domain}", "policy": policy}
      for domain in ("main", "finance") for policy in ("agent-state", "vault")],
]
# The checks of the restore tests. A result line lives 5 minutes in
# Prometheus, so a recording rule keeps the time of the newest one.
AI_RESTORE_CHECKS = ("restore-check", "vault-restore-check")


def ai_inventory(sites):
    """The AI lists for the collector: each part on each site, run or absent."""
    clusters = [site["cluster"] for site in sites]
    parts = [{**part, "clusters": part.get("clusters", [AI_SITE])} for part in AI_PARTS]
    for part in parts:
        assert set(part["clusters"]) <= set(clusters), part
    return {"parts": parts,
            "schedules": [{**item, "clusters": [AI_SITE]} for item in AI_SCHEDULES],
            "results": [{**item, "clusters": [AI_SITE]} for item in AI_RESULTS],
            "backups": [{**item, "clusters": [AI_SITE], "within": OBJECTIVES["rpo"]} for item in AI_BACKUPS]}


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
    # Plausible's health covers both its databases: Postgres and ClickHouse.
    "plausible": ("http://plausible.selfhosted.svc.cluster.local:8000/api/health", ['"postgres":"ok"', '"clickhouse":"ok"']),
    "grafana": ("http://grafana.monitoring.svc.cluster.local/api/health", '"database": "ok"'),
}


SITE_RECORDS = ROOT / "infrastructure/terraform/environments/prod/mikrotik/dns-sites.tf"


def shared_vips():
    """The shared ingress VIPs, each with the site the router prefers, and every
    name on them, from the router's site records (dns-sites.tf)."""
    text = SITE_RECORDS.read_text()
    vips = {}
    for local, cluster in (("vip_wsl", "homelab-wsl"), ("vip_wsl_02", "homelab-wsl-02")):
        address = re.search(rf'^\s*{local}\s*=\s*"([0-9.]+)"', text, re.MULTILINE)[1]
        vips[local] = {"address": address, "preferred": cluster}
    names = [{"name": host.split(".")[0], "host": host, "vip": vips[local]["address"]}
             for host, local in re.findall(
                 r'name\s*=\s*"([^"]+)"\s*\n\s*address\s*=\s*local\.(vip_wsl(?:_02)?)\b', text)]
    assert {n["vip"] for n in names} == {v["address"] for v in vips.values()}, "a shared VIP has no names"
    return list(vips.values()), sorted(names, key=lambda n: n["name"])


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
                     "pvcs": [pvc["name"] for pvc in spec.get("pvcs", []) if not pvc.get("transfer_only")],
                     "objectives": APP_OBJECTIVES.get(name, {})})
        if name in PROBES:
            url, expect = PROBES[name]
            apps[-1]["probe"] = {"url": url, "expect": expect}
    vips, ingress = shared_vips()
    return {"version": 1, "sites": sites, "apps": apps, "databases": databases(sites), "objectives": OBJECTIVES,
            "other_databases": OTHER_DATABASES,
            "vips": vips, "ingress": ingress, "ai": ai_inventory(sites)}


if __name__ == "__main__":
    target = ROOT / "observability/generated/inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(inventory(), indent=2) + "\n")
