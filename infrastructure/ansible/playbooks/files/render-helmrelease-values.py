#!/usr/bin/env python3
"""render-helmrelease-values.py <helmrelease.yaml> <cluster-settings.yaml>

Print a HelmRelease's spec.values as Flux would apply them on that cluster:
cluster-settings' data substituted into the file's text (${VAR} and
${VAR:=default}, Flux's postBuild envsubst), then the YAML parsed. For the
bootstrap playbooks, which install a chart before Flux runs (02-cni-bootstrap.yml),
so Flux's first reconcile doesn't change what they installed.

An unset ${VAR} without a default is an error, not an empty string.
"""
import re
import sys

import yaml


def main() -> int:
    hr_path, settings_path = sys.argv[1:3]
    settings = yaml.safe_load(open(settings_path, encoding="utf-8"))["data"]
    missing = []

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(3)
        if name in settings:
            return str(settings[name])
        if default is not None:
            return default
        missing.append(name)
        return m.group(0)

    text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:=([^}]*))?\}", sub, open(hr_path, encoding="utf-8").read())
    if missing:
        print(f"{hr_path}: not in {settings_path}: {', '.join(sorted(set(missing)))}", file=sys.stderr)
        return 1
    for doc in yaml.safe_load_all(text):
        if doc and doc.get("kind") == "HelmRelease":
            yaml.safe_dump(doc["spec"].get("values", {}), sys.stdout, sort_keys=False)
            return 0
    print(f"{hr_path}: no HelmRelease", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
