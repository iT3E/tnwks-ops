"""Prove historical samples come from the live archive, with strict Store-only queries.

Read-only. No Secrets, pod exec, filesystem access inside a pod, or API writes.
An inverted Store filter must return nothing, proving that sidecars did not
satisfy the historical query. This does not establish a full SLO window.
"""

import argparse
import datetime
import json
import subprocess
import urllib.parse

SITES = ("homelab-wsl", "homelab-wsl-02")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--site", action="append", choices=SITES)
    args = parser.parse_args()
    expected = args.site or list(SITES)
    base = "/api/v1/namespaces/monitoring/services/thanos-query:10902/proxy"

    def get(path, params=None):
        if params:
            path += "?" + urllib.parse.urlencode(params)
        proc = subprocess.run(["kubectl", "--kubeconfig", args.kubeconfig, "get", "--raw", base + path], check=False,
                              capture_output=True, text=True, timeout=30)
        if proc.returncode:
            raise SystemExit("Archive verification transport failed; private upstream details suppressed")
        response = json.loads(proc.stdout)
        if response.get("status") != "success" or response.get("warnings"):
            raise SystemExit("Archive verification query failed or returned partial results")
        return response["data"]

    stores = get("/api/v1/stores").get("store", [])
    store = next((row for row in stores if row["name"] == "observability-store.monitoring.svc.cluster.local:10901"), None)
    if not store or store.get("lastError") or store.get("minTime", 0) >= store.get("maxTime", 0):
        raise SystemExit("Archive Store has no usable historical block window")
    query = 'count by (k8s_cluster) (max_over_time(prometheus_tsdb_head_series{k8s_cluster=~"' + "|".join(expected) + '"}[35d]))'
    params = {"query": query, "time": str((store["maxTime"] - 1) / 1000),
              "partial_response": "false", "timeout": "15s",
              "storeMatch[]": '{__address__="' + store["name"] + '"}'}
    found = get("/api/v1/query", params)
    observed = {sample["metric"].get("k8s_cluster") for sample in found["result"] if float(sample["value"][1]) > 0}
    missing = set(expected) - observed
    if missing:
        raise SystemExit("No archived historical samples yet for: " + ", ".join(sorted(missing)))
    params["storeMatch[]"] = '{__address__="archive-verification-no-such-store.invalid:10901"}'
    control = get("/api/v1/query", params)
    if control["result"]:
        raise SystemExit("Store filtering was not enforced; sidecars may have answered")

    def iso(milliseconds):
        return datetime.datetime.fromtimestamp(milliseconds / 1000, datetime.UTC).isoformat()

    print(json.dumps({"verified_sites": sorted(observed), "strict_store_only_query": True,
                      "negative_store_filter_control": "empty",
                      "archive_window_start": iso(store["minTime"]),
                      "archive_window_end": iso(store["maxTime"]),
                      "complete_28d_slo_window_proven": False}, indent=2))


if __name__ == "__main__":
    main()
