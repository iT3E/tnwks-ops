#!/usr/bin/env python3
"""The owner label check: a required check that only the owner can pass.

    GH_TOKEN=... REPO=<owner>/<name> PR=<number> HEAD_SHA=<sha> OWNER_ID=<id> \\
      python3 -I .github/scripts/owner-label-check.py
    GH_TOKEN=... REPO=<owner>/<name> PR=<number> OWNER_ID=<id> OWNER_LOGIN=<login> \\
      ACTOR=<login> python3 -I .github/scripts/owner-label-check.py --trust

Run by .github/workflows/owner-label.yaml on every PR event that can change
the answer (opened, reopened, synchronize, labeled, unlabeled, edited). It
reports on HEAD_SHA, the head commit of the event, and passes or fails:

- A PR outside the owner tiers passes at once, so Renovate's automerge tiers
  keep merging. A PR is in a tier if impact/high or impact/critical is on it
  now, or a labeled event shows it was ever on it: taking the label off
  doesn't take a PR out of its tier.
- Four rules put a PR in the tier of owner/approved whatever its labels
  (docs/operations/ci.md, "Agent pull requests"):
  - its author is not the owner, Renovate or the App of the main agent
    (MAIN_AGENT_APP below; until it is set, that App is another author);
  - its author is not that App, and someone other than the owner or
    Renovate pushed to its branch since the branch was last created (the
    activity API of the branch): a push of an App into the branch of
    another author;
  - its author is not that App, and a bot other than Renovate is the
    author or the committer of one of its commits;
  - its author is that App, and it touches a path of the "ask first" class
    (ASK_FIRST and ASK_FIRST_NAMES below), as a file or as the old name of
    a renamed file.
- Two rules fail a PR of that App, or a PR that one of the other three
  rules holds, whatever its labels:
  - its base is not the default branch: its checks ran against that base,
    and a green check stays on the head when the base changes later;
  - its title, its body or one of its commit messages holds a skip
    instruction of GitHub Actions (SKIP below): the squash merge would
    carry it into the commit on main, and the push to main would start no
    workflow (the merge notice, the push checks, the applies).
- impact/high needs owner/review; impact/critical needs owner/approved
  (.github/renovate/autoMerge.json5, docs/operations/renovate.md).
- That label must be on the PR now, and its most recent labeled event must
  be the owner's: the repository owner's user account (OWNER_ID), a person,
  with no GitHub App in between. A bot, a GitHub App (its installation
  token, or a user token it holds for the owner) and a workflow token fail.
- That event must be newer than the push that made HEAD_SHA the head of the
  PR branch (the repository activity API), so a new push needs a new label,
  and newer than the last change of the base of the PR.
  The check verifies this itself; it doesn't rely on renovate-review.yaml
  clearing the labels on a push.

With --trust it prints one line, trusted=true or trusted=false, for the
gates of the CI that run code of a PR branch with a secret or on a runner
of the house (.github/workflows/pr-trust.yaml): true only when the author
and the actor of the event are the owner or Renovate, nobody else pushed
to the branch and no other bot made one of its commits, the base is the
default branch, and the newest push of the branch made its head (HEAD_SHA
when given). It prints trusted=false when it can't tell, after a few tries
when the activity API lags.

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
RENOVATE = "renovate[bot]"
# The bot login of the App of the main agent ("<slug>[bot]"). Empty until
# the App exists (task 6.20 of the agent platform sets it): until then the
# App is another author, and each of its PRs waits for owner/approved.
MAIN_AGENT_APP = ""
# The "ask first" class: a PR of the App of the main agent that touches one
# of these paths waits for owner/approved. A path ending in "/" is a
# directory. The list is the class of the design of the agent platform: the
# Ansible, EdgeOS and Terraform source (the Terraform of GitHub among it),
# the CI and its scripts, actions and config, bin/, the files of the
# impact/critical rules of Renovate (the Flux system files and the cluster
# entry, Talos, the CNI, the system upgrades, the data stores and storage),
# the config of each required check, and the agent platform (its gateway
# routes, tool rules and guardrails, its network policies, RBAC and
# admission, the config of each agent, the plugins and the tool servers).
# The list also holds what else runs with the owner's credentials on the
# workstation (Taskfile.yml and .taskfiles/, .envrc, .vscode/,
# observability/, security/) and the other controls of the cluster: the
# Flux sources and the bootstrap, the shared components, the system
# namespace, identity, the network edge and certificates, the runners of
# the house, backups, monitoring and its audit, the security scanners and
# their accepted findings, and the list of namespaces that Flux applies.
# This file is under .github/, so the list is in its own class.
ASK_FIRST = (
    ".github/",
    "bin/",
    "infrastructure/",
    "talos/",
    "talosconfig",
    "kubernetes/clusters/",
    "kubernetes/flux/",
    "kubernetes/bootstrap/",
    "kubernetes/components/",
    "kubernetes/apps/kustomization.yaml",
    "kubernetes/apps/flux-system/",
    "kubernetes/apps/kube-system/",
    "kubernetes/apps/system-upgrade/",
    "kubernetes/apps/databases/",
    "kubernetes/apps/storage/",
    "kubernetes/apps/ai/",
    "kubernetes/apps/security/",
    "kubernetes/apps/auth/",
    "kubernetes/apps/networking/",
    "kubernetes/apps/cert-manager/",
    "kubernetes/apps/tools/",
    "kubernetes/apps/kopiur-system/",
    "kubernetes/apps/monitoring/",
    "security/",
    "observability/",
    ".taskfiles/",
    ".vscode/",
    "Taskfile.yml",
    ".envrc",
    ".betterleaks.toml",
    ".betterleaksignore",
    ".changie.yaml",
    ".pre-commit-config.yaml",
    ".sops.yaml",
    "AGENTS.md",
)
# File names in the class in any folder: an attributes file can hide a
# diff from the owner's view and from a scan.
ASK_FIRST_NAMES = (".gitattributes",)
# The skip instructions of GitHub Actions ("Skipping workflow runs" in the
# GitHub documentation): [skip ci], [ci skip], [no ci], [skip actions],
# [actions skip], and the trailer skip-checks:true or skip-checks: true.
# Matched with any case and any spaces, which is stricter than GitHub.
SKIP = re.compile(
    r"\[\s*(?:skip\s+ci|ci\s+skip|no\s+ci|skip\s+actions|actions\s+skip)\s*\]|skip-checks\s*:\s*true",
    re.IGNORECASE,
)
BASE_CHANGES = ("base_ref_changed", "automatic_base_change_succeeded")
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


def is_owner_user(user: dict, owner_id: int) -> bool:
    return user.get("id") == owner_id and user.get("type") == "User"


def is_renovate(user: dict) -> bool:
    return user.get("login") == RENOVATE and user.get("type") == "Bot"


def is_agent(user: dict) -> bool:
    return bool(MAIN_AGENT_APP) and user.get("login") == MAIN_AGENT_APP and user.get("type") == "Bot"


def others(pushes: list[dict], owner_id: int) -> list[dict]:
    """The entries of the activity of a branch whose actor is not the owner or Renovate."""
    return [p for p in pushes if not (is_owner_user(p.get("actor") or {}, owner_id) or is_renovate(p.get("actor") or {}))]


def since_creation(entries: list[dict]) -> list[dict]:
    """The activity of a branch (newest first) up to and with its newest
    creation: an earlier branch of the same name is another branch."""
    for i, e in enumerate(entries):
        if e.get("activity_type") == "branch_creation":
            return entries[: i + 1]
    return entries


def bot_commits(commits: list[dict]) -> list[dict]:
    """The commits whose author or committer is a bot other than Renovate."""
    def bot(user: dict | None) -> bool:
        return bool(user) and user.get("type") == "Bot" and not is_renovate(user)
    return [c for c in commits if bot(c.get("author")) or bot(c.get("committer"))]


def ask_first(files: list[dict]) -> list[str]:
    """The paths of the files (and old names of renamed files) in the class."""
    hits = []
    for f in files:
        for path in (f.get("filename"), f.get("previous_filename")):
            if not path:
                continue
            if any(path == p or (p.endswith("/") and path.startswith(p)) for p in ASK_FIRST) or (
                path.rsplit("/", 1)[-1] in ASK_FIRST_NAMES
            ):
                hits.append(path)
    return hits


def skip_marker(pr: dict, commits: list[dict]) -> str | None:
    """Where the PR holds a skip instruction of GitHub Actions, or None."""
    for where in ("title", "body"):
        if SKIP.search(pr.get(where) or ""):
            return f"its {where}"
    for c in commits:
        if SKIP.search((c.get("commit") or {}).get("message") or ""):
            return f"the message of commit {safe(str(c.get('sha', ''))[:7])}"
    return None


def base_is_default(pr: dict) -> bool:
    base = pr.get("base") or {}
    default = (base.get("repo") or {}).get("default_branch")
    return bool(default) and base.get("ref") == default


def forced(pr: dict, pushes: list[dict], files: list[dict] | None, owner_id: int,
           commits: list[dict] = ()) -> str | None:
    """Why the PR is in the tier of owner/approved whatever its labels, or None."""
    author = pr.get("user") or {}
    if not (is_owner_user(author, owner_id) or is_renovate(author) or is_agent(author)):
        return f"the author {safe(author.get('login', 'nobody'))} is not the owner, Renovate or the App of the main agent"
    if not is_agent(author):
        pushed = others(pushes, owner_id)
        if pushed:
            return f"{who(pushed[0])} pushed to the branch of another author"
        made = bot_commits(commits)
        if made:
            return f"a bot made commit {safe(str(made[0].get('sha', ''))[:7])} in the branch of another author"
        return None
    if files is None:
        return "the files of the PR could not be read"
    if pr.get("changed_files", 0) > len(files):
        return "the PR has more files than the API lists"
    hits = ask_first(files)
    if hits:
        return f"it touches the ask-first class: {safe(hits[0])}"
    return None


def decide(
    pr: dict,
    events: list[dict],
    push: dict | None,
    missing_labels: list[str],
    head_sha: str,
    owner_id: int,
    pushes: list[dict] | None = None,
    files: list[dict] | None = None,
    commits: list[dict] = (),
) -> Verdict:
    """The answer for HEAD_SHA, from what the API returned. No I/O.

    push is the newest entry of the activity of the PR branch, and pushes
    all of it since the branch was created (newest first); files are the
    files of the PR, read only when its author is the App of the main
    agent; commits are the commits of the PR."""
    sha = safe(head_sha[:7])
    if pr["head"]["sha"] != head_sha:
        return Verdict(False, f"the head moved from {sha} to {safe(pr['head']['sha'][:7])}; the run for the new head decides")
    if missing_labels:
        return Verdict(False, f"labels missing from the repository (renamed or deleted?): {', '.join(missing_labels)}")
    if pushes is None:
        pushes = [push] if push else []

    now = {label["name"] for label in pr.get("labels", [])}
    labeled = [e for e in events if e.get("event") == "labeled" and e.get("label")]
    ever = now | {e["label"].get("name") for e in labeled}
    if pr.get("commits", 0) > len(commits):
        reason = "the PR has more commits than the API lists"
    else:
        reason = forced(pr, pushes, files, owner_id, list(commits))
    if reason or is_agent(pr.get("user") or {}):
        why = reason or "the App of the main agent"
        if not base_is_default(pr):
            base = safe((pr.get("base") or {}).get("ref", "unknown"))
            return Verdict(False, f"{why}: the base {base} is not the default branch; "
                                  "change the base and push again")
        marker = skip_marker(pr, list(commits))
        if marker:
            return Verdict(False, f"{why}: {marker} holds a skip instruction of GitHub Actions, "
                                  "which would stop the workflows of the merge on main; take it out (a commit "
                                  "message needs a new commit) and push again")
    if reason:
        tier, gate = f"{APPROVED} tier ({reason})", APPROVED
    elif CRITICAL in ever:
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
    rebased = [e for e in events if e.get("event") in BASE_CHANGES]
    if rebased:
        newest = max(rebased, key=lambda e: (when(e["created_at"]), e["id"]))
        if when(last["created_at"]) <= when(newest["created_at"]):
            return Verdict(
                False,
                f"{tier}: the owner added {gate} at {safe(last['created_at'])}, before the base changed "
                f"at {safe(newest['created_at'])}; the owner adds it again for this base",
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


def activity(fetch: Fetch, repo: str, pr: dict) -> list[dict]:
    """Each entry of the activity API for the PR branch, newest first; empty
    for a branch in another repository."""
    head_repo = pr["head"]["repo"]
    if head_repo is None or head_repo["full_name"] != repo:
        return []
    ref = urllib.parse.quote(f"refs/heads/{pr['head']['ref']}", safe="")
    url = f"{API}/repos/{repo}/activity?ref={ref}&direction=desc&per_page=100"
    items: list = []
    for _ in range(MAX_PAGES):
        status, body, headers = fetch(url)
        if status != 200 or not isinstance(body, list):
            raise RuntimeError(f"GET {url} returned {status}")
        items.extend(body)
        if any(e.get("activity_type") == "branch_creation" for e in body):
            return since_creation(items)
        url = next_link(headers)
        if url is None:
            return items
    raise RuntimeError(f"more than {MAX_PAGES} pages of activity; refusing to decide on part of them")


def gather(fetch: Fetch, repo: str, pr_number: int) -> tuple[dict, list, list[dict], list[str], list[dict] | None, list[dict]]:
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
    pushes = activity(fetch, repo, pr)
    files = None
    if is_agent(pr.get("user") or {}):
        files = get_all(fetch, f"{base}/pulls/{pr_number}/files?per_page=100")
    commits = get_all(fetch, f"{base}/pulls/{pr_number}/commits?per_page=100")
    return pr, events, pushes, missing, files, commits


def run(fetch: Fetch, repo: str, pr_number: int, head_sha: str, owner_id: int, sleep=time.sleep) -> Verdict:
    verdict = Verdict(False, "not checked")
    for attempt in range(ATTEMPTS):
        if attempt:
            sleep(RETRY_SECONDS)
        try:
            pr, events, pushes, missing, files, commits = gather(fetch, repo, pr_number)
            verdict = decide(pr, events, pushes[0] if pushes else None, missing, head_sha, owner_id,
                             pushes=pushes, files=files, commits=commits)
        except (RuntimeError, KeyError, TypeError, ValueError, OSError) as e:
            return Verdict(False, f"could not read the PR: {safe(e)}")
        if not verdict.retry:
            return verdict
    return verdict


def trusted(pr: dict, pushes: list[dict], actor: str, owner_id: int, owner_login: str,
            commits: list[dict] = (), head_sha: str = "") -> bool:
    """For the gates of the CI: the author and the actor are the owner or
    Renovate, the head is in this repository, the base is the default
    branch, nobody else pushed and no other bot made a commit, and the
    newest push of the branch made the head (an activity list that lags
    the push must not read as clean)."""
    author = pr.get("user") or {}
    head = pr["head"]["sha"]
    return (
        (is_owner_user(author, owner_id) or is_renovate(author))
        and actor in (owner_login, RENOVATE)
        and pr["head"]["repo"] is not None
        and pr["head"]["repo"]["full_name"] == pr["base"]["repo"]["full_name"]
        and base_is_default(pr)
        and (not head_sha or head == head_sha)
        and bool(pushes)
        and pushes[0].get("after") == head
        and not others(pushes, owner_id)
        and pr.get("commits", 0) <= len(commits)
        and not bot_commits(list(commits))
    )


def trust_main(fetch: Fetch | None = None, sleep=time.sleep) -> int:
    """--trust: one line on stdout, trusted=true or trusted=false. Exit 0.

    A false answer is read again a few times, as the APIs can lag the push."""
    answer = False
    try:
        repo = os.environ["REPO"]
        pr_number = int(os.environ["PR"])
        owner_id = int(os.environ["OWNER_ID"])
        actor = os.environ["ACTOR"]
        owner_login = os.environ["OWNER_LOGIN"]
        head_sha = os.environ.get("HEAD_SHA", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("REPO")
        if head_sha and not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ValueError("HEAD_SHA")
        fetch = fetch or http_fetch(os.environ["GH_TOKEN"])
        for attempt in range(ATTEMPTS):
            if attempt:
                sleep(RETRY_SECONDS)
            pr = get(fetch, f"{API}/repos/{repo}/pulls/{pr_number}")
            commits = get_all(fetch, f"{API}/repos/{repo}/pulls/{pr_number}/commits?per_page=100")
            pushes = activity(fetch, repo, pr)
            answer = trusted(pr, pushes, actor, owner_id, owner_login, commits, head_sha)
            head = pr["head"]["sha"]
            same = pr["head"]["repo"] is not None and pr["head"]["repo"]["full_name"] == repo
            lag = same and ((head_sha and head != head_sha) or not pushes or pushes[0].get("after") != head)
            if answer or not lag:
                break
    except (KeyError, RuntimeError, TypeError, ValueError, OSError) as e:
        answer = False
        print(f"::warning title=Trust::could not tell, so not trusted: {safe(e)}", file=sys.stderr)
    print(f"trusted={'true' if answer else 'false'}")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--trust"]:
        return trust_main()
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
