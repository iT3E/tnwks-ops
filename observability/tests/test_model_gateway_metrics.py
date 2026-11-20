"""The metrics of the model gateway (kubernetes/apps/ai/litellm, docs/apps/litellm.md, "Metrics").

The gateway makes its series from a keep list (configmap.yaml): each metric
that the list names, with the labels that it names and no other. These tests
read the files:

- the keep list names only the kept labels, never a label of a person, a
  device or a key, and no setting adds a label;
- the cap of the series of each metric keeps the worst case under the gate of
  the design (5,000 series);
- the scrape (helmrelease.yaml, serviceMonitor) sends the key of the scrape,
  keeps only the series of the keep list, and writes `undeclared` for each
  key name that is not in its declared list, which these tests name;
- the key of the scrape is pinned in plain text (scripts/ensure_keys.py): its
  model list is a name that is no model and its only route is /metrics, and
  an entry with other values is refused before any call;
- the daily check (scripts/key_check.py, key-check.yaml) calls each model
  name of the config with that key, passes only when each call is refused,
  and never prints the key.

At the pinned release (v1.109.1) the facts below are in the source:
`prometheus_metrics_config` and `prometheus_metrics_max_series_per_metric`
(litellm/__init__.py), the labels of each metric
(litellm/types/integrations/prometheus.py, PrometheusMetricLabels), and
/metrics asks for a key by default (require_auth_for_metrics_endpoint). A key
that is not an admin key reads /metrics only when its allowed_routes name it
(litellm/proxy/auth/route_checks.py).
"""

import http.server
import importlib.util
import json
import re
import threading
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/ai/litellm/app"
CONFIGMAP = APP / "configmap.yaml"
HELMRELEASE = APP / "helmrelease.yaml"
KUSTOMIZATION = APP / "kustomization.yaml"
KEY_CHECK = APP / "key-check.yaml"
ENSURE_KEYS = APP / "scripts/ensure_keys.py"
KEY_CHECK_SCRIPT = APP / "scripts/key_check.py"
SCRAPE_SECRET = "secret-metrics-scrape.sops.yaml"

# The metrics of the keep list and their kind at the pinned release.
KEPT = {"litellm_proxy_total_requests_metric": "counter", "litellm_proxy_failed_requests_metric": "counter",
        "litellm_request_total_latency_metric": "histogram", "litellm_input_tokens_metric": "counter",
        "litellm_output_tokens_metric": "counter"}
# The labels that a series may keep: the name of the key, the name asked for,
# the provider and the status (design section 4.2.8, step 2).
ALLOWED = {"api_key_alias", "requested_model", "api_provider", "status_code", "exception_status"}
# Never: a person, a device, a session, or the hash of a key.
FORBIDDEN = {"end_user", "user", "user_email", "user_alias", "client_ip", "user_agent", "hashed_api_key",
             "team", "team_alias", "org_id", "org_alias", "project_id", "project_alias", "tag"}
# The histogram of the pinned release: 17 bounds, +Inf, _sum and _count.
HISTOGRAM_SERIES = 20
# The gate of the design: the new metric names of a step stay under it.
GATE = 5000
# The key names that the scrape keeps as they are. The key of each trust
# domain, the transcriber's key, the key of the scrape, "None" (a call with no
# valid key) and "other" (the cap). Each other name reads `undeclared`.
DECLARED = {"agents-main", "agents-finance", "agents-workflows", "agents-pilot", "transcriber",
            "metrics-scrape", "None", "other"}


def config():
    return yaml.safe_load(yaml.safe_load(CONFIGMAP.read_text())["data"]["config.yaml"])


def endpoint():
    values = yaml.safe_load(HELMRELEASE.read_text())["spec"]["values"]
    monitors = values["serviceMonitor"]
    assert list(monitors) == ["app"]
    assert monitors["app"]["serviceName"] == "litellm-app"
    [only] = monitors["app"]["endpoints"]
    return only


def relabel(labels, rules):
    """Prometheus metric relabeling: keep, replace (the default), labeldrop.
    Each regex is anchored. Returns None for a dropped series."""
    labels = dict(labels)
    for rule in rules:
        action = rule.get("action", "replace")
        regex = re.compile(f"^(?:{rule.get('regex', '(.*)')})$")
        if action == "labeldrop":
            labels = {name: value for name, value in labels.items() if not regex.match(name)}
            continue
        value = rule.get("separator", ";").join(labels.get(name, "") for name in rule["sourceLabels"])
        match = regex.match(value)
        if action == "keep":
            if not match:
                return None
        elif action == "replace":
            if match:
                result = match.expand(rule.get("replacement", "$1").replace("$", "\\"))
                if result:
                    labels[rule["targetLabel"]] = result
                else:
                    labels.pop(rule["targetLabel"], None)
        else:
            raise AssertionError(f"action {action} is not in this model")
    return labels


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_keep_list_names_only_the_kept_labels():
    settings = config()["litellm_settings"]
    assert settings["callbacks"] == ["prometheus"]
    groups = settings["prometheus_metrics_config"]
    named = [metric for group in groups for metric in group["metrics"]]
    assert sorted(named) == sorted(KEPT), "each kept metric once, and no other"
    for group in groups:
        # A group with no label list keeps each label of its metrics.
        labels = set(group["include_labels"])
        assert labels, group["group"]
        assert labels <= ALLOWED, (group["group"], sorted(labels - ALLOWED))
        assert not labels & FORBIDDEN
        assert {"api_key_alias", "requested_model", "api_provider"} <= labels, group["group"]
    # No setting that adds a label to each metric, and /metrics keeps its key.
    for name in settings:
        assert not name.startswith(("custom_prometheus", "prometheus_emit", "prometheus_exclude")), name
        assert name not in ("prometheus_deployment_and_latency_caller_identity",
                            "prometheus_user_budget_label_include_email_alias"), name
    assert settings.get("require_auth_for_metrics_endpoint", True) is True
    assert "prometheus" not in json.dumps(config()["general_settings"]).lower()


def test_the_cap_keeps_the_worst_case_under_the_gate():
    cap = config()["litellm_settings"]["prometheus_metrics_max_series_per_metric"]
    assert isinstance(cap, int) and cap > 0
    # Each metric has `cap` label sets and one overflow set ("other").
    worst = sum((cap + 1) * (HISTOGRAM_SERIES if kind == "histogram" else 1) for kind in KEPT.values())
    assert worst < GATE, worst


def test_the_scrape_sends_the_key_of_the_scrape():
    point = endpoint()
    assert point["path"] == "/metrics" and point["port"] == "http"
    assert point["authorization"] == {"type": "Bearer",
                                      "credentials": {"name": "litellm-metrics-scrape", "key": "token"}}
    resources = yaml.safe_load(KUSTOMIZATION.read_text())["resources"]
    assert f"./{SCRAPE_SECRET}" in resources


def test_the_secret_of_the_scrape_key_is_in_git():
    """Fails until the lead adds the encrypted file (the two editor scripts of
    the report of step 2). Its name and key only: no value is read."""
    text = (APP / SCRAPE_SECRET).read_text()
    assert re.search(r"(?m)^\s+name: litellm-metrics-scrape$", text)
    assert re.search(r"(?m)^\s+token: ENC\[", text), "the value is encrypted"


def test_the_scrape_keeps_only_the_series_of_the_keep_list():
    rules = endpoint()["metricRelabelings"]
    kept = set()
    for metric, kind in KEPT.items():
        kept |= {f"{metric}_{end}" for end in (("bucket", "sum", "count") if kind == "histogram" else ("total",))}
    others = {f"{metric}_created" for metric in KEPT} | {
        "litellm_in_flight_requests", "litellm_spend_metric_total", "litellm_deployment_state",
        "litellm_llm_api_latency_metric_bucket", "process_resident_memory_bytes", "python_gc_objects_collected_total"}
    for name in kept:
        assert relabel({"__name__": name, "api_key_alias": "agents-main"}, rules) is not None, name
    for name in others:
        assert relabel({"__name__": name, "api_key_alias": "agents-main"}, rules) is None, name


def test_the_scrape_names_the_declared_key_names():
    rules = endpoint()["metricRelabelings"]
    [declared] = [rule for rule in rules if rule.get("targetLabel") == "__tmp_declared"]
    assert set(declared["regex"].split("|")) == DECLARED
    # Each trust domain of the gateway config has its key name in the list.
    domains = set(config()["router_settings"]["model_group_alias"])
    assert domains == {name for name in DECLARED if name.startswith("agents-")}


@pytest.mark.parametrize("alias, shown", [
    ("agents-main", "agents-main"), ("transcriber", "transcriber"), ("metrics-scrape", "metrics-scrape"),
    ("None", "None"), ("other", "other"), ("made-by-hand", "undeclared"), ("agents-main-old", "undeclared"),
    ("Agents-Main", "undeclared"), ("", None)])
def test_a_key_name_outside_the_list_reads_undeclared(alias, shown):
    rules = endpoint()["metricRelabelings"]
    labels = {"__name__": "litellm_proxy_total_requests_metric_total", "requested_model": "agents-main",
              "status_code": "200"}
    if alias:
        labels["api_key_alias"] = alias
    out = relabel(labels, rules)
    assert out.get("api_key_alias") == shown
    assert "__tmp_declared" not in out
    assert {name: value for name, value in out.items() if name != "api_key_alias"} == {
        name: value for name, value in labels.items() if name != "api_key_alias"}


@pytest.fixture
def ensure_keys(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test-master-not-a-key")
    module = load(ENSURE_KEYS, "ensure_keys_under_test")
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        return (404, {}) if method == "GET" else (200, {})

    monkeypatch.setattr(module, "call", fake)
    return module, calls


def test_the_scrape_key_is_pinned_in_plain_text(ensure_keys):
    module, calls = ensure_keys
    assert module.PINNED == {"metrics-scrape": {"models": ["no-default-models"], "allowed_routes": ["/metrics"]}}
    line = module.ensure({"alias": "metrics-scrape", "key": "sk-test-scrape-0000000000",
                          "models": ["no-default-models"], "allowed_routes": ["/metrics"]})
    assert line == "metrics-scrape: created (models: no-default-models, routes: /metrics)"
    [(_, _, _), (method, path, spec)] = calls
    assert (method, path) == ("POST", "/key/generate")
    assert spec["models"] == ["no-default-models"] and spec["allowed_routes"] == ["/metrics"]
    assert "sk-test-scrape" not in line


@pytest.mark.parametrize("entry", [
    {"models": []},
    {"models": ["no-default-models"]},
    {"models": ["no-default-models", "agents-main"], "allowed_routes": ["/metrics"]},
    {"models": ["no-default-models"], "allowed_routes": ["/metrics", "/v1/chat/completions"]},
    {"allowed_routes": ["/metrics"]},
])
def test_an_other_scrape_key_entry_is_refused_before_any_call(ensure_keys, entry):
    module, calls = ensure_keys
    with pytest.raises(RuntimeError, match="refused"):
        module.ensure({"alias": "metrics-scrape", "key": "sk-test-scrape-0000000000", **entry})
    assert calls == []


def test_the_other_keys_keep_their_form(ensure_keys):
    module, calls = ensure_keys
    line = module.ensure({"alias": "agents-main", "key": "sk-test-main-000000000000",
                          "models": ["agents-main", "agents-main-embeddings"]})
    assert line == "agents-main: created (models: agents-main, agents-main-embeddings)"
    assert "allowed_routes" not in calls[-1][2]


def test_the_check_calls_each_model_name_of_the_config():
    module = load(KEY_CHECK_SCRIPT, "key_check_under_test")
    parsed = config()
    names = {entry["model_name"] for entry in parsed["model_list"]}
    names |= set(parsed["router_settings"]["model_group_alias"])
    assert set(module.model_names(CONFIGMAP.read_text())) == names
    assert len(names) >= 10


class Gateway(http.server.BaseHTTPRequestHandler):
    """A fake gateway: `answers` maps a model name to a status; each call is
    recorded with its key and body."""

    answers: ClassVar[dict] = {}
    seen: ClassVar[list] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Gateway.seen.append((self.path, self.headers["Authorization"], body))
        status = Gateway.answers.get(body["model"], 403)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"echo": self.headers["Authorization"]}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Gateway.answers, Gateway.seen = {}, []
    (tmp_path / "token").write_text("sk-test-scrape-0123456789abcdef\n")
    monkeypatch.setenv("LITELLM_URL", f"http://127.0.0.1:{server.server_address[1]}")
    monkeypatch.setenv("CONFIG_FILE", str(CONFIGMAP))
    monkeypatch.setenv("KEY_FILE", str(tmp_path / "token"))
    yield load(KEY_CHECK_SCRIPT, "key_check_under_test")
    server.shutdown()


def test_the_check_passes_when_each_call_is_refused(gateway, capsys):
    assert gateway.main() == 0
    out = capsys.readouterr().out
    names = set(gateway.model_names(CONFIGMAP.read_text())) | {"no-default-models"}
    assert {body["model"] for _, _, body in Gateway.seen} == names
    for path, key, body in Gateway.seen:
        assert path == "/v1/chat/completions"
        assert key == "Bearer sk-test-scrape-0123456789abcdef"
        assert body["messages"] == [{"role": "user", "content": "ping"}] and body["max_tokens"] == 1
    assert "sk-test-scrape" not in out
    assert json.loads(out.splitlines()[-1])["result"] == "ok"


@pytest.mark.parametrize("status", [200, 401, 400, 500, 429])
def test_the_check_fails_on_any_other_answer(gateway, capsys, status):
    Gateway.answers = {"agents-main": status}
    assert gateway.main() == 1
    out = capsys.readouterr().out
    assert "sk-test-scrape" not in out
    lines = [json.loads(line) for line in out.splitlines()]
    assert [line["name"] for line in lines if line.get("result") == "failed" and "name" in line] == ["agents-main"]
    assert lines[-1]["result"] == "failed"


def test_the_check_fails_with_no_gateway(gateway, capsys, monkeypatch):
    monkeypatch.setattr(gateway, "URL", "http://127.0.0.1:9")
    assert gateway.main() == 1
    assert "sk-test-scrape" not in capsys.readouterr().out


def test_the_cronjob_reads_the_key_the_config_and_the_script():
    [job] = [doc for doc in yaml.safe_load_all(KEY_CHECK.read_text()) if doc]
    assert job["kind"] == "CronJob" and job["metadata"]["name"] == "litellm-key-check"
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["key"]["secret"]["secretName"] == "litellm-metrics-scrape"
    assert volumes["config"]["configMap"]["name"] == "litellm-config"
    generated = {item["name"]: item["files"] for item in yaml.safe_load(KUSTOMIZATION.read_text())["configMapGenerator"]}
    assert volumes["scripts"]["configMap"]["name"] in generated
    assert generated[volumes["scripts"]["configMap"]["name"]] == ["key_check.py=./scripts/key_check.py"]
    [container] = pod["containers"]
    assert container["command"] == ["python3", "/scripts/key_check.py"]
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["KEY_FILE"] == "/key/token" and env["CONFIG_FILE"] == "/config/config.yaml"
    # The key is a file, never an environment variable.
    assert not any("valueFrom" in item for item in container["env"])
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["fsGroup"] == 65534
