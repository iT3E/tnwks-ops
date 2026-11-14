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
import shutil
import subprocess
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
# its argv, its stdin and whether GITHUB_TOKEN reached it. Not an f-string,
# so nothing in it is expanded; the shebang is prepended.
GH_STUB_BODY = textwrap.dedent(r"""
    import base64, json, os, sys
    args = sys.argv[1:]
    if args == ["__stub_ping__"]:
        print("stub-gh"); sys.exit(0)
    state = json.load(open(os.environ["STUB_GH_STATE"]))
    stdin = sys.stdin.read() if args[:2] == ["secret", "set"] else None
    with open(os.environ["STUB_GH_LOG"], "a") as f:
        f.write(json.dumps({"argv": args, "stdin": stdin, "token": "GITHUB_TOKEN" in os.environ}) + "\n")
    def content(text):
        print(base64.encodebytes(text.encode()).decode(), end=""); sys.exit(0)
    def not_found():
        sys.stderr.write("gh: Not Found (HTTP 404)\n"); sys.exit(1)
    if args[:2] == ["api", "user"]:
        print(state["login"]); sys.exit(0)
    if args[:2] == ["api", "--paginate"]:
        for r in state["repos"]:
            print(json.dumps(r))
        sys.exit(0)
    if args[0] == "api" and "/actions/secrets/PRIVACY_TERMS" in args[1]:
        name = args[1].split("/")[2]
        if name in state.get("error", []):
            sys.stderr.write(f"gh: Server Error (HTTP 500) for {name}\n"); sys.exit(1)
        if name in state["secret"]:
            print("PRIVACY_TERMS"); sys.exit(0)
        not_found()
    if args[0] == "api" and "/contents/" in args[1]:
        name = args[1].split("/")[2]
        path, _, ref = args[1].split("/contents/", 1)[1].partition("?ref=")
        if name in state.get("contents_error", []):
            sys.stderr.write(f"gh: Server Error (HTTP 500) for {name}\n"); sys.exit(1)
        if path == "bin/privacy-check":
            if name in state.get("no_script", []):
                not_found()
            reader = "opaque_prefix='#privacy-terms:v1:'" if name in state.get("reads", []) else ""
            content("#!/usr/bin/env bash\n" + reader + "\n")
        if path == ".github/workflows/privacy-text.yaml":
            if name == "github-workflows" and ref:
                fixed = ref.removeprefix("sha=") in state.get("fixed_shas", [])
                content("on:\n  workflow_call:\n" + ("    # author_association\n" if fixed else ""))
            pin = state.get("pins", {}).get(name)
            if pin is None:
                not_found()
            if pin == "local-fixed":
                content("on:\n  workflow_call:\n    # author_association\n")
            content("jobs:\n  text:\n    uses: iT3E/github-workflows/.github/workflows/privacy-text.yaml@" + pin + " # v1\n")
        not_found()
    if args[:3] == ["secret", "set", "PRIVACY_TERMS"]:
        name = args[4].split("/")[1]
        if name in state.get("fail_set", []):
            # Worst case: a gh that quotes its stdin in the error.
            sys.stderr.write(f"failed to set secret PRIVACY_TERMS for iT3E/{name}: {stdin}\n"); sys.exit(1)
        sys.exit(0)
    sys.stderr.write(f"stub gh: unexpected {args}\n"); sys.exit(2)
""")
GH_STUB = f"#!{sys.executable}\n{GH_STUB_BODY}"
FIXED = "f" * 40
OLD = "0" * 40


class StubBroken(RuntimeError):
    """The stub gh doesn't answer: stop before anything can reach a real gh."""


def write_stub(ghdir: Path, text: str = GH_STUB, mode: int = 0o700) -> Path:
    gh = ghdir / "gh"
    gh.write_text(text)
    gh.chmod(mode)
    return gh


def stub_guard(gh: Path) -> None:
    """`gh` on PATH must be this stub, and the stub must answer a ping. Called
    with the stub as an absolute path, so a broken stub raises here and the
    lookup never falls through to another gh on PATH."""
    found = shutil.which("gh", path=os.environ.get("PATH", ""))
    if found is None or Path(found).resolve() != gh.resolve():
        raise StubBroken(f"gh resolves to {found}, not the stub")
    try:
        r = subprocess.run([str(gh), "__stub_ping__"], capture_output=True, text=True, timeout=30)
    except OSError as e:
        raise StubBroken(f"the stub gh doesn't run: {e}") from None
    if r.returncode != 0 or r.stdout.strip() != "stub-gh":
        raise StubBroken("the stub gh doesn't answer its ping")


_isolation = tempfile.TemporaryDirectory()


def setUpModule():
    # No login a real gh could use, and a stub that answers, before any test.
    home = Path(_isolation.name) / "home"
    home.mkdir()
    os.environ["HOME"] = str(home)
    os.environ["GH_CONFIG_DIR"] = str(home / "gh-config")
    os.environ.pop("GH_TOKEN", None)
    ghdir = Path(_isolation.name) / "ghbin"
    ghdir.mkdir()
    gh = write_stub(ghdir)
    os.environ["PATH"] = f"{ghdir}{os.pathsep}{os.environ['PATH']}"
    stub_guard(gh)


def tearDownModule():
    _isolation.cleanup()


class StubGuard(unittest.TestCase):
    """The suite stops when the stub is broken."""

    def check_broken(self, text, mode):
        with tempfile.TemporaryDirectory() as d:
            ghdir = Path(d)
            gh = write_stub(ghdir, text, mode)
            saved = os.environ["PATH"]
            os.environ["PATH"] = f"{ghdir}{os.pathsep}{saved}"
            try:
                with self.assertRaises(StubBroken):
                    stub_guard(gh)
            finally:
                os.environ["PATH"] = saved

    def test_not_executable(self):
        self.check_broken(GH_STUB, 0o600)

    def test_bad_shebang(self):
        self.check_broken("    " + GH_STUB, 0o700)

    def test_no_answer(self):
        self.check_broken(f"#!{sys.executable}\nimport sys; sys.exit(0)\n", 0o700)

    def test_gh_points_at_the_stub(self):
        self.assertTrue(Path(privacy_terms.GH).is_absolute() or privacy_terms.GH == "gh")


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
        sops.chmod(0o700)
        # The real encoder of this repo.
        (self.repo / "bin/privacy-check").write_text((ROOT / "bin/privacy-check").read_text())
        ghdir = d / "ghbin"
        ghdir.mkdir()
        gh = write_stub(ghdir)
        self.state = d / "state.json"
        self.log = d / "calls.jsonl"
        self.set_state()
        self.edit_plan("unchanged")
        self.saved = (privacy_terms.REPO, privacy_terms.SOPS, privacy_terms.ENCODER, privacy_terms.GH,
                      dict(os.environ))
        privacy_terms.REPO = self.repo
        privacy_terms.SOPS = sops
        privacy_terms.ENCODER = self.repo / "bin/privacy-check"
        privacy_terms.GH = str(gh)
        os.environ["PATH"] = f"{ghdir}{os.pathsep}{os.environ['PATH']}"
        os.environ["STUB_GH_STATE"] = str(self.state)
        os.environ["STUB_GH_LOG"] = str(self.log)
        os.environ["GITHUB_TOKEN"] = "stub-env-token"
        stub_guard(gh)

    def tearDown(self):
        privacy_terms.REPO, privacy_terms.SOPS, privacy_terms.ENCODER, privacy_terms.GH, env = self.saved
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
        self.assertNotRegex(out, r"\d+ terms?\b")
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

    # --- the opaque form, for ready repositories only --------------------------

    def decode(self, value: str, text: str) -> int:
        """bin/privacy-check --text with this value as the list: 1 is a hit."""
        env = {k: v for k, v in os.environ.items() if k != "PRIVACY_TERMS"}
        env["PRIVACY_TERMS"] = value
        r = subprocess.run(["bash", str(ROOT / "bin/privacy-check"), "--text"], input=text,
                           capture_output=True, text=True, cwd=ROOT, env=env)
        return r.returncode

    def ready(self, *names):
        return {"reads": list(names), "pins": {n: FIXED for n in names}, "fixed_shas": [FIXED]}

    def test_sync_gives_the_opaque_form_to_ready_repos_only(self):
        st = self.ready("tnwks-ops", PRIVATE[0])
        st["reads"].append(PRIVATE[1])            # script ready, comment check old
        st["pins"][PRIVATE[1]] = OLD
        st["pins"][PRIVATE[2]] = FIXED            # comment check fixed, script old
        self.set_state(**st)
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 0, out)
        by_repo = {c["argv"][4].split("/")[1]: c["stdin"] for c in self.sets()}
        self.assertEqual(by_repo[PRIVATE[1]], TERMS)
        self.assertEqual(by_repo[PRIVATE[2]], TERMS)
        value = by_repo["tnwks-ops"]
        self.assertEqual(by_repo[PRIVATE[0]], value)
        self.assertTrue(value.startswith("#privacy-terms:v1:"))
        self.assertEqual(len(value.splitlines()), 1)
        for term in ("zebrafinch", "quokka@example.invalid"):
            self.assertNotIn(term, value.lower())
            self.assertEqual(self.decode(value, f"a {term} here"), 1)
        self.assertEqual(self.decode(value, "nothing here"), 0)
        self.assertIn("the opaque form: 1 public (tnwks-ops) and 1 private (not named)", out)
        self.assertIn("the plain list, not ready for the opaque form: 2 private (not named)", out)
        self.assertNothingLeaks(out)
        self.assertNotRegex(out, r"\d+ terms?\b")

    def test_public_repo_not_ready_says_why(self):
        self.set_state(reads=["tnwks-ops"], pins={"tnwks-ops": OLD}, fixed_shas=[FIXED])
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 0, out)
        self.assertTrue(all(c["stdin"] == TERMS for c in self.sets()))
        self.assertIn("tnwks-ops: its .github/workflows/privacy-text.yaml pins a release without the fixed comment check", out)

    def test_script_without_reader_keeps_the_plain_list(self):
        self.set_state(pins={"tnwks-ops": FIXED}, fixed_shas=[FIXED])
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 0, out)
        self.assertTrue(all(c["stdin"] == TERMS for c in self.sets()))
        self.assertIn("tnwks-ops: its bin/privacy-check doesn't read the opaque form", out)

    def test_no_script_or_no_comment_check_keeps_the_plain_list(self):
        self.set_state(reads=["tnwks-ops"], no_script=[PRIVATE[0]], fixed_shas=[FIXED])
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 0, out)
        self.assertTrue(all(c["stdin"] == TERMS for c in self.sets()))
        self.assertIn("tnwks-ops: it has no .github/workflows/privacy-text.yaml", out)

    def test_the_workflows_repo_itself(self):
        self.set_state(repos=[["github-workflows", False, False]], secret=["github-workflows"],
                       reads=["github-workflows"], pins={"github-workflows": "local-fixed"})
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.sets()[0]["stdin"].startswith("#privacy-terms:v1:"))

    def test_sync_stops_before_setting_when_a_file_is_unreadable(self):
        self.set_state(**self.ready("tnwks-ops"), contents_error=[PRIVATE[1]])
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 1)
        self.assertEqual(self.sets(), [])
        self.assertIn("can't read bin/privacy-check of a private repository", out)
        self.assertNothingLeaks(out)

    def test_sync_stops_when_the_encoder_fails(self):
        (self.repo / "bin/privacy-check").write_text("echo 'zebrafinch' >&2; exit 2\n")
        self.set_state(**self.ready("tnwks-ops"))
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 1)
        self.assertEqual(self.sets(), [])
        self.assertIn("bin/privacy-check --encode failed (exit 2)", out)
        self.assertNothingLeaks(out)

    def test_dry_run_names_the_forms_and_sets_nothing(self):
        self.set_state(**self.ready("tnwks-ops"))
        rc, out = self.run_main("sync", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sets(), [])
        self.assertIn("the opaque form: 1 public (tnwks-ops)", out)

    def test_every_gh_call_went_to_the_stub(self):
        self.set_state(**self.ready("tnwks-ops"))
        rc, out = self.run_main("sync")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.calls(), "the stub logged the calls")
        self.assertTrue(Path(privacy_terms.GH).is_absolute())

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
