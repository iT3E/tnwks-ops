"""Read-only source-presence audit through Thanos' Kubernetes Service proxy.

Only bounded site identities and counts are retained; upstream labels, endpoint
names, response bodies and credentials are never included in error output.
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import subprocess
import urllib.parse
from pathlib import Path

from contracts import panel_contracts
from dashboards import CLUSTERS


def summarize(response):
    if response.get("status") != "success":
        return {"status": "query-error"}
    sites = {}
    for sample in response.get("data", {}).get("result", []):
        labels = sample.get("metric", {})
        site = labels.get("k8s_cluster", labels.get("cluster", ""))
        site = site if site in CLUSTERS else "unlabeled"
        sites[site] = sites.get(site, 0) + int(float(sample["value"][1]))
    return {"status": "partial" if response.get("warnings") else "success", "series_by_site": sites}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    contracts = panel_contracts()
    sources = {source["metric"]: source for panel in contracts for source in panel["sources"]}

    def inspect(name):
        # ALERTS has the external observer site, normalized by the dashboard's
        # alert query; all other sources use authoritative k8s_cluster.
        expression = f"count by (k8s_cluster, cluster) ({name})"
        endpoint = "/api/v1/namespaces/monitoring/services/thanos-query:10902/proxy/api/v1/query?"
        endpoint += urllib.parse.urlencode({"query": expression, "timeout": "10s"})
        row = dict(sources[name])
        try:
            process = subprocess.run(["kubectl", "--kubeconfig", str(args.kubeconfig), "get", "--raw", endpoint], check=False,
                                     capture_output=True, text=True, timeout=20)
            row.update(summarize(json.loads(process.stdout)) if process.returncode == 0 else {"status": "transport-error"})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            row["status"] = "transport-error"
        return row

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(inspect, sorted(sources)))
    encoded = json.dumps(contracts, sort_keys=True).encode()
    result = {"observed_at": datetime.datetime.now(datetime.UTC).isoformat(),
              "contract_sha256": hashlib.sha256(encoded).hexdigest(),
              "expected_sites": list(CLUSTERS), "sources": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Audited {len(rows)} source metrics for {len(contracts)} panels; report: {args.output}")
    for row in rows:
        if row["status"] != "success" or not row.get("series_by_site"):
            print(row["metric"], row["availability"], row["status"], "no evidence" if not row.get("series_by_site") else "partial evidence")
    if any(row["status"] != "success" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
