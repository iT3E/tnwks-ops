#!/usr/bin/env python3
"""The owner label check: a required check that only the owner can pass.

    GH_TOKEN=... REPO=<owner>/<name> PR=<number> HEAD_SHA=<sha> OWNER_ID=<id> \\
      python3 -I .github/scripts/owner-label-check.py

Run by .github/workflows/owner-label.yaml on every PR event that can change
the answer (opened, reopened, synchronize, labeled, unlabeled). It reports
on HEAD_SHA, the head commit of the event, and passes or fails:

- A PR outside the owner tiers passes at once, so Renovate's automerge tiers
  keep merging. A PR is in a tier if impact/high or impact/critical is on it
  now, or a labeled event shows it was ever on it: taking the label off
  doesn't take a PR out of its tier.
- impact/high needs owner/review; impact/critical needs owner/approved
  (.github/renovate/autoMerge.json5, docs/operations/renovate.md).
- That label must be on the PR now, and its most recent labeled event must
  be the owner's: the repository owner's user account (OWNER_ID), a person,
  with no GitHub App in between. A bot, a GitHub App (its installation
  token, or a user token it holds for the owner) and a workflow token fail.
- That event must be newer than the push that made HEAD_SHA the head of the
  PR branch (the repository activity API), so a new push needs a new label.
  The check verifies this itself; it doesn't rely on renovate-review.yaml
  clearing the labels on a push.

It fails closed: an API error, a page it can't read, a missing event or
push, a label of the tiers that doesn't exist in the repository (renamed or
deleted), a head that moved, or a PR from another repository in a tier. A
missing event or push is retried a few times first, because the APIs can
lag the webhook by a few seconds.

Stdlib only. Exit 0 passes, 1 fails, with one ::error:: annotation.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

API = "https://api.github.com"
HIGH, CRITICAL = "impact/high", "impact/critical"
REVIEW, APPROVED = "owner/review", "owner/approved"
LABELS = (HIGH, CRITICAL, REVIEW, APPROVED)
MAX_PAGES = 20
ATTEMPTS = 3
RETRY_SECONDS = 10


@dataclass(frozen=True)
class Verdict:
    ok: bool
    message: str
    retry: bool = False


def safe(text: object) -> str:
    """Text from the API, made safe for a log line and an annotation."""
    return re.sub(r"[^A-Za-z0-9_./:+,\[\] -]", "?", str(text))[:120]


def when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def who(event: dict) -> str:
    actor = event.get("actor") or {}
    app = event.get("performed_via_github_app")
    text = f"{safe(actor.get('login', 'nobody'))} ({safe(actor.get('type', 'unknown'))})"
    if app:
        text += f" through the GitHub App {safe(app.get('slug') or app.get('id'))}"
    return text


def is_owner(event: dict, owner_id: int) -> bool:
    actor = event.get("actor") or {}
    return (
        actor.get("id") == owner_id
        and actor.get("type") == "User"
        and event.get("performed_via_github_app") is None
    )


def decide(
    pr: dict,
    events: list[dict],
    push: dict | None,
    missing_labels: list[str],
    head_sha: str,
    owner_id: int,
) -> Verdict:
    """The answer for HEAD_SHA, from what the API returned. No I/O."""
    sha = safe(head_sha[:7])
    if pr["head"]["sha"] != head_sha:
        return Verdict(False, f"the head moved from {sha} to {safe(pr['head']['sha'][:7])}; the run for the new head decides")
    if missing_labels:
        return Verdict(False, f"labels missing from the repository (renamed or deleted?): {', '.join(missing_labels)}")

    now = {label["name"] for label in pr.get("labels", [])}
    labeled = [e for e in events if e.get("event") == "labeled" and e.get("label")]
    ever = now | {e["label"].get("name") for e in labeled}
    if CRITICAL in ever:
        tier, gate = CRITICAL, APPROVED
    elif HIGH in ever:
        tier, gate = HIGH, REVIEW
    else:
        return Verdict(True, f"not in an owner tier: no {HIGH} or {CRITICAL} label, now or before")

    if pr["head"]["repo"] is None or pr["head"]["repo"]["full_name"] != pr["base"]["repo"]["full_name"]:
        return Verdict(False, f"{tier}: the head is in another repository, so its push can't be checked")
    if gate not in now:
        return Verdict(False, f"{tier}: waits for the owner to add {gate} on {sha}")

    mine = sorted((e for e in labeled if e["label"].get("name") == gate), key=lambda e: (when(e["created_at"]), e["id"]))
    if not mine:
        return Verdict(False, f"{tier}: {gate} is on the PR, but no labeled event was found for it", retry=True)
    last = mine[-1]
    if not is_owner(last, owner_id):
        return Verdict(False, f"{tier}: {gate} was last added by {who(last)}, not by the owner")

    if push is None or push.get("after") != head_sha:
        return Verdict(False, f"{tier}: no push of {sha} found on the PR branch", retry=True)
    if when(last["created_at"]) <= when(push["timestamp"]):
        return Verdict(
            False,
            f"{tier}: the owner added {gate} at {safe(last['created_at'])}, before the push of {sha} "
            f"at {safe(push['timestamp'])}; the owner adds it again for this head",
        )
    return Verdict(True, f"{tier}: the owner added {gate} at {safe(last['created_at'])}, after the push of {sha} at {safe(push['timestamp'])}")


# ---------------------------------------------------------------------- API

Fetch = Callable[[str], tuple[int, object, dict]]


def http_fetch(token: str) -> Fetch:
    def fetch(url: str) -> tuple[int, object, dict]:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "owner-label-check",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.load(resp), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, None, dict(e.headers or {})

    return fetch


def next_link(headers: dict) -> str | None:
    link = next((v for k, v in headers.items() if k.lower() == "link"), "") or ""
    for part in link.split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
        if m:
            return m.group(1)
    return None


def get(fetch: Fetch, url: str) -> object:
    status, body, _ = fetch(url)
    if status != 200:
        raise RuntimeError(f"GET {url} returned {status}")
    return body


def get_all(fetch: Fetch, url: str) -> list:
    items: list = []
    for _ in range(MAX_PAGES):
        status, body, headers = fetch(url)
        if status != 200 or not isinstance(body, list):
            raise RuntimeError(f"GET {url} returned {status}")
        items.extend(body)
        url = next_link(headers)
        if url is None:
            return items
    raise RuntimeError(f"more than {MAX_PAGES} pages of events; refusing to decide on part of them")


def gather(fetch: Fetch, repo: str, pr_number: int) -> tuple[dict, list, dict | None, list[str]]:
    base = f"{API}/repos/{repo}"
    pr = get(fetch, f"{base}/pulls/{pr_number}")
    missing = []
    for name in LABELS:
        status, _, _ = fetch(f"{base}/labels/{urllib.parse.quote(name, safe='')}")
        if status == 404:
            missing.append(name)
        elif status != 200:
            raise RuntimeError(f"GET label {name} returned {status}")
    events = get_all(fetch, f"{base}/issues/{pr_number}/events?per_page=100")
    push = None
    head_repo = pr["head"]["repo"]
    if head_repo is not None and head_repo["full_name"] == repo:
        ref = urllib.parse.quote(f"refs/heads/{pr['head']['ref']}", safe="")
        activity = get(fetch, f"{base}/activity?ref={ref}&direction=desc&per_page=1")
        if not isinstance(activity, list):
            raise RuntimeError("the activity API did not return a list")
        push = activity[0] if activity else None
    return pr, events, push, missing


def run(fetch: Fetch, repo: str, pr_number: int, head_sha: str, owner_id: int, sleep=time.sleep) -> Verdict:
    verdict = Verdict(False, "not checked")
    for attempt in range(ATTEMPTS):
        if attempt:
            sleep(RETRY_SECONDS)
        try:
            verdict = decide(*gather(fetch, repo, pr_number), head_sha=head_sha, owner_id=owner_id)
        except (RuntimeError, KeyError, TypeError, ValueError, OSError) as e:
            return Verdict(False, f"could not read the PR: {safe(e)}")
        if not verdict.retry:
            return verdict
    return verdict


def main() -> int:
    try:
        repo = os.environ["REPO"]
        pr_number = int(os.environ["PR"])
        head_sha = os.environ["HEAD_SHA"]
        owner_id = int(os.environ["OWNER_ID"])
        token = os.environ["GH_TOKEN"]
    except (KeyError, ValueError) as e:
        print(f"::error title=Owner label::bad input: {safe(e)}")
        return 1
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        print("::error title=Owner label::bad input: REPO or HEAD_SHA")
        return 1
    verdict = run(http_fetch(token), repo, pr_number, head_sha, owner_id)
    if verdict.ok:
        print(f"#{pr_number}: pass, {verdict.message}")
        return 0
    print(f"::error title=Owner label::#{pr_number}: {verdict.message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
