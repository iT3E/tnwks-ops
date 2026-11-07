"""Read-only, bounded Kubernetes evidence exporter; never reads Secrets or mutates APIs."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import random
import socket
import ssl
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
        # Desired replicas per app on this site, from the last workloads pass.
        self.desired: dict[str, int] = {}
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
            "services": self.services,
            "recovery": self.recovery,
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
        self.desired = {}
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
                        self.desired[app["name"]] = self.desired.get(app["name"], 0) + desired
                        metrics.add("homelab_workload_ready_replicas", ready, **scope)
                        for condition in obj.get("status", {}).get("conditions", []):
                            if condition.get("type") == "Available":
                                metrics.add("homelab_workload_transition_timestamp_seconds",
                                            timestamp(condition.get("lastTransitionTime")), **scope)

    def services(self, metrics: Metrics, labels: dict):
        """Content probes, only for the apps this site runs: a standby has nothing to
        answer, and the rules show it as Standby from its replica count."""
        apps = [app for app in self.inventory["apps"]
                if app.get("probe") and self.desired.get(app["name"], 0) > 0]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda app: content_probe(**app["probe"]), apps))
        for app, (ok, duration) in zip(apps, results):
            scope = dict(labels, app=app["name"], observability_target="service")
            metrics.add("probe_success", int(ok), **scope)
            metrics.add("probe_duration_seconds", duration, **scope)

    def recovery(self, metrics: Metrics, labels: dict):
        """Per warm app, whether this site could take it over: 1 met, 3 not met; a
        prerequisite with no evidence is left out, so the rules keep it Unknown.

        database: every one of its Postgres clusters here is healthy (primary or replica).
        backup: each has a completed base backup here within 30 h, never in the future.
        restore: a completed restore of each required PVC here within 8 days. A
          running app is not a restore drill; apps without copied PVCs need no drill.
        fence: a lease naming this site blocks promotion. A successful fleet fence
          Job cannot attest that it read this app's lease or fenced a former primary.
        """
        now = self.clock()
        warm = [app for app in self.inventory["apps"] if app.get("tier") == "warm"]
        if not warm:
            return
        clusters = {obj["metadata"]["name"]: obj for obj in
                    self.api.get("/apis/postgresql.cnpg.io/v1/namespaces/databases/clusters").get("items", [])}
        backups: dict[str, float] = {}
        for obj in self.api.get("/apis/postgresql.cnpg.io/v1/namespaces/databases/backups").get("items", []):
            status = obj.get("status", {})
            done = timestamp(status.get("stoppedAt"))
            if status.get("phase") == "completed" and done:
                name = obj.get("spec", {}).get("cluster", {}).get("name")
                backups[name] = max(backups.get(name, 0), done)
        fence_job = next((obj for obj in self.api.get("/apis/batch/v1/namespaces/databases/cronjobs").get("items", [])
                          if obj["metadata"]["name"] == "site-lease-fence"), None)
        fence_ok = timestamp((fence_job or {}).get("status", {}).get("lastSuccessfulTime"))
        for app in warm:
            scope = dict(labels, app=app["name"])

            def state(prerequisite, value):
                metrics.add("homelab_recovery_prerequisite_state", value, prerequisite=prerequisite, **scope)

            dbs = [clusters.get(name) for name in app.get("databases", [])]
            state("database", 1 if dbs and all(
                db and db.get("status", {}).get("phase") == "Cluster in healthy state"
                and db.get("status", {}).get("readyInstances", 0) >= db.get("spec", {}).get("instances", 1)
                for db in dbs) else 3)

            ages = [now - backups[name] for name in app.get("databases", []) if name in backups]
            if app.get("databases") and len(ages) == len(app["databases"]):
                state("backup", 1 if all(0 <= age <= 30 * 3600 for age in ages) else 3)

            if not app.get("pvcs"):
                state("restore", 1)
            else:
                drills = self.api.get(
                    f"/apis/kopiur.home-operations.com/v1alpha1/namespaces/{app['namespace']}/restores"
                    "?labelSelector=app.kubernetes.io%2Fname%3Dstandby-restore").get("items", [])
                newest = {}
                for obj in drills:
                    pvc = obj["metadata"].get("labels", {}).get("tnwks.us/pvc")
                    created = timestamp(obj["metadata"].get("creationTimestamp"))
                    if pvc in app["pvcs"] and created and created > newest.get(pvc, (0, ""))[0]:
                        newest[pvc] = (created, obj.get("status", {}).get("phase", ""))
                if len(newest) == len(app["pvcs"]):
                    ok = all(phase in ("Completed", "Succeeded") and 0 <= now - created <= 8 * 86400
                             for created, phase in newest.values())
                    state("restore", 1 if ok else 3)
                    if ok:
                        metrics.add("homelab_restore_last_success_timestamp_seconds",
                                    min(created for created, _ in newest.values()), **scope)

            if fence_ok and 0 <= now - fence_ok:
                metrics.add("homelab_fence_job_last_success_timestamp_seconds", fence_ok, **scope)
            lease = lease_txt(f"{app['name']}.lease.tnwks.local.")
            valid = lease in {site["cluster"] for site in self.inventory["sites"]} | {"none"}
            metrics.add("homelab_lease_authority_reachable", int(valid), **scope)
            if valid:
                metrics.add("homelab_site_lease_blocks_local", int(lease == self.cluster), **scope)
                if lease == self.cluster:
                    state("fence", 3)
                # No positive fence evidence: the CronJob can succeed even when
                # every DNS lookup failed. A current TXT answer cannot backfill it.

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
# The archive's block index, as the Compactor last synced it from the bucket.
ARCHIVE_INDEX = "http://observability-compactor.monitoring.svc.cluster.local:10902/api/v1/blocks"


def archive_index(url: str = ARCHIVE_INDEX, timeout: float = 5) -> dict[str, dict] | None:
    """Per source cluster: newest archived sample, oldest archived sample, block
    count and bytes, from the Compactor's block list. None if it can't be read.
    Each block carries the external `cluster` label of the Prometheus that made it."""
    try:
        with urllib.request.build_opener(NoRedirect()).open(url, timeout=timeout) as response:
            blocks = json.load(response)["data"]["blocks"]
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        return None
    sites: dict[str, dict] = {}
    for block in blocks:
        cluster = (block.get("thanos") or {}).get("labels", {}).get("cluster")
        if not cluster:
            continue
        site = sites.setdefault(cluster, {"newest": 0.0, "oldest": float("inf"), "blocks": 0, "bytes": 0})
        site["newest"] = max(site["newest"], block["maxTime"] / 1000)
        site["oldest"] = min(site["oldest"], block["minTime"] / 1000)
        site["blocks"] += 1
        site["bytes"] += sum(f.get("size_bytes", 0) for f in block["thanos"].get("files", []))
    return sites


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


def lease_txt(name: str, timeout: float = 2.0) -> str | None:
    """First TXT string of an absolute name from the pod's resolver, or None (no
    answer, an error, or no record). Same lookup as the fence and the app gates."""
    try:
        with open("/etc/resolv.conf") as f:
            server = next(line.split()[1] for line in f if line.startswith("nameserver"))
        qid = random.randint(0, 0xFFFF)
        query = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
        for label in name.rstrip(".").split("."):
            query += bytes([len(label)]) + label.encode()
        query += b"\x00" + struct.pack(">HH", 16, 1)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(query, (server, 53))
            resp, _ = s.recvfrom(4096)
        rid, flags, qd, an, _, _ = struct.unpack(">HHHHHH", resp[:12])
        if rid != qid or flags & 0x000F:
            return None
        off = 12
        for _ in range(qd):
            while resp[off]:
                off += resp[off] + 1
            off += 5
        for _ in range(an):
            if resp[off] & 0xC0 == 0xC0:
                off += 2
            else:
                while resp[off]:
                    off += resp[off] + 1
                off += 1
            rtype, _, _, rdlen = struct.unpack(">HHIH", resp[off:off + 10])
            off += 10
            if rtype == 16 and rdlen:
                return resp[off + 1:off + 1 + resp[off]].decode()
            off += rdlen
    except (OSError, StopIteration, IndexError, struct.error, UnicodeDecodeError):
        return None
    return None


def _mqtt_string(value: str) -> bytes:
    data = value.encode()
    return len(data).to_bytes(2, "big") + data


def _mqtt_packet(kind: int, body: bytes) -> bytes:
    length, encoded = len(body), b""
    while True:
        byte, length = length % 128, length // 128
        encoded += bytes([byte | (0x80 if length else 0)])
        if not length:
            return bytes([kind]) + encoded + body


def _mqtt_read(sock) -> tuple[int, bytes]:
    head = sock.recv(1)
    if not head:
        raise OSError("closed")
    length, shift = 0, 0
    while True:
        byte = sock.recv(1)[0]
        length += (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            break
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            raise OSError("closed")
        body += chunk
    return head[0], body


def mqtt_probe(host: str, username: str, password: str, port: int = 1883,
               timeout: float = 3) -> tuple[bool, int | None, float]:
    """Log in (MQTT 3.1.1) and read the retained $SYS client count. Returns whether
    the broker accepted the login, the count if it arrived in time, and the duration.
    Reads only $SYS; never subscribes to application topics."""
    start = time.monotonic()
    clients = None
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_mqtt_packet(0x10, _mqtt_string("MQTT") + bytes([4, 0xC2]) + (10).to_bytes(2, "big")
                                      + _mqtt_string("observability-evidence-" + os.environ.get("CLUSTER_NAME", "x"))
                                      + _mqtt_string(username) + _mqtt_string(password)))
            kind, body = _mqtt_read(sock)
            if kind != 0x20 or len(body) < 2 or body[1] != 0:
                return False, None, time.monotonic() - start
            topic = "$SYS/broker/clients/connected"
            sock.sendall(_mqtt_packet(0x82, (1).to_bytes(2, "big") + _mqtt_string(topic) + b"\x00"))
            deadline = start + timeout
            while clients is None and time.monotonic() < deadline:
                kind, body = _mqtt_read(sock)
                if kind & 0xF0 == 0x30:
                    size = int.from_bytes(body[:2], "big")
                    if body[2:2 + size].decode() == topic:
                        clients = int(body[2 + size:].decode().strip())
            sock.sendall(b"\xe0\x00")
    except (OSError, ValueError, IndexError):
        return clients is not None, clients, time.monotonic() - start
    return True, clients, time.monotonic() - start


def content_probe(url: str, expect: str, timeout: float = 3) -> tuple[bool, float]:
    """2xx and the expected string in the first 64 KiB. Fixed inventory URLs only."""
    start = time.monotonic()
    context = None
    if url.startswith("https://"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
            ok = 200 <= response.status < 300 and expect in response.read(65536).decode("utf-8", "replace")
    except (OSError, urllib.error.URLError, ValueError):
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
                if os.environ.get("MQTT_PASSWORD"):
                    ok, clients, duration = mqtt_probe(os.environ.get("MQTT_HOST", "mosquitto"),
                                                       os.environ.get("MQTT_USERNAME", ""),
                                                       os.environ["MQTT_PASSWORD"])
                    labels = {"observer_cluster": args.cluster, "k8s_cluster": args.cluster,
                              "observability_target": "mqtt"}
                    peers.add("probe_success", int(ok), **labels)
                    peers.add("probe_duration_seconds", duration, **labels)
                    if clients is not None:
                        peers.add("homelab_mqtt_clients_connected", clients,
                                  observer_cluster=args.cluster, k8s_cluster=args.cluster)
                index = archive_index()
                if index is not None:
                    for cluster, site in sorted(index.items()):
                        scope = {"observer_cluster": args.cluster, "k8s_cluster": cluster}
                        peers.add("homelab_archive_newest_sample_timestamp_seconds", site["newest"], **scope)
                        peers.add("homelab_archive_oldest_sample_timestamp_seconds", site["oldest"], **scope)
                        peers.add("homelab_archive_blocks", site["blocks"], **scope)
                        peers.add("homelab_archive_bytes", site["bytes"], **scope)
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
