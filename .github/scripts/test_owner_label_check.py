"""Tests for .github/scripts/owner-label-check.py.

    python3 -m unittest discover -s .github/scripts -p 'test_owner_label_check.py' -v

The API bodies below are recorded responses of this repository (a PR's
issue events, the activity API of its branch, the PR), trimmed to the
fields the script reads, with neutral logins and IDs.
"""
from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("owner_label_check", HERE / "owner-label-check.py")
check = importlib.util.module_from_spec(spec)
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


def pr(labels: list[str], head: str = HEAD, head_repo: str | None = REPO) -> dict:
    return {
        "number": 42,
        "state": "open",
        "labels": [{"name": n, "color": "000000"} for n in labels],
        "head": {
            "sha": head,
            "ref": "renovate/example",
            "repo": None if head_repo is None else {"full_name": head_repo},
        },
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }


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
        self.assertEqual(sum(1 for c in api.calls if "/pulls/42" in c), check.ATTEMPTS)

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


if __name__ == "__main__":
    unittest.main()
