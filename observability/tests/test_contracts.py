import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import Collector, Metrics, timestamp
from dashboards import CLUSTERS, catalogue, generated, resource
from inventory import inventory
from kiosk_proxy import sanitize


EMPTY_IS_NORMAL = {"Override creation age", "Workloads needing attention",
                   "Current firing alerts", "Firing now", "Pending now"}


def test_sdk_generates_isolated_household_and_operator_resources():
    boards = catalogue()
    operator = [b for b in boards if not b.kiosk]
    assert len({b.uid for b in operator}) == len(operator)
    house = next(b for b in boards if b.kiosk)
    assert house.uid == "home"  # Stable embedded URL on a separate Grafana instance.
    for board in boards:
        obj = resource(board)
        assert obj["apiVersion"] == "dashboard.grafana.app/v2"
        assert obj["spec"]["editable"] is False
        assert obj["spec"]["timeSettings"]["autoRefresh"] == "1m"
        assert obj["spec"]["layout"]["kind"] == "TabsLayout"
        elements = obj["spec"]["elements"]
        assert len({p["spec"]["id"] for p in elements.values()}) == len(elements)
        for panel in elements.values():
            spec = panel["spec"]
            assert spec["description"]
            if spec["vizConfig"]["group"] != "text":
                # Nothing to list is the normal state for these, not missing evidence.
                if spec["title"] not in EMPTY_IS_NORMAL:
                    assert "Unknown" in spec["vizConfig"]["spec"]["fieldConfig"]["defaults"]["noValue"]
        variables = obj["spec"]["variables"]
        if board.kiosk:
            assert not variables
            assert not obj["spec"]["links"]
        else:
            variable = next(v["spec"] for v in variables if v["spec"]["name"] == "cluster")
            assert variable["query"] == ",".join(CLUSTERS)
            assert variable["includeAll"]


def test_generation_is_deterministic_and_configmap_payloads_are_bounded():
    a, b = generated(), generated()
    assert a == b
    for path, text in a.items():
        if path.suffix == ".json" and "dashboards" in path.parts:
            assert len(text.encode()) < 500_000
    queries = json.loads(next(v for p, v in a.items() if p.name == "panel-queries.json"))
    for query in queries:
        assert query["expr"]
        assert " or vector(0)" not in query["expr"] or query["title"] == "Reporting sites"


def test_inventory_comes_from_the_existing_failover_catalogue():
    inv = inventory()
    assert {site["cluster"] for site in inv["sites"]} == set(CLUSTERS)
    names = {app["name"] for app in inv["apps"]}
    assert {"home-assistant", "firefly", "media", "unifi"} <= names
    firefly = next(app for app in inv["apps"] if app["name"] == "firefly")
    assert firefly["databases"] == ["firefly-pg", "finance-sync-pg"]
    assert firefly["fencing_required"]
    transcriber = next(app for app in inv["apps"] if app["name"] == "transcriber")
    assert "deployment/transcriber-frontend" in transcriber["workloads"]
    dbs = {(db["cluster"], db["name"]) for db in inv["databases"]}
    assert ("homelab-wsl", "litellm-pg") in dbs
    assert ("homelab-wsl-02", "litellm-pg") not in dbs
    assert all(name != "postgres" for _, name in dbs)  # Retired in v2.0.0.


class FakeApi:
    fail = False
    workload_present = True

    def get(self, path):
        if self.fail:
            raise OSError("private API detail must not appear in metrics")
        if "configmaps?" in path:
            return {"items": [{"metadata": {"creationTimestamp": "2026-11-06T00:00:00Z"},
                               "data": {"TEST_REPLICAS": "0", "PRIVATE_TOKEN": "redacted"}}]}
        if "deployments" in path and self.workload_present:
            return {"items": [{"metadata": {"name": "test"}, "spec": {"replicas": 0},
                               "status": {"readyReplicas": 0}}]}
        if "kustomizations" in path:
            return {"items": [{"metadata": {"name": "test", "namespace": "flux-system"},
                               "status": {"conditions": [{"type": "Ready", "status": "Unknown"}]}}]}
        return {"items": []}


@pytest.fixture
def small_inventory():
    return {"sites": [{"id": "one", "cluster": "one"}, {"id": "two", "cluster": "two"}],
            "apps": [{"name": "test", "namespace": "apps", "prefix": "TEST", "tier": "warm",
                      "clusters": ["one", "two"], "workloads": ["deployment/test"],
                      "databases": ["test-pg"]}]}


def test_missing_workload_retains_expected_rows_and_standby_is_zero_replicas(small_inventory):
    api = FakeApi()
    c = Collector(api, small_inventory, "one", clock=lambda: 1000)
    initial = c.collect().decode()
    assert 'k8s_cluster="two"' in initial
    assert 'homelab_workload_desired_replicas' in initial
    assert "PRIVATE_TOKEN" not in initial
    assert "redacted" not in initial
    assert "homelab_fence_last_verified" not in initial  # Ready API objects are not fence proof.
    api.workload_present = False
    missing = c.collect().decode()
    assert "homelab_expected_workload_info" in missing
    assert "homelab_workload_desired_replicas" not in missing
    assert "homelab_workload_present" in missing


def test_content_probe_only_where_the_app_runs(small_inventory, monkeypatch):
    import collector
    calls = []
    monkeypatch.setattr(collector, "content_probe", lambda url, expect: (calls.append(url), (True, 0.1))[1])
    small_inventory["apps"][0]["probe"] = {"url": "http://test.apps.svc:80/health", "expect": "ok"}
    api = FakeApi()
    standby = Collector(api, small_inventory, "one", clock=lambda: 1000).collect().decode()
    assert calls == [] and 'observability_target="service"' not in standby  # replicas 0 here
    running = Collector(api, small_inventory, "one", clock=lambda: 1000)
    running.workloads = lambda metrics, labels: running.desired.update(test=1)
    out = running.collect().decode()
    assert calls == ["http://test.apps.svc:80/health"]
    assert 'probe_success{app="test",k8s_cluster="one",observability_target="service",observer_cluster="one"} 1' in out


def test_recovery_prerequisites_from_evidence_only(small_inventory, monkeypatch):
    import collector
    now = 2_000_000_000

    class Api(FakeApi):
        def get(self, path):
            if path.endswith("/clusters"):
                return {"items": [{"metadata": {"name": "test-pg"}, "spec": {"instances": 1},
                                   "status": {"phase": "Cluster in healthy state", "readyInstances": 1}}]}
            if path.endswith("/backups"):
                return {"items": [{"spec": {"cluster": {"name": "test-pg"}},
                                   "status": {"phase": "completed", "stoppedAt": "2033-05-18T02:00:00Z"}}]}
            if path.endswith("/cronjobs"):
                return {"items": [{"metadata": {"name": "site-lease-fence"},
                                   "status": {"lastSuccessfulTime": "2033-05-18T03:32:00Z"}}]}
            if "/restores" in path:
                return {"items": [{"metadata": {"labels": {"tnwks.us/pvc": "data"},
                                                "creationTimestamp": "2033-05-14T00:00:00Z"},
                                   "status": {"phase": "Failed"}}]}
            return super().get(path)

    small_inventory["apps"][0]["pvcs"] = ["data"]
    leases = {"test.lease.tnwks.local.": "one"}
    monkeypatch.setattr(collector, "lease_txt", leases.get)
    out = Collector(Api(), small_inventory, "one", clock=lambda: now).collect().decode()

    def got(prerequisite):
        line = next(row for row in out.splitlines() if f'prerequisite="{prerequisite}"' in row)
        return line.rsplit(" ", 1)[1]
    assert got("database") == "1" and got("backup") == "1"
    assert got("restore") == "3"  # the standby's last drill failed
    assert got("fence") == "3"  # the lease names this site
    leases["test.lease.tnwks.local."] = "two"
    out = Collector(Api(), small_inventory, "one", clock=lambda: now).collect().decode()
    assert 'prerequisite="fence"' not in out  # a fleet Job cannot attest a former primary's fence
    assert 'homelab_fence_last_verified_timestamp_seconds' not in out
    assert 'homelab_fence_job_last_success_timestamp_seconds' in out
    assert 'homelab_lease_authority_reachable{app="test",k8s_cluster="one",observer_cluster="one"} 1' in out
    leases.clear()  # unreadable lease: no fence evidence at all
    out = Collector(Api(), small_inventory, "one", clock=lambda: now).collect().decode()
    assert 'prerequisite="fence"' not in out


@pytest.mark.parametrize("scenario", ["unhealthy", "future-backup", "future-restore", "running-without-drill"])
def test_recovery_never_substitutes_readiness_or_future_timestamps_for_proof(small_inventory, monkeypatch, scenario):
    import datetime
    import collector
    now = 2_000_000_000

    def iso(seconds):
        return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat()

    class Api(FakeApi):
        def get(self, path):
            if path.endswith("/clusters"):
                return {"items": [{"metadata": {"name": "test-pg"}, "spec": {"instances": 1},
                                   "status": {"phase": "Cluster in unhealthy state" if scenario == "unhealthy"
                                              else "Cluster in healthy state", "readyInstances": 1}}]}
            if path.endswith("/backups"):
                return {"items": [{"spec": {"cluster": {"name": "test-pg"}},
                                   "status": {"phase": "completed", "stoppedAt": iso(now + 60 if scenario == "future-backup" else now - 60)}}]}
            if "/restores" in path and scenario != "running-without-drill":
                return {"items": [{"metadata": {"labels": {"tnwks.us/pvc": "data"},
                                                "creationTimestamp": iso(now + 60 if scenario == "future-restore" else now - 60)},
                                   "status": {"phase": "Completed"}}]}
            return super().get(path)

    small_inventory["apps"][0]["pvcs"] = ["data"]
    monkeypatch.setattr(collector, "lease_txt", lambda _: "two")
    c = Collector(Api(), small_inventory, "one", clock=lambda: now)
    if scenario == "running-without-drill":
        c.workloads = lambda metrics, labels: c.desired.update(test=1)
    out = c.collect().decode()
    if scenario == "running-without-drill":
        assert 'prerequisite="restore"' not in out
    else:
        prerequisite = {"unhealthy": "database", "future-backup": "backup", "future-restore": "restore"}[scenario]
        line = next(row for row in out.splitlines() if f'prerequisite="{prerequisite}"' in row)
        assert line.endswith(" 3")


def test_mqtt_probe_logs_in_and_reads_the_sys_client_count():
    import socket
    import threading
    import collector

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    seen = {}

    def broker(accept: bool):
        conn, _ = server.accept()
        with conn:
            kind, body = collector._mqtt_read(conn)
            seen["connect"] = (kind, b"MQTT" in body, b"secret" in body)
            conn.sendall(bytes([0x20, 2, 0, 0 if accept else 5]))
            if not accept:
                return
            kind, body = collector._mqtt_read(conn)
            seen["subscribe"] = body[4:].rstrip(b"\x00").decode()
            conn.sendall(bytes([0x90, 3, 0, 1, 0]))
            topic = b"$SYS/broker/clients/connected"
            conn.sendall(collector._mqtt_packet(0x31, len(topic).to_bytes(2, "big") + topic + b"7"))
            collector._mqtt_read(conn)  # DISCONNECT

    port = server.getsockname()[1]
    thread = threading.Thread(target=broker, args=(True,))
    thread.start()
    assert collector.mqtt_probe("127.0.0.1", "observability", "secret", port=port)[:2] == (True, 7)
    thread.join()
    assert seen["connect"] == (0x10, True, True)
    assert seen["subscribe"] == "$SYS/broker/clients/connected"
    thread = threading.Thread(target=broker, args=(False,))
    thread.start()
    assert collector.mqtt_probe("127.0.0.1", "observability", "wrong", port=port)[:2] == (False, None)
    thread.join()
    server.close()


def test_archive_index_summarizes_blocks_per_source_site(tmp_path):
    import collector

    def block(cluster, lo, hi, size):
        return {"minTime": lo * 1000, "maxTime": hi * 1000,
                "thanos": {"labels": {"cluster": cluster}, "files": [{"size_bytes": size}, {}]}}

    index = tmp_path / "blocks.json"
    index.write_text(json.dumps({"status": "success", "data": {"blocks": [
        block("one", 100, 200, 5), block("one", 200, 400, 7), block("two", 50, 90, 1),
        {"minTime": 0, "maxTime": 999000, "thanos": {"labels": {}}}]}}))
    assert collector.archive_index(index.as_uri()) == {
        "one": {"newest": 400, "oldest": 100, "blocks": 2, "bytes": 12},
        "two": {"newest": 90, "oldest": 50, "blocks": 1, "bytes": 1}}
    assert collector.archive_index((tmp_path / "missing.json").as_uri()) is None


def test_failed_collection_retains_old_success_time_without_cached_healthy_samples(small_inventory):
    api = FakeApi()
    now = [1000]
    c = Collector(api, small_inventory, "one", clock=lambda: now[0])
    c.collect()
    api.fail = True
    now[0] = 2000
    failed = c.collect().decode()
    assert "homelab_expected_site_info" in failed
    assert 'homelab_observer_last_success_timestamp_seconds{k8s_cluster="one",observer_cluster="one"} 1000' in failed
    assert "homelab_workload_ready_replicas" not in failed
    assert "private API detail" not in failed


def test_metrics_escape_labels_without_exposing_config_contents():
    m = Metrics()
    m.add("example", 1, name='line\nquote"slash\\')
    m.add("example", float("nan"))
    assert m.render() == b'example{name="line\\nquote\\"slash\\\\"} 1\n'
    assert timestamp("bad") is None
    assert timestamp("2026-11-06T00:00:00Z") > 0


def vector(*states):
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {"domain": "internet", "private_entity": "redacted"},
         "value": [100 + i, str(state)]} for i, state in enumerate(states)]}}


def test_household_facade_drops_identity_and_never_hides_unknown_or_failed_observer():
    cleaned = sanitize(vector(3, 1))
    assert cleaned["data"]["result"][0]["value"][1] == "3"
    assert cleaned["data"]["result"][0]["metric"] == {"domain": "internet"}
    assert sanitize(vector(0, 1))["data"]["result"][0]["value"][1] == "0"
    assert sanitize(vector(1, 4))["data"]["result"][0]["value"][1] == "1"
    assert sanitize(vector())["data"]["result"] == []
    with pytest.raises(ValueError):
        sanitize(vector(999))
    with pytest.raises(ValueError):
        sanitize({"status": "error"})
