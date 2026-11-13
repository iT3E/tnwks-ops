"""Offline manifests and generated-artifact validation; no API server writes."""

import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from generate import ROOT
from versions import CADDY, GARAGE, THANOS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chart", type=Path)
    args = parser.parse_args()
    if args.chart:
        assert hashlib.sha256(args.chart.read_bytes()).hexdigest() == "03bd40e5feb9419ea88e183c7eb569198b9a3e1ec1c0009dfdc9058a05d0a9f1"
    command = ["kustomize", "build"] if shutil.which("kustomize") else ["kubectl", "kustomize"]
    for path in ["grafana/dashboards", "observability-evidence/app", "garage/app",
                 "observability-pki/app", "thanos-metrics/app", "thanos-metrics/archive",
                 "observability-evidence/rules", "security-registry/rules"]:
        rendered = subprocess.run(command + [str(ROOT / "kubernetes/apps/monitoring" / path)],
                                  capture_output=True, text=True, check=True).stdout
        objects = list(yaml.safe_load_all(rendered))
        assert objects, path
        for obj in objects:
            if obj["kind"] == "ConfigMap":
                assert sum(len(v.encode()) for v in obj.get("data", {}).values()) < 900_000
            if obj["kind"] in ("Role", "ClusterRole"):
                for rule in obj["rules"]:
                    assert "secrets" not in rule.get("resources", [])
                    assert set(rule["verbs"]) <= {"get", "list", "watch"}
            if obj["kind"] == "Role" and obj["metadata"]["name"] == "observability-site-overrides":
                assert obj["metadata"]["namespace"] == "flux-system"
                assert obj["rules"][0]["resourceNames"] == ["site-overrides"]
        print(f"Rendered {path}: {len(objects)} resources")
        if args.chart:
            for obj in objects:
                if obj["kind"] != "HelmRelease":
                    continue
                params = {"CLUSTER_NAME": "homelab-wsl", "PEER_CLUSTER_NAME": "homelab-wsl-02",
                          "OBSERVABILITY_STORAGE_CLASS": "local-path",
                          "OBSERVABILITY_ARCHIVE_NODE": "homelab-wsl-02-worker-1"}

                def replace(match, params=params):
                    key, default = match.groups()
                    value = params.get(key, default)
                    assert value is not None, f"missing test substitution {key}"
                    return value

                values = re.sub(r"\$\{([A-Z_0-9]+)(?::=([^}]+))?\}", replace,
                                yaml.safe_dump(obj["spec"]["values"]))
                with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as file:
                    file.write(values)
                    file.flush()
                    template = subprocess.run(["helm", "template", obj["metadata"]["name"],
                        str(args.chart), "--namespace", "monitoring", "--values", file.name],
                        capture_output=True, text=True, check=True).stdout
                workloads = [o for o in yaml.safe_load_all(template) if o and o["kind"] in ("Deployment", "StatefulSet")]
                assert workloads
                if obj["metadata"]["name"] == "observability-evidence":
                    assert workloads[0]["spec"]["template"]["spec"]["serviceAccountName"] == "observability-evidence"
                print(f"Helm schema/render passed: {obj['metadata']['name']}")
    garage = yaml.safe_load((ROOT / "kubernetes/apps/monitoring/garage/app/helmrelease.yaml").read_text())
    pod = garage["spec"]["values"]["controllers"]["garage"]["pod"]
    # The rehearsal runs the versions.py pins; they must be what the site deploys.
    controller = garage["spec"]["values"]["controllers"]["garage"]
    images = {name: f"{c['image']['repository']}:{c['image']['tag']}"
              for group in ("initContainers", "containers") for name, c in controller[group].items()}
    assert images == {"credentials": CADDY, "app": GARAGE, "tls": CADDY}, images
    assert pod["nodeSelector"] == {"kubernetes.io/hostname": "${OBSERVABILITY_ARCHIVE_NODE:=homelab-wsl-02-worker-1}"}
    for cluster in ["wsl", "wsl-02"]:
        graph = subprocess.run(command + ["--load-restrictor=LoadRestrictionsNone",
                               str(ROOT / "kubernetes/clusters" / cluster / "apps")],
                               capture_output=True, text=True, check=True).stdout
        names = {obj["metadata"]["name"] for obj in yaml.safe_load_all(graph) if obj}
        assert {"observability-pki", "observability-evidence", "observability-rules", "thanos-metrics"} <= names
        if cluster == "wsl":
            assert not names & {"observability-garage", "thanos-metrics-archive"}
        else:
            assert {"observability-garage", "thanos-metrics-archive"} <= names
    print("Per-site archive placement passed for both cluster graphs")
    prometheus = yaml.safe_load((ROOT / "kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml").read_text())
    assert prometheus["spec"]["values"]["prometheus"]["prometheusSpec"]["thanos"]["image"] == THANOS
    query = (ROOT / "kubernetes/apps/monitoring/thanos-metrics/app/helmrelease.yaml").read_text()
    assert "--no-query.partial-response" not in query, "a lost site must not error every panel"
    print("Sidecar image and partial-response contract passed")


if __name__ == "__main__":
    main()
