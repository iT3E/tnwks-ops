"""Tests for bin/privacy-terms, against a stub sops and a stub gh. Nothing
reaches GitHub; the terms and repository names here are made up.

    python3 -m unittest discover -s .github/scripts -p 'test_privacy_terms.py' -v
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("privacy_terms", str(ROOT / "bin/privacy-terms"))
spec = importlib.util.spec_from_loader("privacy_terms", loader)
privacy_terms = importlib.util.module_from_spec(spec)
loader.exec_module(privacy_terms)

TERMS = "zebrafinch\nquokka@example.invalid\n# a comment\n"
NEW_TERMS = TERMS + "marmoset\n"
PRIVATE = ["hidden-alpha", "hidden-beta", "hidden-gamma"]

# The terms file is JSON here: {"terms": ...}, or {"broken": true} to fail
# decryption. Edit mode follows edit.json: {"mode": ..., "terms": ...}.
SOPS_STUB = textwrap.dedent(f"""\
    #!{sys.executable}
    import json, sys
    from pathlib import Path
    args = sys.argv[1:]
    if args[:3] == ["-d", "--extract", '["terms"]']:
        doc = json.loads(Path(args[3]).read_text())
        if doc.get("broken"):
            sys.stderr.write("stub sops: cannot decrypt\\n")
            sys.exit(128)
        sys.stdout.write(doc["terms"])
        sys.exit(0)
    assert len(args) == 1, args
    path = Path(args[0])
    plan = json.loads((path.parent.parent / "edit.json").read_text())
    mode = plan["mode"]
    if mode == "unchanged":
        sys.exit(200)
    if mode == "abort":
        sys.exit(1)
    if mode == "break":
        path.write_text(json.dumps({{"broken": True}}))
    elif mode == "reencrypt":
        doc = json.loads(path.read_text())
        doc["nonce"] = doc.get("nonce", 0) + 1
        path.write_text(json.dumps(doc))
    elif mode in ("change", "change-then-fail"):
        path.write_text(json.dumps({{"terms": plan["terms"]}}))
    if mode == "change-then-fail":
        sys.exit(1)
    sys.exit(0)
""")

# gh: state from $STUB_GH_STATE, every call appended to $STUB_GH_LOG with
# its argv, its stdin and whether GITHUB_TOKEN reached it.
GH_STUB = textwrap.dedent(f"""\
    #!{sys.executable}
    import json, os, sys
    state = json.load(open(os.environ["STUB_GH_STATE"]))
    args = sys.argv[1:]
    stdin = sys.stdin.read() if args[:2] == ["secret", "set"] else None
    with open(os.environ["STUB_GH_LOG"], "a") as f:
        f.write(json.dumps({{"argv": args, "stdin": stdin, "token": "GITHUB_TOKEN" in os.environ}}) + "\\n")
    if args[:2] == ["api", "user"]:
        print(state["login"]); sys.exit(0)
    if args[:2] == ["api", "--paginate"]:
        for r in state["repos"]:
            print(json.dumps(r))
        sys.exit(0)
    if args[0] == "api" and "/actions/secrets/PRIVACY_TERMS" in args[1]:
        name = args[1].split("/")[2]
        if name in state.get("error", []):
            sys.stderr.write(f"gh: Server Error (HTTP 500) for {{name}}\\n"); sys.exit(1)
        if name in state["secret"]:
            print("PRIVACY_TERMS"); sys.exit(0)
        sys.stderr.write("gh: Not Found (HTTP 404)\\n"); sys.exit(1)
    if args[:3] == ["secret", "set", "PRIVACY_TERMS"]:
        name = args[4].split("/")[1]
        if name in state.get("fail_set", []):
            # Worst case: a gh that quotes its stdin in the error.
            sys.stderr.write(f"failed to set secret PRIVACY_TERMS for iT3E/{{name}}: {{stdin}}\\n"); sys.exit(1)
        sys.exit(0)
    sys.stderr.write(f"stub gh: unexpected {{args}}\\n"); sys.exit(2)
""")


class PrivacyTerms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.repo = d / "repo"
        (self.repo / "bin").mkdir(parents=True)
        (self.repo / ".github").mkdir()
        self.terms_file = self.repo / ".github/privacy-terms.sops.yaml"
        self.terms_file.write_text(json.dumps({"terms": TERMS}))
        sops = self.repo / "bin/sops"
        sops.write_text(SOPS_STUB)
        ghdir = d / "ghbin"
        ghdir.mkdir()
        gh = ghdir / "gh"
        gh.write_text(GH_STUB)
        for f in (sops, gh):
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        self.state = d / "state.json"
        self.log = d / "calls.jsonl"
        self.set_state()
        self.edit_plan("unchanged")
        self.saved = (privacy_terms.REPO, privacy_terms.SOPS, dict(os.environ))
        privacy_terms.REPO = self.repo
        privacy_terms.SOPS = sops
        os.environ["PATH"] = f"{ghdir}{os.pathsep}{os.environ['PATH']}"
        os.environ["STUB_GH_STATE"] = str(self.state)
        os.environ["STUB_GH_LOG"] = str(self.log)
        os.environ["GITHUB_TOKEN"] = "stub-env-token"

    def tearDown(self):
        privacy_terms.REPO, privacy_terms.SOPS, env = self.saved
        os.environ.clear()
        os.environ.update(env)
        self.tmp.cleanup()

    def set_state(self, **kw):
        state = {
            "login": "iT3E",
            "repos": [["tnwks-ops", False, False], ["public-without", False, False],
                      ["old-public", False, True], *[[n, True, False] for n in PRIVATE],
                      ["private-without", True, False]],
            "secret": ["tnwks-ops", "old-public", *PRIVATE],
        }
        state.update(kw)
        self.state.write_text(json.dumps(state))

    def edit_plan(self, mode, terms=NEW_TERMS):
        (self.repo / "edit.json").write_text(json.dumps({"mode": mode, "terms": terms}))

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = privacy_terms.main(list(argv))
        return rc, out.getvalue()

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def sets(self):
        return [c for c in self.calls() if c["argv"][:2] == ["secret", "set"]]

    def assertNothingLeaks(self, out):
        for term in ("zebrafinch", "quokka", "marmoset", *PRIVATE, "private-without"):
            self.assertNotIn(term, out)
        for c in self.calls():
            self.assertFalse(c["token"], "GITHUB_TOKEN must not reach gh")
            for term in ("zebrafinch", "quokka", "marmoset"):
                self.assertNotIn(term, " ".join(c["argv"]), "a term on gh's command line")

    # --- sync ----------------------------------------------------------------

    def test_sync_sets_only_where_the_secret_exists(self):
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 0, out)
        repos = sorted(c["argv"][4] for c in self.sets())
        self.assertEqual(repos, sorted(["iT3E/tnwks-ops", *[f"iT3E/{n}" for n in PRIVATE]]))
        self.assertTrue(all(c["stdin"] == TERMS for c in self.sets()), "terms go on stdin, unchanged")
        self.assertIn("1 public (tnwks-ops) and 3 private (not named)", out)
        self.assertIn("Archived, skipped: 1 public (old-public)", out)
        self.assertIn("2 terms", out)
        self.assertNothingLeaks(out)

    def test_sync_dry_run_sets_nothing(self):
        rc, out = self.run_main("sync", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sets(), [])
        self.assertIn("would set PRIVACY_TERMS on these 4 repositories", out)
        self.assertNothingLeaks(out)

    def test_sync_stops_before_setting_when_a_repo_is_unknown(self):
        self.set_state(error=[PRIVATE[1]])
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 1)
        self.assertEqual(self.sets(), [])
        self.assertIn("can't tell whether a private repository has PRIVACY_TERMS", out)
        self.assertNothingLeaks(out)

    def test_sync_failure_is_loud_and_says_how_to_retry(self):
        self.set_state(fail_set=["tnwks-ops", PRIVATE[0]])
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.sets()), 4, "the others are still set")
        self.assertIn("FAILED on 1 public (tnwks-ops) and 1 private (not named)", out)
        self.assertIn("  tnwks-ops: gh exit 1\n", out)
        self.assertIn("  a private repository: gh exit 1\n", out)
        self.assertNotIn("failed to set secret", out, "gh's stderr isn't printed")
        self.assertIn("task privacy:sync", out)
        self.assertNothingLeaks(out)

    def test_sync_refuses_another_login(self):
        self.set_state(login="someone-else")
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 1)
        self.assertIn("not iT3E", out)
        self.assertEqual(self.sets(), [])

    def test_sync_refuses_an_empty_list(self):
        self.terms_file.write_text(json.dumps({"terms": "# nothing\n\n"}))
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 1)
        self.assertIn("no terms", out)
        self.assertEqual(self.calls(), [])

    def test_sync_needs_a_list_that_decrypts(self):
        self.terms_file.write_text(json.dumps({"broken": True}))
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 1)
        self.assertIn("doesn't decrypt (sops exit 128)", out)
        self.assertNotIn("cannot decrypt", out, "sops's own output isn't printed")
        self.assertEqual(self.calls(), [])

    # --- edit ----------------------------------------------------------------

    def test_edit_with_a_change_syncs(self):
        self.edit_plan("change")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.sets()), 4)
        self.assertTrue(all(c["stdin"] == NEW_TERMS for c in self.sets()), "the new list")
        self.assertIn("The list changed", out)
        self.assertNothingLeaks(out)

    def test_edit_dry_run_syncs_nothing(self):
        self.edit_plan("change")
        rc, out = self.run_main("edit", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sets(), [])
        self.assertIn("--dry-run: would set", out)

    def test_edit_unchanged_does_not_sync(self):
        self.edit_plan("unchanged")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 0)
        self.assertIn("didn't change", out)
        self.assertEqual(self.calls(), [], "not even a GET")

    def test_edit_aborted_does_not_sync(self):
        self.edit_plan("abort")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 1)
        self.assertIn("aborted or failed, and the file is as it was. Not synced", out)
        self.assertEqual(self.calls(), [])

    def test_edit_failed_after_writing_does_not_sync(self):
        self.edit_plan("change-then-fail")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 1)
        self.assertIn("CHANGED anyway", out)
        self.assertEqual(self.calls(), [])

    def test_edit_that_breaks_decryption_does_not_sync(self):
        self.edit_plan("break")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 1)
        self.assertIn("doesn't decrypt", out)
        self.assertIn("Not synced", out)
        self.assertEqual(self.calls(), [])

    def test_edit_to_an_empty_list_does_not_sync(self):
        self.edit_plan("change", terms="# all gone\n")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 1)
        self.assertIn("no terms after the edit", out)
        self.assertEqual(self.calls(), [])

    def test_edit_that_only_reencrypts_does_not_sync(self):
        self.edit_plan("reencrypt")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 0)
        self.assertIn("terms didn't change", out)
        self.assertEqual(self.calls(), [])

    def test_edit_then_failed_sync_says_how_to_retry(self):
        self.edit_plan("change")
        self.set_state(fail_set=[PRIVATE[2]])
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 1)
        self.assertIn("FAILED on 1 private (not named)", out)
        self.assertIn("The edit is saved; the sync is what failed", out)
        self.assertIn("task privacy:sync", out)
        self.assertNothingLeaks(out)

    def test_edit_then_discovery_error_says_how_to_retry(self):
        self.edit_plan("change")
        self.set_state(error=["tnwks-ops"])
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 1)
        self.assertEqual(self.sets(), [])
        self.assertIn("can't tell whether tnwks-ops has PRIVACY_TERMS", out)
        self.assertIn("task privacy:sync", out)

    def test_edit_needs_a_list_that_decrypts_first(self):
        self.terms_file.write_text(json.dumps({"broken": True}))
        self.edit_plan("change")
        rc, out = self.run_main("edit")
        self.assertEqual(rc, 1)
        self.assertIn("doesn't decrypt", out)
        self.assertEqual(json.loads(self.terms_file.read_text()), {"broken": True}, "the editor never ran")


if __name__ == "__main__":
    unittest.main()
