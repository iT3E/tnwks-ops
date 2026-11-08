#!/usr/bin/env python3
"""ci.yaml covers .github/ci/domains.toml.

    python3 .github/scripts/workflow-conformance.py [repo root]

Each domain and group is an output of ci.yaml's `changes` job and has a job
gated on it, and Build Success needs every job and runs unless cancelled.
Fails (exit 1) with a GitHub annotation per violation.

The general workflow conventions (timeouts, runner labels, pins,
permissions, checkout credentials, same-repo guards) are github-workflows'
workflow-conformance action, which actionlint.yaml runs before this.

Reads YAML through `yq -o=json` (on GitHub's runner images) to stay stdlib.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

errors: list[str] = []


def err(path: Path, msg: str) -> None:
    errors.append(f"::error file={path}::{msg}")


def load(path: Path):
    out = subprocess.run(["yq", "-o=json", ".", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def check_ci(root: Path) -> None:
    path = root / ".github/workflows/ci.yaml"
    config = tomllib.loads((root / ".github/ci/domains.toml").read_text())
    domains = set(config.get("domains", {}))
    groups = set(config.get("groups", {}))
    jobs = load(path)["jobs"]
    outputs = set((jobs.get("changes") or {}).get("outputs", {}))
    gate = next((j for j in jobs.values() if j.get("name") == "Build Success"), None)
    if gate is None:
        err(path, "no Build Success job")
        return
    gate_id = next(k for k, v in jobs.items() if v is gate)
    needs = set(gate.get("needs") or [])
    for d in sorted(domains):
        if d not in outputs and d != "observability_rehearsals":
            err(path, f"domain {d}: not an output of the changes job")
    for d in sorted(domains - {"observability_rehearsals"}):
        job = jobs.get(d)
        if job is None:
            err(path, f"domain {d}: no job named {d}")
        elif f"needs.changes.outputs.{d} == 'true'" not in str(job.get("if", "")):
            err(path, f"job {d}: not gated on needs.changes.outputs.{d}")
    for g in sorted(groups):
        job = jobs.get(g)
        if g not in outputs:
            err(path, f"group {g}: not an output of the changes job")
        if job is None:
            err(path, f"group {g}: no job named {g}")
        elif f"needs.changes.outputs.{g} != '[]'" not in str(job.get("if", "")):
            err(path, f"job {g}: not gated on needs.changes.outputs.{g} != '[]'")
    missing = sorted(set(jobs) - {gate_id} - needs)
    if missing:
        err(path, f"Build Success doesn't need: {', '.join(missing)}")
    if str(gate.get("if", "")).replace(" ", "") != "${{!cancelled()}}":
        err(path, "Build Success must run with if: ${{ !cancelled() }}")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    check_ci(root)
    for e in errors:
        print(e.replace(f"file={root}/", "file="))
    print(f"{len(errors)} violation(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
