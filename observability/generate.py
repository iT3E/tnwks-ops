"""Regenerate all checked-in observability artifacts, or reject stale copies."""

import argparse
import json
from pathlib import Path

import yaml

from dashboards import generated
from inventory import inventory
from thanos_manifests import generated_platform
from rules import fixtures, rule_resource
from vendor import generated_upstream

ROOT = Path(__file__).resolve().parents[1]


def artifacts():
    files = generated()
    files.update(generated_platform())
    files.update(generated_upstream())
    inv = json.dumps(inventory(), indent=2) + "\n"
    files[ROOT / "observability/generated/inventory.json"] = inv
    app = ROOT / "kubernetes/apps/monitoring/observability-evidence/app/config"
    files[app / "inventory.json"] = inv
    files[app / "collector.py"] = (ROOT / "observability/collector.py").read_text()
    resource = rule_resource()
    rules = {
        ROOT / "kubernetes/apps/monitoring/observability-evidence/rules/rules.yaml": resource,
        ROOT / "observability/generated/rules-for-test.yaml": {"groups": resource["spec"]["groups"]},
        ROOT / "observability/generated/rule-tests.yaml": fixtures(),
    }
    for path, data in rules.items():
        files[path] = "---\n" + yaml.safe_dump(data, sort_keys=False, width=110)
    # Flux substitutes the site at reconcile time; promtool sees the fixture's.
    test_rules = ROOT / "observability/generated/rules-for-test.yaml"
    files[test_rules] = files[test_rules].replace("${CLUSTER_NAME}", "one")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, expected in artifacts().items():
        if path.exists() and path.read_text() == expected:
            continue
        stale.append(path.relative_to(ROOT))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected)
    if args.check and stale:
        for path in stale:
            print(f"stale generated artifact: {path}")
        raise SystemExit(1)
    print(f"{'Checked' if args.check else 'Generated'} {len(artifacts())} reproducible artifacts")


if __name__ == "__main__":
    main()
