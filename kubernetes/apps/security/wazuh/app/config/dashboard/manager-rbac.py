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
RULE = "tnwks-dashboard-reader"
CONDITION = {"FIND": {"backend_roles": "wazuh-readers"}}
CA = "/etc/wazuh-manager/certs/root-ca.pem"


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


def reconcile(api, auth, enabled):
    mapping = select(api, "/security/rules", RULE, auth)
    if not enabled:
        if mapping is not None:
            api.request_json("DELETE", "/security/rules?rule_ids=" + str(mapping["id"]), auth)
        return

    role = select(api, "/security/roles", "readonly", auth)
    if role is None:
        raise RuntimeError("the manager's read-only role is unavailable")

    if mapping is None:
        api.request_json("POST", "/security/rules", auth, {"name": RULE, "rule": CONDITION})
        mapping = select(api, "/security/rules", RULE, auth)

    if mapping is None or mapping["rule"] != CONDITION or set(mapping["roles"]) - {role["id"]}:
        raise RuntimeError("the dashboard mapping does not match its read-only declaration")

    if role["id"] not in mapping["roles"]:
        query = urlencode({"rule_ids": mapping["id"]})
        api.request_json("POST", f"/security/roles/{role['id']}/rules?{query}", auth)


def main():
    enabled = Path("/dashboard-access/enabled").read_text().strip() == "true"
    password = os.environ.pop("WAZUH_MANAGER_API_PASSWORD")
    basic = base64.b64encode(f"wazuh:{password}".encode()).decode()
    api = LocalManager()
    deadline = time.monotonic() + 300
    while True:
        try:
            answer = api.request_json("POST", "/security/user/authenticate", "Basic " + basic)
            break
        except (OSError, http.client.HTTPException, RuntimeError):
            if time.monotonic() >= deadline:
                raise SystemExit("Manager API authentication did not become ready") from None
            time.sleep(5)
    reconcile(api, "Bearer " + answer["token"], enabled)


if __name__ == "__main__":
    main()
