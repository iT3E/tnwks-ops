"""Verify the Cognito gate and the manager's read-only role mapping."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WAZUH = ROOT / "kubernetes/apps/security/wazuh/app"
ROLES = ROOT / "kubernetes/apps/security/wazuh-settings/app/config/accounts.json"


@pytest.fixture
def manager_rbac():
    file = WAZUH / "config/dashboard/manager-rbac.py"
    spec = importlib.util.spec_from_file_location("manager_rbac", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_internal_ingress_requires_the_same_group_as_the_indexer():
    ingress, policy = yaml.safe_load_all((WAZUH / "dashboard-access.yaml").read_text())
    annotations = ingress["metadata"]["annotations"]
    spec = ingress["spec"]
    assert spec["ingressClassName"] == "nginx-internal"
    assert spec["tls"][0]["hosts"] == [spec["rules"][0]["host"]]
    group = parse_qs(annotations["nginx.ingress.kubernetes.io/auth-url"].split("?", 1)[1])["allowed_groups"]
    role = json.loads(ROLES.read_text())["roles"]["tnwks-dashboard-reader"]
    assert group == role["backend_roles"] == ["wazuh-readers"]
    assert annotations["nginx.ingress.kubernetes.io/auth-response-headers"] == "Authorization"
    logout = annotations["nginx.ingress.kubernetes.io/server-snippet"]
    assert "location = /auth/logout {" in logout and "if ($request_method != POST)" in logout
    assert "rewrite ^ /api/v1/auth/logout last;" in logout
    cookies = annotations["nginx.ingress.kubernetes.io/configuration-snippet"]
    assert '$request_uri ~ "^/auth/logout([?]|$)"' in cookies
    assert all(f"{name}=; Path=/; Max-Age=0;" in cookies for name in ("wz-token", "wz-user", "wz-api"))
    assert role["tenant_permissions"] == [
        {"tenant_patterns": ["global_tenant"], "allowed_actions": ["kibana_all_read"]}
    ]
    (rule,) = policy["spec"]["ingress"]
    (source,) = rule["fromEndpoints"]
    assert source["matchLabels"]["k8s:app.kubernetes.io/instance"] == "ingress-nginx-internal"
    assert source["matchLabels"]["io.cilium.k8s.policy.cluster"] == "${CLUSTER_NAME}"


def test_dashboard_reader_has_only_the_declared_index_reads():
    role = json.loads(ROLES.read_text())["roles"]["tnwks-dashboard-reader"]
    assert role["cluster_permissions"] == ["cluster_composite_ops_ro"]
    assert all(permission["allowed_actions"] == ["read"] for permission in role["index_permissions"])
    assert {index for permission in role["index_permissions"] for index in permission["index_patterns"]} == {
        "wazuh-findings-v5-*",
        ".ds-wazuh-findings-v5-*",
        "wazuh-events-v5-*",
        ".ds-wazuh-events-v5-*",
        "wazuh-states-sca",
        "wazuh-states-vulnerabilities",
        "wazuh-states-fim-*",
        "wazuh-states-inventory-*",
        "wazuh-threatintel-enrichments-*",
        "wazuh-active-responses",
        ".ds-wazuh-active-responses-*",
        ".kibana",
    }


def test_jwt_dashboard_has_an_authenticated_ingress_and_a_local_readiness_check():
    server = next(doc for doc in yaml.safe_load_all((WAZUH.parent / "ks.yaml").read_text())
                  if doc["metadata"]["name"] == "security-wazuh-server")
    patch = next(p["patch"] for p in server["spec"]["patches"] if p["target"]["name"] == "wazuh-dashboard")
    (dashboard,) = yaml.safe_load(patch)["spec"]["template"]["spec"]["containers"]
    assert "--opensearch_security.auth.type=jwt" in dashboard["args"]
    logout_index = dashboard["args"].index("--opensearch_security.auth.logout_url")
    assert dashboard["args"][logout_index + 1] == "$(WAZUH_DASHBOARD_LOGOUT_URL)"
    env = {item["name"]: item["valueFrom"] for item in dashboard["env"]}
    assert env["WAZUH_DASHBOARD_LOGOUT_URL"] == {
        "configMapKeyRef": {"name": "wazuh-dashboard-logout", "key": "url"}
    }
    assert "--opensearch_security.multitenancy.enabled=true" in dashboard["args"]
    assert "--opensearch_security.multitenancy.tenants.enable_private=false" in dashboard["args"]
    assert not any(arg.startswith("--opensearch_security.readonly_mode.roles") for arg in dashboard["args"])
    assert "--status.allowAnonymous=true" in dashboard["args"]
    readiness = dashboard["readinessProbe"]["exec"]["command"][-1]
    assert "--cacert " in readiness and "127.0.0.1" in readiness and "/api/status" in readiness
    for probe in ("startupProbe", "livenessProbe"):
        assert dashboard[probe] == {"exec": None, "tcpSocket": {"port": "dashboard"}}


def test_dashboard_logout_uses_the_registered_cognito_callback():
    app = yaml.safe_load((WAZUH / "kustomization.yaml").read_text())
    (config,) = [item for item in app["configMapGenerator"] if item["name"] == "wazuh-dashboard-logout"]
    assert config["options"]["annotations"]["kustomize.toolkit.fluxcd.io/substitute"] == "enabled"
    (value,) = config["literals"]
    key, url = value.split("=", 1)
    assert key == "url"
    logout = urlsplit(url)
    assert (logout.scheme, logout.netloc, logout.path) == ("https", "auth.${SECRET_DOMAIN}", "/logout")
    assert parse_qs(logout.query) == {
        "client_id": ["${OAUTH2_PROXY_CLIENT_ID}"], "logout_uri": ["https://oauth2.${INTERNAL_DOMAIN}/oauth2/sign_out"],
    }


def test_indexer_preserves_its_notifications_hook_when_enabling_dashboard_access():
    server = next(doc for doc in yaml.safe_load_all((WAZUH.parent / "ks.yaml").read_text())
                  if doc["metadata"]["name"] == "security-wazuh-server")
    patch = next(p["patch"] for p in server["spec"]["patches"] if p["target"]["name"] == "wazuh-indexer")
    indexer = yaml.safe_load(patch)
    annotations = indexer["metadata"]["annotations"]
    assert set(annotations["configmap.reloader.stakater.com/reload"].split(",")) == {
        "wazuh-notifications", "wazuh-dashboard-access",
    }
    pod = indexer["spec"]["template"]["spec"]
    config_maps = {v["name"]: v["configMap"]["name"] for v in pod["volumes"] if "configMap" in v}
    assert config_maps == {"notifications": "wazuh-notifications", "dashboard-access": "wazuh-dashboard-access"}
    (container,) = pod["containers"]
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["plugins.security.restapi.roles_enabled"].split(",") == [
        "all_access", "security_rest_api_access", "tnwks-dashboard-admin",
    ]
    assert [(v["name"], v["mountPath"], v["readOnly"]) for v in container["volumeMounts"]] == [
        ("notifications", "/notifications", True),
        ("dashboard-access", "/dashboard-access", True),
    ]
    shell, options, command = container["lifecycle"]["postStart"]["exec"]["command"]
    assert (shell, options) == ("/bin/bash", "-ec")
    assert command.splitlines() == [
        "/bin/bash /notifications/indexer-notifications.sh",
        "exec /bin/bash /dashboard-access/indexer-auth.sh",
    ]


class Manager:
    def __init__(self):
        self.rules = {}
        self.mutations = []

    def request_json(self, method, path, auth, body=None):
        route = urlsplit(path)
        query = parse_qs(route.query)
        if method == "GET":
            name = query["q"][0].removeprefix("name=")
            if route.path.endswith("/roles"):
                role_id = {"administrator": 1, "readonly": 2}[name]
                records = [{"id": role_id, "name": name}]
            else:
                records = [self.rules[name]] if name in self.rules else []
            return {"affected_items": records, "total_affected_items": len(records)}
        self.mutations.append((method, path))
        if method == "DELETE":
            rule_id = int(query["rule_ids"][0])
            name = next(name for name, rule in self.rules.items() if rule["id"] == rule_id)
            del self.rules[name]
        elif route.path == "/security/rules":
            rule_id = max((rule["id"] for rule in self.rules.values()), default=100) + 1
            self.rules[body["name"]] = {"id": rule_id, **body, "roles": []}
        elif route.path in ("/security/roles/1/rules", "/security/roles/2/rules"):
            rule_id = int(query["rule_ids"][0])
            rule = next(rule for rule in self.rules.values() if rule["id"] == rule_id)
            rule["roles"] = [int(route.path.split("/")[3])]
        else:
            raise AssertionError(path)
        return {}


def test_the_manager_rule_is_idempotent_and_rolls_back(manager_rbac):
    manager = Manager()
    manager_rbac.reconcile(manager, "fixture", enabled=True, admin_enabled=True)
    reader = manager.rules["tnwks-dashboard-reader"]
    admin = manager.rules["tnwks-dashboard-admin"]
    assert (reader["rule"], reader["roles"]) == ({"FIND": {"backend_roles": "wazuh-readers"}}, [2])
    assert (admin["rule"], admin["roles"]) == ({"FIND": {"backend_roles": "wazuh-admins"}}, [1])
    assert len(manager.mutations) == 4
    manager_rbac.reconcile(manager, "fixture", enabled=True, admin_enabled=True)
    assert len(manager.mutations) == 4
    manager_rbac.reconcile(manager, "fixture", enabled=True, admin_enabled=False)
    assert manager.rules == {"tnwks-dashboard-reader": reader}
    manager_rbac.reconcile(manager, "fixture", enabled=False, admin_enabled=True)
    assert manager.rules == {}


def test_manager_rule_with_another_role_is_refused(manager_rbac):
    manager = Manager()
    name = "tnwks-dashboard-admin"
    manager.rules[name] = {"id": 101, "name": name, "rule": {"FIND": {"backend_roles": "admins"}}, "roles": [1]}
    with pytest.raises(RuntimeError, match="administrator declaration"):
        manager_rbac.reconcile(manager, "fixture", enabled=True, admin_enabled=True)
    assert manager.mutations == []


def test_manager_retries_when_daemons_are_starting_after_login(manager_rbac):
    class StartingManager(Manager):
        attempts = 0

        def request_json(self, method, path, auth, body=None):
            if path == "/security/user/authenticate":
                return {"token": "fixture-token"}
            if method == "GET" and self.attempts == 0:
                self.attempts += 1
                raise manager_rbac.ManagerStarting("fixture daemon is starting")
            return super().request_json(method, path, auth, body)

    manager = StartingManager()
    waits = []
    manager_rbac.reconcile_on_startup(manager, "fixture", True, False, clock=lambda: 0, sleep=waits.append)
    assert waits == [5]
    assert manager.rules["tnwks-dashboard-reader"]["roles"] == [2]


def test_manager_reauthenticates_if_deleting_a_rule_revokes_the_token(manager_rbac):
    class RevokingManager(Manager):
        token_number = 0

        def request_json(self, method, path, auth, body=None):
            if path == "/security/user/authenticate":
                self.token_number += 1
                return {"token": str(self.token_number)}
            if auth != f"Bearer {self.token_number}":
                raise manager_rbac.ManagerTokenExpired
            reply = super().request_json(method, path, auth, body)
            if method == "DELETE":
                self.token_number += 1
            return reply

    manager = RevokingManager()
    manager_rbac.reconcile_on_startup(manager, "fixture", True, True, clock=lambda: 0, sleep=lambda _: None)
    waits = []
    manager_rbac.reconcile_on_startup(manager, "fixture", True, False, clock=lambda: 0, sleep=waits.append)
    assert waits == [5]
    assert set(manager.rules) == {"tnwks-dashboard-reader"}


@pytest.mark.parametrize("error", [1017, 1000])
def test_only_manager_daemon_startup_is_a_retryable_api_error(manager_rbac, error):
    manager = manager_rbac.LocalManager.__new__(manager_rbac.LocalManager)
    manager.request = lambda *_: None
    manager.close = lambda: None
    manager.getresponse = lambda: SimpleNamespace(
        status=500, read=lambda: json.dumps({"error": error}).encode(),
    )
    with pytest.raises(RuntimeError) as exc:
        manager.request_json("GET", "/security/rules", "fixture")
    assert isinstance(exc.value, manager_rbac.ManagerStarting) == (error == 1017)


# The start hook of the indexer runs indexer-notifications.sh, then
# indexer-auth.sh. These tests run both scripts with stubs for curl, sleep, the
# clock (date +%s), setpriv and securityadmin.sh, and with their paths and
# TMPDIR moved under tmp_path. Only sleep moves the clock. No stub calls gh.
STUB = r'''
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

root = Path(os.environ["STUB_ROOT"])
tool, args = sys.argv[1], sys.argv[2:]
clock = int((root / "clock").read_text())
if tool == "securityadmin":
    config = Path(args[args.index("-f") + 1]).read_text()
    key = "securityadmin"
    call = {"method": "securityadmin", "path": "", "clock": clock, "config": config}
else:
    with_value = {"--cacert", "--cert", "--key", "--connect-timeout", "--max-time", "--resolve",
                  "--output", "--write-out", "--request", "--header", "--data", "--json"}
    options, url, index = {}, None, 0
    while index < len(args):
        if args[index] in with_value:
            options[args[index]] = args[index + 1]
            index += 2
        else:
            url = args[index]
            index += 1
    body = options.get("--data", options.get("--json"))
    method = options.get("--request", "POST" if body is not None else "GET")
    route = urlsplit(url)
    key = f"{method} {route.path}"
    call = {"method": method, "path": route.path, "query": route.query, "body": body,
            "max_time": int(options["--max-time"]), "clock": clock,
            "resolve": options["--resolve"], "write_out": options["--write-out"]}
with (root / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps(call) + "\n")
answers = json.loads((root / "answers.json").read_text())
queue = answers.get(key, [[418, ""]])
status, text = queue.pop(0) if len(queue) > 1 else queue[0]
(root / "answers.json").write_text(json.dumps(answers))
if tool == "securityadmin":
    sys.exit(status)
if status == 0:
    sys.stdout.write("000")
    sys.exit(7)
Path(options["--output"]).write_text(text)
sys.stdout.write(str(status))
'''

START = 1_000_000
LOG_LINE = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ wazuh-indexer start hook, (notifications|auth): "
CHANNEL = "/_plugins/_notifications/configs/tnwks-wazuh-alertmanager"
MAPPING = "/_plugins/_security/api/rolesmapping/wazuh_ai_assistant"
OWN_CHANNEL = json.dumps({"config_list": [{"config_id": "tnwks-wazuh-alertmanager", "config": {
    "name": "tnwks-wazuh-alertmanager", "config_type": "webhook"}}]}, separators=(",", ":"))


class Hook:
    def __init__(self, root: Path):
        self.root = root
        (root / "bin").mkdir()
        (root / "stub.py").write_text(STUB)
        stub = f'#!/bin/bash\nexec {sys.executable} "$STUB_ROOT/stub.py"'
        self.tool(root / "bin/curl", f'{stub} curl "$@"')
        # The service user can write to the log of the container: this stub
        # keeps its arguments and writes what it reads to service-user.log.
        self.tool(root / "bin/setpriv", "#!/bin/bash\nprintf '%s\\037' \"$@\" >> \"$STUB_ROOT/setpriv.calls\"\n"
                  'echo >> "$STUB_ROOT/setpriv.calls"\ncat >> "$STUB_ROOT/service-user.log"')
        (root / "tmp").mkdir()
        clock = '"$STUB_ROOT/clock"'
        self.tool(root / "bin/sleep", f"#!/bin/bash\necho $(( $(cat {clock}) + $1 )) > {clock}")
        date = shutil.which("date")
        self.tool(root / "bin/date",
                  f'#!/bin/bash\nif [ "$*" = +%s ]; then cat {clock}; else exec {date} "$@"; fi')
        tools = root / "indexer/plugins/opensearch-security/tools"
        tools.mkdir(parents=True)
        self.tool(tools / "securityadmin.sh", f'{stub} securityadmin "$@"')
        packaged = root / "indexer/config/opensearch-security"
        packaged.mkdir(parents=True)
        (packaged / "config.yml").write_text(
            "config:\n  dynamic:\n    kibana:\n      multitenancy_enabled: false\n    authc:\n      basic: {}\n")
        (root / "dashboard-access").mkdir()
        (root / "dashboard-access/indexer-oidc.yaml").write_text("# fixture\n      openid_auth_domain: {}\n")
        (root / "notifications").mkdir()
        (root / "clock").write_text(str(START))
        self.answers = {"GET /_cluster/health": [[200, "{}"]],
                        "GET /_plugins/_security/api/securityconfig": [[200, "{}"]]}

    @staticmethod
    def tool(path: Path, text: str):
        path.write_text(text + "\n")
        path.chmod(0o755)

    def run(self, name: str, enabled: bool):
        script, mount = {"notifications": ("config/indexer-notifications.sh", "notifications/"),
                         "auth": ("config/dashboard/indexer-auth.sh", "dashboard-access/")}[name]
        text = (WAZUH / script).read_text()
        for path, moved in [("/proc/1/fd/2", "container.log"), ("/tmp/wazuh-indexer-hook-deadline", "deadline"),
                            ("/usr/share/wazuh-indexer", "indexer"), (f"/{mount}", mount)]:
            assert path in text
            text = text.replace(path, f"{self.root}/{moved}")
        (self.root / f"{name}.sh").write_text(text)
        value = "true" if enabled else "false"
        (self.root / "notifications/notifications-enabled").write_text(value + "\n")
        (self.root / "dashboard-access/enabled").write_text(value)
        (self.root / "answers.json").write_text(json.dumps(self.answers))
        env = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
               "STUB_ROOT": str(self.root), "TMPDIR": str(self.root / "tmp")}
        return subprocess.run(["bash", str(self.root / f"{name}.sh")], env=env, capture_output=True,
                              text=True, timeout=120, check=False)

    @property
    def calls(self):
        file = self.root / "calls.jsonl"
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    @property
    def writes(self):
        return [(c["method"], c["path"]) for c in self.calls if c["method"] != "GET"]

    @property
    def clock(self):
        return int((self.root / "clock").read_text())

    @property
    def log(self):
        return self.read_log("container.log")

    def read_log(self, name: str):
        lines = (self.root / name).read_text().splitlines()
        assert lines and all(re.match(LOG_LINE, line) for line in lines)
        return "\n".join(lines)


@pytest.fixture
def hook(tmp_path):
    return Hook(tmp_path)


def test_the_hook_scripts_wait_for_a_yellow_cluster_over_loopback_before_any_write(hook):
    hook.answers["GET /_cluster/health"] = [[0, ""], [503, ""], [408, '{"timed_out":true}'], [200, "{}"]]
    hook.answers[f"GET {CHANNEL}"] = [[404, ""]]
    hook.answers["POST /_plugins/_notifications/configs"] = [[200, "{}"]]
    assert hook.run("notifications", enabled=True).returncode == 0
    calls = hook.calls
    assert [(c["method"], c["path"]) for c in calls[:4]] == [("GET", "/_cluster/health")] * 4
    assert {c["query"] for c in calls[:4]} == {"wait_for_status=yellow&timeout=5s"}
    assert {c["resolve"] for c in calls} == {"wazuh-indexer.security.svc.cluster.local:9200:127.0.0.1"}
    assert {c["write_out"] for c in calls} == {"%{http_code}"}
    assert "cluster health: try again in 5 s (HTTP 000, curl exit 7)" in hook.log
    assert "cluster health: try again in 5 s (HTTP 408)" in hook.log
    assert hook.clock == START + 15


@pytest.mark.parametrize("enabled", [True, False])
def test_the_auth_step_writes_only_after_a_yellow_cluster_and_the_security_api(hook, enabled):
    hook.answers["GET /_cluster/health"] = [[503, ""], [200, "{}"]]
    hook.answers["GET /_plugins/_security/api/securityconfig"] = [[503, ""], [200, "{}"]]
    hook.answers[f"PATCH {MAPPING}"] = [[200, "{}"]]
    hook.answers["securityadmin"] = [[0, ""]]
    assert hook.run("auth", enabled=enabled).returncode == 0
    reads = [c["path"] for c in hook.calls[:4]]
    assert reads == ["/_cluster/health"] * 2 + ["/_plugins/_security/api/securityconfig"] * 2
    assert len(hook.calls) == 6


@pytest.mark.parametrize("write", ["check", "create", "update", "delete"])
def test_the_channel_step_retries_a_server_error_until_it_passes(hook, write):
    enabled = write != "delete"
    check = [[404, ""]] if write in ("check", "create") else [[200, OWN_CHANNEL]]
    if write == "check":
        check = [[500, "fixture-body"], [0, ""]] + check
    hook.answers[f"GET {CHANNEL}"] = check
    retried = [[502, "fixture-body"], [503, ""], [200, "{}"]]
    hook.answers["POST /_plugins/_notifications/configs"] = retried if write == "create" else [[200, "{}"]]
    hook.answers[f"PUT {CHANNEL}"] = retried
    hook.answers[f"DELETE {CHANNEL}"] = [[500, ""], [404, ""]]
    result = hook.run("notifications", enabled=enabled)
    assert result.returncode == 0, result.stderr
    expected = {"check": [("POST", "/_plugins/_notifications/configs")],
                "create": [("POST", "/_plugins/_notifications/configs")] * 3,
                "update": [("PUT", CHANNEL)] * 3,
                "delete": [("DELETE", CHANNEL)] * 2}[write]
    assert hook.writes == expected
    name = "check" if write == "check" else write
    assert f"channel {name}: try again in 5 s (HTTP 5" in hook.log
    assert "fixture-body" not in hook.log + result.stdout + result.stderr


@pytest.mark.parametrize("answers", [
    {f"GET {CHANNEL}": [[404, ""]], "POST /_plugins/_notifications/configs": [[409, "{}"]]},
    {f"GET {CHANNEL}": [[200, OWN_CHANNEL]], f"PUT {CHANNEL}": [[409, "{}"]]},
    {f"GET {CHANNEL}": [[409, "{}"]]},
    {f"GET {CHANNEL}": [[200, OWN_CHANNEL.replace('"name":"tnwks-wazuh-alertmanager"', '"name":"other"')]]},
    {f"GET {CHANNEL}": [[200, OWN_CHANNEL.replace('"webhook"', '"slack"')]]},
], ids=["create-409", "update-409", "check-409", "other-name", "other-type"])
def test_a_conflict_or_another_object_stays_a_hard_error(hook, answers):
    hook.answers.update(answers)
    result = hook.run("notifications", enabled=True)
    assert result.returncode == 1
    channel_calls = [c for c in hook.calls if c["path"] != "/_cluster/health"]
    assert len(channel_calls) == len(answers)
    assert "try again" not in hook.log
    assert "failed" in hook.log.splitlines()[-1]
    if "409" not in str(answers):
        assert "already in use" in hook.log


def test_the_channel_step_fails_at_the_deadline_and_no_try_passes_it(hook):
    hook.answers[f"GET {CHANNEL}"] = [[500, ""]]
    result = hook.run("notifications", enabled=True)
    assert result.returncode == 1
    assert "channel check: failed (HTTP 500), no time is left before the deadline" in hook.log
    deadline = START + 300
    assert hook.clock <= deadline
    assert all(1 <= c["max_time"] <= max(1, min(10, deadline - c["clock"])) for c in hook.calls)


def test_the_auth_step_keeps_the_deadline_of_the_hook(hook):
    # Step 1 passes after 200 seconds, so step 2 has the 100 seconds that are left.
    hook.answers[f"GET {CHANNEL}"] = [[500, ""]] * 40 + [[404, ""]]
    hook.answers["POST /_plugins/_notifications/configs"] = [[200, "{}"]]
    assert hook.run("notifications", enabled=True).returncode == 0
    assert hook.clock == START + 200
    hook.answers["securityadmin"] = [[1, ""]]
    hook.answers[f"PATCH {MAPPING}"] = [[200, "{}"]]
    result = hook.run("auth", enabled=False)
    assert result.returncode == 1
    assert "auth config: failed (securityadmin.sh exit 1), no time is left before the deadline" in hook.log
    assert START + 295 < hook.clock <= START + 300
    assert hook.writes[-1] == ("securityadmin", "")


@pytest.mark.parametrize("enabled", [True, False])
def test_the_auth_step_retries_each_write_on_its_own_and_keeps_their_order(hook, enabled):
    hook.answers[f"PATCH {MAPPING}"] = [[500, ""], [0, ""], [200, "{}"]]
    hook.answers["securityadmin"] = [[1, ""], [124, ""], [0, ""]]
    result = hook.run("auth", enabled=enabled)
    assert result.returncode == 0, result.stderr
    mapping, auth = [("PATCH", MAPPING)] * 3, [("securityadmin", "")] * 3
    assert hook.writes == (mapping + auth if enabled else auth + mapping)
    users = ["admin", "kibanaserver", "wazuh-manager"] if enabled else ["*"]
    patch = next(c for c in hook.calls if c["method"] == "PATCH")
    assert json.loads(patch["body"]) == [{"op": "replace", "path": "/users", "value": users}]
    configs = {c["config"] for c in hook.calls if c["method"] == "securityadmin"}
    assert len(configs) == 1 and ("openid_auth_domain" in configs.pop()) == enabled
    assert "assistant mapping: try again in 5 s (HTTP 500)" in hook.log
    assert "auth config: try again in 5 s (securityadmin.sh exit 124)" in hook.log


def test_a_refused_mapping_stays_a_hard_error_and_admits_no_auth_domain(hook):
    hook.answers[f"PATCH {MAPPING}"] = [[403, "{}"]]
    hook.answers["securityadmin"] = [[0, ""]]
    result = hook.run("auth", enabled=True)
    assert result.returncode == 1
    assert hook.writes == [("PATCH", MAPPING)]
    assert "assistant mapping: failed (HTTP 403)" in hook.log


def test_each_log_line_is_written_as_root_while_root_may_open_the_log(hook):
    hook.answers[f"GET {CHANNEL}"] = [[500, ""], [404, ""]]
    hook.answers["POST /_plugins/_notifications/configs"] = [[200, "{}"]]
    assert hook.run("notifications", enabled=True).returncode == 0
    assert "channel check: try again in 5 s (HTTP 500)" in hook.log
    assert not (hook.root / "setpriv.calls").exists()


@pytest.mark.parametrize("name", ["notifications", "auth"])
def test_each_log_line_is_written_as_the_service_user_when_root_may_not_open_the_log(hook, name):
    # PID 1 runs as the service user, so a write of root to its stderr fails.
    (hook.root / "container.log").mkdir()
    hook.answers[f"GET {CHANNEL}"] = [[500, ""], [404, ""]]
    hook.answers["POST /_plugins/_notifications/configs"] = [[200, "{}"]]
    hook.answers[f"PATCH {MAPPING}"] = [[500, ""], [200, "{}"]]
    hook.answers["securityadmin"] = [[0, ""]]
    result = hook.run(name, enabled=True)
    assert result.returncode == 0, result.stderr
    log = hook.read_log("service-user.log")
    assert "try again in 5 s (HTTP 500)" in log
    calls = (hook.root / "setpriv.calls").read_text().splitlines()
    assert len(calls) == len(log.splitlines())
    assert {tuple(call.split("\x1f")[:-1]) for call in calls} == {(
        "--reuid=wazuh-indexer", "--regid=wazuh-indexer", "--init-groups",
        "--inh-caps=-all,+dac_override", "--ambient-caps=+dac_override",
        "/bin/sh", "-c", f"cat >> {hook.root}/container.log",
    )}
    assert log in result.stderr.replace("\r", "")


@pytest.mark.parametrize("written", [str(START - 10), str(START + 10_000), "0", "not-a-time"])
def test_the_auth_step_takes_300_seconds_for_a_deadline_that_is_not_in_the_next_300(hook, written):
    (hook.root / "deadline").write_text(written + "\n")
    hook.answers["securityadmin"] = [[1, ""]]
    hook.answers[f"PATCH {MAPPING}"] = [[200, "{}"]]
    result = hook.run("auth", enabled=False)
    assert result.returncode == 1
    assert "the deadline of step 1 is not in the next 300 s, so this step has 300 s" in hook.log
    assert "no time is left before the deadline" in hook.log
    assert START + 295 < hook.clock <= START + 300
    assert not (hook.root / "deadline").exists()


def test_a_create_that_gets_a_409_after_a_retry_checks_the_channel_again_and_updates_it(hook):
    # The first POST made the channel but gave no answer.
    hook.answers[f"GET {CHANNEL}"] = [[404, ""], [200, OWN_CHANNEL]]
    hook.answers["POST /_plugins/_notifications/configs"] = [[0, ""], [409, "{}"]]
    hook.answers[f"PUT {CHANNEL}"] = [[200, "{}"]]
    result = hook.run("notifications", enabled=True)
    assert result.returncode == 0, result.stderr
    assert hook.writes == [("POST", "/_plugins/_notifications/configs")] * 2 + [("PUT", CHANNEL)]
    assert "channel create: HTTP 409 after a retry, so check the channel again" in hook.log
    assert "channel update: done (HTTP 200)" in hook.log


def test_a_409_after_a_retry_on_a_channel_of_another_object_stays_a_hard_error(hook):
    hook.answers[f"GET {CHANNEL}"] = [[404, ""], [200, OWN_CHANNEL.replace('"webhook"', '"slack"')]]
    hook.answers["POST /_plugins/_notifications/configs"] = [[503, ""], [409, "{}"]]
    result = hook.run("notifications", enabled=True)
    assert result.returncode == 1
    assert hook.writes == [("POST", "/_plugins/_notifications/configs")] * 2
    assert "already in use" in hook.log
