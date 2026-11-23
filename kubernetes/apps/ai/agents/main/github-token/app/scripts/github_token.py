"""The token job of the App of the main agent (Design Doc of effort 0003,
section 4.2.6, "The credential of the code host server";
docs/apps/github-token.md).

Each run makes one installation token of the App, for the one repository
and the permissions of GITHUB_APP_PERMISSIONS, and writes it into the
Secret TOKEN_SECRET (key TOKEN_KEY) of this namespace, which the proxy of
the code host server reads. An installation token lives one hour at most.

- The private key of the App is a file that openssl reads to sign the
  JWT. This process never reads it.
- The token is held in this process and goes to the API server in the
  body of one request. It is never an argument, an env var, a file or a
  line of output.
- The job asks for each permission of GITHUB_APP_PERMISSIONS exactly.
  When the installation has more than GITHUB_APP_INSTALLED (the
  permissions of the App by the design), or more repositories, it logs
  one line with result=drift and goes on: the token stays narrow. When the
  installation has less than the token asks for, or the token that comes
  back is not exactly what was asked for, it fails.
- The account of the job may update the one Secret by its name, and
  nothing else (rbac.yaml). It writes with an update (PUT), so it never
  reads the Secret back. A PUT replaces the labels, so the body carries
  the two owner labels of Flux: a prune of the Kustomization still removes
  the Secret.
- One line of output for each run: "github-token result=ok ..." or
  "github-token result=failed step=<step> ...". No body of an answer is
  printed: an error of the code host can quote a request.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
# The code host refuses a JWT that lives longer than 10 minutes, and one
# whose start is in the future: the start is a minute back, for clock skew.
JWT_BACK = 60
JWT_LIFE = 540
# An installation token lives one hour. A token that would live longer is
# not one that this job asked for.
TOKEN_LIFE_MAX = 3600 + 60
LEVELS = {"read": 1, "write": 2, "admin": 3}
# The Kustomization of Flux that makes the Secret (../../ks.yaml): its owner
# labels, which a PUT must keep.
FLUX_OWNER = {"kustomize.toolkit.fluxcd.io/name": "agents-main-github-token",
              "kustomize.toolkit.fluxcd.io/namespace": "flux-system"}


class Failed(Exception):
    def __init__(self, step: str, detail: str = "") -> None:
        super().__init__(step)
        self.step = step
        self.detail = detail


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise Failed("config", f"missing={name}")
    return value


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt(app_id: str, key_file: str, now: int) -> str:
    """An RS256 JWT of the App. openssl signs it with the key file."""
    head = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iat": now - JWT_BACK, "exp": now + JWT_LIFE, "iss": app_id}
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{head}.{body}".encode()
    done = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_file], input=signing,
                          capture_output=True, timeout=30, check=False)
    if done.returncode != 0 or not done.stdout:
        # The error of openssl names a file, never the key.
        raise Failed("jwt", "sign=failed")
    return f"{head}.{body}.{b64url(done.stdout)}"


# The headers of each API, and only to that API: the API server answers 406
# to an Accept that it can not serve.
CODE_HOST = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
KUBERNETES = {"Accept": "application/json"}


def call(url: str, auth: str, method: str = "GET", body: dict | None = None,
         context: ssl.SSLContext | None = None, step: str = "", api: dict = CODE_HOST) -> dict:
    headers = {"Authorization": auth, "User-Agent": "tnwks-github-token", **api}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as answer:
            text = answer.read()
    except urllib.error.HTTPError as e:
        raise Failed(step, f"status={e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Failed(step, "status=unreachable") from None
    try:
        return json.loads(text or b"{}")
    except ValueError:
        raise Failed(step, "answer=not-json") from None


def wanted(name: str = "GITHUB_APP_PERMISSIONS") -> dict[str, str]:
    perms = {}
    for item in env(name).split(","):
        name, _, level = item.strip().partition(":")
        if level not in LEVELS or not name:
            raise Failed("config", "permissions=bad")
        perms[name] = level
    return perms


def short(perms: dict, want: dict) -> bool:
    return any(LEVELS.get(perms.get(k, ""), 0) < LEVELS[v] for k, v in want.items())


def more(perms: dict, want: dict) -> bool:
    return any(k not in want or LEVELS.get(v, 0) > LEVELS[want[k]] for k, v in perms.items())


def expires(value: str) -> int:
    try:
        return int(datetime.datetime.fromisoformat(value).timestamp())
    except (AttributeError, ValueError):
        raise Failed("token", "expires=bad") from None


def run(now: int | None = None) -> list[str]:
    now = int(time.time()) if now is None else now
    api = env("GITHUB_API", "https://api.github.com").rstrip("/")
    repository = env("GITHUB_REPOSITORY")
    owner, _, repo = repository.partition("/")
    if not owner or not repo or "/" in repo:
        raise Failed("config", "repository=bad")
    want = wanted()
    installed = wanted("GITHUB_APP_INSTALLED")
    if short(installed, want):
        raise Failed("config", "permissions=more-than-installed")
    drift = []

    app = f"Bearer {jwt(env('GITHUB_APP_ID'), env('GITHUB_APP_KEY_FILE'), now)}"
    install = call(f"{api}/repos/{owner}/{repo}/installation", app, step="installation")
    if not isinstance(install.get("id"), int):
        raise Failed("installation", "id=missing")
    have = install.get("permissions") or {}
    if short(have, want):
        raise Failed("installation", "permissions=less-than-asked")
    if more(have, installed):
        drift.append("permissions")
    if install.get("repository_selection") != "selected":
        drift.append("repositories")

    token = call(f"{api}/app/installations/{install['id']}/access_tokens", app, "POST",
                 {"repositories": [repo], "permissions": want}, step="token")
    value = token.get("token")
    if not isinstance(value, str) or not value:
        raise Failed("token", "token=missing")
    if (token.get("permissions") or {}) != want:
        raise Failed("token", "permissions=not-as-asked")
    names = [r.get("full_name", "").lower() for r in token.get("repositories") or []]
    if names != [repository.lower()]:
        raise Failed("token", "repositories=not-as-asked")
    if token.get("repository_selection") not in (None, "selected"):
        raise Failed("token", "repositories=not-as-asked")
    until = expires(token.get("expires_at", ""))
    if not now < until <= now + TOKEN_LIFE_MAX:
        raise Failed("token", "expires=out-of-range")

    write(value, env("TOKEN_SECRET"), env("TOKEN_KEY"), until)
    del value
    stamp = datetime.datetime.fromtimestamp(until, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # The result is letters only, so the aggregator of Vector counts the
    # line (MainGithubAppDrift).
    lines = [f"github-token result=drift what={x}" for x in drift]
    lines.append(f"github-token result=ok secret={env('TOKEN_SECRET')} expires={stamp}")
    return lines


def write(value: str, name: str, key: str, until: int) -> None:
    namespace = env("POD_NAMESPACE")
    host = env("KUBERNETES_SERVICE_HOST")
    port = env("KUBERNETES_SERVICE_PORT", "443")
    scheme = os.environ.get("KUBE_API_SCHEME", "https")
    sa = os.environ.get("KUBE_SA_DIR", SA)
    with open(f"{sa}/token") as f:
        bearer = f.read().strip()
    context = ssl.create_default_context(cafile=f"{sa}/ca.crt") if scheme == "https" else None
    stamp = datetime.datetime.fromtimestamp(until, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # The whole object, as secret-token.yaml has it, with no resourceVersion:
    # an update that needs no read first.
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/name": "github-token", **FLUX_OWNER},
            "annotations": {"kustomize.toolkit.fluxcd.io/ssa": "IfNotPresent",
                            "agents.home.arpa/expires": stamp},
        },
        "type": "Opaque",
        "data": {key: base64.b64encode(value.encode()).decode()},
    }
    url = f"{scheme}://{host}:{port}/api/v1/namespaces/{namespace}/secrets/{name}"
    call(url, f"Bearer {bearer}", "PUT", body, context=context, step="secret", api=KUBERNETES)


def main() -> int:
    try:
        lines = run()
    except Failed as e:
        print(" ".join(x for x in ("github-token result=failed", f"step={e.step}", e.detail) if x),
              flush=True)
        return 1
    for line in lines:
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
