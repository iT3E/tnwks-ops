import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import Collector, Metrics, timestamp
from dashboards import CLUSTERS, catalogue, generated, resource
from inventory import inventory
from kiosk_proxy import sanitize


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
