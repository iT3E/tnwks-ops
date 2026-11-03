"""Make LiteLLM's virtual keys match git: the declarative way to give an app
its own key. Runs as a sidecar in the LiteLLM pod (Reloader restarts the pod
when the keys Secret changes, so this runs again).

KEYS_FILE is a JSON list from a SOPS Secret:
  [{"alias": "transcriber", "key": "sk-...", "models": ["claude-sonnet"]}]

For each entry the key is created (POST /key/generate with that exact key
value) or, if it exists, updated (models, alias). Keys LiteLLM has that are
not in the file are left alone. Key values are never logged.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000").rstrip("/")
MASTER = os.environ["LITELLM_MASTER_KEY"]
KEYS_FILE = os.environ.get("KEYS_FILE", "/keys/keys.json")
RECHECK_SECONDS = int(os.environ.get("RECHECK_SECONDS", "21600"))


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        URL + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {MASTER}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def wait_ready() -> None:
    for _ in range(300):
        try:
            with urllib.request.urlopen(URL + "/health/readiness", timeout=5) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    sys.exit("litellm never became ready")


def ensure(entry: dict) -> str:
    key, alias, models = entry["key"], entry["alias"], entry.get("models", [])
    spec = {"key": key, "key_alias": alias, "models": models, "metadata": {"managed_by": "tnwks-ops"}}
    status, _ = call("GET", "/key/info?" + urllib.parse.urlencode({"key": key}))
    if status == 200:
        status, body = call("POST", "/key/update", spec)
        verb = "updated"
    else:
        status, body = call("POST", "/key/generate", spec)
        verb = "created"
    if status != 200:
        detail = str(body.get("error") or body.get("detail") or body)[:200]
        raise RuntimeError(f"{alias}: {verb[:-1]} failed ({status}): {detail}")
    return f"{alias}: {verb} (models: {', '.join(models) or 'all'})"


def run_once() -> bool:
    with open(KEYS_FILE) as f:
        entries = json.load(f)
    ok = True
    for entry in entries:
        try:
            print(ensure(entry), flush=True)
        except Exception as e:  # keep going: one bad entry shouldn't block the rest
            ok = False
            print(f"ERROR {e}", flush=True)
    return ok


if __name__ == "__main__":
    while True:
        wait_ready()
        ok = run_once()
        # Retry soon after a failure; otherwise re-check now and then (a
        # restored database, say).
        time.sleep(60 if not ok else RECHECK_SECONDS)
