"""Check the passive destination and the fields of Wazuh alerts."""
import importlib.util
import json
from ipaddress import ip_address, ip_network
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "kubernetes/apps/security/wazuh-settings/app/scripts"


@pytest.fixture
def monitors(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("test_findings_monitors", SCRIPTS / "monitors.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_active_response_channel_stops_reconciliation_before_writes(monitors):
    class Indexer:
        def call(self, method, path):
            assert method == "GET" and "config_type=active_response" in path
            return 200, {"total_hits": 1, "total_hit_relation": "eq"}

    with pytest.raises(monitors.Stop, match="expected zero active response channels"):
        monitors.apply(Indexer(), "fixture", ["high"], print)


@pytest.mark.parametrize("level", ["high", "critical"])
def test_monitor_sends_one_static_notification_per_new_rule(monitors, level):
    monitor = monitors.monitor(level, "fixture")
    search = monitor["inputs"][0]["search"]
    assert search["indices"] == ["wazuh-findings-v5-*"]
    assert not search["query"]["_source"]
    action = monitor["triggers"][0]["bucket_level_trigger"]["actions"][0]
    assert action["destination_id"] == monitors.CHANNEL_ID
    assert action["action_execution_policy"]["action_execution_scope"]["per_alert"] == {
        "actionable_alerts": ["NEW"]
    }
    (payload,) = json.loads(action["message_template"]["source"])
    assert set(payload["labels"]) == {"alertname", "cluster", "severity", "rule_id"}
    assert payload["labels"]["rule_id"] == "{{#ctx.newAlerts}}{{bucket_keys}}{{/ctx.newAlerts}}"
    assert set(payload["annotations"]) == {"summary"}


def test_indexer_webhook_exception_is_only_for_its_alertmanager_service():
    wazuh = yaml.safe_load_all((ROOT / "kubernetes/apps/security/wazuh/ks.yaml").read_text())
    server = next(doc for doc in wazuh if doc["metadata"]["name"] == "security-wazuh-server")
    patch = next(p for p in server["spec"]["patches"] if p["target"].get("name") == "wazuh-indexer"
                 and p["target"]["kind"] == "StatefulSet")
    (container,) = yaml.safe_load(patch["patch"])["spec"]["template"]["spec"]["containers"]
    settings = {entry["name"]: entry.get("value") for entry in container["env"]}
    assert settings["opensearch.notifications.core.allowed_config_types"] == "webhook"
    denied = [ip_network(cidr.strip()) for cidr in settings[
        "opensearch.notifications.core.http.host_deny_list"].split(",")]
    service = yaml.safe_load((ROOT / "kubernetes/clusters/wsl-02/apps/alertmanager"
                              "/wazuh-webhook-service.yaml").read_text())
    allowed = ip_address(service["spec"]["clusterIP"])
    assert not any(allowed in subnet for subnet in denied)
    four = [subnet for subnet in denied if subnet.version == 4 and subnet.subnet_of(ip_network("10.0.0.0/8"))]
    assert sum(subnet.num_addresses for subnet in four) == ip_network("10.0.0.0/8").num_addresses - 1
    assert len(set(four)) == len(four)
