"""Tests for the gates of the CI that hold a branch of another identity
(docs/operations/ci.md, "Agent pull requests").

    python3 -m unittest discover -s .github/scripts -p 'test_ci_gates.py' -v

The shell tests take the run block of one step out of its workflow file and
run it with bash in a throwaway clone, with the env that GitHub would give
it: the job Changes of ci.yaml, the answer of pr-trust.yaml and the merge
notice (with curl stubbed). The static tests read the conditions of the
gates. Stdlib only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def text(workflow: str) -> str:
    return (WORKFLOWS / workflow).read_text()


def step(workflow: str, name: str) -> str:
    """The run block of the step with this name, as plain shell."""
    lines = text(workflow).splitlines()
    start = lines.index(f"      - name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body = []
    for line in lines[run + 1:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line)
    return textwrap.dedent("\n".join(body)) + "\n"


def job(workflow: str, name: str) -> str:
    """The text of one job, up to the next job."""
    lines = text(workflow).splitlines()
    start = lines.index(f"  {name}:")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("  ") and not lines[i].startswith("   ")
                and lines[i].strip().endswith(":") and not lines[i].strip().startswith("#")), len(lines))
    return "\n".join(lines[start:end])


def clean_env(**extra: str) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GITHUB_", "GH_"))}
    e.update(GIT_AUTHOR_NAME="Example Person", GIT_AUTHOR_EMAIL="person@example.invalid")
    e.update(GIT_COMMITTER_NAME="Example Person", GIT_COMMITTER_EMAIL="person@example.invalid")
    e.update(extra)
    return e


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.runner = self.base / "runner"
        self.runner.mkdir()
        self.output = self.runner / "output"
        self.output.write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self, workflow: str, name: str, cwd: Path, path_prefix: Path | None = None, **env: str):
        e = clean_env(RUNNER_TEMP=str(self.runner), GITHUB_OUTPUT=str(self.output), **env)
        if path_prefix:
            e["PATH"] = f"{path_prefix}:{e['PATH']}"
        return subprocess.run(["bash", "-e", "-c", step(workflow, name)], capture_output=True, text=True,
                              cwd=cwd, env=e)

    def outputs(self) -> dict:
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)


class Changes(Sandbox):
    """A branch of another identity can not choose which jobs run."""

    def setUp(self):
        super().setUp()
        origin, self.clone = self.base / "origin", self.base / "clone"
        subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
        (origin / "bin").mkdir()
        shutil.copy(ROOT / "bin" / "ci-changes", origin / "bin" / "ci-changes")
        shutil.copytree(ROOT / ".github" / "ci", origin / ".github" / "ci")
        (origin / "kubernetes").mkdir()
        (origin / "kubernetes" / "a.yaml").write_text("a: 1\n")
        self.git(origin, "add", ".")
        self.git(origin, "commit", "-q", "-m", "init")
        subprocess.run(["git", "clone", "-q", str(origin), str(self.clone)], check=True, env=clean_env())
        self.base_sha = self.git(self.clone, "rev-parse", "HEAD").strip()
        self.git(self.clone, "checkout", "-q", "-b", "agent/x")
        # The branch changes a manifest, and turns its own copy of the
        # script into one that reports no change.
        (self.clone / "kubernetes" / "a.yaml").write_text("a: 2\n")
        (self.clone / "bin" / "ci-changes").write_text("#!/usr/bin/env python3\nprint('kubernetes=false')\n")
        self.git(self.clone, "commit", "-q", "-am", "change")
        self.head = self.git(self.clone, "rev-parse", "HEAD").strip()

    def git(self, where: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True, text=True,
                              env=clean_env()).stdout

    def domains(self, trusted: str) -> subprocess.CompletedProcess:
        return self.run_step("ci.yaml", "Domains", self.clone, EVENT="pull_request", PR_BASE=self.base_sha,
                             PR_HEAD=self.head, PUSH_BEFORE="", SHA=self.head, TRUSTED=trusted,
                             DEFAULT_BRANCH="main")

    def test_another_identity_gets_the_script_of_main(self):
        r = self.domains("false")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.outputs().get("kubernetes"), "true", r.stdout + r.stderr)

    def test_an_empty_answer_counts_as_another_identity(self):
        r = self.domains("")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.outputs().get("kubernetes"), "true")

    def test_the_owner_alone_runs_the_script_of_the_branch(self):
        r = self.domains("true")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.outputs().get("kubernetes"), "false")


class TrustAnswer(Sandbox):
    """pr-trust.yaml answers true or false, and false when in doubt."""

    def answer(self, event: str, actor: str, script: str | None = None) -> str:
        work = self.base / "work"
        (work / ".github" / "scripts").mkdir(parents=True, exist_ok=True)
        if script is not None:
            (work / ".github" / "scripts" / "owner-label-check.py").write_text(script)
        r = self.run_step("pr-trust.yaml", "Answer", work, EVENT=event, REPO="example/ops", PR="1",
                          OWNER_ID="1", OWNER_LOGIN="example-owner", ACTOR=actor)
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.outputs()["trusted"]

    def test_a_pr_takes_the_answer_of_the_script(self):
        self.assertEqual(self.answer("pull_request", "example-owner", "print('trusted=true')\n"), "true")
        self.assertEqual(self.answer("pull_request", "example-owner", "print('trusted=false')\n"), "false")

    def test_a_script_that_fails_or_says_something_else_is_false(self):
        self.assertEqual(self.answer("pull_request", "example-owner", "raise SystemExit(1)\n"), "false")
        self.assertEqual(self.answer("pull_request", "example-owner", "print('trusted=yes')\n"), "false")

    def test_another_event_trusts_the_owner_and_renovate_only(self):
        self.assertEqual(self.answer("workflow_dispatch", "example-owner"), "true")
        self.assertEqual(self.answer("push", "renovate[bot]"), "true")
        self.assertEqual(self.answer("workflow_dispatch", "example-app[bot]"), "false")


FAKE_CURL = r"""#!/usr/bin/env bash
# Answers the GitHub API from files, and records a post to the chat API.
url=""
for a in "$@"; do case "$a" in http*) url="$a" ;; esac; done
case "$url" in
  *"/commits/"*"/pulls") cat "$FAKE_DIR/pulls.json" ;;
  *"/compare/"*) cat "$FAKE_DIR/compare.json" ;;
  *"/sendMessage") printf '%s\n' "$@" > "$FAKE_DIR/post.txt" ;;
  *) exit 22 ;;
esac
"""


class MergeNotice(Sandbox):
    def setUp(self):
        super().setUp()
        self.fake = self.base / "fake"
        self.fake.mkdir()
        (self.fake / "curl").write_text(FAKE_CURL)
        (self.fake / "curl").chmod(0o755)
        (self.fake / "pulls.json").write_text(json.dumps([
            {"number": 42, "title": "fix(apps): a change", "body": "Why: the reason of the agent.",
             "merged_at": "2026-11-21T10:00:00Z", "html_url": "https://github.com/example/ops/pull/42"}]))
        (self.fake / "compare.json").write_text(json.dumps(
            {"files": [{"filename": "kubernetes/apps/a.yaml"}, {"filename": "docs/b.md"}]}))

    def notice(self, token: str = "123:fake-token", chat: str = "1001"):
        return self.run_step("merge-notice.yaml", "Post the notice", self.base, path_prefix=self.fake,
                             FAKE_DIR=str(self.fake), GH_TOKEN="fake", BOT_TOKEN=token, CHAT_ID=chat,
                             REPO="example/ops", BEFORE="a" * 40, AFTER="b" * 40, ACTOR="example-app[bot]",
                             SERVER="https://github.com")

    def test_the_notice_names_the_pr_its_files_and_its_reason(self):
        if not shutil.which("jq"):
            self.skipTest("no jq")
        r = self.notice()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        post = (self.fake / "post.txt").read_text()
        self.assertIn("chat_id=1001", post)
        self.assertIn("Merged by example-app[bot]: #42 fix(apps): a change", post)
        self.assertIn("Files (2):\nkubernetes/apps/a.yaml\ndocs/b.md", post)
        self.assertIn("Reason:\nWhy: the reason of the agent.", post)
        self.assertNotIn("fake-token", post.split("https://api.telegram.org")[0])

    def test_missing_secrets_fail(self):
        r = self.notice(token="")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.fake / "post.txt").exists())


class Conditions(unittest.TestCase):
    """The conditions of the gates, as text."""

    def test_the_trust_answer_comes_from_the_script_of_main(self):
        t = text("pr-trust.yaml")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", t)
        self.assertIn("sparse-checkout: .github/scripts/owner-label-check.py", t)
        self.assertIn("owner-label-check.py --trust", t)

    def test_the_terraform_plan_runs_for_the_trusted_only(self):
        self.assertIn("inputs.trusted == 'true' &&", job("terraform.yaml", "plan"))
        self.assertIn("inputs.trusted != 'true' ||", job("terraform.yaml", "plan-approval"))
        self.assertIn("trusted: ${{ needs.trust.outputs.trusted }}", job("ci.yaml", "terraform"))

    def test_build_success_needs_the_trust_job(self):
        self.assertIn("      - trust\n", job("ci.yaml", "status"))

    def test_the_rendered_diff_runs_the_scripts_of_main_for_others(self):
        d = job("kubernetes.yaml", "diff")
        self.assertIn('"${SCRIPTS}/.github/flate/supply.sh"', d)
        self.assertIn('python3 -I "${SCRIPTS}/.github/scripts/flate-diff-comment.py"', d)
        self.assertIn("trusted: ${{ needs.trust.outputs.trusted }}", job("ci.yaml", "kubernetes"))

    def test_the_edgeos_plan_runs_for_the_trusted_only(self):
        self.assertIn("needs.trust.outputs.trusted == 'true' &&", job("edgeos-plan.yaml", "plan"))

    def test_the_host_check_waits_for_the_owner_and_applies_from_main_only(self):
        self.assertIn("github.event_name != 'workflow_dispatch' || github.ref == 'refs/heads/main'",
                      job("ansible-hosts.yaml", "plan"))
        approve = job("ansible-hosts.yaml", "approve")
        self.assertIn("name: external-contributions", approve)
        self.assertIn("github.event.pull_request.user.login == github.repository_owner", approve)
        self.assertIn("environment: prod", job("ansible-hosts.yaml", "apply-gate"))
        run = job("ansible-hosts.yaml", "run")
        self.assertIn("needs: [trust, plan, approve, apply-gate]", run)
        self.assertIn("needs.apply-gate.result == 'success'", run)
        self.assertIn("needs.approve.result == 'success'", run)

    def test_the_link_check_runs_on_main_only(self):
        self.assertIn("if: github.ref == 'refs/heads/main'", job("link-check.yaml", "link-check"))

    def test_the_privacy_check_runs_on_each_push_of_an_agent_branch(self):
        self.assertIn('branches: ["main", "agent/**"]', text("privacy.yaml"))

    def test_the_notice_has_no_path_filter_and_no_code_of_the_repository(self):
        t = text("merge-notice.yaml")
        self.assertNotIn("paths", t.split("permissions:")[0])
        self.assertNotIn("uses:", t)
        self.assertNotIn("actions/checkout", t)
        self.assertIn("environment: merge-notice", t)
        self.assertIn("if: vars.MAIN_AGENT_APP != '' && github.actor == vars.MAIN_AGENT_APP", t)


if __name__ == "__main__":
    unittest.main()
