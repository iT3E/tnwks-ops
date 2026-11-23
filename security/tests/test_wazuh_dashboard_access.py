"""Verify the Cognito gate and the manager's read-only role mapping."""
import importlib.util
import json
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
    assert all(p["allowed_actions"] == ["read"] for p in role["index_permissions"])
    assert ".kibana" in {index for p in role["index_permissions"] for index in p["index_patterns"]}
    assert role["tenant_permissions"] == [
        {"tenant_patterns": ["global_tenant"], "allowed_actions": ["kibana_all_read"]}
    ]
    (rule,) = policy["spec"]["ingress"]
    (source,) = rule["fromEndpoints"]
    assert source["matchLabels"]["k8s:app.kubernetes.io/instance"] == "ingress-nginx-internal"
    assert source["matchLabels"]["io.cilium.k8s.policy.cluster"] == "${CLUSTER_NAME}"


def test_jwt_dashboard_has_an_authenticated_ingress_and_a_local_readiness_check():
    server = next(doc for doc in yaml.safe_load_all((WAZUH.parent / "ks.yaml").read_text())
                  if doc["metadata"]["name"] == "security-wazuh-server")
    patch = next(p["patch"] for p in server["spec"]["patches"] if p["target"]["name"] == "wazuh-dashboard")
    (dashboard,) = yaml.safe_load(patch)["spec"]["template"]["spec"]["containers"]
    assert "--opensearch_security.auth.type=jwt" in dashboard["args"]
    assert "--opensearch_security.multitenancy.enabled=true" in dashboard["args"]
    assert "--opensearch_security.multitenancy.tenants.enable_private=false" in dashboard["args"]
    assert not any(arg.startswith("--opensearch_security.readonly_mode.roles") for arg in dashboard["args"])
    assert "--status.allowAnonymous=true" in dashboard["args"]
    readiness = dashboard["readinessProbe"]["exec"]["command"][-1]
    assert "--cacert " in readiness and "127.0.0.1" in readiness and "/api/status" in readiness
    for probe in ("startupProbe", "livenessProbe"):
        assert dashboard[probe] == {"exec": None, "tcpSocket": {"port": "dashboard"}}


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
        self.rule = None
        self.mutations = []

    def request_json(self, method, path, auth, body=None):
        route = urlsplit(path)
        if method == "GET":
            records = [{"id": 2, "name": "readonly"}] if route.path.endswith("/roles") else (
                [self.rule] if self.rule else []
            )
            return {"affected_items": records, "total_affected_items": len(records)}
        self.mutations.append((method, path))
        if method == "DELETE":
            self.rule = None
        elif route.path == "/security/rules":
            self.rule = {"id": 101, "name": body["name"], "rule": body["rule"], "roles": []}
        elif route.path == "/security/roles/2/rules":
            assert parse_qs(route.query) == {"rule_ids": ["101"]}
            self.rule["roles"] = [2]
        else:
            raise AssertionError(path)
        return {}


def test_the_manager_rule_is_idempotent_and_rolls_back(manager_rbac):
    manager = Manager()
    manager_rbac.reconcile(manager, "fixture", enabled=True)
    assert manager.rule["rule"] == {"FIND": {"backend_roles": "wazuh-readers"}}
    assert len(manager.mutations) == 2
    manager_rbac.reconcile(manager, "fixture", enabled=True)
    assert len(manager.mutations) == 2
    manager_rbac.reconcile(manager, "fixture", enabled=False)
    assert manager.rule is None
    assert manager.mutations[-1] == ("DELETE", "/security/rules?rule_ids=101")


def test_manager_rule_with_another_role_is_refused(manager_rbac):
    manager = Manager()
    manager.rule = {"id": 101, "name": manager_rbac.RULE, "rule": manager_rbac.CONDITION, "roles": [1]}
    with pytest.raises(RuntimeError, match="read-only declaration"):
        manager_rbac.reconcile(manager, "fixture", enabled=True)
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
    manager_rbac.reconcile_on_startup(manager, "fixture", True, clock=lambda: 0, sleep=waits.append)
    assert waits == [5]
    assert manager.rule["roles"] == [2]


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
