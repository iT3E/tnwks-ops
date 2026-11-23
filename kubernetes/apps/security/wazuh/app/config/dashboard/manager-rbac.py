"""Reconcile the manager API's authorization rule for the dashboard group."""
import base64
import http.client
import json
import os
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlencode

HOST = "wazuh-api.security.svc.cluster.local"
READER = ("tnwks-dashboard-reader", "wazuh-readers", "readonly")
ADMIN = ("tnwks-dashboard-admin", "wazuh-admins", "administrator")
CA = "/etc/wazuh-manager/certs/root-ca.pem"


class ManagerStarting(RuntimeError):
    """The API can accept a login before all manager daemons are ready."""


class LocalManager(http.client.HTTPSConnection):
    def __init__(self):
        self.tls = ssl.create_default_context(cafile=CA)
        super().__init__(HOST, port=55000, context=self.tls, timeout=6)

    def connect(self):
        stream = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self.tls.wrap_socket(stream, server_hostname=HOST)

    def request_json(self, method, path, authorization, body=None):
        try:
            self.request(method, path, json.dumps(body) if body else None,
                         {"Authorization": authorization, "Content-Type": "application/json"})
            response = self.getresponse()
            raw = response.read()
        finally:
            self.close()
        if response.status == 500:
            try:
                error = json.loads(raw)
            except ValueError:
                error = None
            if isinstance(error, dict) and error.get("error") == 1017:
                raise ManagerStarting("Manager daemons are not ready")
        if response.status != 200:
            raise RuntimeError(f"{method} {path} returned HTTP {response.status}")
        result = json.loads(raw)
        if result.get("error") or result.get("data", {}).get("total_failed_items", 0):
            raise RuntimeError(f"{method} {path} was refused by the manager API")
        return result["data"]


def select(api, path, name, auth):
    result = api.request_json("GET", path + "?" + urlencode({"q": "name=" + name}), auth)
    items = result["affected_items"]
    if result["total_affected_items"] > 1 or any(item["name"] != name for item in items):
        raise RuntimeError(f"ambiguous manager resource {name}")
    return items[0] if items else None


def reconcile_mapping(api, auth, enabled, rule_name, group, role_name):
    mapping = select(api, "/security/rules", rule_name, auth)
    if not enabled:
        if mapping is not None:
            api.request_json("DELETE", "/security/rules?rule_ids=" + str(mapping["id"]), auth)
        return

    role = select(api, "/security/roles", role_name, auth)
    if role is None:
        raise RuntimeError(f"the manager's {role_name} role is unavailable")

    condition = {"FIND": {"backend_roles": group}}
    if mapping is None:
        api.request_json("POST", "/security/rules", auth, {"name": rule_name, "rule": condition})
        mapping = select(api, "/security/rules", rule_name, auth)

    if mapping is None or mapping["rule"] != condition or set(mapping["roles"]) - {role["id"]}:
        raise RuntimeError(f"the dashboard mapping does not match its {role_name} declaration")

    if role["id"] not in mapping["roles"]:
        query = urlencode({"rule_ids": mapping["id"]})
        api.request_json("POST", f"/security/roles/{role['id']}/rules?{query}", auth)


def reconcile(api, auth, enabled, admin_enabled):
    reconcile_mapping(api, auth, enabled and admin_enabled, *ADMIN)
    reconcile_mapping(api, auth, enabled, *READER)


def reconcile_on_startup(api, basic, enabled, admin_enabled, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + 300
    while True:
        try:
            answer = api.request_json("POST", "/security/user/authenticate", "Basic " + basic)
        except (OSError, http.client.HTTPException, RuntimeError):
            pass
        else:
            try:
                reconcile(api, "Bearer " + answer["token"], enabled, admin_enabled)
            except (OSError, http.client.HTTPException, ManagerStarting):
                pass
            else:
                return
        remaining = deadline - clock()
        if remaining <= 0:
            raise SystemExit("Manager API reconciliation did not become ready")
        sleep(min(5, remaining))


def main():
    enabled = Path("/dashboard-access/enabled").read_text().strip() == "true"
    admin_enabled = Path("/dashboard-access/admin-enabled").read_text().strip() == "true"
    password = os.environ.pop("WAZUH_MANAGER_API_PASSWORD")
    basic = base64.b64encode(f"wazuh:{password}".encode()).decode()
    reconcile_on_startup(LocalManager(), basic, enabled, admin_enabled)


if __name__ == "__main__":
    main()
