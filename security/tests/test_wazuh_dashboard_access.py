"""Verify the Cognito gate and the manager's read-only role mapping."""
import importlib.util
import json
from pathlib import Path
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
    (rule,) = policy["spec"]["ingress"]
    (source,) = rule["fromEndpoints"]
    assert source["matchLabels"]["k8s:app.kubernetes.io/instance"] == "ingress-nginx-internal"
    assert source["matchLabels"]["io.cilium.k8s.policy.cluster"] == "${CLUSTER_NAME}"


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
