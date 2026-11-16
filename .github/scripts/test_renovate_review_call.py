"""Tests for the call step of the platform review job in renovate-review.yaml.

    python3 -m unittest discover -s .github/scripts -p 'test_renovate_review_call.py' -v

The test reads the script of the step "Ask the run receiver for a review"
from the workflow, and runs it with bash against a fake run receiver on
127.0.0.1. A `curl` on PATH writes its argument list to a file and runs the
real curl, with the mode of each `@file` it reads; a `sleep` on PATH returns at once. It needs bash, curl and jq.
The token and the error text below are test values, not secrets.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE.parent / "workflows" / "renovate-review.yaml"
STEP = "      - name: Ask the run receiver for a review\n"
TOKEN = "test-receiver-token-0123456789"
ERROR_TEXT = "test-error-text-of-a-failed-run"
PR = 7
SHA = "abcdef0123456789abcdef0123456789abcdef01"
KEY = f"renovate-review-{PR}-{SHA[:7]}"
REAL_CURL = shutil.which("curl")


def step_script() -> str:
    """The `run: |` block of the step, without its indent."""
    lines = WORKFLOW.read_text().splitlines(keepends=True)
    i = lines.index(STEP)
    while lines[i].strip() != "run: |":
        i += 1
    out = []
    for line in lines[i + 1 :]:
        if line.strip() and not line.startswith(" " * 10):
            break
        out.append(line[10:] if line.strip() else "\n")
    return "".join(out)


class Receiver:
    """A fake run receiver. `get` and `post` map a key to the codes of its
    answers in order; the last code stays. 0 closes the connection with no
    answer. A key with no entry answers GET 404 and POST 200."""

    def __init__(self, get: dict | None = None, post: dict | None = None):
        self.get = {k: list(v) for k, v in (get or {}).items()}
        self.post = {k: list(v) for k, v in (post or {}).items()}
        self.calls: list[tuple[str, str]] = []
        self.auth: set[str] = set()
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, codes: list, key: str, default: int):
                code = (codes.pop(0) if len(codes) > 1 else codes[0]) if codes else default
                if code == 0:
                    self.close_connection = True
                    return
                status = {200: "succeeded", 202: "running", 502: "failed", 504: "timed_out"}.get(code)
                body = {"key": key, "agent": "renovate-reviewer", "job": "run-test", "status": status}
                if code == 200:
                    body["output"] = {"status": "reviewed", "verdict": "ready", "comment_url": "https://example.test/c/1"}
                if code in (502, 504, 503, 404, 409):
                    body["error"] = ERROR_TEXT
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authorized(self) -> bool:
                receiver.auth.add(self.headers.get("authorization") or "")
                if self.headers.get("authorization") != f"Bearer {TOKEN}":
                    self.send_response(401)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                key = self.path.rsplit("/", 1)[1]
                receiver.calls.append(("GET", key))
                self.answer(receiver.get.setdefault(key, []), key, 404)

            def do_POST(self):
                if not self.authorized():
                    return
                req = json.loads(self.rfile.read(int(self.headers["content-length"])))
                assert req == {
                    "agent": "renovate-reviewer",
                    "input": {"repo": "example/ops", "pr": PR, "head_sha": SHA},
                    "key": req["key"],
                    "wait": True,
                }, req
                receiver.calls.append(("POST", req["key"]))
                self.answer(receiver.post.setdefault(req["key"], []), req["key"], 200)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(REAL_CURL and shutil.which("jq") and shutil.which("bash"), "needs bash, curl and jq")
class CallStep(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.runner_temp = self.tmp / "runner"
        self.runner_temp.mkdir()
        self.argv_log = self.tmp / "curl-argv.jsonl"
        curl = self.bin / "curl"
        curl.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "modes = [oct(os.stat(a[1:]).st_mode & 0o777) for a in sys.argv[1:] if a.startswith('@')]\n"
            f"open({str(self.argv_log)!r}, 'a').write(json.dumps({{'argv': sys.argv, 'modes': modes}}) + '\\n')\n"
            f"os.execv({REAL_CURL!r}, [{REAL_CURL!r}] + sys.argv[1:])\n"
        )
        curl.chmod(0o755)
        sleep = self.bin / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)

    def run_step(self, receiver: Receiver, token: str = TOKEN) -> tuple[int, str]:
        self.addCleanup(receiver.close)
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "RUN_RECEIVER_TOKEN": token,
            "RUN_RECEIVER_URL": receiver.url,
            "REPO": "example/ops",
            "PR": str(PR),
            "SHA": SHA,
            "RUNNER_TEMP": str(self.runner_temp),
        }
        done = subprocess.run(
            ["bash", "-e", "-c", step_script()], env=env, capture_output=True, text=True, timeout=120, check=False
        )
        log = done.stdout + done.stderr
        # Never the token or the error text of a run in the log, never the
        # token in an argument list, and no header file left behind.
        self.assertNotIn(token, log)
        self.assertNotIn(ERROR_TEXT, log)
        argv = self.argv_log.read_text() if self.argv_log.exists() else ""
        self.assertNotIn(token, argv)
        # Each curl reads the token from the header file, and only its owner
        # can read that file.
        for line in argv.splitlines():
            self.assertEqual(json.loads(line)["modes"], ["0o600"], line)
        self.assertEqual(list(self.runner_temp.glob("receiver-auth.*")), [])
        return done.returncode, log

    def check(self, receiver: Receiver, code: int, calls: list[tuple[str, str]]) -> str:
        rc, log = self.run_step(receiver)
        self.assertEqual((rc, receiver.calls), (code, calls), log)
        self.assertEqual(receiver.auth, {f"Bearer {TOKEN}"})
        return log

    def test_a_first_attempt_that_ends_well(self):
        log = self.check(Receiver(), 0, [("GET", f"{KEY}-1"), ("POST", f"{KEY}-1")])
        self.assertIn("verdict ready", log)
        self.assertIn("comment https://example.test/c/1", log)

    def test_a_failed_attempt_is_followed_by_the_next_key(self):
        self.check(
            Receiver(post={f"{KEY}-1": [502]}), 0, [("GET", f"{KEY}-1"), ("POST", f"{KEY}-1"), ("POST", f"{KEY}-2")]
        )

    def test_three_failed_attempts_stop(self):
        log = self.check(
            Receiver(post={f"{KEY}-1": [502], f"{KEY}-2": [504], f"{KEY}-3": [502]}),
            1,
            [("GET", f"{KEY}-1"), ("POST", f"{KEY}-1"), ("POST", f"{KEY}-2"), ("POST", f"{KEY}-3")],
        )
        self.assertIn("3 attempts at abcdef0 failed", log)

    def test_an_earlier_success_makes_no_new_run(self):
        self.check(Receiver(get={f"{KEY}-1": [502], f"{KEY}-2": [200]}), 0, [("GET", f"{KEY}-1"), ("GET", f"{KEY}-2")])

    def test_two_earlier_failures_leave_attempt_3(self):
        self.check(
            Receiver(get={f"{KEY}-1": [502], f"{KEY}-2": [504]}),
            0,
            [("GET", f"{KEY}-1"), ("GET", f"{KEY}-2"), ("GET", f"{KEY}-3"), ("POST", f"{KEY}-3")],
        )

    def test_three_earlier_failures_leave_no_attempt(self):
        self.check(
            Receiver(get={f"{KEY}-1": [502], f"{KEY}-2": [502], f"{KEY}-3": [504]}),
            1,
            [("GET", f"{KEY}-1"), ("GET", f"{KEY}-2"), ("GET", f"{KEY}-3")],
        )

    def test_a_running_run_is_joined_with_the_same_key(self):
        self.check(Receiver(get={f"{KEY}-1": [202]}), 0, [("GET", f"{KEY}-1"), ("POST", f"{KEY}-1")])

    def test_a_call_with_no_answer_goes_again_with_the_same_key(self):
        self.check(
            Receiver(post={f"{KEY}-1": [503, 0, 200]}),
            0,
            [("GET", f"{KEY}-1"), ("POST", f"{KEY}-1"), ("POST", f"{KEY}-1"), ("POST", f"{KEY}-1")],
        )

    def test_a_first_read_with_no_answer_goes_again(self):
        self.check(
            Receiver(get={f"{KEY}-1": [503, 0, 404]}),
            0,
            [("GET", f"{KEY}-1"), ("GET", f"{KEY}-1"), ("GET", f"{KEY}-1"), ("POST", f"{KEY}-1")],
        )

    def test_a_first_read_with_no_answer_three_times_fails_with_no_run(self):
        log = self.check(Receiver(get={f"{KEY}-1": [503]}), 1, [("GET", f"{KEY}-1")] * 3)
        self.assertIn("answered 503", log)

    def test_an_answer_of_202_is_read_until_the_run_ends(self):
        self.check(
            Receiver(get={f"{KEY}-1": [404, 202, 200]}, post={f"{KEY}-1": [202]}),
            0,
            [("GET", f"{KEY}-1"), ("POST", f"{KEY}-1"), ("GET", f"{KEY}-1"), ("GET", f"{KEY}-1")],
        )

    def test_a_conflict_is_not_tried_again(self):
        self.check(Receiver(post={f"{KEY}-1": [409]}), 1, [("GET", f"{KEY}-1"), ("POST", f"{KEY}-1")])

    def test_a_wrong_token_fails_at_once(self):
        receiver = Receiver()
        rc, _ = self.run_step(receiver, token="test-wrong-token-9876543210")
        self.assertEqual((rc, receiver.calls), (1, []))

    def test_curl_does_not_get_the_token_in_its_env(self):
        # The step unsets the variable after it writes the header file.
        self.assertIn("unset RUN_RECEIVER_TOKEN", step_script())


if __name__ == "__main__":
    unittest.main()
