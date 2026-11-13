"""An installation token of the GitHub App of the Renovate reviewer.

scripts/providers.sh runs it and reads the token from standard output; the
token goes on to the provider github-reviewer through an env var. Nothing
else goes to standard output, and no key or token goes to the log.

1. A JWT of the App: RS256, signed by openssl with the private key file,
   issued 60 s in the past (clock drift) and valid 9 minutes.
2. The installation of the App on the repository.
3. An installation token for that one repository, with the permissions of
   the provider profile and no more. The answer must grant exactly those
   (and metadata read, which every token has), or the run fails.

Environment:
    GITHUB_APP_ID           the App ID (or its client ID)
    GITHUB_APP_KEY_FILE     the private key (PEM). Default /github-app/private-key.pem
    GITHUB_REPOSITORY       owner/name. Default iT3E/tnwks-ops
    GITHUB_API_URL          default https://api.github.com
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The permissions of the profile workflow-github-reviewer: contents and the
# check runs to read, and pull requests to read and to comment on (POST
# issues/*/comments on a pull request needs pull_requests write).
PERMISSIONS = {"contents": "read", "pull_requests": "write", "checks": "read"}
JWT_LIFETIME = 540
CLOCK_DRIFT = 60


class TokenError(RuntimeError):
    pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: str, key_file: str, now: int) -> str:
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iat": now - CLOCK_DRIFT, "exp": now + JWT_LIFETIME, "iss": app_id}
    signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
    done = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_file],
        input=signing_input.encode(),
        capture_output=True,
        timeout=30,
    )
    if done.returncode != 0 or not done.stdout:
        raise TokenError(f"openssl could not sign the JWT (exit {done.returncode})")
    return f"{signing_input}.{b64url(done.stdout)}"


def call(api: str, method: str, path: str, jwt: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        api.rstrip("/") + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {jwt}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tnwks-workflow-providers",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            message = str(json.loads(e.read() or b"{}").get("message", ""))[:200]
        except ValueError:
            message = ""
        raise TokenError(f"{method} {path}: {e.code} {message}".rstrip()) from None


def installation_token(api: str, app_id: str, key_file: str, repository: str, now: int) -> dict:
    owner, name = repository.split("/", 1)
    jwt = app_jwt(app_id, key_file, now)
    installation = call(api, "GET", f"/repos/{owner}/{name}/installation", jwt)
    installation_id = installation.get("id")
    if not isinstance(installation_id, int):
        raise TokenError(f"no installation of the App on {repository}")
    answer = call(
        api,
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        jwt,
        {"repositories": [name], "permissions": PERMISSIONS},
    )
    token = answer.get("token")
    if not isinstance(token, str) or not token:
        raise TokenError("the answer has no token")
    granted = answer.get("permissions") or {}
    extra = {k: v for k, v in granted.items() if k not in PERMISSIONS and (k, v) != ("metadata", "read")}
    if any(granted.get(k) != v for k, v in PERMISSIONS.items()) or extra:
        raise TokenError(f"the token has the permissions {granted}, not {PERMISSIONS}")
    repos = [r.get("full_name", "").lower() for r in answer.get("repositories") or []]
    if repos != [repository.lower()]:
        raise TokenError(f"the token is for {repos}, not [{repository}]")
    return {"token": token, "installation_id": installation_id, "expires_at": answer.get("expires_at")}


def main() -> int:
    app_id = os.environ.get("GITHUB_APP_ID", "").strip()
    if not re.fullmatch(r"[0-9]+|Iv[0-9A-Za-z.]+", app_id):
        print("GITHUB_APP_ID is not an App ID or a client ID", file=sys.stderr)
        return 2
    key_file = os.environ.get("GITHUB_APP_KEY_FILE", "/github-app/private-key.pem")
    repository = os.environ.get("GITHUB_REPOSITORY", "iT3E/tnwks-ops")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    try:
        out = installation_token(api, app_id, key_file, repository, int(time.time()))
    except (TokenError, OSError, subprocess.SubprocessError) as e:
        print(f"github app token: {e}", file=sys.stderr)
        return 1
    print(
        f"github app token: installation {out['installation_id']}, {repository} only, "
        f"{','.join(f'{k}:{v}' for k, v in PERMISSIONS.items())}, expires {out['expires_at']}",
        file=sys.stderr,
    )
    sys.stdout.write(out["token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
