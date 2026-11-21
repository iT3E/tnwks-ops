"""Tests for .github/scripts/owner-label-check.py.

    python3 -m unittest discover -s .github/scripts -p 'test_owner_label_check.py' -v

The API bodies below are recorded responses of this repository (a PR's
issue events, the activity API of its branch, the PR), trimmed to the
fields the script reads, with neutral logins and IDs.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import sys
import unittest
from pathlib import Path

# The content rule parses YAML with PyYAML. CI installs the pinned version
# of .github/scripts/owner-label-requirements.txt; with no parser the rule
# holds each file under kubernetes/ (test_no_parser_holds_the_app).
import yaml  # noqa: F401

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("owner_label_check", HERE / "owner-label-check.py")
check = importlib.util.module_from_spec(spec)
# dataclasses looks the module up in sys.modules (Python 3.12 fails without it).
sys.modules[spec.name] = check
spec.loader.exec_module(check)

REPO = "example/ops"
OWNER_ID = 1001
HEAD = "d94fb5feddd8f36a56899f90dcebba1787aedbdb"
OLD_HEAD = "476290ce0123456789abcdef0123456789abcdef"

OWNER = {"login": "owner-account", "id": OWNER_ID, "type": "User", "site_admin": False}
DEPS_BOT = {"login": "dependency-bot[bot]", "id": 2002, "type": "Bot", "site_admin": False}
WORKFLOW_BOT = {"login": "workflow-bot[bot]", "id": 3003, "type": "Bot", "site_admin": False}
REVIEWER_BOT = {"login": "reviewer-app[bot]", "id": 4004, "type": "Bot", "site_admin": False}
OTHER_USER = {"login": "someone-else", "id": 5005, "type": "User", "site_admin": False}
REVIEWER_APP = {"id": 777, "slug": "reviewer-app", "node_id": "A_1", "owner": OWNER, "name": "reviewer app"}
AGENT_APP = {"login": "agent-app[bot]", "id": 6006, "type": "Bot", "site_admin": False}
# The recorded bodies name Renovate with a neutral login.
check.RENOVATE = DEPS_BOT["login"]

_ids = iter(range(34866273000, 34866299999))


def event(kind: str, actor: dict, at: str, label: str | None = None, app: dict | None = None) -> dict:
    e = {
        "id": next(_ids),
        "node_id": "LE_x",
        "actor": actor,
        "event": kind,
        "commit_id": None,
        "commit_url": None,
        "created_at": at,
        "performed_via_github_app": app,
    }
    if label is not None:
        e["label"] = {"name": label, "color": "1D76DB"}
    return e


# Renovate opens an impact/high PR and labels it; the labeler adds its area.
OPENED = [
    event("labeled", DEPS_BOT, "2026-11-02T01:23:59Z", "impact/high"),
    event("labeled", DEPS_BOT, "2026-11-02T01:23:59Z", "renovate/container"),
    event("labeled", WORKFLOW_BOT, "2026-11-02T01:24:06Z", "area/kubernetes"),
    event("head_ref_force_pushed", DEPS_BOT, "2026-11-02T14:18:29Z"),
]
# The activity API's newest entry for the PR branch.
PUSH = {
    "id": 1,
    "node_id": "RA_x",
    "before": OLD_HEAD,
    "after": HEAD,
    "ref": "refs/heads/renovate/example",
    "timestamp": "2026-11-02T14:18:29Z",
    "activity_type": "force_push",
    "actor": DEPS_BOT,
}


def pr(labels: list[str], head: str = HEAD, head_repo: str | None = REPO, user: dict = DEPS_BOT,
       changed_files: int = 1, base: str = "main", title: str = "chore: a change", body: str | None = None) -> dict:
    return {
        "number": 42,
        "state": "open",
        "title": title,
        "body": body,
        "user": user,
        "changed_files": changed_files,
        "labels": [{"name": n, "color": "000000"} for n in labels],
        "head": {
            "sha": head,
            "ref": "renovate/example",
            "repo": None if head_repo is None else {"full_name": head_repo},
        },
        "base": {"ref": base, "repo": {"full_name": REPO, "default_branch": "main"}},
    }


def ready(f: dict) -> dict:
    """A file of the files API as gather() leaves it: a plain file, with no
    Flux object and no diff, unless the test says otherwise. A file with
    "raw" stays as it is."""
    if f.pop("raw", False):
        return f
    f = dict(f)
    if f.get("status") != "removed":
        f.setdefault("mode", "100644")
        f.setdefault("patch", "")
    if any((n or "").startswith("kubernetes/") for n in (f.get("filename"), f.get("previous_filename"))):
        f.setdefault("head", None)
        f.setdefault("base", None)
    return f


def decide(p, events, push=PUSH, missing=(), head=HEAD):
    return check.decide(p, events, push, list(missing), head, OWNER_ID)


class Tiers(unittest.TestCase):
    def test_automerge_tier_passes(self):
        events = [event("labeled", DEPS_BOT, "2026-11-02T01:23:59Z", "type/patch")]
        v = decide(pr(["type/patch", "area/kubernetes"]), events, push=None)
        self.assertTrue(v.ok, v.message)
        self.assertIn("not in an owner tier", v.message)

    def test_owner_pr_without_tier_labels_passes(self):
        self.assertTrue(decide(pr([]), [], push=None).ok)

    def test_tier_label_removed_still_gated(self):
        events = OPENED + [event("unlabeled", REVIEWER_BOT, "2026-11-02T15:00:00Z", "impact/high")]
        v = decide(pr(["renovate/container"]), events)
        self.assertFalse(v.ok)
        self.assertIn("waits for the owner to add owner/review", v.message)

    def test_critical_needs_approved_not_review(self):
        events = OPENED + [
            event("labeled", DEPS_BOT, "2026-11-02T01:23:59Z", "impact/critical"),
            event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review"),
        ]
        v = decide(pr(["impact/high", "impact/critical", "owner/review"]), events)
        self.assertFalse(v.ok)
        self.assertIn("owner/approved", v.message)

    def test_critical_with_owner_approval_passes(self):
        events = OPENED + [
            event("labeled", DEPS_BOT, "2026-11-02T01:23:59Z", "impact/critical"),
            event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/approved"),
        ]
        v = decide(pr(["impact/high", "impact/critical", "owner/approved"]), events)
        self.assertTrue(v.ok, v.message)


class WhoApplied(unittest.TestCase):
    def test_no_label_fails(self):
        v = decide(pr(["impact/high"]), OPENED)
        self.assertFalse(v.ok)
        self.assertFalse(v.retry)

    def test_owner_label_after_push_passes(self):
        events = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        v = decide(pr(["impact/high", "owner/review"]), events)
        self.assertTrue(v.ok, v.message)

    def test_workflow_token_fails(self):
        events = OPENED + [event("labeled", WORKFLOW_BOT, "2026-11-02T15:00:00Z", "owner/review")]
        v = decide(pr(["impact/high", "owner/review"]), events)
        self.assertFalse(v.ok)
        self.assertIn("workflow-bot[bot] (Bot)", v.message)

    def test_reviewer_app_installation_token_fails(self):
        events = OPENED + [event("labeled", REVIEWER_BOT, "2026-11-02T15:00:00Z", "owner/review")]
        self.assertFalse(decide(pr(["impact/high", "owner/review"]), events).ok)

    def test_app_acting_for_the_owner_fails(self):
        # A user access token of a GitHub App: the actor is the owner, but the
        # event names the App.
        events = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review", app=REVIEWER_APP)]
        v = decide(pr(["impact/high", "owner/review"]), events)
        self.assertFalse(v.ok)
        self.assertIn("through the GitHub App reviewer-app", v.message)

    def test_other_user_fails(self):
        events = OPENED + [event("labeled", OTHER_USER, "2026-11-02T15:00:00Z", "owner/review")]
        self.assertFalse(decide(pr(["impact/high", "owner/review"]), events).ok)

    def test_owner_id_with_bot_type_fails(self):
        fake = dict(OWNER, type="Bot")
        events = OPENED + [event("labeled", fake, "2026-11-02T15:00:00Z", "owner/review")]
        self.assertFalse(decide(pr(["impact/high", "owner/review"]), events).ok)

    def test_latest_event_counts(self):
        # The owner added it, the App took it off and put it back.
        events = OPENED + [
            event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review"),
            event("unlabeled", REVIEWER_BOT, "2026-11-02T15:01:00Z", "owner/review"),
            event("labeled", REVIEWER_BOT, "2026-11-02T15:02:00Z", "owner/review"),
        ]
        self.assertFalse(decide(pr(["impact/high", "owner/review"]), events).ok)

    def test_events_out_of_order_sorted_by_time(self):
        events = OPENED + [
            event("labeled", REVIEWER_BOT, "2026-11-02T15:02:00Z", "owner/review"),
            event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review"),
        ]
        self.assertFalse(decide(pr(["impact/high", "owner/review"]), events).ok)

    def test_label_on_but_event_missing_retries(self):
        v = decide(pr(["impact/high", "owner/review"]), OPENED)
        self.assertFalse(v.ok)
        self.assertTrue(v.retry)


class Freshness(unittest.TestCase):
    def test_label_before_push_fails(self):
        events = [event("labeled", OWNER, "2026-11-02T10:00:00Z", "owner/review")] + OPENED
        v = decide(pr(["impact/high", "owner/review"]), events)
        self.assertFalse(v.ok)
        self.assertIn("adds it again", v.message)

    def test_label_same_second_as_push_fails(self):
        events = OPENED + [event("labeled", OWNER, PUSH["timestamp"], "owner/review")]
        self.assertFalse(decide(pr(["impact/high", "owner/review"]), events).ok)

    def test_push_not_found_retries(self):
        events = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        v = decide(pr(["impact/high", "owner/review"]), events, push=None)
        self.assertFalse(v.ok)
        self.assertTrue(v.retry)

    def test_newest_push_is_another_sha_fails(self):
        events = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        push = dict(PUSH, after=OLD_HEAD)
        self.assertFalse(decide(pr(["impact/high", "owner/review"]), events, push=push).ok)

    def test_head_moved_since_event_fails(self):
        events = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        v = decide(pr(["impact/high", "owner/review"], head=OLD_HEAD), events)
        self.assertFalse(v.ok)
        self.assertIn("head moved", v.message)

    def test_fork_in_tier_fails(self):
        events = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        v = decide(pr(["impact/high", "owner/review"], head_repo="someone/fork"), events, push=None)
        self.assertFalse(v.ok)
        self.assertIn("another repository", v.message)


class Labels(unittest.TestCase):
    def test_missing_label_fails_every_pr(self):
        v = decide(pr([]), [], push=None, missing=["owner/review"])
        self.assertFalse(v.ok)
        self.assertIn("owner/review", v.message)


class FakeAPI:
    """Serves recorded bodies by URL; anything else is a 500."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str):
        self.calls.append(url)
        # An exact URL, or a key ending in "?" for any query.
        answer = self.routes.get(url) or next(
            (a for k, a in self.routes.items() if k.endswith("?") and url.startswith(k)), (500, None, {})
        )
        return answer() if callable(answer) else answer


BASE = f"{check.API}/repos/{REPO}"


def routes(p: dict, pages: list[list], push: list | None = None) -> dict:
    r = {f"{BASE}/pulls/42": (200, p, {})}
    for name in check.LABELS:
        r[f"{BASE}/labels/{name.replace('/', '%2F')}"] = (200, {"name": name}, {})
    for i, page in enumerate(pages):
        url = f"{BASE}/issues/42/events?per_page=100" + ("" if i == 0 else f"&page={i + 1}")
        nxt = {"Link": f'<{BASE}/issues/42/events?per_page=100&page={i + 2}>; rel="next"'} if i + 1 < len(pages) else {}
        r[url] = (200, page, nxt)
    r[f"{BASE}/activity?"] = (200, [PUSH] if push is None else push, {})
    r[f"{BASE}/pulls/42/commits?per_page=100"] = (200, [], {})
    return r


class Run(unittest.TestCase):
    def go(self, api: FakeAPI) -> check.Verdict:
        return check.run(api, REPO, 42, HEAD, OWNER_ID, sleep=lambda s: None)

    def test_events_on_a_second_page_are_read(self):
        filler = [event("labeled", WORKFLOW_BOT, "2026-11-02T01:30:00Z", "area/kubernetes") for _ in range(100)]
        last = [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        api = FakeAPI(routes(pr(["impact/high", "owner/review"]), [OPENED + filler, last]))
        v = self.go(api)
        self.assertTrue(v.ok, v.message)
        self.assertTrue(any("page=2" in c for c in api.calls))

    def test_unreadable_page_fails(self):
        r = routes(pr(["impact/high", "owner/review"]), [OPENED, []])
        r[f"{BASE}/issues/42/events?per_page=100&page=2"] = (502, None, {})
        v = self.go(FakeAPI(r))
        self.assertFalse(v.ok)
        self.assertIn("could not read", v.message)

    def test_endless_pages_fail(self):
        r = routes(pr(["impact/high"]), [OPENED])
        loop = {"Link": f'<{BASE}/issues/42/events?per_page=100>; rel="next"'}
        r[f"{BASE}/issues/42/events?per_page=100"] = (200, OPENED, loop)
        v = self.go(FakeAPI(r))
        self.assertFalse(v.ok)
        self.assertIn("pages", v.message)

    def test_api_error_fails(self):
        r = routes(pr(["impact/high"]), [OPENED])
        r[f"{BASE}/pulls/42"] = (403, None, {})
        self.assertFalse(self.go(FakeAPI(r)).ok)

    def test_renamed_label_fails(self):
        r = routes(pr([]), [[]])
        r[f"{BASE}/labels/owner%2Freview"] = (404, None, {})
        v = self.go(FakeAPI(r))
        self.assertFalse(v.ok)
        self.assertIn("renamed or deleted", v.message)

    def test_lagging_event_is_retried(self):
        late = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        answers = iter([(200, OPENED, {}), (200, late, {})])
        r = routes(pr(["impact/high", "owner/review"]), [OPENED])
        r[f"{BASE}/issues/42/events?per_page=100"] = lambda: next(answers)
        v = self.go(FakeAPI(r))
        self.assertTrue(v.ok, v.message)

    def test_retries_stop(self):
        api = FakeAPI(routes(pr(["impact/high", "owner/review"]), [OPENED]))
        v = self.go(api)
        self.assertFalse(v.ok)
        self.assertEqual(sum(1 for c in api.calls if c == f"{BASE}/pulls/42"), check.ATTEMPTS)

    def test_branch_ref_is_quoted(self):
        p = pr(["impact/high", "owner/review"])
        p["head"]["ref"] = "renovate/a&b c"
        events = OPENED + [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        api = FakeAPI(routes(p, [events]))
        self.assertTrue(self.go(api).ok)
        activity = next(c for c in api.calls if "/activity?" in c)
        self.assertIn("ref=refs%2Fheads%2Frenovate%2Fa%26b%20c&", activity)

    def test_no_activity_lookup_for_fork(self):
        api = FakeAPI(routes(pr([], head_repo="someone/fork"), [[]]))
        self.assertTrue(self.go(api).ok)
        self.assertFalse(any("/activity" in c for c in api.calls))


class Safe(unittest.TestCase):
    def test_strips_newlines_and_markup(self):
        self.assertEqual(check.safe("a\n::set-env name=x::y"), "a?::set-env name?x::y")


class Main(unittest.TestCase):
    def test_bad_input_fails(self):
        import os
        from unittest import mock

        env = {"REPO": REPO, "PR": "42", "HEAD_SHA": "not-a-sha", "OWNER_ID": "1001", "GH_TOKEN": "x"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("builtins.print"):
            self.assertEqual(check.main(), 1)
        env = copy.deepcopy(env)
        del env["OWNER_ID"]
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("builtins.print"):
            self.assertEqual(check.main(), 1)


def entry(actor: dict, at: str = "2026-11-02T14:18:29Z", after: str = HEAD, kind: str = "push") -> dict:
    return dict(PUSH, id=next(_ids), actor=actor, timestamp=at, after=after, activity_type=kind)


class AgentApp(unittest.TestCase):
    """The three rules of the tier of owner/approved for the App of the main agent."""

    def setUp(self):
        check.MAIN_AGENT_APP = AGENT_APP["login"]

    def tearDown(self):
        check.MAIN_AGENT_APP = ""

    def go(self, p, events=(), pushes=None, files=()):
        pushes = [entry(p["user"])] if pushes is None else pushes
        return check.decide(p, list(events), pushes[0] if pushes else None, [], HEAD, OWNER_ID,
                            pushes=pushes, files=[ready(f) for f in files])

    def test_another_author_waits_for_owner_approved(self):
        for author in (OTHER_USER, REVIEWER_BOT, WORKFLOW_BOT):
            v = self.go(pr([], user=author))
            self.assertFalse(v.ok, author)
            self.assertIn("owner/approved tier (the author", v.message)
            self.assertIn("waits for the owner to add owner/approved", v.message)

    def test_another_author_passes_with_the_owners_label_after_the_push(self):
        events = [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/approved")]
        v = self.go(pr(["owner/approved"], user=OTHER_USER), events)
        self.assertTrue(v.ok, v.message)

    def test_owner_review_is_not_enough_for_another_author(self):
        events = [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/review")]
        self.assertFalse(self.go(pr(["owner/review"], user=OTHER_USER), events).ok)

    def test_until_the_app_is_set_it_is_another_author(self):
        check.MAIN_AGENT_APP = ""
        v = self.go(pr([], user=AGENT_APP), files=[{"filename": "docs/a.md"}])
        self.assertFalse(v.ok)
        self.assertIn("is not the owner, Renovate or the App", v.message)

    def test_a_push_of_an_app_into_the_branch_of_the_owner(self):
        pushes = [entry(AGENT_APP, "2026-11-02T14:20:00Z"), entry(OWNER, "2026-11-02T14:00:00Z")]
        v = self.go(pr([], user=OWNER), pushes=pushes)
        self.assertFalse(v.ok)
        self.assertIn("agent-app[bot] (Bot) pushed to the branch of another author", v.message)

    def test_an_old_push_of_an_app_still_counts(self):
        pushes = [entry(OWNER, "2026-11-02T14:20:00Z"), entry(AGENT_APP, "2026-11-02T14:00:00Z", after=OLD_HEAD)]
        self.assertFalse(self.go(pr([], user=OWNER), pushes=pushes).ok)

    def test_a_push_of_an_app_into_a_renovate_branch(self):
        pushes = [entry(AGENT_APP, kind="force_push"), entry(DEPS_BOT, "2026-11-02T01:00:00Z")]
        self.assertFalse(self.go(pr(["type/patch"]), pushes=pushes).ok)

    def test_the_owner_and_renovate_alone_pass_as_before(self):
        self.assertTrue(self.go(pr([], user=OWNER), pushes=[entry(OWNER)]).ok)
        self.assertTrue(self.go(pr(["type/patch"]), pushes=[entry(DEPS_BOT)]).ok)

    def test_the_app_outside_the_class_passes_at_once(self):
        files = [{"filename": "kubernetes/apps/selfhosted/example/app/configmap.yaml", "head": CONFIGMAP,
                  "base": CONFIGMAP, "patch": "@@ -1 +1 @@\n-  a: b\n+  a: c"}, {"filename": "docs/a.md"}]
        v = self.go(pr([], user=AGENT_APP, changed_files=2), files=files)
        self.assertTrue(v.ok, v.message)

    def test_the_owner_may_push_into_the_branch_of_the_app(self):
        files = [{"filename": "docs/a.md"}]
        v = self.go(pr([], user=AGENT_APP), pushes=[entry(OWNER), entry(AGENT_APP, "2026-11-02T14:00:00Z")], files=files)
        self.assertTrue(v.ok, v.message)

    def test_each_path_of_the_class_holds_the_app(self):
        for path in ("infrastructure/ansible/roles/x/tasks/main.yml", ".github/workflows/ci.yaml",
                     ".github/scripts/owner-label-check.py", "bin/ci-changes", "talos/wsl/patch.yaml",
                     "kubernetes/apps/ai/mcp-gateway/app/config/config.yaml", "kubernetes/clusters/wsl/cluster-settings.yaml",
                     "kubernetes/apps/kube-system/cilium/app/helmrelease.yaml", ".betterleaks.toml", "AGENTS.md"):
            v = self.go(pr([], user=AGENT_APP), files=[{"filename": path}])
            self.assertFalse(v.ok, path)
            self.assertIn("ask-first class", v.message)

    def test_a_rename_out_of_the_class_holds_the_app(self):
        files = [{"filename": "docs/old-policy.yaml", "previous_filename": "kubernetes/apps/ai/agents/main/agent/app/networkpolicy.yaml"}]
        self.assertFalse(self.go(pr([], user=AGENT_APP), files=files).ok)

    def test_a_name_that_only_starts_like_a_path_is_not_in_the_class(self):
        v = self.go(pr([], user=AGENT_APP), files=[{"filename": "binaries/readme.md"}, {"filename": "AGENTS.md.bak"}])
        self.assertTrue(v.ok, v.message)

    def test_more_files_than_listed_holds_the_app(self):
        v = self.go(pr([], user=AGENT_APP, changed_files=3001), files=[{"filename": "docs/a.md"}])
        self.assertFalse(v.ok)
        self.assertIn("more files than the API lists", v.message)

    def test_the_class_passes_with_the_owners_label(self):
        events = [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/approved")]
        v = self.go(pr(["owner/approved"], user=AGENT_APP), events, files=[{"filename": "infrastructure/ansible/site.yml"}])
        self.assertTrue(v.ok, v.message)

    def test_a_fork_of_another_author_fails(self):
        v = self.go(pr([], user=OTHER_USER, head_repo="someone-else/ops"), pushes=[])
        self.assertFalse(v.ok)
        self.assertIn("another repository", v.message)


CONFIGMAP = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: example\ndata:\n  a: b\n"
HELMRELEASE = "---\napiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\nmetadata:\n  name: example\n"
APP = "kubernetes/apps/selfhosted/example/"


class Content(unittest.TestCase):
    """The content rule: what a PR of the App outside the class may not hold."""

    def setUp(self):
        check.MAIN_AGENT_APP = AGENT_APP["login"]

    def tearDown(self):
        check.MAIN_AGENT_APP = ""

    def go(self, *files):
        return check.decide(pr([], user=AGENT_APP, changed_files=len(files)), [], entry(AGENT_APP), [], HEAD,
                            OWNER_ID, pushes=[entry(AGENT_APP)], files=[ready(f) for f in files])

    def held(self, why, *files):
        v = self.go(*files)
        self.assertFalse(v.ok, files)
        self.assertIn("owner/approved tier (", v.message)
        self.assertIn(why, v.message)

    def test_a_new_flux_object_holds_the_app(self):
        for kind in ("helm.toolkit.fluxcd.io/v2", "kustomize.toolkit.fluxcd.io/v1", "source.toolkit.fluxcd.io/v1",
                     "notification.toolkit.fluxcd.io/v1beta3", "image.toolkit.fluxcd.io/v1beta2"):
            self.held("a Flux object", {"filename": APP + "app/x.yaml", "status": "added",
                                         "head": f"apiVersion: {kind}\nkind: X\n"})

    def test_a_change_of_a_flux_object_holds_the_app(self):
        self.held("a Flux object", {"filename": APP + "app/helmrelease.yaml", "status": "modified",
                                     "head": HELMRELEASE, "base": HELMRELEASE, "patch": "+  interval: 1h"})

    def test_a_flux_object_at_the_base_only_holds_the_app(self):
        # A Kustomization turned into a ConfigMap, or deleted.
        self.held("a Flux object", {"filename": APP + "ks.yaml", "status": "modified", "head": CONFIGMAP,
                                     "base": "apiVersion: kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"})
        self.held("a Flux object", {"filename": APP + "ks.yaml", "status": "removed", "base": HELMRELEASE})

    def test_a_flux_object_in_json_or_quoted_holds_the_app(self):
        self.held("a Flux object", {"filename": APP + "app/x.json", "status": "added",
                                     "head": '{"apiVersion": "kustomize.toolkit.fluxcd.io/v1"}'})
        self.held("a Flux object", {"filename": APP + "app/x.yaml", "status": "added",
                                     "head": "apiVersion: 'helm.toolkit.fluxcd.io/v2'\n"})

    def test_an_admission_object_holds_the_app(self):
        for kind in ("ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding", "MutatingAdmissionPolicy",
                     "ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"):
            self.held("an admission object", {"filename": APP + "app/x.yaml", "status": "added",
                                               "head": f"apiVersion: admissionregistration.k8s.io/v1\nkind: {kind}\n"})

    def test_a_sops_file_of_another_folder_by_a_relative_path_holds_the_app(self):
        # A test object of the design: a reference to a SOPS file of the class.
        patch = "@@ -1,2 +1,3 @@\n resources:\n   - ./helmrelease.yaml\n+  - ../../../../ai/agents/main/secret.sops.yaml"
        self.held("a path out of its folder", {"filename": APP + "app/kustomization.yaml", "head": "x", "base": "x",
                                                "patch": patch})

    def test_other_paths_out_of_the_folder_hold_the_app(self):
        for line in ("+  - ../../../../components/volsync", "+path:../../../other/app", "+  - ./a/../../../other",
                     '+  "files": ["../../../x.yaml"]', "+  - ../..", "+  - ../../../../../../etc"):
            self.held("a path out of its folder", {"filename": APP + "app/kustomization.yaml", "patch": line})

    def test_paths_in_the_folder_and_other_dots_pass(self):
        for line in ("+  - ../base", "+  - ./helmrelease.yaml", "+  version: 1..2", "+  note: wait...",
                     "-  - ../../../../components/volsync", " - ../../../../components/volsync", "+++ b/x"):
            v = self.go({"filename": APP + "app/overlay/kustomization.yaml", "patch": line})
            self.assertTrue(v.ok, (line, v.message))

    def test_a_path_out_of_a_folder_outside_kubernetes_passes(self):
        self.assertTrue(self.go({"filename": "docs/a.md", "patch": "+see ../../kubernetes/apps/ai"}).ok)

    def test_a_link_holds_the_app(self):
        # A test object of the design: a link to a SOPS file of the class.
        self.held("a link or a submodule", {"filename": APP + "app/secret.yaml", "mode": "120000"})
        self.held("a link or a submodule", {"filename": "docs/link", "mode": "120000"})
        self.held("a link or a submodule", {"filename": "vendor/module", "mode": "160000"})

    def test_an_encrypted_file_holds_the_app(self):
        # A copy of an encrypted file of the class would decrypt outside it.
        for f in ({"filename": APP + "app/secret.sops.yaml", "status": "added"},
                  {"filename": APP + "app/x.yaml", "previous_filename": APP + "app/x.sops.yaml", "status": "renamed"},
                  {"filename": "docs/old.sops.json", "status": "removed"}):
            self.held("an encrypted file", f)

    # The six shapes of the review that a text rule let through, each a file
    # that Flux reads as an admission object, a Flux object, a remote source
    # or encrypted content.
    def test_an_admission_object_behind_anchors_holds_the_app(self):
        text = ("x-names: [&v admissionregistration.k8s.io/v1, &k MutatingWebhookConfiguration]\n"
                "apiVersion: *v\nkind: *k\nmetadata: {name: x}\n")
        self.held("an admission object", {"filename": APP + "app/hook.yaml", "status": "added", "head": text})

    def test_an_admission_object_with_a_folded_kind_holds_the_app(self):
        text = "apiVersion: >-\n  admissionregistration.k8s.io/v1\nkind: >-\n  MutatingWebhookConfiguration\n"
        self.held("an admission object", {"filename": APP + "app/hook.yaml", "status": "added", "head": text})
        text = "apiVersion: v1\nkind: >-\n  MutatingWebhook\n  Configuration\n"
        self.assertTrue(self.go({"filename": APP + "app/x.yaml", "status": "added", "head": text}).ok,
                        "a folded scalar joins with a space, so this kind is another")
        # An escaped line break of a double-quoted scalar joins with no space.
        text = 'apiVersion: v1\nkind: "Mutating\\\n  WebhookConfiguration"\n'
        self.held("an admission object", {"filename": APP + "app/hook.yaml", "status": "added", "head": text})

    def test_an_admission_object_with_a_tagged_kind_holds_the_app(self):
        text = "apiVersion: !!str admissionregistration.k8s.io/v1\nkind: !!str ValidatingAdmissionPolicy\n"
        self.held("an admission object", {"filename": APP + "app/policy.yaml", "status": "added", "head": text})
        text = 'apiVersion: v1\nkind: "Mutating\\x57ebhookConfiguration"\n'
        self.held("an admission object", {"filename": APP + "app/hook.yaml", "status": "added", "head": text})

    def test_a_flux_object_with_a_folded_api_version_holds_the_app(self):
        text = "apiVersion: >-\n  kustomize.toolkit.fluxcd.io/v1\nkind: Kustomization\n"
        self.held("a Flux object", {"filename": APP + "app/x.yaml", "status": "added", "head": text})
        text = 'apiVersion: "kustomize.toolkit.\\x66luxcd.io/v1"\nkind: Kustomization\n'
        self.held("a Flux object", {"filename": APP + "app/x.yaml", "status": "added", "head": text})

    def test_a_remote_resource_in_a_kustomization_holds_the_app(self):
        for entry in ("https://example.invalid/hook.yaml", "github.com/example/repo//deploy?ref=v1",
                      "git@example.invalid:example/repo.git", "example.invalid/example/repo/deploy"):
            text = f"resources:\n  - ./helmrelease.yaml\n  - {entry}\n"
            self.held("a remote source", {"filename": APP + "app/kustomization.yaml", "head": text, "base": None})
        text = "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Component\ncomponents: [https://example.invalid/c]\n"
        self.held("a remote source", {"filename": APP + "app/component.yaml", "head": text, "base": None})
        text = "patches:\n  - path: https://example.invalid/p.yaml\n"
        self.held("a remote source", {"filename": APP + "app/kustomization.yaml", "head": text, "base": None})
        text = "helmCharts:\n  - name: x\n    repo: https://example.invalid\n"
        self.held("a remote source", {"filename": APP + "app/kustomization.yaml", "head": text, "base": None})

    def test_local_entries_of_a_kustomization_pass(self):
        text = ("resources:\n  - ./helmrelease.yaml\n  - app.yaml\n  - sub/dir\n"
                "configMapGenerator:\n  - name: c\n    files: [config.yaml=./files/config.yaml]\n"
                "images:\n  - name: app\n    newName: ghcr.io/example/app\n")
        v = self.go({"filename": APP + "app/kustomization.yaml", "head": text, "base": text})
        self.assertTrue(v.ok, v.message)

    def test_encrypted_content_under_another_name_holds_the_app(self):
        # A copy of an encrypted file of the class with no ".sops." in its name:
        # Flux decrypts by the content, not by the name.
        for text in ("apiVersion: v1\nkind: Secret\nstringData:\n  a: ENC[AES256_GCM,data:x,iv:y,tag:z,type:str]\n",
                     "apiVersion: v1\nkind: Secret\nsops:\n  age: []\n  mac: x\n",
                     '{"data": "x", "sops": {"mac": "x"}}',
                     "A=ENC[AES256_GCM,data:x,type:str]\nsops_mac=x\n"):
            self.held("encrypted content", {"filename": APP + "app/secret.yaml", "head": text, "base": None})
            self.held("encrypted content", {"filename": APP + "app/old.yaml", "status": "removed", "base": text})

    def test_a_file_that_does_not_parse_holds_the_app(self):
        for text in ("a: [1, 2\n", "a: !custom x\n", "a: 1\na: 2\n", "? [a]\n: b\n"):
            self.held("could not be parsed", {"filename": APP + "app/x.yaml", "head": text, "base": None})

    def test_a_kind_that_is_not_a_string_holds_the_app(self):
        self.held("not a string", {"filename": APP + "app/x.yaml", "head": "apiVersion: v1\nkind: [a]\n", "base": None})

    def test_an_admission_object_in_a_list_or_a_value_holds_the_app(self):
        text = "apiVersion: v1\nkind: List\nitems:\n  - apiVersion: admissionregistration.k8s.io/v1\n    kind: X\n"
        self.held("an admission object", {"filename": APP + "app/x.yaml", "head": text, "base": None})

    def test_plain_manifests_pass(self):
        text = (CONFIGMAP + "---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: x\n"
                "spec:\n  template:\n    metadata:\n      labels: &l {app: x}\n    spec: {}\n  selector:\n"
                "    matchLabels: *l\n")
        v = self.go({"filename": APP + "app/deployment.yaml", "head": text, "base": text, "patch": "+  a: c"})
        self.assertTrue(v.ok, v.message)

    def test_no_parser_holds_the_app(self):
        def missing():
            raise check.Unparsed("no YAML parser (PyYAML) in the job")
        real, check.yaml_loader = check.yaml_loader, missing
        try:
            self.held("could not be parsed", {"filename": APP + "app/configmap.yaml", "head": CONFIGMAP,
                                               "base": CONFIGMAP})
        finally:
            check.yaml_loader = real

    def test_a_removed_link_passes(self):
        self.assertTrue(self.go({"filename": "docs/link", "status": "removed"}).ok)

    def test_what_could_not_be_read_holds_the_app(self):
        self.held("the mode of", {"raw": True, "filename": "docs/a.md", "status": "added"})
        self.held("the content of", {"raw": True, "filename": APP + "app/x.yaml", "mode": "100644", "patch": ""})
        self.held("the diff of", {"raw": True, "filename": APP + "app/x.bin", "mode": "100644", "head": None,
                                  "base": None})


class AgentRun(unittest.TestCase):
    def setUp(self):
        check.MAIN_AGENT_APP = AGENT_APP["login"]

    def tearDown(self):
        check.MAIN_AGENT_APP = ""

    def test_the_files_are_read_for_the_app_only(self):
        r = routes(pr([], user=AGENT_APP), [[]], push=[entry(AGENT_APP)])
        r[f"{BASE}/pulls/42/files?per_page=100"] = (200, [{"filename": "infrastructure/edgeos/x.json"}], {})
        api = FakeAPI(r)
        v = check.run(api, REPO, 42, HEAD, OWNER_ID, sleep=lambda s: None)
        self.assertFalse(v.ok)
        self.assertIn("ask-first class", v.message)
        api = FakeAPI(routes(pr([], user=OWNER), [[]], push=[entry(OWNER)]))
        self.assertTrue(check.run(api, REPO, 42, HEAD, OWNER_ID, sleep=lambda s: None).ok)
        self.assertFalse(any("/files" in c for c in api.calls))

    def content_routes(self, files, tree, texts):
        p = pr([], user=AGENT_APP, changed_files=len(files))
        p["base"]["sha"] = OLD_HEAD
        r = routes(p, [[]], push=[entry(AGENT_APP)])
        r[f"{BASE}/pulls/42/files?per_page=100"] = (200, files, {})
        r[f"{BASE}/git/trees/{HEAD}?recursive=1"] = tree
        for (path, ref), text in texts.items():
            body = None if text is None else {"type": "file", "encoding": "base64",
                                              "content": base64.b64encode(text.encode()).decode()}
            r[f"{BASE}/contents/{path}?ref={ref}"] = (404, None, {}) if text is None else (200, body, {})
        return FakeAPI(r)

    def run_check(self, api):
        return check.run(api, REPO, 42, HEAD, OWNER_ID, sleep=lambda s: None)

    def test_the_content_is_read_from_the_tree_and_the_contents_api(self):
        path = APP + "app/configmap.yaml"
        files = [{"filename": path, "status": "modified", "patch": "+  a: c"}, {"filename": "docs/a.md", "patch": ""}]
        tree = (200, {"truncated": False, "tree": [{"path": path, "mode": "100644"},
                                                   {"path": "docs/a.md", "mode": "100644"}]}, {})
        api = self.content_routes(files, tree, {(path, HEAD): CONFIGMAP, (path, OLD_HEAD): CONFIGMAP})
        v = self.run_check(api)
        self.assertTrue(v.ok, v.message)
        self.assertFalse(any("docs/a.md" in c and "/contents/" in c for c in api.calls))
        api = self.content_routes(files, tree, {(path, HEAD): HELMRELEASE, (path, OLD_HEAD): CONFIGMAP})
        self.assertIn("a Flux object", self.run_check(api).message)

    def test_a_new_file_is_not_read_at_the_base(self):
        path = APP + "app/configmap.yaml"
        files = [{"filename": path, "status": "added", "patch": "+a: b"}]
        tree = (200, {"truncated": False, "tree": [{"path": path, "mode": "100644"}]}, {})
        api = self.content_routes(files, tree, {(path, HEAD): CONFIGMAP})
        self.assertTrue(self.run_check(api).ok)
        self.assertFalse(any(f"ref={OLD_HEAD}" in c for c in api.calls))

    def test_a_link_in_the_tree_holds_the_app(self):
        path = APP + "app/secret.yaml"
        files = [{"filename": path, "status": "added", "patch": "+../../../../ai/x.sops.yaml"}]
        tree = (200, {"truncated": False, "tree": [{"path": path, "mode": "120000"}]}, {})
        api = self.content_routes(files, tree, {(path, HEAD): "x"})
        self.assertIn("a link or a submodule", self.run_check(api).message)

    def test_a_tree_that_is_not_whole_or_a_content_that_is_not_a_file_fails(self):
        path = APP + "app/configmap.yaml"
        files = [{"filename": path, "status": "added", "patch": "+a: b"}]
        api = self.content_routes(files, (200, {"truncated": True, "tree": []}, {}), {(path, HEAD): CONFIGMAP})
        self.assertIn("could not read", self.run_check(api).message)
        tree = (200, {"truncated": False, "tree": [{"path": path, "mode": "100644"}]}, {})
        api = self.content_routes(files, tree, {})
        api.routes[f"{BASE}/contents/{path}?ref={HEAD}"] = (200, {"type": "file", "encoding": "none"}, {})
        self.assertIn("could not read", self.run_check(api).message)

    def test_too_many_files_under_kubernetes_fail(self):
        files = [{"filename": f"{APP}app/{n}.yaml", "status": "added", "patch": ""}
                 for n in range(check.MAX_CONTENT_FILES + 1)]
        api = self.content_routes(files, (200, {"truncated": False, "tree": []}, {}), {})
        self.assertIn("could not read", self.run_check(api).message)

    def test_unreadable_files_fail(self):
        api = FakeAPI(routes(pr([], user=AGENT_APP), [[]], push=[entry(AGENT_APP)]))
        v = check.run(api, REPO, 42, HEAD, OWNER_ID, sleep=lambda s: None)
        self.assertFalse(v.ok)

    def test_each_page_of_the_activity_is_read(self):
        r = routes(pr([], user=OWNER), [[]])
        first = f"{BASE}/activity?ref=refs%2Fheads%2Frenovate%2Fexample&direction=desc&per_page=100"
        r[first] = (200, [entry(OWNER)], {"Link": f'<{first}&page=2>; rel="next"'})
        r[first + "&page=2"] = (200, [entry(AGENT_APP, "2026-11-01T00:00:00Z")], {})
        v = check.run(FakeAPI(r), REPO, 42, HEAD, OWNER_ID, sleep=lambda s: None)
        self.assertFalse(v.ok)
        self.assertIn("pushed to the branch of another author", v.message)


class Trust(unittest.TestCase):
    """--trust, for the gates of the CI (pr-trust.yaml)."""

    def answer(self, p, pushes, actor="owner-account"):
        return check.trusted(p, pushes, actor, OWNER_ID, "owner-account")

    def test_the_owner_and_renovate_alone_are_trusted(self):
        self.assertTrue(self.answer(pr([], user=OWNER), [entry(OWNER)]))
        self.assertTrue(self.answer(pr([]), [entry(DEPS_BOT)], actor=DEPS_BOT["login"]))

    def test_another_actor_is_not(self):
        self.assertFalse(self.answer(pr([], user=OWNER), [entry(OWNER)], actor=AGENT_APP["login"]))

    def test_another_author_is_not(self):
        check.MAIN_AGENT_APP = AGENT_APP["login"]
        try:
            self.assertFalse(self.answer(pr([], user=AGENT_APP), [entry(AGENT_APP)]))
        finally:
            check.MAIN_AGENT_APP = ""

    def test_a_push_of_an_app_into_the_branch_is_not(self):
        self.assertFalse(self.answer(pr([], user=OWNER), [entry(OWNER), entry(AGENT_APP, "2026-11-01T00:00:00Z")]))

    def test_a_fork_is_not(self):
        self.assertFalse(self.answer(pr([], user=OWNER, head_repo="someone-else/ops"), []))

    def test_main_prints_false_when_it_can_not_tell(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        env = {"REPO": REPO, "PR": "42", "OWNER_ID": str(OWNER_ID), "ACTOR": "owner-account", "OWNER_LOGIN": "owner-account"}
        out, err = io.StringIO(), io.StringIO()
        old = dict(check.os.environ)
        try:
            check.os.environ.update(env)
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(check.trust_main(FakeAPI({})), 0)
        finally:
            check.os.environ.clear()
            check.os.environ.update(old)
        self.assertEqual(out.getvalue(), "trusted=false\n")

    def test_main_prints_true_for_the_owner(self):
        import io
        from contextlib import redirect_stdout
        env = {"REPO": REPO, "PR": "42", "OWNER_ID": str(OWNER_ID), "ACTOR": "owner-account", "OWNER_LOGIN": "owner-account"}
        out = io.StringIO()
        old = dict(check.os.environ)
        try:
            check.os.environ.update(env)
            with redirect_stdout(out):
                check.trust_main(FakeAPI(routes(pr([], user=OWNER), [[]], push=[entry(OWNER)])))
        finally:
            check.os.environ.clear()
            check.os.environ.update(old)
        self.assertEqual(out.getvalue(), "trusted=true\n")


def commit(message: str = "fix: a change", author: dict | None = None, committer: dict | None = None,
           sha: str = HEAD) -> dict:
    return {"sha": sha, "commit": {"message": message}, "author": author, "committer": committer}


WEB_FLOW = {"login": "web-flow", "id": 19864447, "type": "User", "site_admin": False}
APPROVED_NOW = [event("labeled", OWNER, "2026-11-02T15:00:00Z", "owner/approved")]


class Base(unittest.TestCase):
    """A PR held for the owner must have the default branch as its base."""

    def setUp(self):
        check.MAIN_AGENT_APP = AGENT_APP["login"]

    def tearDown(self):
        check.MAIN_AGENT_APP = ""

    def go(self, p, events=(), pushes=None, files=(), commits=()):
        pushes = [entry(p["user"])] if pushes is None else pushes
        return check.decide(p, list(events), pushes[0] if pushes else None, [], HEAD, OWNER_ID,
                            pushes=pushes, files=[ready(f) for f in files], commits=list(commits))

    def test_an_app_pr_to_another_base_fails_even_outside_the_class(self):
        v = self.go(pr([], user=AGENT_APP, base="x"), files=[{"filename": "docs/a.md"}])
        self.assertFalse(v.ok)
        self.assertIn("the base x is not the default branch", v.message)

    def test_the_owners_label_does_not_pass_another_base(self):
        v = self.go(pr(["owner/approved"], user=OTHER_USER, base="x"), APPROVED_NOW)
        self.assertFalse(v.ok)
        self.assertIn("not the default branch", v.message)

    def test_a_stacked_pr_of_the_owner_passes_as_before(self):
        self.assertTrue(self.go(pr([], user=OWNER, base="x")).ok)

    def test_a_label_before_a_change_of_the_base_fails(self):
        for kind in check.BASE_CHANGES:
            events = APPROVED_NOW + [event(kind, AGENT_APP, "2026-11-02T15:30:00Z")]
            v = self.go(pr(["owner/approved"], user=OTHER_USER), events)
            self.assertFalse(v.ok, kind)
            self.assertIn("before the base changed", v.message)

    def test_a_label_after_a_change_of_the_base_passes(self):
        events = [event("base_ref_changed", AGENT_APP, "2026-11-02T14:30:00Z")] + APPROVED_NOW
        self.assertTrue(self.go(pr(["owner/approved"], user=OTHER_USER), events).ok)

    def test_the_trust_needs_the_default_branch(self):
        self.assertFalse(check.trusted(pr([], user=OWNER, base="x"), [entry(OWNER)], "owner-account", OWNER_ID,
                                       "owner-account"))


class Skip(Base):
    """A skip instruction would stop the workflows of the push to main."""

    FORMS = ("[skip ci]", "[ci skip]", "[no ci]", "[skip actions]", "[actions skip]", "[Skip CI]", "[ skip  ci ]",
             "x\n\n\nskip-checks:true", "x\n\n\nskip-checks: true", "x\n\n\nSkip-Checks: TRUE")

    def test_each_form_in_a_commit_message_fails_the_app(self):
        for form in self.FORMS:
            v = self.go(pr(["owner/approved"], user=AGENT_APP), APPROVED_NOW, files=[{"filename": "docs/a.md"}],
                        commits=[commit(f"docs: one {form}", sha=OLD_HEAD), commit()])
            self.assertFalse(v.ok, form)
            self.assertIn("the message of commit 476290c holds a skip instruction", v.message)

    def test_the_title_and_the_body_fail_the_app(self):
        v = self.go(pr([], user=AGENT_APP, title="docs: a change [skip actions]"), files=[{"filename": "docs/a.md"}])
        self.assertIn("its title holds a skip instruction", v.message)
        v = self.go(pr([], user=AGENT_APP, body="text\n[no ci]"), files=[{"filename": "docs/a.md"}])
        self.assertIn("its body holds a skip instruction", v.message)

    def test_a_push_of_an_app_into_the_branch_of_the_owner(self):
        pushes = [entry(AGENT_APP, "2026-11-02T14:20:00Z"), entry(OWNER, "2026-11-02T14:00:00Z")]
        v = self.go(pr(["owner/approved"], user=OWNER), APPROVED_NOW, pushes=pushes,
                    commits=[commit("fix: x [ci skip]")])
        self.assertFalse(v.ok)
        self.assertIn("skip instruction", v.message)

    def test_the_owner_and_renovate_alone_are_not_read_for_it(self):
        self.assertTrue(self.go(pr([], user=OWNER, title="docs: x [skip ci]"), commits=[commit("[skip ci]")]).ok)
        self.assertTrue(self.go(pr(["type/patch"], title="chore: x [skip ci]")).ok)

    def test_words_that_only_look_alike_pass(self):
        v = self.go(pr([], user=AGENT_APP, title="docs: how to skip ci runs", body="skip-checks: false"),
                    files=[{"filename": "docs/a.md"}], commits=[commit("docs: [skip] ci")])
        self.assertTrue(v.ok, v.message)


class Commits(Base):
    """A bot other than Renovate in the commits of another author holds the PR."""

    def test_a_commit_of_another_bot_holds_the_owners_pr(self):
        for c in (commit(author=AGENT_APP), commit(committer=AGENT_APP), commit(author=WORKFLOW_BOT)):
            v = self.go(pr([], user=OWNER), commits=[c])
            self.assertFalse(v.ok)
            self.assertIn("a bot made commit d94fb5f in the branch of another author", v.message)

    def test_commits_of_the_owner_the_web_and_renovate_pass(self):
        self.assertTrue(self.go(pr([], user=OWNER), commits=[commit(author=OWNER, committer=WEB_FLOW)]).ok)
        self.assertTrue(self.go(pr(["type/patch"]), commits=[commit(author=DEPS_BOT, committer=DEPS_BOT)]).ok)
        self.assertTrue(self.go(pr([], user=OWNER), commits=[commit()]).ok)

    def test_more_commits_than_listed_holds_the_pr(self):
        p = pr([], user=OWNER)
        p["commits"] = 251
        v = self.go(p, commits=[commit()] * 250)
        self.assertFalse(v.ok)
        self.assertIn("more commits than the API lists", v.message)

    def test_the_trust_reads_the_commits(self):
        self.assertFalse(check.trusted(pr([], user=OWNER), [entry(OWNER)], "owner-account", OWNER_ID,
                                       "owner-account", [commit(author=AGENT_APP)]))


class Paths(Base):
    def test_each_new_path_of_the_class_holds_the_app(self):
        for path in ("kubernetes/flux/meta/helm/x.yaml", "kubernetes/components/cnpg-cluster/cluster.yaml",
                     "kubernetes/bootstrap/kustomization.yaml", "kubernetes/apps/kube-system/reflector/ks.yaml",
                     "kubernetes/apps/security/prowler/ks.yaml", "security/README.md",
                     "observability/generate.py", "Taskfile.yml", ".taskfiles/ClusterTasks.yml", ".envrc",
                     ".vscode/settings.json", "kubernetes/apps/kustomization.yaml",
                     "kubernetes/apps/auth/oauth2-proxy/ks.yaml", "kubernetes/apps/networking/cloudflared/ks.yaml",
                     "kubernetes/apps/cert-manager/cert-manager/ks.yaml",
                     "kubernetes/apps/tools/actions-runner-controller/ks.yaml",
                     "kubernetes/apps/kopiur-system/kopiur/ks.yaml", "kubernetes/apps/monitoring/alertmanager/ks.yaml",
                     ".changie.yaml", ".gitattributes", "kubernetes/apps/default/x/.gitattributes",
                     "kubernetes/apps/default/hajimari/app/helmrelease.yaml",
                     "kubernetes/apps/production/frigate/ks.yaml",
                     "kubernetes/apps/home-automation/zwave-js-ui/app/helmrelease.yaml",
                     "kubernetes/apps/media/qbittorrent/app/helmrelease.yaml"):
            v = self.go(pr([], user=AGENT_APP), files=[{"filename": path}])
            self.assertFalse(v.ok, path)
            self.assertIn("ask-first class", v.message)

    def test_paths_that_only_start_alike_are_not_in_it(self):
        for path in ("kubernetes/apps/securityx/a.yaml", "security-notes.md", "Taskfile.yml.bak",
                     "kubernetes/apps/default/x/kustomization.yaml", "docs/gitattributes.md"):
            v = self.go(pr([], user=AGENT_APP), files=[{"filename": path}])
            self.assertTrue(v.ok, path)


class Activity(unittest.TestCase):
    """The activity of a branch counts from its newest creation, and lags."""

    def test_a_push_into_an_earlier_branch_of_the_name_does_not_count(self):
        entries = [entry(DEPS_BOT), entry(DEPS_BOT, kind="branch_creation"), entry(AGENT_APP, after=OLD_HEAD)]
        self.assertEqual(check.since_creation(entries), entries[:2])
        self.assertTrue(check.decide(pr(["type/patch"]), [], entries[0], [], HEAD, OWNER_ID,
                                     pushes=check.since_creation(entries)).ok)

    def test_a_creation_by_an_app_counts(self):
        entries = [entry(DEPS_BOT), entry(AGENT_APP, kind="branch_creation")]
        self.assertFalse(check.decide(pr(["type/patch"]), [], entries[0], [], HEAD, OWNER_ID,
                                      pushes=check.since_creation(entries)).ok)

    def test_the_reading_stops_at_the_creation(self):
        r = routes(pr([], user=OWNER), [[]])
        first = f"{BASE}/activity?ref=refs%2Fheads%2Frenovate%2Fexample&direction=desc&per_page=100"
        r[first] = (200, [entry(OWNER), entry(OWNER, kind="branch_creation"), entry(AGENT_APP, after=OLD_HEAD)],
                    {"Link": f'<{first}&page=2>; rel="next"'})
        api = FakeAPI(r)
        v = check.run(api, REPO, 42, HEAD, OWNER_ID, sleep=lambda s: None)
        self.assertTrue(v.ok, v.message)
        self.assertFalse(any("page=2" in c for c in api.calls))

    def test_the_trust_needs_the_newest_push_to_be_the_head(self):
        owner_pr = pr([], user=OWNER)
        self.assertFalse(check.trusted(owner_pr, [], "owner-account", OWNER_ID, "owner-account"))
        self.assertFalse(check.trusted(owner_pr, [entry(OWNER, after=OLD_HEAD)], "owner-account", OWNER_ID,
                                       "owner-account"))
        self.assertFalse(check.trusted(owner_pr, [entry(OWNER)], "owner-account", OWNER_ID, "owner-account",
                                       head_sha=OLD_HEAD))
        self.assertTrue(check.trusted(owner_pr, [entry(OWNER)], "owner-account", OWNER_ID, "owner-account",
                                      head_sha=HEAD))

    def trust(self, api, **env):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        base = {"REPO": REPO, "PR": "42", "OWNER_ID": str(OWNER_ID), "ACTOR": "owner-account",
                "OWNER_LOGIN": "owner-account", "HEAD_SHA": HEAD}
        out = io.StringIO()
        old = dict(check.os.environ)
        try:
            check.os.environ.update(dict(base, **env))
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                check.trust_main(api, sleep=lambda s: None)
        finally:
            check.os.environ.clear()
            check.os.environ.update(old)
        return out.getvalue()

    def test_a_lagging_activity_is_read_again(self):
        # The push of an App (C1) is not listed yet when the owner's push
        # (C2) runs: the newest entry is not the head, so it reads again.
        r = routes(pr([], user=OWNER), [[]])
        answers = iter([(200, [entry(OWNER, after=OLD_HEAD)], {}),
                        (200, [entry(OWNER), entry(AGENT_APP, "2026-11-02T14:18:20Z", after=OLD_HEAD)], {})])
        r[f"{BASE}/activity?"] = lambda: next(answers)
        self.assertEqual(self.trust(FakeAPI(r)), "trusted=false\n")
        r = routes(pr([], user=OWNER), [[]])
        answers = iter([(200, [], {}), (200, [entry(OWNER)], {})])
        r[f"{BASE}/activity?"] = lambda: next(answers)
        self.assertEqual(self.trust(FakeAPI(r)), "trusted=true\n")

    def test_no_second_read_for_an_answer_that_is_not_a_lag(self):
        api = FakeAPI(routes(pr([], user=OTHER_USER), [[]], push=[entry(OTHER_USER)]))
        self.assertEqual(self.trust(api), "trusted=false\n")
        self.assertEqual(sum(1 for c in api.calls if c == f"{BASE}/pulls/42"), 1)

    def test_a_lag_that_stays_is_false(self):
        api = FakeAPI(routes(pr([], user=OWNER), [[]], push=[entry(OWNER, after=OLD_HEAD)]))
        self.assertEqual(self.trust(api), "trusted=false\n")
        self.assertEqual(sum(1 for c in api.calls if c == f"{BASE}/pulls/42"), check.ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
