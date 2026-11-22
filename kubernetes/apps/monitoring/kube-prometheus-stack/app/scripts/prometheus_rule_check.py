"""The daily check of the network rule of Prometheus (networkpolicy.yaml).

The pod of this check has labels of its own, and the rule names it in the
block of the readers, next to Grafana. So it gets exactly the requests that
Grafana gets. It sends:

- GET /api/v1/query: a reader may query, so the answer must be 200.
- POST /api/v1/otlp/v1/metrics with an empty body: only Alloy may write, so
  the proxy of Cilium must answer 403. Without the rule, Prometheus would
  answer 200 and write nothing, because the body holds no data.
- POST /api/v1/write with an empty body: nobody may use the remote write
  path, so 403. Without the rule, Prometheus answers 404 (the path is off).

No request changes anything in Prometheus, with or without the rule. The
check prints one line for each request and one result line, and exits 1 on a
failure, so KubeJobFailed fires.

    prometheus-rule-check request=<name> status=<code> want=<code> result=pass|fail
    prometheus-rule-check result=pass|fail
"""

import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090")
REQUESTS = [
    ("query", "GET", "/api/v1/query?query=vector(1)", None, "application/x-www-form-urlencoded", 200),
    ("otlp-write", "POST", "/api/v1/otlp/v1/metrics", b"", "application/x-protobuf", 403),
    ("remote-write", "POST", "/api/v1/write", b"", "application/x-protobuf", 403),
]


def status(method: str, path: str, body: bytes | None, kind: str) -> int:
    request = urllib.request.Request(URL + path, data=body, method=method, headers={"Content-Type": kind})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (OSError, urllib.error.URLError):
        return 0


def main() -> int:
    ok = True
    for name, method, path, body, kind, want in REQUESTS:
        got = status(method, path, body, kind)
        passed = got == want
        ok = ok and passed
        print(f"prometheus-rule-check request={name} status={got} want={want} "
              f"result={'pass' if passed else 'fail'}", flush=True)
    print(f"prometheus-rule-check result={'pass' if ok else 'fail'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
