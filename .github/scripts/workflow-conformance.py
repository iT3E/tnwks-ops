#!/usr/bin/env python3
"""Workflow conventions that actionlint and zizmor don't enforce.

    python3 .github/scripts/workflow-conformance.py [repo root]

Fails (exit 1) with a GitHub annotation per violation:

- every job that runs steps has `timeout-minutes` (15 at most on ubuntu-slim);
- `runs-on` is a known label: ubuntu-26.04, ubuntu-slim (API-only jobs), or a
  self-hosted label listed in .github/actionlint.yaml;
- a self-hosted job in a `pull_request` workflow only runs for same-repo PRs
  (its `if:` checks head.repo.full_name against github.repository);
- every remote `uses:` (workflows and local actions) is pinned to a full
  commit SHA, and docker:// images to a digest;
- every workflow sets top-level `permissions`;
- every actions/checkout sets `persist-credentials: false`;
- ci.yaml covers .github/ci/domains.toml: each domain and group is an output
  of `changes` and a job gated on it, and Build Success needs every job.

Reads YAML through `yq -o=json` (on GitHub's runner images) to stay stdlib.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

HOSTED = {"ubuntu-26.04", "ubuntu-slim"}
# GitHub ends an ubuntu-slim job at 15 minutes, whatever timeout-minutes says.
SLIM_MAX_MINUTES = 15
SHA = re.compile(r"^[^@]+@[0-9a-f]{40}$")
DIGEST = re.compile(r"^docker://[^@]+@sha256:[0-9a-f]{64}$")
SAME_REPO = re.compile(r"head\.repo\.full_name\s*==\s*github\.repository")
# pull_request runs the PR's own code. pull_request_target runs main's, and
# zizmor flags it checking out the PR.
PR_EVENTS = {"pull_request"}

errors: list[str] = []


def err(path: Path, msg: str) -> None:
    errors.append(f"::error file={path}::{msg}")


def load(path: Path):
    out = subprocess.run(["yq", "-o=json", ".", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def check_uses(path: Path, where: str, uses: str) -> None:
    if uses.startswith("./"):
        return
    if uses.startswith("docker://"):
        if not DIGEST.match(uses):
            err(path, f"{where}: {uses} is not pinned to a digest")
    elif not SHA.match(uses):
        err(path, f"{where}: {uses} is not pinned to a commit SHA")


def check_steps(path: Path, where: str, steps: list) -> None:
    for i, step in enumerate(steps or []):
        uses = step.get("uses")
        if not uses:
            continue
        name = step.get("name") or uses
        check_uses(path, f"{where} step {i + 1} ({name})", uses)
        if uses.split("@")[0] == "actions/checkout":
            if str((step.get("with") or {}).get("persist-credentials")).lower() != "false":
                err(path, f"{where} step {i + 1}: actions/checkout without persist-credentials: false")


def labels_of(runs_on) -> list[str]:
    if isinstance(runs_on, str):
        return [runs_on]
    if isinstance(runs_on, list):
        return [str(x) for x in runs_on]
    if isinstance(runs_on, dict):
        return labels_of(runs_on.get("labels", [])) + ([runs_on["group"]] if "group" in runs_on else [])
    return []


def check_workflow(path: Path, self_hosted: set[str]) -> None:
    wf = load(path)
    on = wf.get("on") or wf.get(True) or {}
    events = set(on) if isinstance(on, (dict, list)) else {on}
    if "permissions" not in wf:
        err(path, "no top-level permissions")
    for job_id, job in (wf.get("jobs") or {}).items():
        where = f"job {job_id}"
        if "uses" in job:
            check_uses(path, where, job["uses"])
            continue
        if "timeout-minutes" not in job:
            err(path, f"{where}: no timeout-minutes")
        labels = labels_of(job.get("runs-on"))
        hosted_or_expr = all(lbl in HOSTED or lbl.startswith("${{") for lbl in labels)
        unknown = [lbl for lbl in labels if lbl not in HOSTED and lbl not in self_hosted and not lbl.startswith("${{")]
        if unknown:
            err(path, f"{where}: runs-on {unknown} is neither {sorted(HOSTED)} nor a self-hosted label in .github/actionlint.yaml")
        timeout = job.get("timeout-minutes")
        if "ubuntu-slim" in labels and isinstance(timeout, int) and timeout > SLIM_MAX_MINUTES:
            err(path, f"{where}: ubuntu-slim jobs stop at {SLIM_MAX_MINUTES} minutes; timeout-minutes is {timeout}")
        if not hosted_or_expr and events & PR_EVENTS and not SAME_REPO.search(str(job.get("if", ""))):
            err(path, f"{where}: self-hosted job in a PR-triggered workflow without a same-repo check in its if:")
        check_steps(path, where, job.get("steps"))


def check_action(path: Path) -> None:
    action = load(path)
    runs = action.get("runs") or {}
    if runs.get("using") == "composite":
        check_steps(path, "composite", runs.get("steps"))
    elif runs.get("using") == "docker" and str(runs.get("image", "")).startswith("docker://"):
        check_uses(path, "image", runs["image"])


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
    lint = load(root / ".github/actionlint.yaml")
    self_hosted = set((lint.get("self-hosted-runner") or {}).get("labels") or []) - HOSTED
    for path in sorted((root / ".github/workflows").glob("*.y*ml")):
        check_workflow(path, self_hosted)
    for path in sorted((root / ".github/actions").glob("*/action.y*ml")):
        check_action(path)
    check_ci(root)
    for e in errors:
        print(e.replace(f"file={root}/", "file="))
    print(f"{len(errors)} violation(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
