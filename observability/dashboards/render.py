"""Files, ConfigMap generators and contracts from the catalogue; --check fails on drift."""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

import yaml

from .boards import catalogue
from .layout import resource
from .queries import ROOT

OUTPUT = ROOT / "kubernetes/apps/monitoring/grafana/dashboards"


def generated() -> dict[Path, str]:
    files = {}
    generators = []
    expressions = []
    for board in catalogue():
        suffix = "house" if board.kiosk else board.uid
        name = f"{suffix}.json"
        files[OUTPUT / name] = json.dumps(resource(board), indent=2, ensure_ascii=False) + "\n"
        generators.append({"name": f"grafana-{suffix}", "files": [name], "options": {
            "annotations": {"grafana_folder": board.folder,
                            "kustomize.toolkit.fluxcd.io/substitute": "disabled"},
            "labels": {"grafana_house_dashboard" if board.kiosk else "grafana_dashboard": "true"}}})
        for tab_name, panels in board.tabs.items():
            expressions.extend({"dashboard": suffix, "tab": tab_name, "title": p.title,
                                "expr": p.expr, "unit": p.unit, "kiosk": board.kiosk} for p in panels)
    cm = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
          "namespace": "monitoring", "configMapGenerator": generators,
          "generatorOptions": {"disableNameSuffixHash": True}}
    files[OUTPUT / "kustomization.yaml"] = "---\n" + yaml.safe_dump(cm, sort_keys=False)
    files[ROOT / "observability/generated/panel-queries.json"] = json.dumps(expressions, indent=2) + "\n"
    files[ROOT / "observability/generated/kiosk-query-allowlist.json"] = json.dumps(
        sorted(p["expr"] for p in expressions if p["kiosk"]), indent=2) + "\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = False
    for path, expected in generated().items():
        actual = path.read_text() if path.exists() else ""
        if actual == expected:
            continue
        changed = True
        if args.check:
            print("".join(difflib.unified_diff(actual.splitlines(True), expected.splitlines(True),
                                               fromfile=str(path), tofile="regenerated")))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected)
    return int(changed and args.check)
