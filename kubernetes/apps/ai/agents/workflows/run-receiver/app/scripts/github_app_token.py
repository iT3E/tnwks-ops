"""An installation token of the GitHub App of the Renovate reviewer.

scripts/providers.sh runs it and reads the token from standard output; the
token goes on to the provider github-reviewer through an env var. Nothing
else goes to standard output, and no key or token goes to the log.

The repository and the permissions come from git, in one place: the env
GITHUB_REPOSITORY and GITHUB_APP_PERMISSIONS of the CronJob
workflow-providers. A token never has more than that list, whatever the App
was granted.

1. A JWT of the App: RS256, signed by openssl with the private key file,
   issued 60 s in the past (clock drift) and valid 9 minutes.
2. The drift check: the permissions of the App (GET /app), and the
   permissions and the repository selection of its installation on the
   repository, against the list in git. One log line:
       app-drift result=ok|drift detail=<names only>
   More than the list (or an installation on all repositories) is drift: a
   log line for an alert, and the token is still scoped to the list. Less
   than the list fails the run, before any token.
3. An installation token for that one repository, with the permissions of
   the list. The answer must grant exactly those (and metadata read, which
   every token has), or the run fails.

Environment:
    GITHUB_APP_ID           the App ID (or its client ID)
    GITHUB_APP_KEY_FILE     the private key (PEM). Default /github-app/private-key.pem
    GITHUB_REPOSITORY       owner/name
    GITHUB_APP_PERMISSIONS  name:level, comma separated (level read or write)
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

JWT_LIFETIME = 540
CLOCK_DRIFT = 60
LEVELS = {"read": 1, "write": 2, "admin": 3}
IMPLIED = {"metadata": "read"}  # every App and every token has it


class TokenError(RuntimeError):
    pass


def parse_permissions(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, level = item.partition(":")
        name, level = name.strip(), level.strip()
        if not sep or not re.fullmatch(r"[a-z_]+", name) or level not in ("read", "write"):
            raise TokenError(f"GITHUB_APP_PERMISSIONS: {item!r} is not name:read or name:write")
        out[name] = level
    if not out:
        raise TokenError("GITHUB_APP_PERMISSIONS is empty")
    return out


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


def compare(where: str, granted: dict, wanted: dict[str, str]) -> tuple[list[str], list[str]]:
    """The names over the list (extra) and under it (missing), as 'where:name'."""
    extra, missing = [], []
    for name, level in sorted((granted or {}).items()):
        if IMPLIED.get(name) == level:
            continue
        if name not in wanted or LEVELS.get(level, 9) > LEVELS[wanted[name]]:
            extra.append(f"{where}:{name}")
    for name, level in sorted(wanted.items()):
        if LEVELS.get((granted or {}).get(name), 0) < LEVELS[level]:
            missing.append(f"{where}:{name}")
    return extra, missing


def drift_check(app: dict, installation: dict, wanted: dict[str, str]) -> list[str]:
    """Log the one drift line; return the names under the list (empty when the mint may go on)."""
    extra, missing = [], []
    for where, granted in (("app", app.get("permissions")), ("installation", installation.get("permissions"))):
        e, m = compare(where, granted, wanted)
        extra += e
        missing += m
    if installation.get("repository_selection") != "selected":
        extra.append("installation:repository_selection")
    detail = ",".join([f"extra:{n}" for n in extra] + [f"missing:{n}" for n in missing]) or "none"
    print(f"app-drift result={'drift' if extra or missing else 'ok'} detail={detail}", file=sys.stderr)
    return missing


def installation_token(api: str, app_id: str, key_file: str, repository: str, wanted: dict, now: int) -> dict:
    owner, name = repository.split("/", 1)
    jwt = app_jwt(app_id, key_file, now)
    app = call(api, "GET", "/app", jwt)
    installation = call(api, "GET", f"/repos/{owner}/{name}/installation", jwt)
    installation_id = installation.get("id")
    if not isinstance(installation_id, int):
        raise TokenError(f"no installation of the App on {repository}")
    missing = drift_check(app, installation, wanted)
    if missing:
        raise TokenError(
            f"the App has less than git asks ({', '.join(missing)}): no token. "
            "Grant it in the App settings (and accept it on the installation), or change the list in git"
        )
    answer = call(
        api,
        "POST",
        f"/app/installations/{installation_id}/access_tokens",
        jwt,
        {"repositories": [name], "permissions": wanted},
    )
    token = answer.get("token")
    if not isinstance(token, str) or not token:
        raise TokenError("the answer has no token")
    extra, short = compare("token", answer.get("permissions"), wanted)
    if extra or short:
        raise TokenError(f"the token does not have exactly the permissions of git ({', '.join(extra + short)})")
    repos = [r.get("full_name", "").lower() for r in answer.get("repositories") or []]
    if repos != [repository.lower()]:
        raise TokenError(f"the token is for {repos}, not [{repository}]")
    return {"token": token, "installation_id": installation_id, "expires_at": answer.get("expires_at")}


def main() -> int:
    app_id = os.environ.get("GITHUB_APP_ID", "").strip()
    if not re.fullmatch(r"[0-9]+|Iv[0-9A-Za-z.]+", app_id):
        print("github app token: GITHUB_APP_ID is not an App ID or a client ID", file=sys.stderr)
        return 2
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repository):
        print("github app token: GITHUB_REPOSITORY is not owner/name", file=sys.stderr)
        return 2
    try:
        wanted = parse_permissions(os.environ.get("GITHUB_APP_PERMISSIONS", ""))
    except TokenError as e:
        print(f"github app token: {e}", file=sys.stderr)
        return 2
    key_file = os.environ.get("GITHUB_APP_KEY_FILE", "/github-app/private-key.pem")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    try:
        out = installation_token(api, app_id, key_file, repository, wanted, int(time.time()))
    except (TokenError, OSError, subprocess.SubprocessError) as e:
        print(f"github app token: {e}", file=sys.stderr)
        return 1
    print(
        f"github app token: installation {out['installation_id']}, {repository} only, "
        f"{','.join(f'{k}:{v}' for k, v in wanted.items())}, expires {out['expires_at']}",
        file=sys.stderr,
    )
    sys.stdout.write(out["token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
