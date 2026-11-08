#!/usr/bin/env python3
"""Post a PR's rendered Kubernetes changes, per live cluster, as one sticky comment.

Runs `flate diff all` (through bin/flate-guard) for each live cluster's
entrypoint against the PR's base branch, and creates or updates a single
comment marked with MARKER. A PR with no rendered changes gets no comment,
or has its existing one updated to say so. flate skips Secret objects, so
no Secret data reaches the comment.

Environment: BASE_REF, PR, REPO, RUN_URL, GH_TOKEN, and RUNNER_TEMP (where
the full per-cluster diffs are written for the artifact upload).
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
            "bin/flate-guard", "diff", "all", "--no-progress",
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


def gh(*args: str) -> str:
    return subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=True).stdout


def main() -> None:
    base, pr, repo, run_url = (os.environ[k] for k in ("BASE_REF", "PR", "REPO", "RUN_URL"))
    out = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / "flate-diff"
    out.mkdir(parents=True, exist_ok=True)

    diffs = {cluster: render(cluster, base, out) for cluster in CLUSTERS}
    existing = gh(
        f"repos/{repo}/issues/{pr}/comments", "--paginate", "--jq",
        f'.[] | select(.user.login == "github-actions[bot]" and (.body | startswith("{MARKER}"))) | .id',
    ).split()

    if not any(diffs.values()) and not existing:
        print("No rendered changes; no comment.")
        return

    text = out / "comment.md"
    text.write_text(body(diffs, base, run_url))
    if existing:
        gh("-X", "PATCH", f"repos/{repo}/issues/comments/{existing[0]}", "-F", f"body=@{text}")
        print(f"Updated comment {existing[0]}.")
    else:
        gh(f"repos/{repo}/issues/{pr}/comments", "-F", f"body=@{text}")
        print("Created the comment.")


if __name__ == "__main__":
    main()
