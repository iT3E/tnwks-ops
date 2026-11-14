#!/usr/bin/env python3
"""Turn one root's drift plan into the metrics Prometheus alerts on.

    TF_SUMMARY="$summary" python3 -I .github/scripts/terraform-drift.py \
        --root github --outcome success --changes true --prom drift.prom

terraform.yaml's drift job runs this after github-workflows' actions/terraform
has planned the root (mode plan; nothing is applied). Its inputs are what the
action exposes: the plan step's outcome, its `changes` output ("true" when the
plan's detailed exit code is 2) and its `summary` output (a counts line, then
one line per changing address), passed in TF_SUMMARY.

Writes the Prometheus text format for the Pushgateway, without labels (the
push URL's grouping key adds `job` and `root`):

    tnwks_terraform_drift_check_timestamp_seconds  when the check finished
    tnwks_terraform_drift_check_drifted            1 if the plan has changes
    tnwks_terraform_drift_check_changed            addresses that would change
    tnwks_terraform_drift_check_failed             1 if the plan did not run

A plan whose step failed or was skipped (the mask step failed first) is a
failed check: drifted and changed are 0, since nothing is known. A plan with
changes but no address line (outputs or imports only) is drift with 0 changed.

Prints one line of counts, never an address or the summary: the action
already printed those, and the job log is public. Drift adds a warning
annotation. Exits 0 for any result; the plan step fails the job on its own.
Exits 2 on bad arguments.

Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

# The counts line of actions/terraform's summary (github-workflows v1.9.2).
COUNTS = re.compile(r"\*\*Plan:\*\* (\d+) to add, (\d+) to change, (\d+) to destroy, (\d+) to move")
ADDRESS = re.compile(r"^- `[^`]+` \S")
ROOT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
PREFIX = "tnwks_terraform_drift_check"
HELP = {
    "timestamp_seconds": "When the drift check of the root finished (Unix time).",
    "drifted": "1 if the plan of main has changes: live differs from the code.",
    "changed": "Resource addresses the plan would change (create, update, delete, replace, move).",
    "failed": "1 if the plan did not run (init, plan or the SOPS mask failed).",
}


# A NamedTuple, not a dataclass: test_terraform_drift.py loads this file by
# path without putting it in sys.modules, as the other tests here do, and on
# Python 3.12 @dataclass looks the module up there and fails.
class Result(NamedTuple):
    failed: bool
    drifted: bool
    changed: int
    counts: tuple[int, int, int, int] | None
    reason: str = ""


def evaluate(outcome: str, changes: str, summary: str) -> Result:
    if outcome != "success":
        return Result(True, False, 0, None, f"the plan step's outcome is {outcome or 'empty'}")
    if changes not in ("true", "false"):
        return Result(True, False, 0, None, "the plan step reported no changes output")
    match = COUNTS.search(summary)
    counts = tuple(int(n) for n in match.groups()) if match else None
    changed = sum(1 for line in summary.splitlines() if ADDRESS.match(line))
    if changes == "false":
        return Result(False, False, 0, counts)
    return Result(False, True, changed, counts)


def render(result: Result, now: float) -> str:
    values = {
        "timestamp_seconds": f"{now:.0f}",
        "drifted": str(int(result.drifted)),
        "changed": str(result.changed),
        "failed": str(int(result.failed)),
    }
    out = []
    for key, value in values.items():
        out += [f"# HELP {PREFIX}_{key} {HELP[key]}", f"# TYPE {PREFIX}_{key} gauge", f"{PREFIX}_{key} {value}"]
    return "\n".join(out) + "\n"


def line(root: str, result: Result) -> str:
    if result.failed:
        return f"{root}: check failed ({result.reason})"
    if not result.drifted:
        return f"{root}: no drift"
    detail = ""
    if result.counts:
        add, change, destroy, move = result.counts
        detail = f" ({add} to add, {change} to change, {destroy} to destroy, {move} to move)"
    return f"{root}: drift, {result.changed} address(es) would change{detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, help="the root's name in domains.toml")
    parser.add_argument("--outcome", required=True, help="steps.<plan>.outcome")
    parser.add_argument("--changes", default="", help="the action's changes output")
    parser.add_argument("--prom", required=True, type=Path, help="metrics file to write")
    parser.add_argument("--now", type=float, help="Unix time (tests)")
    args = parser.parse_args(argv)
    if not ROOT.match(args.root):
        print(f"::error::terraform-drift: not a root name: {args.root!r}", file=sys.stderr)
        return 2

    result = evaluate(args.outcome, args.changes, os.environ.get("TF_SUMMARY", ""))
    args.prom.write_text(render(result, time.time() if args.now is None else args.now))
    text = line(args.root, result)
    print(f"::warning title=Terraform drift::{text}" if result.drifted else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
