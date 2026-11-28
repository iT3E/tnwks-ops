"""The load of a planned stop of the audit sink (kubernetes/apps/monitoring/vector/README.md,
"A planned stop of a security sink").

load.py runs against a small HTTP server on this host, never an API server: the
server answers as RBAC does for the Secret that the account may not read (403),
or as an API server that is not well. The manifest tests read the files of git.
"""

import http.server
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "kubernetes/apps/monitoring/vector/planned-stop"
SCRIPT = FOLDER / "load.py"
README = ROOT / "kubernetes/apps/monitoring/vector/README.md"


class Recorder(http.server.BaseHTTPRequestHandler):
    status = 403
    seen: ClassVar[list] = []

    def do_GET(self):
        type(self).seen.append((self.path, dict(self.headers)))
        body = b'{"kind":"Status","code":%d}' % self.status
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def api():
    handler = type("Handler", (Recorder,), {"seen": [], "status": 403})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    handler.protocol_version = "HTTP/1.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def run(tmp_path, api_url, **settings):
    token = tmp_path / "token"
    token.write_text("test-token\n")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "CLUSTER": "test-site",
        "API": api_url,
        "TOKEN_FILE": str(token),
        "LOG_SECONDS": "100",
        "TIMEOUT_SECONDS": "2",
    }
    env.update({key: str(value) for key, value in settings.items()})
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-I", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60, check=False)
    return done, time.monotonic() - started


def last_line(done):
    lines = [line for line in done.stdout.splitlines() if line.startswith("planned-stop-load ")]
    assert lines, done.stdout + done.stderr
    return dict(field.split("=", 1) for field in lines[-1].split()[1:])


def test_sends_each_marked_request_at_the_rate(tmp_path, api):
    handler, url = api
    done, seconds = run(tmp_path, url, RATE=40, DURATION_SECONDS=1)
    assert done.returncode == 0, done.stderr
    result = last_line(done)
    assert result["result"] == "done"
    assert result["sent"] == result["expected"] == "40"
    assert len(handler.seen) == 40
    assert seconds >= 0.9, "never faster than the rate"
    paths = {path for path, _ in handler.seen}
    assert paths == {"/api/v1/namespaces/monitoring/secrets/vector-planned-stop-marker"}
    agents = [headers["User-Agent"] for _, headers in handler.seen]
    assert agents == [f"vector-planned-stop/test-site/{number:06d}" for number in range(40)]
    assert {headers["Authorization"] for _, headers in handler.seen} == {"Bearer test-token"}


def test_stops_when_the_api_server_is_not_well(tmp_path, api):
    handler, url = api
    handler.status = 500
    done, _ = run(tmp_path, url, RATE=100, DURATION_SECONDS=10, TROUBLE_LIMIT=5)
    assert done.returncode == 0, "a stop of the load is no failed Job"
    result = last_line(done)
    assert result["result"] == "api-trouble"
    assert result["other"] == "5"
    assert len(handler.seen) == 5


def test_stops_when_no_answer_comes(tmp_path):
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    done, _ = run(tmp_path, f"http://127.0.0.1:{port}", RATE=100, DURATION_SECONDS=10, TROUBLE_LIMIT=3)
    assert done.returncode == 0
    result = last_line(done)
    assert result["result"] == "api-trouble"
    assert result["no_answer"] == "3"


def test_the_job_holds_the_rules_of_the_readme():
    documents = [doc for doc in yaml.safe_load_all((FOLDER / "job.yaml").read_text()) if doc]
    kinds = {doc["kind"] for doc in documents}
    assert kinds == {"ServiceAccount", "Job"}, "no Role: the account may read nothing"
    job = next(doc for doc in documents if doc["kind"] == "Job")
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"]}
    rate, duration, grace = int(env["RATE"]), int(env["DURATION_SECONDS"]), int(env["GRACE_SECONDS"])
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] > duration + grace, "a backstop only"
    assert pod["serviceAccountName"] == "vector-planned-stop-load"
    assert container["image"].count("@sha256:") == 1
    readme = README.read_text()
    assert f"{rate} times a" in readme
    assert f"({rate * duration:,} requests)" in readme
    assert f"its {rate * duration:,} requests" in readme
    assert f"{int(env['TROUBLE_LIMIT'])} answers in a row" in readme
