"""Compile all source Flux health expressions and test the Grafana backup gate."""

import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    checks = []
    for path in sorted((ROOT / "kubernetes").rglob("*.yaml")):
        if "healthCheckExprs:" not in path.read_text():
            continue
        for obj in yaml.safe_load_all(path.read_text()):
            if not isinstance(obj, dict) or obj.get("kind") != "Kustomization":
                continue
            for rule in obj.get("spec", {}).get("healthCheckExprs", []):
                for field in ["current", "inProgress", "failed"]:
                    if field not in rule:
                        continue
                    item = {"Name": f"{path.relative_to(ROOT)}:{rule.get('kind')}:{field}",
                            "Expression": rule[field], "Cases": []}
                    if rule.get("kind") == "Backup" and field in ("current", "failed"):
                        desired = "completed" if field == "current" else "failed"
                        item["Cases"] = [{"Object": {}, "Want": False, "AllowError": True},
                            {"Object": {"status": {}}, "Want": False}]
                        item["Cases"] += [{"Object": {"status": {"phase": phase}},
                                           "Want": phase == desired}
                                          for phase in ["started", "completed", "failed"]]
                    checks.append(item)
    subprocess.run(["go", "test", "./..."], cwd=ROOT / "observability/healthcheck", check=True)
    subprocess.run(["go", "run", "."], input=json.dumps(checks), text=True,
                   cwd=ROOT / "observability/healthcheck", check=True)


if __name__ == "__main__":
    main()
