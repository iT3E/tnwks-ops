"""Read-only, bounded Kubernetes evidence exporter; never reads Secrets or mutates APIs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def quote(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    def __init__(self):
        self.lines: list[str] = []

    def add(self, metric_name: str, value: float | int | None, **labels):
        if value is None or not math.isfinite(float(value)):
            return
        suffix = ",".join(f'{key}="{quote(val)}"' for key, val in sorted(labels.items()))
        self.lines.append(f"{metric_name}{{{suffix}}} {float(value):.12g}")

    def render(self) -> bytes:
        return ("\n".join(self.lines) + "\n").encode()


class Kubernetes:
    def __init__(self, endpoint: str | None = None):
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.endpoint = endpoint or f"https://{host}:{port}"
        base = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.token_file = base / "token"
        self.context = ssl.create_default_context(cafile=str(base / "ca.crt"))
        self.deadline = math.inf

    def begin(self):
        self.deadline = time.monotonic() + 10

    def get(self, path: str) -> dict:
        request = urllib.request.Request(self.endpoint + path, headers={
            "Authorization": "Bearer " + self.token_file.read_text().strip(),
            "Accept": "application/json",
        })
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("evidence collection deadline exceeded")
        with urllib.request.urlopen(request, timeout=min(3, remaining), context=self.context) as response:
            # Response size is bounded; a scale change must be reviewed rather than silently
            # consume unbounded memory or emit private API response contents into logs.
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Kubernetes response exceeds the configured evidence budget")
            return json.loads(raw)


class Collector:
    def __init__(self, api, inventory: dict, cluster: str, clock=time.time):
        self.api, self.inventory, self.cluster, self.clock = api, inventory, cluster, clock
        self.last_success: dict[str, float] = {}
        self.lock = threading.Lock()

    def expected_databases(self):
        if "databases" in self.inventory:
            return self.inventory["databases"]
        return [{"name": db, "app": app["name"], "namespace": "databases", "cluster": cluster}
                for app in self.inventory["apps"] for cluster in app["clusters"]
                for db in app.get("databases", [])]

    def collect(self) -> bytes:
        with self.lock:
            return self._collect()

    def _collect(self) -> bytes:
        metrics = Metrics()
        if hasattr(self.api, "begin"):
            self.api.begin()
        now = self.clock()
        labels = {"observer_cluster": self.cluster, "k8s_cluster": self.cluster}
        metrics.add("homelab_observer_info", 1, **labels)
        for site in self.inventory["sites"]:
            metrics.add("homelab_expected_site_info", 1, k8s_cluster=site["cluster"],
                        site=site["id"], observer_cluster=self.cluster)
        for app in self.inventory["apps"]:
            for cluster in app["clusters"]:
                for workload in app["workloads"]:
                    kind, name = workload.split("/", 1)
                    metrics.add("homelab_expected_workload_info", 1, app=app["name"],
                                namespace=app["namespace"], k8s_cluster=cluster,
                                kind=kind, workload=name, tier=app["tier"],
                                observer_cluster=self.cluster)
                # This exporter cannot certify a database fence or an application restore
                # from a Ready workload. Unsupported evidence is deliberately absent.

        for database in self.expected_databases():
            metrics.add("homelab_expected_database_info", 1, app=database["app"],
                        namespace=database["namespace"], db_cluster=database["name"],
                        k8s_cluster=database["cluster"], observer_cluster=self.cluster)

        sections = {
            "workloads": self.workloads,
            "flux": self.flux,
            "overrides": self.overrides,
            "databases": self.databases,
        }
        successes = []
        for name, function in sections.items():
            section = Metrics()
            try:
                function(section, labels)
            except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
                # Do not print the response body, token, resource contents or error URL.
                logging.warning("evidence collection failed: %s", name)
                successes.append(False)
                metrics.add("homelab_collection_success", 0, collector=name, **labels)
            else:
                successes.append(True)
                self.last_success[name] = now
                metrics.lines.extend(section.lines)
                metrics.add("homelab_collection_success", 1, collector=name, **labels)
            metrics.add("homelab_collection_last_success_timestamp_seconds",
                        self.last_success.get(name), collector=name, **labels)
        if all(successes):
            self.last_success["all"] = now
        metrics.add("homelab_observer_last_success_timestamp_seconds",
                    self.last_success.get("all"), **labels)
        return metrics.render()

    def workloads(self, metrics: Metrics, labels: dict):
        by_namespace: dict[str, list] = {}
        for app in self.inventory["apps"]:
            if self.cluster in app["clusters"]:
                by_namespace.setdefault(app["namespace"], []).append(app)
        for namespace, apps in by_namespace.items():
            for kind, plural in [("deployment", "deployments"), ("statefulset", "statefulsets")]:
                response = self.api.get(f"/apis/apps/v1/namespaces/{namespace}/{plural}")
                observed = {obj["metadata"]["name"]: obj for obj in response.get("items", [])}
                for app in apps:
                    for workload in app["workloads"]:
                        expected_kind, name = workload.split("/", 1)
                        if kind != expected_kind:
                            continue
                        scope = dict(labels, namespace=namespace, app=app["name"],
                                     workload=name, kind=kind)
                        obj = observed.get(name)
                        metrics.add("homelab_workload_present", int(obj is not None), **scope)
                        if obj is None:
                            continue
                        # Kubernetes defaults replicas to one, but only for an observed
                        # workload object; a missing object is not an implicit replica.
                        desired = obj.get("spec", {}).get("replicas", 1)
                        ready = obj.get("status", {}).get("readyReplicas", 0)
                        metrics.add("homelab_workload_desired_replicas", desired, **scope)
                        metrics.add("homelab_workload_ready_replicas", ready, **scope)
                        for condition in obj.get("status", {}).get("conditions", []):
                            if condition.get("type") == "Available":
                                metrics.add("homelab_workload_transition_timestamp_seconds",
                                            timestamp(condition.get("lastTransitionTime")), **scope)

    def flux(self, metrics: Metrics, labels: dict):
        for group, version, plural, kind in [
            ("kustomize.toolkit.fluxcd.io", "v1", "kustomizations", "Kustomization"),
            ("helm.toolkit.fluxcd.io", "v2", "helmreleases", "HelmRelease"),
            ("source.toolkit.fluxcd.io", "v1", "gitrepositories", "GitRepository"),
        ]:
            response = self.api.get(f"/apis/{group}/{version}/{plural}")
            for obj in response.get("items", []):
                metadata = obj["metadata"]
                scope = dict(labels, namespace=metadata.get("namespace", "default"),
                             resource=metadata["name"], kind=kind)
                metrics.add("homelab_flux_resource_suspended",
                            int(obj.get("spec", {}).get("suspend", False)), **scope)
                # Absence/Unknown condition is not silently mapped to Ready=False;
                # it retains a distinct observed-state code.
                ready = next((x for x in obj.get("status", {}).get("conditions", [])
                              if x.get("type") == "Ready"), {})
                status = ready.get("status", "Unknown")
                if status in ("True", "False"):
                    metrics.add("homelab_flux_resource_ready", int(status == "True"), **scope)
                metrics.add("homelab_flux_resource_state", {"True": 1, "False": 3}.get(status, 0),
                            **scope)

    def overrides(self, metrics: Metrics, labels: dict):
        # List ConfigMaps rather than fetching the named map: absence is an ordinary
        # result, whereas access/server failures must remain collection failures.
        response = self.api.get("/api/v1/namespaces/flux-system/configmaps?fieldSelector=metadata.name%3Dsite-overrides")
        found = response.get("items", [])
        metrics.add("homelab_site_override_present", int(bool(found)), **labels)
        if found:
            obj = found[0]
            metrics.add("homelab_site_override_created_timestamp_seconds",
                        timestamp(obj["metadata"].get("creationTimestamp")), **labels)
            # Only a bounded count of known placement keys; never raw ConfigMap data.
            known = {app["prefix"] + "_REPLICAS" for app in self.inventory["apps"]}
            metrics.add("homelab_site_override_placement_keys", len(known & obj.get("data", {}).keys()),
                        **labels)

    def databases(self, metrics: Metrics, labels: dict):
        expected = {db["name"] for db in self.expected_databases() if db["cluster"] == self.cluster}
        response = self.api.get("/apis/postgresql.cnpg.io/v1/namespaces/databases/clusters")
        for obj in response.get("items", []):
            name = obj["metadata"]["name"]
            if name not in expected:
                continue
            scope = dict(labels, namespace="databases", db_cluster=name)
            metrics.add("homelab_database_instances", obj.get("spec", {}).get("instances", 1), **scope)
            metrics.add("homelab_database_ready_instances", obj.get("status", {}).get("readyInstances", 0),
                        **scope)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


# Fixed endpoints. Any HTTP answer proves the path, so the archive's 403 for an
# anonymous request counts: this is reachability, not bucket or application health.
PATHS = {
    "archive": "https://observability-archive.monitoring.svc.cluster.local/",
    "internet": "http://1.1.1.1/",
}
CA_FILE = "/tls/ca.crt"


def path_probe(url: str, timeout: float = 3) -> tuple[bool, float]:
    start = time.monotonic()
    handlers = [NoRedirect()]
    if url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=CA_FILE)))
    try:
        urllib.request.build_opener(*handlers).open(url, timeout=timeout).close()
        ok = True
    except urllib.error.HTTPError:
        ok = True
    except (OSError, urllib.error.URLError):
        ok = False
    return ok, time.monotonic() - start


def peer_probe(url: str, timeout: float = 3) -> tuple[bool, float]:
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            # Fixed inventory endpoints only. No user-provided target or arbitrary
            # body extraction; this measures a transport/health endpoint, not app UX.
            ok = 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        ok = False
    return ok, time.monotonic() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", default="/config/inventory.json")
    parser.add_argument("--cluster", default=os.environ.get("CLUSTER_NAME"))
    parser.add_argument("--port", type=int, default=9876)
    args = parser.parse_args()
    inventory = json.loads(Path(args.inventory).read_text())
    if args.cluster not in {site["cluster"] for site in inventory["sites"]}:
        parser.error("cluster must be an entry in the declared inventory")
    collector = Collector(Kubernetes(), inventory, args.cluster)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                body = b"ok\n"
            elif self.path == "/metrics":
                body = collector.collect()
                peers = Metrics()
                for site in inventory["sites"]:
                    if site["cluster"] == args.cluster:
                        continue
                    ok, duration = peer_probe(site["probe_url"])
                    labels = {"observer_cluster": args.cluster, "k8s_cluster": site["cluster"]}
                    peers.add("homelab_peer_probe_success", int(ok), **labels)
                    peers.add("homelab_peer_probe_duration_seconds", duration, **labels)
                for target, url in PATHS.items():
                    ok, duration = path_probe(url)
                    labels = {"observer_cluster": args.cluster, "k8s_cluster": args.cluster,
                              "observability_target": target}
                    peers.add("probe_success", int(ok), **labels)
                    peers.add("probe_duration_seconds", duration, **labels)
                body += peers.render()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
