"""Tests for the gates of the CI that hold a branch of another identity
(docs/operations/ci.md, "Agent pull requests").

    python3 -m unittest discover -s .github/scripts -p 'test_ci_gates.py' -v

The shell tests take the run block of one step out of its workflow file and
run it with bash in a throwaway clone, with the env that GitHub would give
it: the job Changes of ci.yaml, the answer of pr-trust.yaml and the merge
notice (with curl stubbed). The condition tests evaluate the "if:" of each
job that guards a gate, with a small evaluator of the expressions of
GitHub Actions (below), for each kind of run: the owner alone, Renovate,
the App, a push of the App into the branch of the owner, a push to main, a
hand start. A job "if:" with no status function gets an implicit
success(), so a skipped need skips the job, as on GitHub. Stdlib only.
"""
from __future__ import annotations

import json
import math
import os
import re
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

    def domains(self, trusted: str, base_ref: str = "main", base_sha: str | None = None) -> subprocess.CompletedProcess:
        return self.run_step("ci.yaml", "Domains", self.clone, EVENT="pull_request",
                             PR_BASE=base_sha or self.base_sha, PR_BASE_REF=base_ref,
                             PR_HEAD=self.head, PUSH_BEFORE="", SHA=self.head, TRUSTED=trusted,
                             DEFAULT_BRANCH="main")

    def test_another_identity_fails_on_another_base(self):
        r = self.domains("false", base_ref="x")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("the base is not main", r.stdout)
        self.assertNotIn("kubernetes", self.outputs())

    def test_the_owner_alone_may_have_another_base(self):
        r = self.domains("true", base_ref="x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_another_identity_diffs_from_the_merge_base_with_main(self):
        # A base SHA that already holds the change (the head itself) hides
        # it from a diff against the base; the merge base with main does not.
        r = self.domains("false", base_sha=self.head)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.outputs().get("kubernetes"), "true", r.stdout + r.stderr)

    def test_no_code_of_the_branch_runs_before_the_domains(self):
        changes = job("ci.yaml", "changes")
        names = re.findall(r"^      - name: (.+)$", changes, re.M)
        self.assertLess(names.index("Domains"), names.index("Test bin/ci-changes"), names)
        before = changes.split("- name: Domains")[0]
        self.assertEqual(re.findall(r"^\s+run: ", before, re.M), [], "a run step before Domains")

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
# Answers the GitHub API from files, and records a post to the chat API and
# a new issue. FAKE_CHAT_FAIL makes the chat API fail.
url=""
for a in "$@"; do case "$a" in http*) url="$a" ;; esac; done
case "$url" in
  *"/contents/.github/scripts/owner-label-check.py?ref="*) cat "$FAKE_DIR/script.py" ;;
  *"/commits/"*"/pulls") cat "$FAKE_DIR/pulls.json" ;;
  *"/compare/"*) cat "$FAKE_DIR/compare.json" ;;
  *"/sendMessage") [ -z "${FAKE_CHAT_FAIL:-}" ] || exit 22; printf '%s\n' "$@" > "$FAKE_DIR/post.txt" ;;
  *"/issues") printf '%s\n' "$@" > "$FAKE_DIR/issue.txt" ;;
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
        (self.fake / "sleep").write_text("#!/bin/sh\nexit 0\n")
        (self.fake / "sleep").chmod(0o755)
        self.app("example-app[bot]")
        (self.fake / "pulls.json").write_text(json.dumps([
            {"number": 42, "title": "fix(apps): a change", "body": "Why: the reason of the agent.",
             "merged_at": "2026-11-21T10:00:00Z", "html_url": "https://github.com/example/ops/pull/42"}]))
        (self.fake / "compare.json").write_text(json.dumps(
            {"files": [{"filename": "kubernetes/apps/a.yaml"}, {"filename": "docs/b.md"}]}))

    def app(self, login: str):
        """The script on main, with this login as the App of the main agent."""
        (self.fake / "script.py").write_text(f'RENOVATE = "renovate[bot]"\nMAIN_AGENT_APP = "{login}"\n')

    def notice(self, token: str = "123:fake-token", chat: str = "1001", actor: str = "example-app[bot]", **env):
        return self.run_step("merge-notice.yaml", "Post the notice", self.base, path_prefix=self.fake,
                             FAKE_DIR=str(self.fake), GH_TOKEN="fake", BOT_TOKEN=token, CHAT_ID=chat,
                             REPO="example/ops", BEFORE="a" * 40, AFTER="b" * 40, ACTOR=actor,
                             SERVER="https://github.com", **env)

    def test_another_actor_posts_nothing(self):
        r = self.notice(actor="example-release[bot]")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not a merge of the App", r.stdout)
        self.assertFalse((self.fake / "post.txt").exists())

    def test_until_the_app_is_set_nothing_posts(self):
        self.app("")
        r = self.notice(token="")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.fake / "post.txt").exists())

    def test_a_failed_post_fails_the_step(self):
        if not shutil.which("jq"):
            self.skipTest("no jq")
        r = self.notice(FAKE_CHAT_FAIL="1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("the notice did not post", r.stdout)

    def test_a_failed_notice_opens_an_issue_for_the_owner(self):
        if not shutil.which("jq"):
            self.skipTest("no jq")
        self.assertIn("if: failure()", text("merge-notice.yaml").split("- name: Tell the owner")[1].split("run:")[0])
        r = self.run_step("merge-notice.yaml", "Tell the owner", self.base, path_prefix=self.fake,
                          FAKE_DIR=str(self.fake), GH_TOKEN="fake", REPO="example/ops", AFTER="b" * 40,
                          OWNER="example-owner", RUN_URL="https://github.com/example/ops/actions/runs/7")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        issue = (self.fake / "issue.txt").read_text()
        self.assertIn("https://api.github.com/repos/example/ops/issues", issue)
        body = json.loads(issue.splitlines()[-1])
        self.assertEqual(body["title"], "Merge notice failed for bbbbbbb")
        self.assertEqual(body["assignees"], ["example-owner"])
        self.assertIn("actions/runs/7", body["body"])

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


# ------------------------------------------------- the conditions of the jobs


class Expr:
    """The expressions of GitHub Actions, enough for the "if:" of a job:
    literals, context paths, ! && || == != < <= > >=, parentheses, and the
    functions contains, startsWith, endsWith, fromJSON, toJSON and the
    status functions. && and || give an operand, as on GitHub; == on two
    strings ignores case; other types compare as numbers."""

    TOKEN = re.compile(r"""\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>-?\d+(?:\.\d+)?)|(?P<op>&&|\|\||==|!=|<=|>=|[!<>(),])"""
                       r"""|(?P<name>[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_*][A-Za-z0-9_-]*)*))""")

    def __init__(self, source: str, context: dict, needs: dict, cancelled: bool = False):
        self.tokens, pos = [], 0
        source = source.strip()
        while pos < len(source):
            m = self.TOKEN.match(source, pos)
            if not m or m.end() == pos:
                raise ValueError(f"can not read {source[pos:]!r}")
            kind = m.lastgroup
            self.tokens.append((kind, m.group(kind)))
            pos = m.end()
            while pos < len(source) and source[pos].isspace():
                pos += 1
        self.i = 0
        self.context, self.needs, self.is_cancelled = context, needs, cancelled

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise ValueError(f"expected {value}, got {tok}")
        self.i += 1
        return tok

    def value(self):
        v = self.or_()
        if self.i != len(self.tokens):
            raise ValueError(f"left over: {self.tokens[self.i:]}")
        return v

    def or_(self):
        v = self.and_()
        while self.peek()[1] == "||":
            self.take()
            right = self.and_()
            v = v if truthy(v) else right
        return v

    def and_(self):
        v = self.cmp()
        while self.peek()[1] == "&&":
            self.take()
            right = self.cmp()
            v = right if truthy(v) else v
        return v

    def cmp(self):
        v = self.unary()
        while self.peek()[1] in ("==", "!=", "<", "<=", ">", ">="):
            op = self.take()[1]
            right = self.unary()
            if op == "==":
                v = equal(v, right)
            elif op == "!=":
                v = not equal(v, right)
            else:
                a, b = number(v), number(right)
                v = {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        return v

    def unary(self):
        if self.peek()[1] == "!":
            self.take()
            return not truthy(self.unary())
        return self.primary()

    def primary(self):
        kind, text = self.take()
        if text == "(":
            v = self.or_()
            self.take(")")
            return v
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text)
        if kind != "name":
            raise ValueError(f"unexpected {text}")
        if text in ("true", "false"):
            return text == "true"
        if text == "null":
            return None
        if self.peek()[1] == "(":
            self.take()
            args = []
            while self.peek()[1] != ")":
                args.append(self.or_())
                if self.peek()[1] == ",":
                    self.take()
            self.take(")")
            return self.call(text, args)
        v = self.context
        for part in text.split("."):
            v = v.get(part) if isinstance(v, dict) else None
        return v

    def call(self, name: str, args: list):
        results = [n.get("result") for n in self.needs.values()]
        if name == "success":
            return not self.is_cancelled and all(r == "success" for r in results)
        if name == "failure":
            return any(r == "failure" for r in results)
        if name == "cancelled":
            return self.is_cancelled
        if name == "always":
            return True
        if name == "contains":
            hay, item = args
            if isinstance(hay, list):
                return any(equal(x, item) for x in hay)
            return str(item).lower() in str(hay or "").lower()
        if name == "startsWith":
            return str(args[0] or "").lower().startswith(str(args[1]).lower())
        if name == "endsWith":
            return str(args[0] or "").lower().endswith(str(args[1]).lower())
        if name == "fromJSON":
            return json.loads(args[0])
        if name == "toJSON":
            return json.dumps(args[0])
        raise ValueError(f"no function {name}")


def truthy(v) -> bool:
    if isinstance(v, float) and math.isnan(v):
        return False
    return v not in (None, False, 0, "") if not isinstance(v, (dict, list)) else True


def number(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else 0.0
        except ValueError:
            return math.nan
    return math.nan


def equal(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    if type(a) is type(b) and not isinstance(a, (int, float)):
        return a == b
    return number(a) == number(b)


def job_if(workflow: str, name: str) -> str:
    """The "if:" of a job, as one line, with no ${{ }}."""
    lines = job(workflow, name).splitlines()
    for i, line in enumerate(lines):
        if line.startswith("    if:"):
            rest = line[len("    if:"):].strip()
            if rest in (">-", ">", "|"):
                body = []
                for more in lines[i + 1:]:
                    if more.strip() and not more.startswith("      "):
                        break
                    body.append(more.strip())
                rest = " ".join(body)
            rest = rest.strip()
            if rest.startswith("${{") and rest.endswith("}}"):
                rest = rest[3:-2]
            return rest
    return ""


STATUS = re.compile(r"\b(success|failure|cancelled|always)\s*\(")


def runs(workflow: str, name: str, context: dict, needs: dict | None = None, cancelled: bool = False) -> bool:
    """Whether GitHub would run the job: its "if:", with an implicit
    success() when it has no status function."""
    needs = needs or {}
    source = job_if(workflow, name) or "success()"
    if not STATUS.search(source):
        source = f"success() && ({source})"
    ctx = dict(context, needs=needs)
    return truthy(Expr(source, ctx, needs, cancelled).value())


# The login of the owner, as the workflows name it (read, not written here).
OWNER = re.search(r"user\.login == '([^']+)'", text("edgeos-plan.yaml")).group(1)
REPO = f"{OWNER}/ops"
APP = "example-app[bot]"


def pr_event(author: str, actor: str | None = None, fork: bool = False) -> dict:
    return {"github": {
        "event_name": "pull_request", "ref": "refs/pull/1/merge", "repository": REPO,
        "repository_owner": OWNER, "actor": actor or author,
        "event": {"pull_request": {"user": {"login": author},
                                   "head": {"repo": {"full_name": "someone/fork" if fork else REPO}}}}}}


def push_event(ref: str = "refs/heads/main", event: str = "push", actor: str = OWNER) -> dict:
    return {"github": {"event_name": event, "ref": ref, "repository": REPO, "repository_owner": OWNER,
                       "actor": actor, "event": {}}}


def done(result: str = "success", **outputs: str) -> dict:
    return {"result": result, "outputs": outputs}


class Expressions(unittest.TestCase):
    """The evaluator itself, on the rules of GitHub that the gates lean on."""

    def ev(self, source, context=None, needs=None):
        return Expr(source, context or {}, needs or {}).value()

    def test_operators(self):
        self.assertEqual(self.ev("'a' && 'b'"), "b")
        self.assertEqual(self.ev("'' || 'b'"), "b")
        self.assertTrue(self.ev("'ABC' == 'abc'"))
        self.assertTrue(self.ev("!(1 == 2) && null == null"))
        self.assertTrue(self.ev("contains(fromJSON('[\"x\", \"y\"]'), 'Y')"))
        self.assertTrue(self.ev("a.b-c.d == 'v'", {"a": {"b-c": {"d": "v"}}}))
        self.assertFalse(self.ev("a.missing == 'v'", {"a": {}}))

    def test_a_skipped_need_skips_a_job_with_no_status_function(self):
        needs = {"x": done("skipped")}
        self.assertFalse(runs_text("true", needs))
        self.assertTrue(runs_text("!cancelled()", needs))


def runs_text(source: str, needs: dict) -> bool:
    if not STATUS.search(source):
        source = f"success() && ({source})"
    return truthy(Expr(source, {"needs": needs}, needs).value())


class HostCheck(unittest.TestCase):
    """ansible-hosts.yaml: who runs code on the hosts, and when."""

    W = "ansible-hosts.yaml"

    def flow(self, context: dict, trusted: str | None, approve_result: str = "success",
             gate_result: str = "success") -> dict:
        """The jobs that run, in order, with the results the test gives."""
        ran = {}
        trust = done("skipped")
        if runs(self.W, "trust", context):
            ran["trust"] = True
            trust = done("success", trusted=trusted)
        if not runs(self.W, "plan", context, {}):
            return ran
        ran["plan"] = True
        mode = {"pull_request": "check", "push": "apply", "schedule": "drift"}.get(
            context["github"]["event_name"], "apply")
        plan = done("success", mode=mode, sites='[{"name": "site_a"}]')
        approve = done("skipped")
        if runs(self.W, "approve", context, {"trust": trust, "plan": plan}):
            ran["approve"] = True
            approve = done(approve_result)
        gate = done("skipped")
        if runs(self.W, "apply-gate", context, {"plan": plan}):
            ran["apply-gate"] = True
            gate = done(gate_result)
        ran["run"] = runs(self.W, "run", context, {"trust": trust, "plan": plan, "approve": approve,
                                                  "apply-gate": gate})
        return ran

    def test_the_owner_alone_checks_at_once(self):
        ran = self.flow(pr_event(OWNER), "true")
        self.assertNotIn("approve", ran)
        self.assertTrue(ran["run"])

    def test_the_owner_with_a_push_of_an_app_waits(self):
        ran = self.flow(pr_event(OWNER), "false", approve_result="failure")
        self.assertTrue(ran["approve"])
        self.assertFalse(ran["run"])
        self.assertTrue(self.flow(pr_event(OWNER), "false")["run"])

    def test_an_app_waits_for_the_owner(self):
        for context in (pr_event(APP), pr_event(OWNER, actor=APP)):
            ran = self.flow(context, "false", approve_result="failure")
            self.assertTrue(ran["approve"])
            self.assertFalse(ran["run"])

    def test_an_app_with_a_true_answer_still_waits(self):
        # Only the owner as author skips the wait, whatever the answer says.
        ran = self.flow(pr_event(APP), "true", approve_result="failure")
        self.assertTrue(ran["approve"])
        self.assertFalse(ran["run"])

    def test_renovate_runs_nothing_and_waits_for_nothing(self):
        for trusted in ("true", "false"):
            ran = self.flow(pr_event("renovate[bot]"), trusted)
            self.assertNotIn("plan", ran)
            self.assertNotIn("approve", ran)
            self.assertNotIn("run", ran)

    def test_a_fork_runs_nothing(self):
        self.assertNotIn("plan", self.flow(pr_event("someone", fork=True), "false"))

    def test_a_failed_trust_job_holds_the_run(self):
        context = pr_event(OWNER)
        plan = done("success", mode="check", sites='[{"name": "site_a"}]')
        trust = done("failure")
        self.assertTrue(runs(self.W, "approve", context, {"trust": trust, "plan": plan}))
        self.assertFalse(runs(self.W, "run", context, {"trust": trust, "plan": plan, "approve": done("failure"),
                                                      "apply-gate": done("skipped")}))

    def test_a_push_to_main_applies_after_the_gate(self):
        ran = self.flow(push_event(), None)
        self.assertNotIn("trust", ran)
        self.assertTrue(ran["apply-gate"])
        self.assertTrue(ran["run"])
        self.assertFalse(self.flow(push_event(), None, gate_result="failure")["run"])

    def test_the_nightly_check_needs_no_gate(self):
        ran = self.flow(push_event(event="schedule"), None)
        self.assertNotIn("apply-gate", ran)
        self.assertTrue(ran["run"])

    def test_a_hand_start_runs_on_main_only(self):
        self.assertTrue(self.flow(push_event(event="workflow_dispatch"), None)["run"])
        self.assertEqual(self.flow(push_event("refs/heads/agent/x", "workflow_dispatch"), None), {})


class TerraformPlan(unittest.TestCase):
    """terraform.yaml (called by ci.yaml): the plan with the token, or the wait."""

    W = "terraform.yaml"

    def plan(self, context: dict, trusted: str) -> tuple[bool, bool]:
        ctx = dict(context, inputs={"mode": "plan", "trusted": trusted})
        return runs(self.W, "plan", ctx), runs(self.W, "plan-approval", ctx)

    def test_the_owner_and_renovate_alone_plan_at_once(self):
        self.assertEqual(self.plan(pr_event(OWNER), "true"), (True, False))
        self.assertEqual(self.plan(pr_event("renovate[bot]"), "true"), (True, False))

    def test_every_other_case_waits(self):
        for context, trusted in ((pr_event(OWNER), "false"), (pr_event(APP), "true"), (pr_event(APP), "false"),
                                 (pr_event(OWNER), ""), (pr_event(OWNER, fork=True), "true")):
            self.assertEqual(self.plan(context, trusted), (False, True), (context, trusted))

    def test_the_caller_skips_the_plan_when_the_trust_job_fails(self):
        needs = {"trust": done("failure"), "changes": done("success", terraform='["x"]')}
        self.assertFalse(runs("ci.yaml", "terraform", pr_event(OWNER), needs))
        self.assertFalse(runs("ci.yaml", "kubernetes", pr_event(OWNER),
                              {"trust": done("failure"), "changes": done("success", kubernetes="true")}))

    def test_build_success_runs_when_a_need_fails(self):
        self.assertTrue(runs("ci.yaml", "status", pr_event(OWNER), {"trust": done("failure")}))
        self.assertIn("      - trust\n", job("ci.yaml", "status"))

    def test_the_job_changes_runs_when_the_trust_job_fails(self):
        self.assertTrue(runs("ci.yaml", "changes", pr_event(APP), {"trust": done("failure")}))


class EdgeosPlan(unittest.TestCase):
    def test_the_owner_alone_plans(self):
        W = "edgeos-plan.yaml"
        self.assertTrue(runs(W, "plan", pr_event(OWNER), {"trust": done("success", trusted="true")}))
        for context, trusted in ((pr_event(OWNER), "false"), (pr_event(APP), "true"),
                                 (pr_event("renovate[bot]"), "true")):
            self.assertFalse(runs(W, "plan", context, {"trust": done("success", trusted=trusted)}))
        self.assertFalse(runs(W, "plan", pr_event(OWNER), {"trust": done("failure")}))


class Notice(unittest.TestCase):
    def test_the_notice_job_runs_for_each_actor_but_the_owner_and_renovate(self):
        W = "merge-notice.yaml"
        self.assertTrue(runs(W, "notice", push_event(actor=APP)))
        self.assertTrue(runs(W, "notice", push_event(actor="example-release[bot]")))
        self.assertFalse(runs(W, "notice", push_event(actor=OWNER)))
        self.assertFalse(runs(W, "notice", push_event(actor="renovate[bot]")))


class Static(unittest.TestCase):
    """What can not be evaluated: the text of the steps and triggers."""

    def test_the_trust_answer_comes_from_the_script_of_main(self):
        t = text("pr-trust.yaml")
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", t)
        self.assertIn("sparse-checkout: .github/scripts/owner-label-check.py", t)
        self.assertIn("owner-label-check.py --trust", t)
        self.assertIn("HEAD_SHA: ${{ github.event.pull_request.head.sha }}", t)

    def test_the_terraform_plan_gets_the_answer(self):
        self.assertIn("trusted: ${{ needs.trust.outputs.trusted }}", job("ci.yaml", "terraform"))

    def test_the_rendered_diff_runs_the_scripts_of_main_for_others(self):
        d = job("kubernetes.yaml", "diff")
        self.assertIn('"${SCRIPTS}/.github/flate/supply.sh"', d)
        self.assertIn('python3 -I "${SCRIPTS}/.github/scripts/flate-diff-comment.py"', d)
        self.assertIn("trusted: ${{ needs.trust.outputs.trusted }}", job("ci.yaml", "kubernetes"))

    def test_the_host_gates_use_their_environments(self):
        self.assertIn("name: external-contributions", job("ansible-hosts.yaml", "approve"))
        self.assertIn("environment: prod", job("ansible-hosts.yaml", "apply-gate"))

    def test_the_link_check_runs_on_main_only(self):
        self.assertFalse(runs("link-check.yaml", "link-check", push_event("refs/heads/agent/x", "workflow_dispatch")))
        self.assertTrue(runs("link-check.yaml", "link-check", push_event(event="schedule")))

    def test_the_checks_run_on_each_push_of_an_agent_branch(self):
        for workflow in ("privacy.yaml", "secret-scan.yaml"):
            on = text(workflow).split("permissions:")[0]
            self.assertIn('branches: ["main", "agent/**"]', on, workflow)
            self.assertNotIn("paths-ignore", on, workflow)

    def test_the_notice_has_no_path_filter_and_no_code_of_the_repository(self):
        t = text("merge-notice.yaml")
        self.assertNotIn("paths", t.split("permissions:")[0])
        self.assertNotIn("uses:", t)
        self.assertNotIn("actions/checkout", t)
        self.assertIn("name: merge-notice", t)
        self.assertNotIn("vars.", t)


if __name__ == "__main__":
    unittest.main()
