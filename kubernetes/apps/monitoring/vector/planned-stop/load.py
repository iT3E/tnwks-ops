"""The marked requests of a planned stop of the audit sink.

The API server keeps each request on a Secret in its audit log, also a read
that RBAC refuses (talos/README.md, "The audit policy"). This script asks for
the Secret MARKER, which its account may not read, at RATE requests a second.
Each request names its cluster and its number in its User-Agent, so the
lines of the stop can be counted in Loki afterwards
(../README.md, "A planned stop of a security sink").

It stops at the first of these, always with exit code 0, so that a stop of
the load does not raise the alert of a failed Job:

- it has sent RATE * DURATION_SECONDS requests (result=done);
- DURATION_SECONDS + GRACE_SECONDS have passed (result=time);
- TROUBLE_LIMIT answers in a row were neither 403 nor 404, or no answer
  (result=api-trouble): the API server is not well, and the load stops.

It never sends faster than RATE: a request that is late goes at once, and
the ones after it keep their own times. Each LOG_SECONDS it writes one line
with its counts. The token of its account is read again each minute,
because the kubelet renews it.
"""

import http.client
import os
import ssl
import sys
import time
import urllib.parse

ACCOUNT = "/var/run/secrets/kubernetes.io/serviceaccount"
EXPECTED = (403, 404)


def setting(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        sys.exit(f"planned-stop-load: {name} is not set")
    return value


def connection(api, timeout):
    url = urllib.parse.urlsplit(api)
    if url.scheme == "https":
        context = ssl.create_default_context(cafile=os.path.join(ACCOUNT, "ca.crt"))
        return http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=timeout, context=context)
    return http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)


def read_token(path):
    with open(path, encoding="ascii") as file:
        return file.read().strip()


def log(**fields):
    print("planned-stop-load " + " ".join(f"{key}={value}" for key, value in fields.items()), flush=True)


def main():
    cluster = setting("CLUSTER")
    rate = float(setting("RATE", "12"))
    duration = float(setting("DURATION_SECONDS", "10800"))
    grace = float(setting("GRACE_SECONDS", "300"))
    trouble_limit = int(setting("TROUBLE_LIMIT", "30"))
    log_every = float(setting("LOG_SECONDS", "600"))
    namespace = setting("NAMESPACE", "monitoring")
    marker = setting("MARKER", "vector-planned-stop-marker")
    token_file = setting("TOKEN_FILE", os.path.join(ACCOUNT, "token"))
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    api = setting("API", f"https://{host}:{port}")
    timeout = float(setting("TIMEOUT_SECONDS", "10"))

    total = int(rate * duration)
    path = f"/api/v1/namespaces/{namespace}/secrets/{marker}"
    counts = {"sent": 0, "expected": 0, "other": 0, "no_answer": 0, "late": 0}
    trouble = 0
    token, token_read = read_token(token_file), time.monotonic()
    conn = connection(api, timeout)
    log(result="start", cluster=cluster, rate=rate, requests=total, path=path)
    start = time.monotonic()
    next_log = start + log_every
    result = "done"
    for number in range(total):
        due = start + number / rate
        now = time.monotonic()
        if now > start + duration + grace:
            result = "time"
            break
        if now < due:
            time.sleep(due - now)
        elif now - due > 1:
            counts["late"] += 1
        if time.monotonic() - token_read > 60:
            token, token_read = read_token(token_file), time.monotonic()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"vector-planned-stop/{cluster}/{number:06d}",
        }
        try:
            conn.request("GET", path, headers=headers)
            answer = conn.getresponse()
            answer.read()
            status = answer.status
        except (OSError, http.client.HTTPException):
            conn.close()
            conn = connection(api, timeout)
            status = None
        counts["sent"] += 1
        if status in EXPECTED:
            counts["expected"] += 1
            trouble = 0
        else:
            counts["no_answer" if status is None else "other"] += 1
            trouble += 1
            if trouble >= trouble_limit:
                result = "api-trouble"
                break
        if time.monotonic() >= next_log:
            log(result="running", last=number, **counts)
            next_log += log_every
    conn.close()
    log(result=result, seconds=round(time.monotonic() - start), **counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
