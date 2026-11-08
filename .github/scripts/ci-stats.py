#!/usr/bin/env python3
"""CI health over the last N days, for the nightly ci-stats workflow.

Reads workflow runs and Actions cache usage from the REST API (stdlib only)
and writes a Markdown report to $GITHUB_STEP_SUMMARY, or stdout locally:

  - runs per day, by workflow and by actor;
  - CI (Build Success) time to result, p50/p90, per event;
  - CI flake rate: runs that only passed on a re-run;
  - Actions cache usage;
  - runs waiting or queued for over an hour.

Exits 1 with an ::error:: per alert (flake rate over 2%, cache over 9 GB,
a run waiting over an hour), so the scheduled run fails and GitHub notifies.

    GH_TOKEN=$(gh auth token) python3 .github/scripts/ci-stats.py --repo iT3E/tnwks-ops
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import statistics
import sys
import urllib.request

FLAKE_RATE_MAX = 0.02
CACHE_BYTES_MAX = 9 * 10**9
WAITING_MAX = dt.timedelta(hours=1)
CI_WORKFLOW = "CI"


def ts(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def get(url: str, token: str) -> tuple[dict, str | None]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ci-stats",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        link = r.headers.get("Link") or ""
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        return json.load(r), (m.group(1) if m else None)


def fetch_runs(repo: str, token: str, days: int, now: dt.datetime) -> list[dict]:
    """Every run created in the window. A filtered query returns at most
    1,000 runs, so a range with more is split in half until each fits."""
    runs: dict[int, dict] = {}
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    def fetch(start: dt.datetime, end: dt.datetime) -> None:
        base = f"https://api.github.com/repos/{repo}/actions/runs?per_page=100&created={start.strftime(fmt)}..{end.strftime(fmt)}"
        page, url = get(base, token)
        if page["total_count"] > 1000 and end - start > dt.timedelta(minutes=10):
            mid = start + (end - start) / 2
            fetch(start, mid)
            fetch(mid + dt.timedelta(seconds=1), end)
            return
        while True:
            for r in page["workflow_runs"]:
                runs[r["id"]] = r
            if not url:
                break
            page, url = get(url, token)

    fetch((now - dt.timedelta(days=days)).replace(microsecond=0), now.replace(microsecond=0))
    return list(runs.values())


def fetch_stuck(repo: str, token: str) -> list[dict]:
    """Runs still waiting or queued, however old (the window misses a run
    stuck since before it)."""
    out = []
    for status in ("waiting", "queued", "pending", "requested"):
        page, _ = get(f"https://api.github.com/repos/{repo}/actions/runs?per_page=100&status={status}", token)
        out += page["workflow_runs"]
    return out


def fetch_cache(repo: str, token: str) -> dict:
    usage, _ = get(f"https://api.github.com/repos/{repo}/actions/cache/usage", token)
    return usage


def workflow(r: dict) -> str:
    # GitHub's dynamic workflows (CodeQL default setup, dependency graph) are
    # named after the trigger.
    n = r["name"] or ""
    if n.startswith("PR #") or n == "Push on main":
        return "CodeQL (default setup)"
    if n.startswith("Graph Update"):
        return "Dependency graph"
    return n


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


def summarize(runs: list[dict], cache: dict, now: dt.datetime, days: int) -> tuple[str, list[str]]:
    since = now - dt.timedelta(days=days)
    window = [r for r in runs if ts(r["created_at"]) >= since]
    lines = [f"## CI stats, last {days} days", "", f"{len(window)} runs, {len(window) / days:.0f} a day.", ""]

    lines += ["### Runs a day, by workflow", "", "| Workflow | Runs/day | Skipped | Failed | Cancelled |", "| --- | --- | --- | --- | --- |"]
    by_wf = collections.defaultdict(list)
    for r in window:
        by_wf[workflow(r)].append(r)
    for name, rs in sorted(by_wf.items(), key=lambda kv: -len(kv[1])):
        c = collections.Counter(r["conclusion"] for r in rs)
        lines.append(f"| {name} | {len(rs) / days:.1f} | {c['skipped']} | {c['failure']} | {c['cancelled']} |")

    lines += ["", "### Runs a day, by actor", "", "| Actor | Runs/day |", "| --- | --- |"]
    for actor, n in collections.Counter(r["actor"]["login"] for r in window if r.get("actor")).most_common(10):
        lines.append(f"| {actor} | {n / days:.1f} |")

    ci = [r for r in window if r["name"] == CI_WORKFLOW and r["status"] == "completed" and r["conclusion"] not in ("skipped", "cancelled")]
    lines += ["", "### CI (Build Success)", "", "| Event | Runs | Failed | p50 to result | p90 to result |", "| --- | --- | --- | --- | --- |"]
    for event in sorted({r["event"] for r in ci}):
        rs = [r for r in ci if r["event"] == event]
        secs = [(ts(r["updated_at"]) - ts(r["created_at"])).total_seconds() for r in rs]
        failed = sum(1 for r in rs if r["conclusion"] == "failure")
        lines.append(f"| {event} | {len(rs)} | {failed} | {pct(secs, 0.5):.0f} s | {pct(secs, 0.9):.0f} s |")
    flaky = [r for r in ci if r["conclusion"] == "success" and r.get("run_attempt", 1) > 1]
    rate = len(flaky) / len(ci) if ci else 0.0
    lines += ["", f"Flake rate (passed only on a re-run): **{rate:.1%}** ({len(flaky)} of {len(ci)})."]

    size = cache.get("active_caches_size_in_bytes", 0)
    lines += ["", "### Actions cache", "", f"{size / 10**9:.1f} GB in {cache.get('active_caches_count', 0)} entries."]

    waiting = [r for r in runs if r["status"] in ("waiting", "queued", "pending", "requested")
               and now - ts(r["created_at"]) > WAITING_MAX]
    lines += ["", "### Waiting over an hour", ""]
    lines += [f"- [{workflow(r)} #{r['run_number']}]({r['html_url']}) {r['status']} since {r['created_at']}" for r in waiting] or ["None."]

    alerts = []
    if rate > FLAKE_RATE_MAX:
        alerts.append(f"CI flake rate {rate:.1%} is over {FLAKE_RATE_MAX:.0%}")
    if size > CACHE_BYTES_MAX:
        alerts.append(f"Actions cache {size / 10**9:.1f} GB is over {CACHE_BYTES_MAX / 10**9:.0f} GB")
    for r in waiting:
        alerts.append(f"{workflow(r)} #{r['run_number']} has been {r['status']} since {r['created_at']}")
    return "\n".join(lines) + "\n", alerts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not args.repo or not token:
        ap.error("needs --repo (or GITHUB_REPOSITORY) and GH_TOKEN")
    now = dt.datetime.now(dt.timezone.utc)
    runs = {r["id"]: r for r in fetch_runs(args.repo, token, args.days, now) + fetch_stuck(args.repo, token)}
    report, alerts = summarize(list(runs.values()), fetch_cache(args.repo, token), now, args.days)
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a") as f:
            f.write(report)
    print(report)
    for a in alerts:
        print(f"::error::{a}")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
