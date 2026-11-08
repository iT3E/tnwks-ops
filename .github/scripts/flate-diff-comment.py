#!/usr/bin/env python3
"""Write a PR's rendered Kubernetes changes, per live cluster, as one comment body.

Runs `flate diff all` (through flate-guard, from github-workflows'
setup-flate) for each live cluster's entrypoint against the PR's base branch,
and writes $RUNNER_TEMP/flate-diff/comment.md, starting with MARKER. It sets
the step output `changes` (true when any cluster renders differently);
kubernetes.yaml posts the body with github-workflows' sticky-comment, which
creates the comment only when there are changes. flate skips Secret objects,
so no Secret data reaches the comment.

Environment: BASE_REF, RUN_URL, RUNNER_TEMP (where the comment body and the
full per-cluster diffs are written, for the artifact upload) and
GITHUB_OUTPUT.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = "<!-- flate-diff -->"
CLUSTERS = ("wsl", "wsl-02")
# GitHub rejects comments over 65,536 characters.
LIMIT = 60_000
# Four backticks, so a ``` inside a rendered value can't close the block.
FENCE = "````"


def render(cluster: str, base: str, out: Path) -> str:
    result = subprocess.run(
        [
            "flate-guard", "diff", "all", "--no-progress",
            "-p", f"kubernetes/clusters/{cluster}",
            "--base", f"origin/{base}",
            "-o", "github",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(f"flate diff for {cluster} failed (exit {result.returncode})")
    (out / f"{cluster}.md").write_text(result.stdout)
    return result.stdout.strip()


def resources(diff: str) -> int:
    # Each changed object is introduced by "# <apiVersion>/<Kind>/<ns>/<name>".
    return len({line for line in diff.splitlines() if line.startswith("# ") and "/" in line})


def body(diffs: dict[str, str], base: str, run_url: str) -> str:
    budget = (LIMIT - 2_000) // len(diffs)
    parts = [MARKER, "### Rendered changes", ""]
    truncated = False
    for cluster, diff in diffs.items():
        if not diff:
            parts += [f"`{cluster}`: no rendered changes.", ""]
            continue
        summary = f"<code>{cluster}</code>: {resources(diff)} resource(s) changed"
        if len(diff) > budget:
            diff = diff[:budget].rsplit("\n", 1)[0] + "\n… (truncated)"
            truncated = True
        parts += [f"<details><summary>{summary}</summary>", "", FENCE + "diff", diff, FENCE, "", "</details>", ""]
    note = f"flate diff against `{base}`, per cluster entrypoint. Secrets aren't rendered."
    if truncated:
        note += " Truncated: the full diff is in the run's `flate-diff` artifact."
    parts.append(f"<sub>{note} [Run]({run_url})</sub>")
    return "\n".join(parts) + "\n"


def main() -> None:
    base, run_url = (os.environ[k] for k in ("BASE_REF", "RUN_URL"))
    out = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "flate-diff"
    out.mkdir(parents=True, exist_ok=True)

    diffs = {cluster: render(cluster, base, out) for cluster in CLUSTERS}
    changes = any(diffs.values())
    (out / "comment.md").write_text(body(diffs, base, run_url))
    print("Rendered changes." if changes else "No rendered changes.")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write(f"changes={'true' if changes else 'false'}\n")


if __name__ == "__main__":
    main()
