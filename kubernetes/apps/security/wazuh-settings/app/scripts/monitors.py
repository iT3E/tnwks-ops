"""Reconcile findings monitors using the channel managed by the indexer."""
import json

from indexer import NoAnswer, Stop, q

MONITORS = "/_plugins/_alerting/monitors"
CONFIGS = "/_plugins/_notifications/configs"
PREFIX = "tnwks-wazuh-findings-"
CHANNEL_ID = "tnwks-wazuh-alertmanager"
ALERTMANAGER_URL = "http://alertmanager-wazuh.monitoring.svc.cluster.local:9093/api/v2/alerts"
LEVELS = {"high": ("warning", "2"), "critical": ("critical", "1")}


def channel(url=ALERTMANAGER_URL):
    return {
        "name": CHANNEL_ID,
        "description": "tnwks Wazuh findings",
        "config_type": "webhook",
        "is_enabled": True,
        "webhook": {"url": url, "method": "POST", "header_params": {"Content-Type": "application/json"}},
    }


def monitor(level, cluster, enabled=True):
    severity, priority = LEVELS[level]
    message = json.dumps([{
        "labels": {"alertname": "WazuhFinding", "cluster": cluster, "severity": severity,
                   "rule_id": "{{#ctx.newAlerts}}{{bucket_keys}}{{/ctx.newAlerts}}"},
        "annotations": {"summary": f"Wazuh detected a {level} finding"},
    }])
    return {
        "type": "monitor",
        "name": PREFIX + level,
        "monitor_type": "bucket_level_monitor",
        "enabled": enabled,
        "schedule": {"period": {"interval": 1, "unit": "MINUTES"}},
        "inputs": [{"search": {
            "indices": ["wazuh-findings-v5-*"],
            "query": {
                "size": 0,
                "_source": False,
                "query": {"bool": {"filter": [
                    {"range": {"@timestamp": {"gte": "{{period_end}}||-5m", "lte": "{{period_end}}",
                                             "format": "epoch_millis"}}},
                    {"term": {"wazuh.rule.level": level}},
                ]}},
                "aggregations": {"rules": {"composite": {"size": 100,
                    "sources": [{"rule": {"terms": {"field": "wazuh.rule.id"}}}]} }},
            },
        }}],
        "triggers": [{"bucket_level_trigger": {
            "id": PREFIX + level + "-trigger",
            "name": "New finding",
            "severity": priority,
            "condition": {"buckets_path": {"count": "_count"}, "parent_bucket_path": "rules",
                          "script": {"source": "params.count > 0", "lang": "painless"}},
            "actions": [{
                "id": PREFIX + level + "-notify",
                "name": "Notify Alertmanager",
                "destination_id": CHANNEL_ID,
                "message_template": {"source": message, "lang": "mustache"},
                "throttle_enabled": False,
                "action_execution_policy": {
                    "action_execution_scope": {"per_alert": {"actionable_alerts": ["NEW"]}}
                },
            }],
        }}],
    }


def includes(actual, expected):
    """OpenSearch adds defaults, IDs and user metadata to saved objects."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(includes(actual.get(k), v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            includes(got, wanted) for got, wanted in zip(actual, expected, strict=True)
        )
    return actual == expected


def active_channel_count(client):
    _, response = client.call("GET", CONFIGS + "?config_type=active_response&max_items=1")
    count = response.get("total_hits")
    if type(count) is not int or count < 0 or response.get("total_hit_relation") != "eq":
        raise Stop("could not verify the active response channel count")
    return count


def managed_monitors(client):
    _, response = client.call("POST", MONITORS + "/_search", {
        "track_total_hits": True, "size": 100,
        "query": {"prefix": {"monitor.name.keyword": PREFIX}},
    })
    hits = response["hits"]
    if hits["total"] != {"value": len(hits["hits"]), "relation": "eq"} or response["timed_out"]:
        raise Stop("could not list all managed Wazuh monitors")
    monitors = {}
    for hit in hits["hits"]:
        saved = hit["_source"]
        name = saved["name"]
        if not name.startswith(PREFIX) or name in monitors:
            raise Stop("ambiguous managed Wazuh monitor")
        monitors[name] = (hit["_id"], saved)
    return monitors


def require_zero_channels(client, report_count):
    count = active_channel_count(client)
    if report_count is not None:
        report_count(count)
    if count:
        raise Stop(f"expected zero active response channels, found {count}")


def wait_for_channel(client, enabled, wait, sleep, clock):
    deadline = clock() + wait
    path = f"{CONFIGS}/{q(CHANNEL_ID)}"
    while True:
        try:
            status, data = client.call("GET", path, ok=(200, 404, 503))
        except NoAnswer:
            status = None
        if status == 404 and not enabled:
            return
        if status == 200:
            configs = data.get("config_list")
            if not isinstance(configs, list) or len(configs) != 1 or not includes(configs[0], {"config": channel()}):
                raise Stop("the managed Alertmanager channel differs from its declaration")
            if enabled:
                return
        remaining = deadline - clock()
        if remaining <= 0:
            raise Stop("the indexer has not applied the Alertmanager channel setting")
        sleep(min(5, remaining))


def apply(client, cluster, enabled, log, *, report_count=None):
    require_zero_channels(client, report_count)
    existing = managed_monitors(client)
    for level in LEVELS if enabled else ():
        desired = monitor(level, cluster)
        current = existing.pop(desired["name"], None)
        if current is None:
            client.call("POST", MONITORS, desired)
        elif not includes(current[1], desired):
            client.call("PUT", f"{MONITORS}/{q(current[0])}", desired)
    for monitor_id, _ in existing.values():
        client.call("DELETE", f"{MONITORS}/{q(monitor_id)}")
    require_zero_channels(client, report_count)
    log("active response channels: 0")
    return 0
