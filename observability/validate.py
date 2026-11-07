"""Offline manifests and generated-artifact validation; no API server writes."""

import shutil
import subprocess

import yaml

from generate import ROOT


def main():
    command = ["kustomize", "build"] if shutil.which("kustomize") else ["kubectl", "kustomize"]
    for path in ["grafana/dashboards", "observability-evidence/app", "garage/app",
                 "observability-pki/app", "thanos-metrics/app", "thanos-metrics/compactor"]:
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
    garage = yaml.safe_load((ROOT / "kubernetes/apps/monitoring/garage/app/helmrelease.yaml").read_text())
    pod = garage["spec"]["values"]["controllers"]["garage"]["pod"]
    assert pod["nodeSelector"] == {"observability.home.arpa/archive": "true"}
    # This delivery deliberately cannot activate itself while networking is in flight.
    for cluster in ["wsl", "wsl-02"]:
        graph = subprocess.run(command + ["--load-restrictor=LoadRestrictionsNone",
                               str(ROOT / "kubernetes/clusters" / cluster / "apps")],
                               capture_output=True, text=True, check=True).stdout
        names = {obj["metadata"]["name"] for obj in yaml.safe_load_all(graph) if obj}
        assert not names & {"observability-evidence", "observability-dashboards", "observability-garage",
                            "observability-pki", "thanos-metrics", "thanos-metrics-compactor"}
    print("Activation guard passed for both cluster graphs")


if __name__ == "__main__":
    main()
