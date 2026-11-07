"""Render the maintained Grafana chart and check migration-critical wiring offline."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

from versions import GRAFANA, RENDERER

ROOT = Path(__file__).resolve().parents[1]
CHART_SHA256 = "bfba0d92aa35f10aebc73368167308d563558693e0a7665d31fe92f59f186738"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chart", required=True, type=Path)
    args = parser.parse_args()
    assert hashlib.sha256(args.chart.read_bytes()).hexdigest() == CHART_SHA256
    path = ROOT / "kubernetes/apps/monitoring/grafana/app"
    release = yaml.safe_load((path / "helmrelease.yaml").read_text())
    remediation = release["spec"]["upgrade"]["remediation"]
    assert remediation["retries"] == 0 and not remediation["remediateLastFailure"]
    params = {"STORAGE_CLASS_DEFAULT": "local-path", "INTERNAL_DOMAIN": "example.invalid",
              "OAUTH2_PROXY_CLIENT_ID": "fixture", "CLUSTER_NAME": "homelab-wsl"}
    values = re.sub(r"\$\{([A-Z_0-9]+)(?::=([^}]+))?\}",
                    lambda match: match[2] if match[2] is not None else params[match[1]],
                    yaml.safe_dump(release["spec"]["values"]))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as file:
        file.write(values)
        file.flush()
        rendered = subprocess.check_output(["helm", "template", "grafana", str(args.chart),
            "--namespace", "monitoring", "--values", file.name], text=True)
    objects = [obj for obj in yaml.safe_load_all(rendered) if obj]
    deployments = {obj["metadata"]["name"]: obj for obj in objects if obj["kind"] == "Deployment"}
    grafana = deployments["grafana"]["spec"]["template"]["spec"]
    renderer = deployments["grafana-image-renderer"]["spec"]["template"]["spec"]
    app = next(c for c in grafana["containers"] if c["name"] == "grafana")
    worker = renderer["containers"][0]
    assert app["image"].removeprefix("docker.io/") == GRAFANA
    assert worker["image"].removeprefix("docker.io/") == RENDERER
    env = {item["name"]: item for item in app["env"]}
    render_env = {item["name"]: item for item in worker["env"]}
    secret = {"name": "grafana-renderer-auth", "key": "token"}
    assert env["GF_RENDERING_RENDERER_TOKEN"]["valueFrom"]["secretKeyRef"] == secret
    assert render_env["AUTH_TOKEN"]["valueFrom"]["secretKeyRef"] == secret
    assert render_env["RATE_LIMIT_MAX_LIMIT"]["value"] == "1"
    assert worker["resources"]["limits"]["memory"] == "768Mi"
    search = next(m for m in app["volumeMounts"] if m["name"] == "search")
    assert env["GF_UNIFIED_STORAGE_INDEX_PATH"]["value"].startswith(search["mountPath"] + "/")
    assert not any(c["name"] == "download-dashboards" for c in grafana.get("initContainers", []))
    # One mounted ConfigMap per pinned reference provider (upstream/catalogue.json)
    # and per kept hand-made board group. Mounted files exist when Grafana starts,
    # so those providers may delete; the sidecar's emptyDir doesn't, so it mustn't.
    catalogue = json.loads((ROOT / "kubernetes/apps/monitoring/grafana/upstream/catalogue.json").read_text())
    mounted = release["spec"]["values"]["dashboardsConfigMaps"]
    assert {e["provider"] for e in catalogue} | {"legacy", "windows"} == set(mounted)
    assert sum(m["name"].startswith("dashboards-") for m in app["volumeMounts"]) == len(mounted)
    providers = release["spec"]["values"]["dashboardProviders"]["dashboardproviders.yaml"]["providers"]
    assert {p["name"] for p in providers} == set(mounted)
    assert all(p["options"]["path"] == f"/var/lib/grafana/dashboards/{p['name']}" and not p["disableDeletion"]
               for p in providers)
    assert release["spec"]["values"]["sidecar"]["dashboards"]["provider"]["disableDelete"] is True
    assert grafana["securityContext"]["fsGroup"] == 472
    # Warm app (docs/wsl/multi-site.md): the lease gate's env must reach the pod
    # as strings; a bare on/off substitution would be a YAML boolean.
    gate = next(c for c in grafana["initContainers"] if c["name"] == "site-lease-gate")
    assert all(isinstance(item["value"], str) for item in gate["env"]), gate["env"]
    print("PASS: maintained Grafana chart, pinned images, renderer authentication/concurrency, "
          "writable search index, stable reference mounts and forward-only migration policy")


if __name__ == "__main__":
    main()
