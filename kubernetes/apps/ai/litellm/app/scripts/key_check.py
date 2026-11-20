"""The key of the metrics scrape calls no model (docs/apps/litellm.md, "Metrics").

The CronJob litellm-key-check (key-check.yaml) runs this once a day. It reads
each model name of the gateway config (each `model_name` of `model_list` and
each name of `router_settings.model_group_alias`), adds one name that is no
model, and calls each one with the key of the scrape and a prompt of one fixed
word. The gateway must refuse each call with 403: a key that may not call that
name. Any other answer fails the Job:

  200       the key can call that name;
  401       the gateway does not know the key (no entry in keys.json, or the
            Secret litellm-metrics-scrape holds another value);
  other     the gateway did not answer as planned.

KubeJobFailed fires for a failed Job, and the AI board shows the CronJob on
its Checks tab. One JSON line for each name. The key and the body of an
answer are never printed. Standard library only.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

URL = os.environ.get("LITELLM_URL", "http://litellm-app.ai.svc.cluster.local:4000").rstrip("/")
CONFIG_FILE = os.environ.get("CONFIG_FILE", "/config/config.yaml")
KEY_FILE = os.environ.get("KEY_FILE", "/key/token")
# The one word of each call. A call that the gateway lets through costs one
# output token at most (max_tokens).
WORD = "ping"
# A name that is no model: the pinned model list of the key.
NOT_A_MODEL = "no-default-models"
REFUSED = 403


def model_names(text: str) -> list[str]:
    """Each model name of the config: the `model_name` entries and the names of
    `model_group_alias`. The config is the one of configmap.yaml, a known form,
    so a line parse is enough and the image needs no YAML library."""
    names = re.findall(r"(?m)^\s*- model_name:\s*(\S+)\s*$", text)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        start = re.match(r"^(\s*)model_group_alias:\s*$", line)
        if not start:
            continue
        indent = len(start.group(1))
        for item in lines[index + 1:]:
            if not item.strip() or item.strip().startswith("#"):
                continue
            if len(item) - len(item.lstrip()) <= indent:
                break
            alias = re.match(r"^\s*([^\s:#]+):\s*\S", item)
            if alias:
                names.append(alias.group(1))
    return sorted(set(names))


def call(name: str, key: str) -> int | str:
    body = json.dumps({"model": name, "messages": [{"role": "user", "content": WORD}],
                       "max_tokens": 1}).encode()
    request = urllib.request.Request(URL + "/v1/chat/completions", data=body, method="POST",
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            answer.read()
            return answer.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code
    except (urllib.error.URLError, OSError) as error:
        return type(error).__name__


def main() -> int:
    with open(KEY_FILE) as f:
        key = f.read().strip()
    with open(CONFIG_FILE) as f:
        names = model_names(f.read())
    failures = 0
    if not key or len(names) < 2:
        print(json.dumps({"check": "model-gateway-key", "result": "failed",
                          "detail": "no key" if not key else "no model names in the config"}), flush=True)
        return 1
    for name in [*names, NOT_A_MODEL]:
        got = call(name, key)
        ok = got == REFUSED
        failures += not ok
        print(json.dumps({"check": "model-gateway-key", "name": name, "expected": REFUSED, "got": got,
                          "result": "refused" if ok else "failed"}), flush=True)
    print(json.dumps({"check": "model-gateway-key", "names": len(names) + 1, "failed": failures,
                      "result": "ok" if not failures else "failed"}), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
