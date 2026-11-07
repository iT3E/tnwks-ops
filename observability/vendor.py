"""Validate pinned upstream exports and generate file-provisioned reference dashboards."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "kubernetes/apps/monitoring/grafana/upstream"


def generated_upstream():
    result = {}
    providers = defaultdict(list)
    for entry in json.loads((DIRECTORY / "catalogue.json").read_text()):
        source = (DIRECTORY / "raw" / entry["file"]).read_bytes()
        assert hashlib.sha256(source).hexdigest() == entry["sha256"], entry["file"]
        dashboard = json.loads(source)
        assert dashboard["uid"] == entry["uid"]
        # File provisioning does not perform the UI import's __inputs mapping.
        rendered = json.dumps(dashboard).replace("${DS_PROMETHEUS}", "PBFA97CFB590B2093")
        dashboard = json.loads(rendered)
        dashboard.pop("__inputs", None)
        dashboard["id"] = None
        result[DIRECTORY / entry["file"]] = json.dumps(dashboard, indent=2) + "\n"
        providers[entry["provider"]].append(entry["file"])
    cm = [{"name": f"grafana-upstream-{provider}", "files": files}
          for provider, files in sorted(providers.items())]
    result[DIRECTORY / "kustomization.yaml"] = "---\n" + yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
        "namespace": "monitoring", "configMapGenerator": cm,
        "generatorOptions": {"disableNameSuffixHash": True,
                             "annotations": {"kustomize.toolkit.fluxcd.io/substitute": "disabled"}},
    }, sort_keys=False)
    return result
