"""The token job of the App of the main agent and the key step of the owner
(docs/apps/github-token.md; Design Doc of effort 0003, section 4.2.6, "The
credential of the code host server").

- bin/main-app-key runs in a copy of its files with a fake sops on the PATH
  (a stand-in that keeps each value as base64 in an ENC[...] form, runs the
  editor as sops does, and logs its arguments and environment) and with
  throwaway keys that the test makes with openssl. The key must reach the
  Secret, and never an argument, an env var or a line of output.
- scripts/github_token.py runs against a fake API of the code host and a
  fake API server on loopback. The fake code host checks the signature of
  the JWT with the public key of the throwaway key.
- The manifests: the account updates the one Secret by name, only the job
  mounts the key, the token Secret is made once by Flux, the job never
  touches the stopgap token of the owner, and the folder is listed only
  with its key.

The tests need openssl on the PATH; without it they are skipped, except in
CI.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bin/main-app-key"
MAIN = ROOT / "kubernetes/apps/ai/agents/main"
FOLDER = MAIN / "github-token"
APP = FOLDER / "app"
JOB = APP / "scripts/github_token.py"
KEY_SECRET = APP / "secret.sops.yaml"
REPOSITORY = "iT3E/tnwks-ops"
PERMISSIONS = {"metadata": "read", "contents": "write", "pull_requests": "write", "actions": "read",
               "issues": "write"}

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None and not os.environ.get("CI"),
                                   reason="no openssl here")


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def openssl(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["openssl", *args], input=data, capture_output=True, check=True).stdout


@pytest.fixture(scope="module")
def keys(tmp_path_factory) -> dict[str, Path]:
    """Throwaway keys: the forms that pass and the forms that fail."""
    d = tmp_path_factory.mktemp("keys")
    made = {
        "rsa2048": openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                           "-outform", "PEM"),
        "rsa1024": openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:1024"),
        "ec": openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"),
    }
    # The form the code host gives: PKCS#1, "BEGIN RSA PRIVATE KEY", with
    # CRLF line ends as a download on Windows can have.
    made["rsa-pkcs1-crlf"] = openssl("rsa", "-traditional", data=made["rsa2048"]).replace(b"\n", b"\r\n")
    made["with-passphrase"] = openssl("pkey", "-aes256", "-passout", "pass:x", data=made["rsa2048"])
    made["public"] = openssl("pkey", "-pubout", data=made["rsa2048"])
    made["junk"] = b"-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n"
    made["rsa2048-b"] = openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048")
    out = {}
    for name, data in made.items():
        out[name] = d / f"{name}.pem"
        out[name].write_bytes(data)
    return out


def pem_body(path: Path) -> list[str]:
    """The lines of the key between its header and footer."""
    lines = path.read_bytes().decode().replace("\r\n", "\n").strip().splitlines()
    return [x for x in lines if x and not x.startswith("-----")]


# The fake sops: the clear text of a value is base64 in the ENC form, so the
# test can read it back; each call is logged with its arguments and the
# values of its environment.
FAKE_SOPS = r'''#!/usr/bin/env python3
import base64, json, os, shlex, subprocess, sys, tempfile
import yaml
log = os.environ["FAKE_SOPS_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\n")
if os.environ.get("FAKE_SOPS_FAIL") == "1":
    sys.exit(1)
def dec(v):
    return base64.b64decode(v.split("data:", 1)[1].split(",", 1)[0]).decode()
def enc(v):
    return "ENC[AES256_GCM,data:" + base64.b64encode(str(v).encode()).decode() + ",iv:aa==,tag:bb==,type:str]"
args = sys.argv[1:]
if args[:2] == ["-d", "--extract"]:
    path, data = args[3], yaml.safe_load(open(args[3]))
    value = (data.get("stringData") or {}).get("private-key.pem")
    if value is None:
        sys.stderr.write("component private-key.pem not found\n")
        sys.exit(128)
    sys.stdout.write(dec(value))
    sys.exit(0)
(path,) = args
if os.path.exists(path):
    data = yaml.safe_load(open(path))
    data.pop("sops")
    data["stringData"] = {k: dec(v) for k, v in data["stringData"].items()}
    clear = yaml.safe_dump(data)
else:
    clear = "hello: Welcome to SOPS!\nexample_key: example_value\n"
fd, tmp = tempfile.mkstemp(suffix=".yaml")
os.write(fd, clear.encode())
os.close(fd)
if subprocess.run(shlex.split(os.environ["EDITOR"]) + [tmp]).returncode != 0:
    sys.exit(1)
data = yaml.safe_load(open(tmp))
os.unlink(tmp)
data["stringData"] = {k: enc(v) for k, v in data["stringData"].items()}
data["sops"] = {"age": [{"recipient": "age1test"}], "encrypted_regex": "^(data|stringData)$"}
open(path, "w").write(yaml.safe_dump(data, sort_keys=False))
'''


@pytest.fixture
def tree(tmp_path):
    """A copy of the files of the script, and a PATH with the fake sops."""
    (tmp_path / "bin").mkdir()
    for name in ("main-app-key", "sops"):
        shutil.copy2(ROOT / "bin" / name, tmp_path / "bin" / name)
    app = tmp_path / APP.relative_to(ROOT)
    app.mkdir(parents=True)
    shutil.copy2(APP / "cronjob.yaml", app / "cronjob.yaml")
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "sops").write_text(FAKE_SOPS.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    (fake / "sops").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "FAKE_SOPS_LOG": str(tmp_path / "sops.log")}
    return tmp_path, env


def run_key(tree, *args: str, extra: dict | None = None) -> subprocess.CompletedProcess:
    root, env = tree
    return subprocess.run([sys.executable, str(root / "bin/main-app-key"), *args], cwd=root,
                          env={**env, **(extra or {})}, capture_output=True, text=True, timeout=120, check=False)


def secret_of(root: Path) -> dict:
    return yaml.safe_load((root / APP.relative_to(ROOT) / "secret.sops.yaml").read_text())


def app_id_of(root: Path) -> str:
    (cron,) = docs(root / APP.relative_to(ROOT) / "cronjob.yaml")
    (c,) = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    return {e["name"]: e.get("value") for e in c["env"]}["GITHUB_APP_ID"]


def assert_never_shown(tree, result, key: Path) -> None:
    """No line of the key in the output, an argument or an env var of sops."""
    root, _ = tree
    shown = result.stdout + result.stderr
    calls = [json.loads(x) for x in (root / "sops.log").read_text().splitlines()]
    assert calls
    for line in pem_body(key):
        assert line not in shown
        for call in calls:
            assert line not in " ".join(call["argv"])
            assert not [k for k, v in call["env"].items() if line in v]
    b64 = base64.b64encode(key.read_bytes()).decode()[:40]
    assert b64 not in shown


@needs_openssl
@pytest.mark.parametrize("form", ["rsa2048", "rsa-pkcs1-crlf"])
def test_the_owner_step_puts_the_key_in_the_secret_and_shows_flags_only(tree, keys, form):
    root, _ = tree
    result = run_key(tree, "--app-id", "123456", str(keys[form]))
    assert result.returncode == 0, result.stdout + result.stderr
    secret = secret_of(root)
    assert secret["metadata"] == {"name": "main-github-app"}
    assert set(secret["stringData"]) == {"private-key.pem"}
    value = secret["stringData"]["private-key.pem"]
    assert value.startswith("ENC[AES256_GCM,data:")
    clear = base64.b64decode(value.split("data:", 1)[1].split(",", 1)[0]).decode()
    assert clear == keys[form].read_bytes().decode().replace("\r\n", "\n").strip("\n") + "\n"
    # The key in the Secret is one openssl takes.
    openssl("pkey", "-noout", data=clear.encode())
    assert app_id_of(root) == "123456"
    assert_never_shown(tree, result, keys[form])
    lines = result.stdout.splitlines()
    assert lines[:3] == ["key file form=ok", "secret.sops.yaml key=private-key.pem stored ok",
                         "cronjob.yaml app-id=set ok"]
    assert lines[3].startswith("Now delete the download: ") and lines[3].endswith(f"{form}.pem")
    assert len(lines) == 4 and result.stderr == ""


@needs_openssl
@pytest.mark.parametrize(("form", "flag"), [
    ("rsa1024", "form=too-short"),
    ("ec", "form=not-an-rsa-private-key"),
    ("with-passphrase", "form=has-a-passphrase"),
    ("public", "form=not-a-pem-private-key"),
    ("junk", "form=not-an-rsa-private-key"),
])
def test_the_owner_step_refuses_a_file_that_is_not_the_key_of_an_app(tree, keys, form, flag):
    root, _ = tree
    result = run_key(tree, "--app-id", "123456", str(keys[form]))
    assert result.returncode == 1
    assert result.stdout.splitlines() == [f"key file {flag}"]
    # Nothing is written, and sops is never called.
    assert not (root / APP.relative_to(ROOT) / "secret.sops.yaml").exists()
    assert app_id_of(root) == "SET-BY-MAIN-APP-KEY"
    assert not (root / "sops.log").exists()


@needs_openssl
@pytest.mark.parametrize("args", [["--app-id", "12a"], ["--app-id", "0"], ["--app-id", ""], ["123456"]])
def test_the_owner_step_refuses_a_bad_app_id_or_usage(tree, keys, args):
    root, _ = tree
    result = run_key(tree, *args, str(keys["rsa2048"]))
    assert result.returncode == 1
    assert not (root / APP.relative_to(ROOT) / "secret.sops.yaml").exists()


@needs_openssl
def test_a_held_key_stays_unless_replace(tree, keys):
    root, _ = tree
    assert run_key(tree, "--app-id", "123456", str(keys["rsa2048"])).returncode == 0
    before = (root / APP.relative_to(ROOT) / "secret.sops.yaml").read_text()
    again = run_key(tree, "--app-id", "123456", str(keys["rsa2048-b"]))
    assert again.returncode == 1
    assert again.stdout.splitlines()[-1].startswith("secret.sops.yaml key=private-key.pem held=yes")
    assert (root / APP.relative_to(ROOT) / "secret.sops.yaml").read_text() == before
    new = run_key(tree, "--app-id", "654321", "--replace", str(keys["rsa2048-b"]))
    assert new.returncode == 0, new.stdout
    assert "secret.sops.yaml key=private-key.pem replaced ok" in new.stdout
    clear = base64.b64decode(secret_of(root)["stringData"]["private-key.pem"].split("data:", 1)[1].split(",")[0])
    assert clear.decode().strip() == keys["rsa2048-b"].read_text().strip()
    assert app_id_of(root) == "654321"
    assert_never_shown(tree, new, keys["rsa2048-b"])


@needs_openssl
def test_a_failed_sops_writes_nothing_and_says_so(tree, keys):
    root, _ = tree
    result = run_key(tree, "--app-id", "123456", str(keys["rsa2048"]), extra={"FAKE_SOPS_FAIL": "1"})
    assert result.returncode == 1
    assert result.stdout.splitlines()[-1] == "secret.sops.yaml key=private-key.pem write=failed"
    assert app_id_of(root) == "SET-BY-MAIN-APP-KEY"


@needs_openssl
def test_a_secret_that_sops_can_not_read_is_never_written_over(tree, keys):
    root, _ = tree
    path = root / APP.relative_to(ROOT) / "secret.sops.yaml"
    path.write_text("kind: Secret\nsops: {}\n")
    result = run_key(tree, "--app-id", "123456", "--replace", str(keys["rsa2048"]),
                     extra={"FAKE_SOPS_FAIL": "1"})
    assert result.returncode == 1
    assert result.stdout.splitlines()[-1] == "secret.sops.yaml read=failed"
    assert path.read_text() == "kind: Secret\nsops: {}\n"


# The round trip with the real sops, opt-in: MAIN_APP_KEY_SOPS_TEST=1, with
# sops and age-keygen on the PATH. It makes a throwaway age key and a
# .sops.yaml of the copy, so it reads no file of the repository. No CI job
# sets it.

ROUND_TRIP = os.environ.get("MAIN_APP_KEY_SOPS_TEST") == "1"


@needs_openssl
def test_the_round_trip_with_sops_makes_the_file_and_replaces_the_key(tmp_path, keys):
    if not (ROUND_TRIP and shutil.which("sops") and shutil.which("age-keygen")):
        pytest.skip("opt-in: MAIN_APP_KEY_SOPS_TEST=1, sops and age-keygen")
    (tmp_path / "bin").mkdir()
    for name in ("main-app-key", "sops"):
        shutil.copy2(ROOT / "bin" / name, tmp_path / "bin" / name)
    app = tmp_path / APP.relative_to(ROOT)
    app.mkdir(parents=True)
    shutil.copy2(APP / "cronjob.yaml", app / "cronjob.yaml")
    age = tmp_path / "age.txt"
    subprocess.run(["age-keygen", "-o", str(age)], capture_output=True, check=True)
    recipient = re.search(r"public key: (age1\w+)", age.read_text())[1]
    (tmp_path / ".sops.yaml").write_text(
        "creation_rules:\n  - path_regex: kubernetes/.*\\.sops\\.ya?ml\n"
        f"    encrypted_regex: \"^(data|stringData)$\"\n    age: {recipient}\n")
    env = {**os.environ, "SOPS_AGE_KEY_FILE": str(age)}
    first = subprocess.run([sys.executable, "bin/main-app-key", "--app-id", "7", str(keys["rsa2048"])],
                           cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert first.returncode == 0, first.stdout + first.stderr
    secret = yaml.safe_load((app / "secret.sops.yaml").read_text())
    assert str(secret["stringData"]["private-key.pem"]).startswith("ENC[AES256_GCM,data:")
    for line in pem_body(keys["rsa2048"]):
        assert line not in first.stdout + first.stderr
        assert line not in (app / "secret.sops.yaml").read_text()
    second = subprocess.run([sys.executable, "bin/main-app-key", "--app-id", "8", "--replace",
                             str(keys["rsa2048-b"])], cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=120, check=False)
    assert second.returncode == 0, second.stdout + second.stderr
    clear = subprocess.run(["sops", "-d", "--extract", '["stringData"]["private-key.pem"]',
                            str(app / "secret.sops.yaml")], cwd=tmp_path, env=env, capture_output=True,
                           check=True).stdout.decode()
    assert clear.strip() == keys["rsa2048-b"].read_text().strip()


# The token job, against fakes on loopback.

TOKEN = "ghs_" + "T" * 36
MARKER = "a-body-the-job-never-prints"


class Fake:
    """The API of the code host and the API server, in one server."""

    def __init__(self, public: bytes, tmp: Path) -> None:
        self.public = public
        self.tmp = tmp
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.install = {"id": 42, "permissions": dict(PERMISSIONS), "repository_selection": "selected"}
        self.token = {"token": TOKEN, "permissions": dict(PERMISSIONS),
                      "repositories": [{"full_name": REPOSITORY}], "repository_selection": "selected"}
        self.life = 3600
        self.status: dict[str, int] = {}

    def check_jwt(self, auth: str) -> bool:
        head, body, sig = auth.removeprefix("Bearer ").split(".")
        pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        claims = json.loads(pad(body))
        if json.loads(pad(head)) != {"alg": "RS256", "typ": "JWT"}:
            return False
        if claims["iss"] != "123456" or not 0 < claims["exp"] - claims["iat"] <= 600:
            return False
        if claims["iat"] > time.time():
            return False
        pub = self.tmp / "pub.pem"
        pub.write_bytes(self.public)
        sigf = self.tmp / "sig"
        sigf.write_bytes(pad(sig))
        done = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sigf)],
                              input=f"{head}.{body}".encode(), capture_output=True, check=False)
        return done.returncode == 0

    def handler(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def answer(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def handle_any(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else {}
                fake.calls.append((method, self.path, dict(self.headers), body))
                path = self.path
                auth = self.headers.get("Authorization", "")
                for key, code in fake.status.items():
                    if key in path:
                        return self.answer(code, {"message": MARKER})
                if path == f"/repos/{REPOSITORY}/installation" and method == "GET":
                    if not fake.check_jwt(auth):
                        return self.answer(401, {"message": MARKER})
                    return self.answer(200, fake.install)
                if path == "/app/installations/42/access_tokens" and method == "POST":
                    if not fake.check_jwt(auth):
                        return self.answer(401, {"message": MARKER})
                    until = time.gmtime(time.time() + fake.life)
                    return self.answer(201, {**fake.token, "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", until)})
                if path == "/api/v1/namespaces/agents-main/secrets/github-mcp-app-token" and method == "PUT":
                    if auth != "Bearer sa-token":
                        return self.answer(401, {})
                    return self.answer(200, body)
                return self.answer(404, {"message": MARKER})

            def do_GET(self):
                self.handle_any("GET")

            def do_POST(self):
                self.handle_any("POST")

            def do_PUT(self):
                self.handle_any("PUT")

        return H


@pytest.fixture
def job(tmp_path, keys):
    public = openssl("pkey", "-pubout", data=keys["rsa2048"].read_bytes())
    fake = Fake(public, tmp_path)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sa = tmp_path / "sa"
    sa.mkdir()
    (sa / "token").write_text("sa-token\n")
    (cron,) = docs(APP / "cronjob.yaml")
    (c,) = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    manifest = {e["name"]: e["value"] for e in c["env"] if "value" in e}
    port = server.server_address[1]
    env = {"PATH": os.environ["PATH"], **manifest,
           "GITHUB_APP_ID": "123456", "GITHUB_APP_KEY_FILE": str(keys["rsa2048"]),
           "GITHUB_API": f"http://127.0.0.1:{port}", "POD_NAMESPACE": "agents-main",
           "KUBERNETES_SERVICE_HOST": "127.0.0.1", "KUBERNETES_SERVICE_PORT": str(port),
           "KUBE_API_SCHEME": "http", "KUBE_SA_DIR": str(sa)}

    def go(**extra) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(JOB)], env={**env, **extra}, capture_output=True,
                              text=True, timeout=60, check=False)

    yield fake, go
    server.shutdown()


def puts(fake: Fake) -> list[dict]:
    return [body for method, _, _, body in fake.calls if method == "PUT"]


@needs_openssl
def test_the_job_writes_a_narrow_token_into_the_one_secret(job):
    fake, go = job
    result = go()
    assert result.returncode == 0, result.stdout + result.stderr
    (line,) = result.stdout.splitlines()
    assert re.fullmatch(r"github-token result=ok secret=github-mcp-app-token expires=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                        line)
    assert TOKEN not in result.stdout + result.stderr
    (ask,) = [body for method, path, _, body in fake.calls if method == "POST"]
    assert ask == {"repositories": ["tnwks-ops"], "permissions": PERMISSIONS}
    (body,) = puts(fake)
    assert body["metadata"]["name"] == "github-mcp-app-token"
    assert body["metadata"]["namespace"] == "agents-main"
    assert "resourceVersion" not in body["metadata"]
    assert body["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/ssa"] == "IfNotPresent"
    assert body["data"] == {"GITHUB_MCP_TOKEN": base64.b64encode(TOKEN.encode()).decode()}


@needs_openssl
@pytest.mark.parametrize(("change", "step", "detail"), [
    ({"token": {"permissions": {**PERMISSIONS, "administration": "write"}}}, "token", "permissions=not-as-asked"),
    ({"token": {"permissions": {**PERMISSIONS, "issues": "read"}}}, "token", "permissions=not-as-asked"),
    ({"token": {"repositories": [{"full_name": REPOSITORY}, {"full_name": "iT3E/other"}]}}, "token",
     "repositories=not-as-asked"),
    ({"token": {"repositories": [{"full_name": "iT3E/other"}]}}, "token", "repositories=not-as-asked"),
    ({"token": {"repository_selection": "all"}}, "token", "repositories=not-as-asked"),
    ({"token": {"token": ""}}, "token", "token=missing"),
    ({"life": 7200}, "token", "expires=out-of-range"),
    ({"life": -10}, "token", "expires=out-of-range"),
    ({"install": {"permissions": {**PERMISSIONS, "contents": "read"}}}, "installation",
     "permissions=less-than-asked"),
    ({"install": {"permissions": {"metadata": "read"}}}, "installation", "permissions=less-than-asked"),
    ({"status": {"/installation": 404}}, "installation", "status=404"),
    ({"status": {"/access_tokens": 422}}, "token", "status=422"),
    ({"status": {"/secrets/": 403}}, "secret", "status=403"),
])
def test_the_job_fails_closed_and_prints_no_answer(job, change, step, detail):
    fake, go = job
    for key, value in change.items():
        if isinstance(getattr(fake, key), dict) and key != "status":
            getattr(fake, key).update(value)
        else:
            setattr(fake, key, value)
    result = go()
    assert result.returncode == 1
    assert result.stdout.splitlines() == [f"github-token result=failed step={step} {detail}"]
    assert MARKER not in result.stdout + result.stderr and TOKEN not in result.stdout + result.stderr
    if step != "secret":
        assert puts(fake) == []


@needs_openssl
def test_a_wider_installation_is_drift_and_the_token_stays_narrow(job):
    fake, go = job
    fake.install["permissions"]["administration"] = "write"
    fake.install["repository_selection"] = "all"
    result = go()
    assert result.returncode == 0, result.stdout
    assert result.stdout.splitlines()[:2] == ["github-token result=app-drift what=permissions",
                                             "github-token result=app-drift what=repositories"]
    (ask,) = [body for method, _, _, body in fake.calls if method == "POST"]
    assert ask["permissions"] == PERMISSIONS and ask["repositories"] == ["tnwks-ops"]


@needs_openssl
def test_a_wrong_key_gets_no_token(job, keys):
    fake, go = job
    result = go(GITHUB_APP_KEY_FILE=str(keys["rsa2048-b"]))
    assert result.stdout.splitlines() == ["github-token result=failed step=installation status=401"]
    assert puts(fake) == []


@needs_openssl
def test_a_missing_key_file_fails_at_the_jwt(job, tmp_path):
    fake, go = job
    result = go(GITHUB_APP_KEY_FILE=str(tmp_path / "none.pem"))
    assert result.stdout.splitlines() == ["github-token result=failed step=jwt sign=failed"]
    assert fake.calls == []


# The manifests.

def cron() -> dict:
    (c,) = docs(APP / "cronjob.yaml")
    return c


def pod() -> dict:
    return cron()["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def test_the_token_asks_for_the_permissions_of_the_design_for_the_ops_repository():
    (c,) = pod()["containers"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    asked = dict(x.split(":") for x in env["GITHUB_APP_PERMISSIONS"].split(","))
    # D138 and D140: Metadata read, Contents write, Pull requests write,
    # Actions read, Issues read and write. Nothing else, no Workflows.
    assert asked == PERMISSIONS
    assert env["GITHUB_REPOSITORY"] == REPOSITORY
    assert (env["TOKEN_SECRET"], env["TOKEN_KEY"]) == ("github-mcp-app-token", "GITHUB_MCP_TOKEN")
    assert re.fullmatch(r"SET-BY-MAIN-APP-KEY|[1-9][0-9]*", env["GITHUB_APP_ID"])
    # No value of a secret in an env var: the key is a file.
    assert not [e for e in c["env"] if "valueFrom" in e and "secretKeyRef" in e["valueFrom"]]


def test_a_token_is_never_older_than_its_life_allows():
    # Two runs an hour, so the Secret holds a token with at least 30
    # minutes left; the deadline of a run is far below that.
    minutes = [int(m) for m in cron()["spec"]["schedule"].split()[0].split(",")]
    gaps = [(b - a) % 60 for a, b in zip(minutes, minutes[1:] + minutes[:1])]
    assert max(gaps) <= 30
    assert cron()["spec"]["concurrencyPolicy"] == "Forbid"
    assert cron()["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] <= 600


def test_only_the_job_mounts_the_key_and_reads_it_as_a_file():
    spec = pod()
    (vol,) = [v for v in spec["volumes"] if "secret" in v]
    assert vol["secret"] == {"secretName": "main-github-app", "defaultMode": 0o440,
                             "items": [{"key": "private-key.pem", "path": "private-key.pem"}]}
    holders = [str(p.relative_to(ROOT)) for p in (ROOT / "kubernetes").rglob("*.yaml")
               if "main-github-app" in p.read_text() and p.parent != APP]
    assert holders == []
    (c,) = spec["containers"]
    assert c["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}}
    assert re.fullmatch(r"docker\.io/library/python:[0-9.]+-slim-trixie@sha256:[0-9a-f]{64}", c["image"])


def test_the_account_updates_the_one_secret_by_name_and_nothing_else():
    by = {(d["kind"], d["metadata"]["name"]): d for d in docs(APP / "rbac.yaml")}
    assert set(by) == {("ServiceAccount", "github-token"), ("Role", "github-token"), ("RoleBinding", "github-token")}
    assert by[("Role", "github-token")]["rules"] == [
        {"apiGroups": [""], "resources": ["secrets"], "resourceNames": ["github-mcp-app-token"], "verbs": ["update"]}]
    assert by[("RoleBinding", "github-token")]["subjects"] == [{"kind": "ServiceAccount", "name": "github-token"}]
    assert pod()["serviceAccountName"] == "github-token"
    # No other pod of the repository runs as that account.
    users = [str(p.relative_to(ROOT)) for p in (ROOT / "kubernetes").rglob("*.yaml")
             if re.search(r"serviceAccountName: github-token\b", p.read_text()) and p.parent != APP]
    assert users == []


def test_flux_makes_the_token_secret_once_and_the_job_owns_it():
    (s,) = docs(APP / "secret-token.yaml")
    assert s["metadata"]["name"] == "github-mcp-app-token"
    assert s["metadata"]["annotations"] == {"kustomize.toolkit.fluxcd.io/ssa": "IfNotPresent"}
    assert s["stringData"] == {"GITHUB_MCP_TOKEN": "pending"}
    # The job writes the same name, labels and annotation back.
    text = JOB.read_text()
    assert '"kustomize.toolkit.fluxcd.io/ssa": "IfNotPresent"' in text
    assert '"app.kubernetes.io/name": "github-token"' in text


def test_the_job_never_touches_the_stopgap_token_of_the_owner():
    # The stopgap of D143 is the Secret github-mcp-token of the code host
    # server. The write step does not start on it.
    for path in APP.rglob("*"):
        if path.is_file() and path.name != "secret.sops.yaml":
            assert not re.search(r"github-mcp-token\b", path.read_text()), path.name


def test_the_policy_lets_out_the_code_host_and_the_api_server_only():
    (p,) = docs(APP / "networkpolicy.yaml")
    spec = p["spec"]
    assert spec["endpointSelector"] == {"matchLabels": {"app.kubernetes.io/name": "github-token"}}
    assert "ingress" not in spec and "ingressDeny" not in spec
    fqdn, api, dns = spec["egress"]
    assert fqdn == {"toFQDNs": [{"matchName": "api.github.com"}],
                    "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]}
    assert api == {"toEntities": ["kube-apiserver"]}
    assert dns["toPorts"][0]["rules"] == {"dns": [{"matchName": "api.github.com"}]}
    labels = cron()["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
    assert labels == {"app.kubernetes.io/name": "github-token", "agents.home.arpa/dns": "own-rule"}


def test_the_folder_is_listed_only_with_its_key():
    resources = yaml.safe_load((MAIN / "kustomization.yaml").read_text())["resources"]
    listed = "./github-token/ks.yaml" in resources
    assert listed == KEY_SECRET.exists()
    assert "./secret.sops.yaml" in yaml.safe_load((APP / "kustomization.yaml").read_text())["resources"]


def test_the_key_secret_holds_ciphertext_only_with_the_recipients_of_the_namespace():
    if not KEY_SECRET.exists():
        pytest.skip("the owner has not run bin/main-app-key yet")
    (secret,) = docs(KEY_SECRET)
    assert secret["metadata"]["name"] == "main-github-app"
    assert list(secret["stringData"]) == ["private-key.pem"]
    # No value is printed in a failure message: the assert is on a bool.
    assert str(secret["stringData"]["private-key.pem"]).startswith("ENC[AES256_GCM,data:")
    assert secret["sops"]["encrypted_regex"] == "^(data|stringData)$"
    def recipients(path):
        return sorted(a["recipient"] for a in docs(path)[0]["sops"]["age"])
    other = MAIN / "github-mcp/app/secret.sops.yaml"
    assert recipients(KEY_SECRET) == recipients(other)


def test_the_folder_builds(tmp_path):
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        if os.environ.get("CI"):
            pytest.fail("no kubectl")
        pytest.skip("no kubectl here")
    copy = tmp_path / "app"
    shutil.copytree(APP, copy)
    if not (copy / "secret.sops.yaml").exists():
        (copy / "secret.sops.yaml").write_text(
            "apiVersion: v1\nkind: Secret\nmetadata:\n  name: main-github-app\nstringData:\n  private-key.pem: x\n")
    out = subprocess.run([kubectl, "kustomize", str(copy)], capture_output=True, text=True, check=True).stdout
    kinds = sorted((d["kind"], d["metadata"]["name"]) for d in yaml.safe_load_all(out) if d)
    assert [k for k in kinds if k[0] != "ConfigMap"] == sorted([
        ("CiliumNetworkPolicy", "github-token"), ("CronJob", "github-token"), ("Role", "github-token"),
        ("RoleBinding", "github-token"), ("Secret", "github-mcp-app-token"), ("Secret", "main-github-app"),
        ("ServiceAccount", "github-token")])
    (cm,) = [d for d in yaml.safe_load_all(out) if d and d["kind"] == "ConfigMap"]
    assert cm["metadata"]["name"].startswith("github-token-script-")
    assert cm["data"]["github_token.py"] == JOB.read_text()
